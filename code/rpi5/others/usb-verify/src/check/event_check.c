/*
 * ============================================================
 * event_check.c — 事件等待/匹配和降级检查实现
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 实现事件轮询等待、类型匹配断言和降级指标检查。
 * ============================================================
 */
#include "event_check.h"
#include "usbd_device.h"

#include <errno.h>
#include <string.h>
#include <time.h>

/*
 * fv_wait_for_event — 轮询等待匹配事件
 *
 * 实现逻辑:
 *   1) 初始化 remaining = cmd->timeout_ms
 *   2) 循环调用 usbd_read_event(fd, &ev, remaining)
 *      - 成功且 event_type 匹配 → 拷贝到 matched，返回 0
 *      - 成功但类型不匹配 → 继续等待（remaining -= 100）
 *      - 失败 → 返回错误码
 *   3) remaining <= 0 → 返回 -ETIMEDOUT
 *
 * 每次读取的超时为 remaining 而非固定值，确保总等待时间不超过 timeout_ms。
 * 使用 clock_gettime(CLOCK_MONOTONIC) 测量实际已用时间，避免固定扣减的精度偏差。
 */
int fv_wait_for_event(int fd, const struct fv_command *cmd,
                       struct vendor_lechao_usbd_event *matched)
{
    struct timespec ts_start, ts_now;
    clock_gettime(CLOCK_MONOTONIC, &ts_start);
    int timeout_ms = cmd->timeout_ms;

    while (1) {
        clock_gettime(CLOCK_MONOTONIC, &ts_now);
        int elapsed_ms = (ts_now.tv_sec - ts_start.tv_sec) * 1000 +
                         (ts_now.tv_nsec - ts_start.tv_nsec) / 1000000;
        int remaining = timeout_ms - elapsed_ms;
        if (remaining <= 0)
            break;

        struct vendor_lechao_usbd_event ev;
        int rc = usbd_read_event(fd, &ev, remaining);
        if (rc < 0)
            return rc;

        if (ev.event_type == cmd->expect_event_type) {
            if (matched)
                *matched = ev;
            return 0;
        }
    }

    return -ETIMEDOUT;
}

/*
 * fv_check_event — 断言事件类型匹配
 * 只检查 event_type 一个字段，actual == expected → PASS。
 */
int fv_check_event(const struct vendor_lechao_usbd_event *event,
                   const struct fv_command *cmd,
                   struct fv_check_report *report)
{
    memset(report, 0, sizeof(*report));

    if (report->count >= FV_MAX_CHECK_ENTRIES)
        return -1;

    struct fv_check_entry *e = &report->entries[report->count++];
    e->field_name = "event_type";
    e->actual = event->event_type;
    e->expected = cmd->expect_event_type;
    e->passed = (event->event_type == cmd->expect_event_type) ? 1 : 0;
    if (!e->passed)
        report->failed++;

    return report->failed > 0 ? -1 : 0;
}
