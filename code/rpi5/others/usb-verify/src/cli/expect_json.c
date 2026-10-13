/*
 * ============================================================
 * expect_json.c — usb-fault-inject expect JSON 消费实现
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 解析 usb-fault-inject expect_output_by_id() 输出的单行
 *           JSON，并把 expect 对象映射为 fv_command 的断言阈值/
 *           事件类型，打通跨设备期望值契约（KI-20261008-001）。
 *
 * 说明: 本模块不依赖第三方 JSON 库（host 单测仅用 gcc），实现一个
 *       针对该契约（对象 + 字符串/整数 + 嵌套 expect 对象）的最小
 *       严格解析器；逐字段范围校验，任何非法输入返回 -1（CXX-003）。
 * ============================================================
 */
#include "expect_json.h"
#include "fv_ioctl_compat.h"

#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* expect JSON 文件大小上限（防御超大文件读爆内存，CXX-003） */
#define FV_EXPECT_JSON_MAX (64 * 1024)

/* 解析器游标：指向被解析文本及其位置 */
struct fv_json_parser {
    const char *s;
    size_t      len;
    size_t      pos;
};

/* 返回当前字节（无符号），到达末尾返回 -1 */
static int jp_peek(const struct fv_json_parser *p)
{
    return p->pos < p->len ? (int)(unsigned char)p->s[p->pos] : -1;
}

/* 跳过 JSON 空白（空格/制表/换行/回车） */
static void jp_skip_ws(struct fv_json_parser *p)
{
    while (p->pos < p->len && isspace((unsigned char)p->s[p->pos]))
        p->pos++;
}

/* 断言当前字节为 c，匹配则前移一位；否则返回 -1 */
static int jp_expect(struct fv_json_parser *p, char c)
{
    if (jp_peek(p) != (int)(unsigned char)c)
        return -1;
    p->pos++;
    return 0;
}

/* 匹配并跳过字面量（true/false/null），不匹配返回 -1 */
static int jp_expect_literal(struct fv_json_parser *p, const char *lit)
{
    size_t l = strlen(lit);
    if (p->pos + l > p->len || memcmp(p->s + p->pos, lit, l) != 0)
        return -1;
    p->pos += l;
    return 0;
}

/*
 * jp_parse_string — 解析 JSON 字符串到 out（含转义解码）
 * 支持标准简单转义；\uXXXX 与非法转义一律判失败（契约内 fault 名
 * 均为 ASCII）。超长（含结尾 NUL 放不下）判失败，禁止静默截断。
 */
static int jp_parse_string(struct fv_json_parser *p, char *out, size_t outsz)
{
    if (jp_expect(p, '"') != 0)
        return -1;

    size_t n = 0;
    for (;;) {
        int c = jp_peek(p);
        if (c < 0)
            return -1;
        if (c == '"') {
            p->pos++;
            break;
        }
        if (c == '\\') {
            p->pos++;
            int e = jp_peek(p);
            if (e < 0)
                return -1;
            char decoded;
            switch (e) {
            case '"':  decoded = '"';  break;
            case '\\': decoded = '\\'; break;
            case '/':  decoded = '/';  break;
            case 'b':  decoded = '\b'; break;
            case 'f':  decoded = '\f'; break;
            case 'n':  decoded = '\n'; break;
            case 'r':  decoded = '\r'; break;
            case 't':  decoded = '\t'; break;
            default:   return -1;      /* \uXXXX / 非法转义 */
            }
            p->pos++;
            if (n + 1 >= outsz)
                return -1;
            out[n++] = decoded;
        } else {
            if (n + 1 >= outsz)
                return -1;
            out[n++] = (char)c;
            p->pos++;
        }
    }
    out[n] = '\0';
    return 0;
}

/* 跳过 JSON 字符串（不解码），用于跳过未知字段的值 */
static int jp_skip_string(struct fv_json_parser *p)
{
    if (jp_expect(p, '"') != 0)
        return -1;
    for (;;) {
        int c = jp_peek(p);
        if (c < 0)
            return -1;
        if (c == '\\') {
            p->pos += 2;
            if (p->pos > p->len)
                return -1;
            continue;
        }
        if (c == '"') {
            p->pos++;
            return 0;
        }
        p->pos++;
    }
}

/*
 * jp_parse_int — 解析十进制整数（可选负号）
 * 溢出、无数字均返回 -1。
 */
static int jp_parse_int(struct fv_json_parser *p, long *out)
{
    int neg = 0;
    if (jp_peek(p) == '-') {
        neg = 1;
        p->pos++;
    }
    if (!isdigit(jp_peek(p)))
        return -1;

    long v = 0;
    while (isdigit(jp_peek(p))) {
        int d = p->s[p->pos] - '0';
        if (v > (LONG_MAX - d) / 10)
            return -1; /* 防溢出 */
        v = v * 10 + d;
        p->pos++;
    }
    *out = neg ? -v : v;
    return 0;
}

/* 跳过一个任意 JSON 值（字符串/数字/对象/数组/字面量），用于未知字段 */
static int jp_skip_value(struct fv_json_parser *p)
{
    int c = jp_peek(p);
    if (c == '"')
        return jp_skip_string(p);

    if (c == '{' || c == '[') {
        char open = (char)c;
        char close = (c == '{') ? '}' : ']';
        int depth = 0;
        for (;;) {
            int d = jp_peek(p);
            if (d < 0)
                return -1;
            if (d == '"') {
                if (jp_skip_string(p) != 0)
                    return -1;
                continue;
            }
            if (d == (int)(unsigned char)open) {
                depth++;
            } else if (d == (int)(unsigned char)close) {
                depth--;
                if (depth == 0) {
                    p->pos++;
                    return 0;
                }
            }
            p->pos++;
        }
    }

    if (c == '-' || isdigit(c)) {
        long tmp;
        return jp_parse_int(p, &tmp);
    }

    if (jp_expect_literal(p, "true") == 0 ||
        jp_expect_literal(p, "false") == 0 ||
        jp_expect_literal(p, "null") == 0)
        return 0;
    return -1;
}

/*
 * jp_parse_expect — 解析内层 expect 对象
 * 逐字段范围校验：kernel_event ∈ [0, RATE_DEGRADED]，计数字段 >= -1。
 */
static int jp_parse_expect(struct fv_json_parser *p, struct fv_expect_json *out)
{
    if (jp_expect(p, '{') != 0)
        return -1;
    out->has_expect = 1;

    jp_skip_ws(p);
    if (jp_peek(p) == '}') {
        p->pos++;
        return 0;
    }

    for (;;) {
        char key[32];
        if (jp_parse_string(p, key, sizeof(key)) != 0) {
            fprintf(stderr, "[expect-json] expect 字段名解析失败\n");
            return -1;
        }
        jp_skip_ws(p);
        if (jp_expect(p, ':') != 0)
            return -1;
        jp_skip_ws(p);

        int known =
            strcmp(key, "kernel_event") == 0 || strcmp(key, "error_count") == 0 ||
            strcmp(key, "reset_count") == 0 || strcmp(key, "stall_count") == 0 ||
            strcmp(key, "corrupt_count") == 0 || strcmp(key, "timeout_count") == 0;

        if (!known) {
            if (jp_skip_value(p) != 0)
                return -1;
        } else {
            long val;
            if (jp_parse_int(p, &val) != 0) {
                fprintf(stderr, "[expect-json] expect.%s 非整数\n", key);
                return -1;
            }
            if (strcmp(key, "kernel_event") == 0) {
                if (val < 0 ||
                    val > (long)VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED) {
                    fprintf(stderr,
                            "[expect-json] kernel_event=%ld 越界（合法 0..%d）\n",
                            val, (int)VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED);
                    return -1;
                }
                out->kernel_event = (int)val;
                out->has_kernel_event = 1;
            } else {
                if (val < -1) {
                    fprintf(stderr, "[expect-json] expect.%s=%ld 越界（须 >=-1）\n",
                            key, val);
                    return -1;
                }
                if (strcmp(key, "error_count") == 0)        out->error_count = (int)val;
                else if (strcmp(key, "reset_count") == 0)   out->reset_count = (int)val;
                else if (strcmp(key, "stall_count") == 0)   out->stall_count = (int)val;
                else if (strcmp(key, "corrupt_count") == 0) out->corrupt_count = (int)val;
                else                                        out->timeout_count = (int)val;
            }
        }

        jp_skip_ws(p);
        int c = jp_peek(p);
        if (c == ',') {
            p->pos++;
            jp_skip_ws(p);
            continue;
        }
        if (c == '}') {
            p->pos++;
            return 0;
        }
        fprintf(stderr, "[expect-json] expect 对象分隔符非法\n");
        return -1;
    }
}

/*
 * jp_parse_top — 解析顶层对象，提取 fault 与 expect
 * 以已解析内层 expect 的方式增量填充 out（调用前已初始化默认值）。
 */
static int jp_parse_top(struct fv_json_parser *p, struct fv_expect_json *out)
{
    jp_skip_ws(p);
    if (jp_expect(p, '{') != 0) {
        fprintf(stderr, "[expect-json] 顶层不是 JSON 对象\n");
        return -1;
    }
    jp_skip_ws(p);
    if (jp_peek(p) == '}') {
        p->pos++;
    } else {
        for (;;) {
            char key[32];
            if (jp_parse_string(p, key, sizeof(key)) != 0) {
                fprintf(stderr, "[expect-json] 顶层字段名解析失败\n");
                return -1;
            }
            jp_skip_ws(p);
            if (jp_expect(p, ':') != 0)
                return -1;
            jp_skip_ws(p);

            if (strcmp(key, "fault") == 0) {
                if (jp_parse_string(p, out->fault, sizeof(out->fault)) != 0) {
                    fprintf(stderr, "[expect-json] fault 字段非字符串\n");
                    return -1;
                }
                out->has_fault = 1;
            } else if (strcmp(key, "expect") == 0) {
                if (jp_parse_expect(p, out) != 0)
                    return -1;
            } else {
                if (jp_skip_value(p) != 0)
                    return -1;
            }

            jp_skip_ws(p);
            int c = jp_peek(p);
            if (c == ',') {
                p->pos++;
                jp_skip_ws(p);
                continue;
            }
            if (c == '}') {
                p->pos++;
                break;
            }
            fprintf(stderr, "[expect-json] 顶层对象分隔符非法\n");
            return -1;
        }
    }

    jp_skip_ws(p);
    if (p->pos != p->len) {
        fprintf(stderr, "[expect-json] JSON 尾部存在多余内容\n");
        return -1;
    }
    return 0;
}

int fv_parse_expect_json_buf(const char *buf, size_t len,
                             struct fv_expect_json *out)
{
    if (buf == NULL || out == NULL || len == 0) {
        fprintf(stderr, "[expect-json] 空输入\n");
        return -1;
    }

    struct fv_expect_json tmp;
    memset(&tmp, 0, sizeof(tmp));
    tmp.kernel_event  = -1;
    tmp.error_count   = -1;
    tmp.reset_count   = -1;
    tmp.stall_count   = -1;
    tmp.corrupt_count = -1;
    tmp.timeout_count = -1;

    struct fv_json_parser p = { buf, len, 0 };
    if (jp_parse_top(&p, &tmp) != 0)
        return -1;

    if (!tmp.has_fault) {
        fprintf(stderr, "[expect-json] 缺少 fault 字段\n");
        return -1;
    }
    if (!tmp.has_expect) {
        fprintf(stderr, "[expect-json] 缺少 expect 对象\n");
        return -1;
    }
    if (!tmp.has_kernel_event) {
        fprintf(stderr, "[expect-json] 缺少 expect.kernel_event 字段\n");
        return -1;
    }

    *out = tmp;
    return 0;
}

/* read_whole_file — 读取整个文件到 malloc 缓冲，成功返回 0 */
static int read_whole_file(const char *path, char **out_buf, size_t *out_len)
{
    FILE *f = fopen(path, "rb");
    if (f == NULL) {
        fprintf(stderr, "[expect-json] 无法打开文件 '%s': %s\n",
                path, strerror(errno));
        return -1;
    }
    if (fseek(f, 0, SEEK_END) != 0) {
        fprintf(stderr, "[expect-json] 文件 '%s' 定位失败\n", path);
        fclose(f);
        return -1;
    }
    long sz = ftell(f);
    if (sz <= 0 || (unsigned long)sz > FV_EXPECT_JSON_MAX) {
        fprintf(stderr, "[expect-json] 文件 '%s' 大小非法(%ld)\n", path, sz);
        fclose(f);
        return -1;
    }
    if (fseek(f, 0, SEEK_SET) != 0) {
        fprintf(stderr, "[expect-json] 文件 '%s' 回绕失败\n", path);
        fclose(f);
        return -1;
    }

    char *buf = malloc((size_t)sz);
    if (buf == NULL) {
        fprintf(stderr, "[expect-json] 内存分配失败(%ld)\n", sz);
        fclose(f);
        return -1;
    }
    size_t rd = fread(buf, 1, (size_t)sz, f);
    fclose(f);
    if (rd != (size_t)sz) {
        fprintf(stderr, "[expect-json] 文件 '%s' 读取不完整(%zu/%ld)\n",
                path, rd, sz);
        free(buf);
        return -1;
    }

    *out_buf = buf;
    *out_len = rd;
    return 0;
}

int fv_parse_expect_json_file(const char *path, struct fv_expect_json *out)
{
    if (path == NULL || out == NULL)
        return -1;

    char *buf = NULL;
    size_t len = 0;
    if (read_whole_file(path, &buf, &len) != 0)
        return -1;

    int rc = fv_parse_expect_json_buf(buf, len, out);
    free(buf);
    return rc;
}

int fv_apply_expect_json(const struct fv_expect_json *ej,
                         struct fv_command *cmd)
{
    if (ej == NULL || cmd == NULL)
        return -1;

    if (ej->has_kernel_event &&
        cmd->expect_event_type == VENDOR_LECHAO_USBD_EVENT_NONE) {
        cmd->expect_event_type = (uint32_t)ej->kernel_event;
    }

    if (ej->error_count   >= 0 && cmd->error_ge   == 0) cmd->error_ge   = (uint64_t)ej->error_count;
    if (ej->reset_count   >= 0 && cmd->reset_ge   == 0) cmd->reset_ge   = (uint64_t)ej->reset_count;
    if (ej->stall_count   >= 0 && cmd->stall_ge   == 0) cmd->stall_ge   = (uint64_t)ej->stall_count;
    if (ej->corrupt_count >= 0 && cmd->corrupt_ge == 0) cmd->corrupt_ge = (uint64_t)ej->corrupt_count;
    if (ej->timeout_count >= 0 && cmd->timeout_ge == 0) cmd->timeout_ge = (uint64_t)ej->timeout_count;

    return 0;
}
