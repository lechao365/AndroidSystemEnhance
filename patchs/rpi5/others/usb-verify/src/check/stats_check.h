/*
 * ============================================================
 * stats_check.h — 统计断言检查接口
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 对内核统计快照执行阈值断言检查，
 *           生成 fv_check_report 报告。
 *
 * 断言规则:
 *   对 cmd 中每个非零的 *_ge 参数，检查 stats 中对应字段
 *   是否 >= 阈值。支持的字段:
 *     stall_count, timeout_count, corrupt_count,
 *     disconnect_count, probe_count
 * ============================================================
 */
#ifndef STATS_CHECK_H
#define STATS_CHECK_H

#include "types.h"
#include "parse.h"

/*
 * fv_check_stats — 执行统计阈值断言检查
 * @stats: 内核统计快照（来自 usbd_get_stats）
 * @cmd:   命令参数（包含断言阈值）
 * @report: 输出参数，检查结果
 * 返回: 0 全部通过，-1 有断言失败
 */
int fv_check_stats(const struct vendor_lechao_usbd_stats *stats,
                   const struct fv_command *cmd,
                   struct fv_check_report *report);

#endif
