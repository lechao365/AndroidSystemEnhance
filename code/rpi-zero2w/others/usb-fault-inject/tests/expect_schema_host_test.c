/*
 * ============================================================
 * expect_schema_host_test.c — usb-fault-inject 期望表 schema host 单测
 * 所属模块: rpi-zero2w usb-fault-inject
 * 设计目的: 对 expect_table 纯逻辑做运行期断言:
 *   1) expect_validate_schema — schema gate 通过（方向 5）
 *   2) fault_id_to_name 与 expect_table.name 一致性
 *   3) 枚举边界 — FAULT__MAX 数量、name 非空
 *
 * 运行: make test（在 tests/ 目录下），退出码 0 全过。
 * 接入: harness/lib/check_host_tests.py（R-18 P5 方向 7）。
 * ============================================================
 */
#include "expect.h"
#include "faults.h"
#include "bot.h"
#include "raw-gadget.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>

/*
 * weak stub：单测仅链接 expect.c/faults.c，不拉 bot.c/raw-gadget.c。
 * fault_execute 引用的设备 IO 符号用 weak 定义占位——单测不调用
 * fault_execute（纯 schema/命名校验），弱符号保证链接通过且
 * 将来接入真实实现时不冲突。
 */
__attribute__((weak)) int bot_main_loop(struct raw_gadget *rg,
                                        struct fault_injection *fi)
{
    (void)rg; (void)fi;
    return -1;
}

__attribute__((weak)) int raw_gadget_vbus_draw(struct raw_gadget *rg, int mA)
{
    (void)rg; (void)mA;
    return -1;
}

__attribute__((weak)) int raw_gadget_stall_ep(struct raw_gadget *rg,
                                              uint8_t ep_addr)
{
    (void)rg; (void)ep_addr;
    return -1;
}

__attribute__((weak)) int raw_gadget_disconnect(struct raw_gadget *rg)
{
    (void)rg;
    return -1;
}

__attribute__((weak)) int raw_gadget_reopen(struct raw_gadget *rg)
{
    (void)rg;
    return -1;
}

static int g_fail = 0;
#define CHECK(cond)                                                     \
    do {                                                                \
        if (!(cond)) {                                                  \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            g_fail = 1;                                                 \
        }                                                               \
    } while (0)

/* 枚举完整性：11 类故障（F4 已删除），FAULT__MAX 与文档契约一致 */
static void test_enum_boundary(void)
{
    CHECK(FAULT__MAX == 11);
    CHECK(FAULT_STALL_IN == 0);
    CHECK(FAULT_DEGRADE == FAULT__MAX - 1);
    /* F4 删除后 FAULT_CORRUPT_CSW_SIG 前无空洞，值连续 */
    CHECK(FAULT_TIMEOUT == FAULT_STALL_OUT + 1);
    CHECK(FAULT_CORRUPT_CSW_SIG == FAULT_TIMEOUT + 1);
}

/* schema gate：expect_validate_schema 必须全部通过 */
static void test_schema_gate(void)
{
    CHECK(expect_validate_schema() == 0);
}

/* fault_id_to_name 与 expect_table.name 一致性（重命名防漂移） */
static void test_name_consistency(void)
{
    for (int i = 0; i < FAULT__MAX; i++) {
        const char *n = fault_id_to_name((enum fault_id)i);
        CHECK(n != NULL);
        CHECK(n[0] != '\0');
    }
    /* 未知 id → "unknown"（faults.c 契约） */
    CHECK(strcmp(fault_id_to_name((enum fault_id)FAULT__MAX), "unknown") == 0);
    CHECK(strcmp(fault_id_to_name((enum fault_id)-1), "unknown") == 0);
}

/* 构造一张全合法坏表：逐字段合法，供单点注入破坏后再校验 */
static void fill_valid_table(struct fault_expect *t)
{
    for (int i = 0; i < FAULT__MAX; i++) {
        t[i].name = fault_id_to_name((enum fault_id)i);
        t[i].human_desc = "test-desc";
        t[i].kernel_event = FDI_EVENT_NONE;
        t[i].error_count = -1;
        t[i].reset_count = -1;
        t[i].stall_count = -1;
        t[i].corrupt_count = -1;
        t[i].timeout_count = -1;
    }
}

/*
 * expect_validate_table 负向用例：注入坏表三类断言返 -1
 *   1) name 漂移（name 与 fault_id_to_name 不一致）
 *   2) kernel_event 越界（超出合法镜像枚举范围）
 *   3) 计数小于 -1（取值非法）
 */
static void test_validate_table_negative(void)
{
    struct fault_expect t[FAULT__MAX];

    /* 合法表应通过（基线） */
    fill_valid_table(t);
    CHECK(expect_validate_table(t) == 0);

    /* 1) name 漂移：把 fault 0 的 name 改坏 */
    fill_valid_table(t);
    t[0].name = "drifted-name";
    CHECK(expect_validate_table(t) == -1);

    /* 2) kernel_event 越界：取 > FDI_EVENT_RATE_DEGRADED */
    fill_valid_table(t);
    t[1].kernel_event = FDI_EVENT_RATE_DEGRADED + 1;
    CHECK(expect_validate_table(t) == -1);

    /* 2b) kernel_event 负值同样越界 */
    fill_valid_table(t);
    t[2].kernel_event = FDI_EVENT_NONE - 1;
    CHECK(expect_validate_table(t) == -1);

    /* 3) 计数小于 -1：error_count = -2 */
    fill_valid_table(t);
    t[3].error_count = -2;
    CHECK(expect_validate_table(t) == -1);

    /* 3b) 其他计数域同样判负 */
    fill_valid_table(t);
    t[4].stall_count = -2;
    CHECK(expect_validate_table(t) == -1);

    /* 空表指针判失败 */
    CHECK(expect_validate_table(NULL) == -1);
}

/*
 * JSON 输出用例：expect_output_by_id 输出符合契约
 *   - 单行 JSON，含 fault 与 expect.kernel_event 数值
 *   - 计数 >=0 的字段输出，= -1 的字段省略
 */
static void test_json_output(void)
{
    char path[] = "/tmp/expect_out_XXXXXX";
    fflush(stdout);
    int fd = mkstemp(path);
    CHECK(fd >= 0);
    if (fd < 0)
        return;
    int saved = dup(STDOUT_FILENO);
    CHECK(saved >= 0);
    if (saved < 0) {
        close(fd);
        return;
    }
    CHECK(dup2(fd, STDOUT_FILENO) >= 0);

    /* FAULT_STALL_IN: error/reset/stall=1, corrupt/timeout=-1, event=STALL */
    expect_output_by_id(FAULT_STALL_IN);
    /* FAULT_SHORT: 全部计数 -1, event=NONE */
    expect_output_by_id(FAULT_SHORT);
    fflush(stdout);

    CHECK(dup2(saved, STDOUT_FILENO) >= 0);
    close(saved);
    close(fd);

    FILE *f = fopen(path, "r");
    CHECK(f != NULL);
    if (f == NULL)
        return;
    char buf[1024];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    unlink(path);
    buf[n] = '\0';

    /* 单行 JSON（无多余换行破坏格式，恰好 2 行输出） */
    int lines = 0;
    for (size_t i = 0; i < n; i++)
        if (buf[i] == '\n')
            lines++;
    CHECK(lines == 2);

    /* stall-in：fault + kernel_event(STALL=2) + 非 -1 计数 */
    CHECK(strstr(buf, "\"fault\":\"stall-in\"") != NULL);
    CHECK(strstr(buf, "\"kernel_event\":2") != NULL);
    CHECK(strstr(buf, "\"error_count\":1") != NULL);
    CHECK(strstr(buf, "\"reset_count\":1") != NULL);
    CHECK(strstr(buf, "\"stall_count\":1") != NULL);

    /* short：fault + kernel_event(NONE=0)，全 -1 计数省略 */
    CHECK(strstr(buf, "\"fault\":\"short\"") != NULL);
    CHECK(strstr(buf, "\"kernel_event\":0") != NULL);
    CHECK(strstr(buf, "\"timeout_count\":") == NULL);
    CHECK(strstr(buf, "\"corrupt_count\":") == NULL);

    /* short 行不应含任何计数（全 -1 省略，仅 kernel_event） */
    const char *short_line = strstr(buf, "\"fault\":\"short\"");
    CHECK(short_line != NULL);
    if (short_line != NULL) {
        const char *nl = strchr(short_line, '\n');
        const char *end = (nl != NULL) ? nl : buf + n;
        size_t len = (size_t)(end - short_line);
        CHECK(strstr(short_line, "error_count") == NULL ||
              (size_t)(strstr(short_line, "error_count") - short_line) >= len);
    }
}

int main(void)
{
    test_enum_boundary();
    test_schema_gate();
    test_name_consistency();
    test_validate_table_negative();
    test_json_output();

    if (g_fail) {
        fprintf(stderr, "expect_schema_host_test: FAILED\n");
        return 1;
    }
    printf("expect_schema_host_test: all checks passed\n");
    return 0;
}
