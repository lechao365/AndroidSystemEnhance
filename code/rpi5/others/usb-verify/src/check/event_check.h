/*
 * ============================================================
 * event_check.h — 事件等待/匹配检查接口
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 提供 2 个检查函数:
 *   fv_wait_for_event  — 轮询等待匹配指定类型的事件
 *   fv_check_event     — 断言事件类型是否匹配
 *
 * 注: 降级指标检查（check degrade）统一走 output.c 的
 *     output_degrade_check（本模块原 fv_check_degrade 为死代码，
 *     与 output_degrade_check 逻辑等价，R-18 P5 方向 3 已删除）。
 * ============================================================
 */
#ifndef EVENT_CHECK_H
#define EVENT_CHECK_H

#include "types.h"
#include "parse.h"

/*
 * fv_wait_for_event — 轮询等待匹配指定类型的事件
 * @fd: 设备 fd
 * @cmd: 命令参数（expect_event_type 和 timeout_ms）
 * @matched: 输出参数，接收匹配到的事件
 * 返回: 0 成功（找到匹配事件），-ETIMEDOUT 超时，其他负值为 -errno
 *
 * 实现逻辑: 循环调用 usbd_read_event，每次 100ms 超时，
 *           直到读到 event_type == expect_event_type 或总超时。
 */
int fv_wait_for_event(int fd, const struct fv_command *cmd,
                      struct vendor_lechao_usbd_event *matched);

/*
 * fv_check_event — 断言事件类型是否匹配期望值
 * @event: 待检查的事件
 * @cmd:   包含 expect_event_type
 * @report: 输出检查结果
 * 返回: 0 匹配，-1 不匹配
 */
int fv_check_event(const struct vendor_lechao_usbd_event *event,
                   const struct fv_command *cmd,
                   struct fv_check_report *report);

#endif
