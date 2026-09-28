/*
 * ============================================================
 * fv_pure_host_test.c — usb-verify 纯逻辑 host 单测
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 对不依赖设备 ioctl 的纯函数做编译期+运行期断言:
 *   1) fv_parse_event_type — 事件名↔内核枚举映射契约
 *   2) fv_check_stats       — 统计阈值断言逻辑（含 error/reset）
 *   3) fv_check_event       — 事件类型匹配断言
 *   4) output_check_report  — 断言报告汇总（failed 计数）
 *
 * 运行: make test（在 tests/ 目录下），退出码 0 全过。
 * 接入: harness/lib/check_host_tests.py（R-18 P5 方向 6）。
 * ============================================================
 */
#define _DEFAULT_SOURCE
#include "parse.h"
#include "stats_check.h"
#include "event_check.h"
#include "output.h"
#include "fv_ioctl_compat.h"

#include <stdio.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>

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
 * 验证 output_check_report 对 failed>0 报告返回 0（输出层不判定成败，
 * 成败由 fv_check_stats/fv_check_event 返回码承载——契约回归保护）。
 */
static void test_output_report(void)
{
    struct fv_check_report report;
    memset(&report, 0, sizeof(report));

    CHECK(output_check_report(&report, 0) == 0);
    CHECK(output_check_report(&report, 1) == 0);

    struct fv_check_entry *e = &report.entries[report.count++];
    e->field_name = "stall_count";
    e->actual = 1;
    e->expected = 5;
    e->passed = 0;
    report.failed = 1;
    CHECK(output_check_report(&report, 0) == 0);
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

int main(void)
{
    test_parse_event_type();
    test_check_stats();
    test_check_event();
    test_output_report();
    test_output_event_degrade();

    if (g_fail) {
        fprintf(stderr, "fv_pure_host_test: FAILED\n");
        return 1;
    }
    printf("fv_pure_host_test: all checks passed\n");
    return 0;
}
