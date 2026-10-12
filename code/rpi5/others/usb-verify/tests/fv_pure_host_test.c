/*
 * ============================================================
 * fv_pure_host_test.c — usb-verify 纯逻辑 host 单测
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 对不依赖设备 ioctl 的纯函数做编译期+运行期断言:
 *   1) fv_parse_event_type — 事件名↔内核枚举映射契约
 *   2) fv_check_stats       — 统计阈值断言逻辑（含 error/reset）
 *   3) fv_check_event       — 事件类型匹配断言
 *   4) output_check_report  — 断言报告汇总（failed 计数 / 返回码契约）
 *   5) output_degrade_check — check degrade 断言失败返回非零（CXX-004）
 *
 * 运行: make test（在 tests/ 目录下），退出码 0 全过。
 * 接入: harness/lib/check_host_tests.py（R-18 P5 方向 6）。
 * ============================================================
 */
#define _DEFAULT_SOURCE
#include "parse.h"
#include "expect_json.h"
#include "stats_check.h"
#include "event_check.h"
#include "output.h"
#include "fv_ioctl_compat.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <stddef.h>

/* 简易断言宏：失败打印行号并置全局失败标记 */
static int g_fail = 0;
#define CHECK(cond)                                                     \
    do {                                                                \
        if (!(cond)) {                                                  \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            g_fail = 1;                                                 \
        }                                                               \
    } while (0)

/*
 * test_parse_event_type — 事件名映射契约
 * 与内核 vendor_lechao_usbd_event_type 枚举严格对应。
 */
static void test_parse_event_type(void)
{
    CHECK(fv_parse_event_type("stall") == VENDOR_LECHAO_USBD_EVENT_STALL);
    CHECK(fv_parse_event_type("timeout") == VENDOR_LECHAO_USBD_EVENT_TIMEOUT);
    CHECK(fv_parse_event_type("corrupt") == VENDOR_LECHAO_USBD_EVENT_DATA_CORRUPT);
    CHECK(fv_parse_event_type("reset") == VENDOR_LECHAO_USBD_EVENT_RESET);
    CHECK(fv_parse_event_type("transport_error") ==
          VENDOR_LECHAO_USBD_EVENT_TRANSPORT_ERROR);
    CHECK(fv_parse_event_type("degrade") == VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED);
    /* 特殊映射：disconnect → RESET、probe → NONE */
    CHECK(fv_parse_event_type("disconnect") == VENDOR_LECHAO_USBD_EVENT_RESET);
    CHECK(fv_parse_event_type("probe") == VENDOR_LECHAO_USBD_EVENT_NONE);
    /* 未知类型 → 0xFFFFFFFF */
    CHECK(fv_parse_event_type("bogus") == 0xFFFFFFFF);
    CHECK(fv_parse_event_type("") == 0xFFFFFFFF);
}

/*
 * test_proto_macros — 传输协议宏契约 + stats 布局守卫（R1 UAS 维测）
 * 断言 VENDOR_LECHAO_USBD_PROTO_BOT==0 / PROTO_UAS==1，且 stats.protocol
 * 字节紧跟 enabled（原 reserved[0] 语义化，零 ABI bump）——偏移相邻性
 * 防未来在 enabled/protocol 间插入字段致布局漂移。
 */
static void test_proto_macros(void)
{
    CHECK(VENDOR_LECHAO_USBD_PROTO_BOT == 0);
    CHECK(VENDOR_LECHAO_USBD_PROTO_UAS == 1);
    /* 零 ABI bump 守卫：protocol 复用原 reserved[0]，须与 enabled 相邻 */
    CHECK(offsetof(struct vendor_lechao_usbd_stats, protocol) ==
          offsetof(struct vendor_lechao_usbd_stats, enabled) + 1);
}

/*
 * test_check_stats — 统计阈值断言
 * 覆盖全部 7 个 *_ge 字段（stall/timeout/corrupt/disconnect/probe/error/reset）。
 */
static void test_check_stats(void)
{
    struct vendor_lechao_usbd_stats stats;
    struct fv_command cmd;
    struct fv_check_report report;

    memset(&stats, 0, sizeof(stats));
    memset(&cmd, 0, sizeof(cmd));

    /* 全 0 阈值 → 无断言，返回 0 */
    CHECK(fv_check_stats(&stats, &cmd, &report) == 0);
    CHECK(report.count == 0);

    /* error_ge 达标 / 未达标 */
    stats.error_count = 5;
    cmd.error_ge = 3;
    CHECK(fv_check_stats(&stats, &cmd, &report) == 0);
    CHECK(report.count == 1 && report.failed == 0);
    CHECK(report.entries[0].passed == 1);

    stats.error_count = 2;
    CHECK(fv_check_stats(&stats, &cmd, &report) == -1);
    CHECK(report.count == 1 && report.failed == 1);
    CHECK(report.entries[0].passed == 0);

    /* reset_ge */
    memset(&cmd, 0, sizeof(cmd));
    memset(&stats, 0, sizeof(stats));
    stats.reset_count = 10;
    cmd.reset_ge = 10;
    CHECK(fv_check_stats(&stats, &cmd, &report) == 0);
    CHECK(report.entries[0].passed == 1);

    cmd.reset_ge = 11;
    CHECK(fv_check_stats(&stats, &cmd, &report) == -1);

    /* 多字段组合：全部达标 → pass */
    memset(&cmd, 0, sizeof(cmd));
    memset(&stats, 0, sizeof(stats));
    stats.stall_count = 2;
    stats.timeout_count = 1;
    stats.corrupt_count = 3;
    stats.disconnect_count = 4;
    stats.probe_count = 5;
    stats.error_count = 6;
    stats.reset_count = 7;
    cmd.stall_ge = 2;
    cmd.timeout_ge = 1;
    cmd.corrupt_ge = 3;
    cmd.disconnect_ge = 4;
    cmd.probe_ge = 5;
    cmd.error_ge = 6;
    cmd.reset_ge = 7;
    CHECK(fv_check_stats(&stats, &cmd, &report) == 0);
    CHECK(report.count == 7 && report.failed == 0);

    /* 任一不达标 → fail */
    stats.stall_count = 1;
    CHECK(fv_check_stats(&stats, &cmd, &report) == -1);
    CHECK(report.failed == 1);

    /* R1 UAS：protocol 为纯标识字段，不参与阈值断言（fv_check_stats 按
     * *_ge 字段逐个断言，非整结构 memcmp）——置 UAS 值不应改变判定 */
    memset(&cmd, 0, sizeof(cmd));
    memset(&stats, 0, sizeof(stats));
    stats.protocol = VENDOR_LECHAO_USBD_PROTO_UAS;
    CHECK(fv_check_stats(&stats, &cmd, &report) == 0);
    CHECK(report.count == 0 && report.failed == 0);
}

/*
 * test_check_event — 事件类型匹配断言
 */
static void test_check_event(void)
{
    struct vendor_lechao_usbd_event ev;
    struct fv_command cmd;
    struct fv_check_report report;

    memset(&ev, 0, sizeof(ev));
    memset(&cmd, 0, sizeof(cmd));

    ev.event_type = VENDOR_LECHAO_USBD_EVENT_STALL;
    cmd.expect_event_type = VENDOR_LECHAO_USBD_EVENT_STALL;
    CHECK(fv_check_event(&ev, &cmd, &report) == 0);
    CHECK(report.count == 1 && report.failed == 0);
    CHECK(report.entries[0].passed == 1);

    cmd.expect_event_type = VENDOR_LECHAO_USBD_EVENT_TIMEOUT;
    CHECK(fv_check_event(&ev, &cmd, &report) == -1);
    CHECK(report.entries[0].passed == 0);
}

/*
 * test_output_report — 断言报告汇总输出逻辑
 * 验证 output_check_report 返回码契约：failed==0 → 0；
 * failed>0 → -1（CXX-004：断言失败必须由返回码可见，供 main.c
 * 置 FV_ERR_CHECK，禁止打印 FAIL 却返回成功码致 CI 误判 PASS）。
 */
static void test_output_report(void)
{
    struct fv_check_report report;
    memset(&report, 0, sizeof(report));

    /* 全通过（无失败项）→ 返回 0 */
    CHECK(output_check_report(&report, 0) == 0);
    CHECK(output_check_report(&report, 1) == 0);

    struct fv_check_entry *e = &report.entries[report.count++];
    e->field_name = "stall_count";
    e->actual = 1;
    e->expected = 5;
    e->passed = 0;
    report.failed = 1;
    /* 存在失败断言 → 必须返回非零（文本 / JSON 两分支一致） */
    CHECK(output_check_report(&report, 0) == -1);
    CHECK(output_check_report(&report, 1) == -1);
}

/*
 * test_degrade_check_fail_nonzero — check degrade 断言失败须返回非零
 * CXX-004 破坏即判红用例：任一降级断言未达标时 output_degrade_check
 * 必须返回 -1，main.c 据此置 FV_ERR_CHECK 退出码。此前恒返回 0，
 * 断言失败仅打印 FAIL 而退出码为 0（脚本/CI 误判 PASS 的真 bug）。
 * 覆盖三项断言（rate_drop / latency_rise / stall）的达标/未达标两态。
 */
static void test_degrade_check_fail_nonzero(void)
{
    struct vendor_lechao_usbd_stats stats;
    struct fv_command cmd;

    memset(&stats, 0, sizeof(stats));
    memset(&cmd, 0, sizeof(cmd));
    /* 无阈值 → 无断言 → 返回 0 */
    CHECK(output_degrade_check(&stats, &cmd, 0) == 0);

    /* stall_count 未达标 → 非零；达标 → 0 */
    stats.stall_count = 1;
    cmd.stall_ge = 5;
    CHECK(output_degrade_check(&stats, &cmd, 0) == -1);
    stats.stall_count = 5;
    CHECK(output_degrade_check(&stats, &cmd, 0) == 0);

    /* rate_drop (peak-current) 未达标 → 非零；达标 → 0 */
    memset(&stats, 0, sizeof(stats));
    memset(&cmd, 0, sizeof(cmd));
    stats.peak_rate = 100;
    stats.current_rate = 90; /* drop = 10 */
    cmd.rate_drop_ge = 50;
    CHECK(output_degrade_check(&stats, &cmd, 1) == -1);
    cmd.rate_drop_ge = 10;
    CHECK(output_degrade_check(&stats, &cmd, 1) == 0);

    /* latency_rise 未达标 → 非零；达标 → 0 */
    memset(&stats, 0, sizeof(stats));
    memset(&cmd, 0, sizeof(cmd));
    stats.last_transport_latency_ns = 100;
    cmd.latency_rise_ge = 500;
    CHECK(output_degrade_check(&stats, &cmd, 1) == -1);
    cmd.latency_rise_ge = 100;
    CHECK(output_degrade_check(&stats, &cmd, 1) == 0);
}

/*
 * test_output_event_degrade — RATE_DEGRADED 事件名映射（R-18 P5 方向 3）
 * 验证 output_event JSON 分支对 degrade 事件输出 "rate_degraded" 而非
 * "unknown"（此前 event_type_name 缺该 case，JSON 不可机读）。
 */
static void test_output_event_degrade(void)
{
    struct vendor_lechao_usbd_event ev;
    memset(&ev, 0, sizeof(ev));
    ev.event_type = VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED;
    ev.timestamp_ns = 123;
    ev.event_value = 50;
    ev.status = 0;
    ev.data_direction = VENDOR_LECHAO_USBD_DIR_READ;
    ev.valid = 1;

    /* 重定向 stdout 到临时文件，读回断言 JSON 内容 */
    char path[] = "/tmp/fv_out_XXXXXX";
    int fd = mkstemp(path);
    CHECK(fd >= 0);
    FILE *f = fdopen(dup(fd), "w");
    CHECK(f != NULL);
    FILE *saved = stdout;
    stdout = f;
    CHECK(output_event(&ev, 1, 0) == 0);
    fflush(f);
    stdout = saved;

    /* 读回并断言 event_type 为 rate_degraded */
    lseek(fd, 0, SEEK_SET);
    char buf[512] = {0};
    ssize_t n = read(fd, buf, sizeof(buf) - 1);
    CHECK(n > 0);
    buf[n > 0 ? n : 0] = '\0';
    CHECK(strstr(buf, "\"rate_degraded\"") != NULL);
    CHECK(strstr(buf, "\"unknown\"") == NULL);

    fclose(f);
    close(fd);
    unlink(path);
}

/*
 * test_expect_json_parse_valid — 合法 expect JSON 解析 + 阈值/事件类型映射
 * KI-20261008-001：消费 usb-fault-inject expect_output_by_id() 单行 JSON。
 */
static void test_expect_json_parse_valid(void)
{
    const char *json =
        "{\"fault\":\"stall-in\",\"expect\":{\"kernel_event\":2,"
        "\"error_count\":1,\"reset_count\":1,\"stall_count\":1}}";
    struct fv_expect_json ej;
    memset(&ej, 0, sizeof(ej));

    CHECK(fv_parse_expect_json_buf(json, strlen(json), &ej) == 0);
    CHECK(ej.has_fault == 1 && strcmp(ej.fault, "stall-in") == 0);
    CHECK(ej.has_kernel_event == 1 && ej.kernel_event == 2);
    CHECK(ej.error_count == 1);
    CHECK(ej.reset_count == 1);
    CHECK(ej.stall_count == 1);
    /* 未出现的计数字段保持 -1（不校验） */
    CHECK(ej.corrupt_count == -1);
    CHECK(ej.timeout_count == -1);

    /* 映射到 fv_command：事件类型 + 各自 *_ge */
    struct fv_command cmd;
    memset(&cmd, 0, sizeof(cmd));
    CHECK(fv_apply_expect_json(&ej, &cmd) == 0);
    CHECK(cmd.expect_event_type == VENDOR_LECHAO_USBD_EVENT_STALL);
    CHECK(cmd.error_ge == 1);
    CHECK(cmd.reset_ge == 1);
    CHECK(cmd.stall_ge == 1);
    CHECK(cmd.corrupt_ge == 0); /* -1 未提供 → 不设阈值 */
    CHECK(cmd.timeout_ge == 0);
}

/*
 * test_expect_json_counts_sentinel — 计数 -1 不设阈值 / >=0 设阈值
 */
static void test_expect_json_counts_sentinel(void)
{
    const char *json =
        "{\"fault\":\"degrade\",\"expect\":{\"kernel_event\":6,"
        "\"corrupt_count\":-1,\"timeout_count\":0}}";
    struct fv_expect_json ej;
    memset(&ej, 0, sizeof(ej));
    CHECK(fv_parse_expect_json_buf(json, strlen(json), &ej) == 0);
    CHECK(ej.corrupt_count == -1);
    CHECK(ej.timeout_count == 0);

    struct fv_command cmd;
    memset(&cmd, 0, sizeof(cmd));
    CHECK(fv_apply_expect_json(&ej, &cmd) == 0);
    CHECK(cmd.corrupt_ge == 0); /* -1 → 不设 */
    CHECK(cmd.timeout_ge == 0); /* 0 → 不产生断言（actual>=0 恒真） */
    CHECK(cmd.expect_event_type == VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED);
}

/*
 * test_expect_json_apply_cli_priority — 命令行阈值优先，JSON 只填空缺
 */
static void test_expect_json_apply_cli_priority(void)
{
    const char *json =
        "{\"fault\":\"stall-in\",\"expect\":{\"kernel_event\":2,"
        "\"error_count\":1,\"stall_count\":5}}";
    struct fv_expect_json ej;
    memset(&ej, 0, sizeof(ej));
    CHECK(fv_parse_expect_json_buf(json, strlen(json), &ej) == 0);

    struct fv_command cmd;
    memset(&cmd, 0, sizeof(cmd));
    cmd.error_ge = 9; /* 命令行显式阈值 */
    cmd.expect_event_type = VENDOR_LECHAO_USBD_EVENT_TIMEOUT; /* 命令行显式类型 */
    CHECK(fv_apply_expect_json(&ej, &cmd) == 0);
    CHECK(cmd.error_ge == 9); /* 不被 JSON 覆盖 */
    CHECK(cmd.expect_event_type == VENDOR_LECHAO_USBD_EVENT_TIMEOUT);
    CHECK(cmd.stall_ge == 5); /* 空缺字段由 JSON 填充 */
}

/*
 * test_expect_json_invalid — 非法 JSON / 缺字段 / 尾部多余 → 判失败
 */
static void test_expect_json_invalid(void)
{
    struct fv_expect_json ej;
    const char *cases[] = {
        "not json",
        "{}",
        "{\"fault\":\"x\"}",                                   /* 缺 expect */
        "{\"expect\":{\"kernel_event\":2}}",                   /* 缺 fault */
        "{\"fault\":\"x\",\"expect\":{\"error_count\":1}}",    /* 缺 kernel_event */
        "{\"fault\":\"x\",\"expect\":{\"kernel_event\":\"s\"}}", /* 非整数 */
        "{\"fault\":\"x\",\"expect\":{\"kernel_event\":2}}xz", /* 尾部多余 */
        "{\"fault\":\"x\",\"expect\":{\"kernel_event\":2}",     /* 未闭合 */
        "{\"fault\":\"x\",\"expect\":{\"error_count\":-2}}",    /* 计数 < -1 */
    };
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); i++) {
        memset(&ej, 0, sizeof(ej));
        CHECK(fv_parse_expect_json_buf(cases[i], strlen(cases[i]), &ej) == -1);
    }

    /* NULL / 空输入 */
    CHECK(fv_parse_expect_json_buf(NULL, 0, &ej) == -1);
    CHECK(fv_parse_expect_json_buf("x", 0, &ej) == -1);
}

/*
 * test_expect_json_kernel_event_range — kernel_event 越界判失败
 */
static void test_expect_json_kernel_event_range(void)
{
    struct fv_expect_json ej;

    const char *ok = "{\"fault\":\"x\",\"expect\":{\"kernel_event\":6}}";
    CHECK(fv_parse_expect_json_buf(ok, strlen(ok), &ej) == 0);
    CHECK(ej.kernel_event == 6);

    const char *over = "{\"fault\":\"x\",\"expect\":{\"kernel_event\":7}}";
    CHECK(fv_parse_expect_json_buf(over, strlen(over), &ej) == -1);

    const char *neg = "{\"fault\":\"x\",\"expect\":{\"kernel_event\":-1}}";
    CHECK(fv_parse_expect_json_buf(neg, strlen(neg), &ej) == -1);
}

/*
 * test_expect_json_file — 文件通道：合法文件解析成功 / 文件不存在判失败
 */
static void test_expect_json_file(void)
{
    char path[] = "/tmp/fv_expect_XXXXXX";
    int fd = mkstemp(path);
    CHECK(fd >= 0);
    const char *json =
        "{\"fault\":\"timeout\",\"expect\":{\"kernel_event\":4,"
        "\"timeout_count\":1}}";
    ssize_t wn = write(fd, json, strlen(json));
    CHECK(wn == (ssize_t)strlen(json));
    close(fd);

    struct fv_expect_json ej;
    memset(&ej, 0, sizeof(ej));
    CHECK(fv_parse_expect_json_file(path, &ej) == 0);
    CHECK(strcmp(ej.fault, "timeout") == 0);
    CHECK(ej.kernel_event == 4);
    CHECK(ej.timeout_count == 1);
    unlink(path);

    /* 文件不存在 → 判失败 */
    memset(&ej, 0, sizeof(ej));
    CHECK(fv_parse_expect_json_file("/tmp/fv_expect_does_not_exist_XXXX",
                                    &ej) == -1);
}

int main(void)
{
    test_proto_macros();
    test_parse_event_type();
    test_check_stats();
    test_check_event();
    test_output_report();
    test_degrade_check_fail_nonzero();
    test_output_event_degrade();
    test_expect_json_parse_valid();
    test_expect_json_counts_sentinel();
    test_expect_json_apply_cli_priority();
    test_expect_json_invalid();
    test_expect_json_kernel_event_range();
    test_expect_json_file();

    if (g_fail) {
        fprintf(stderr, "fv_pure_host_test: FAILED\n");
        return 1;
    }
    printf("fv_pure_host_test: all checks passed\n");
    return 0;
}
