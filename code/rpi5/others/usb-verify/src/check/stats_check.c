/*
 * ============================================================
 * stats_check.c — 统计断言检查实现
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 将内核 stats 的字段值与命令行指定的阈值比较，
 *           生成结构化的检查报告。
 *
 * 断言逻辑: actual >= expected → PASS，否则 FAIL
 * ============================================================
 */
#include "stats_check.h"

#include <string.h>

/*
 * add_entry — 向报告中添加一条断言结果
 * @name: 字段名（用于输出显示）
 * @actual: 实际值
 * @expected: 期望最小值（阈值）
 * 判定: actual >= expected → 通过
 */
static void add_entry(struct fv_check_report *report, const char *name,
                      uint64_t actual, uint64_t expected)
{
    if (report->count >= FV_MAX_CHECK_ENTRIES)
        return;
    struct fv_check_entry *e = &report->entries[report->count++];
    e->field_name = name;
    e->actual = actual;
    e->expected = expected;
    e->passed = (actual >= expected) ? 1 : 0;
    if (!e->passed)
        report->failed++;
}

/*
 * fv_check_stats — 遍历所有非零阈值参数，逐项断言
 * 只有 cmd 中对应的 *_ge > 0 时才检查该字段。
 */
int fv_check_stats(const struct vendor_lechao_usbd_stats *stats,
                   const struct fv_command *cmd,
                   struct fv_check_report *report)
{
    memset(report, 0, sizeof(*report));

    if (cmd->stall_ge > 0)
        add_entry(report, "stall_count", stats->stall_count, cmd->stall_ge);
    if (cmd->timeout_ge > 0)
        add_entry(report, "timeout_count", stats->timeout_count, cmd->timeout_ge);
    if (cmd->corrupt_ge > 0)
        add_entry(report, "corrupt_count", stats->corrupt_count, cmd->corrupt_ge);
    if (cmd->disconnect_ge > 0)
        add_entry(report, "disconnect_count", stats->disconnect_count, cmd->disconnect_ge);
    if (cmd->probe_ge > 0)
        add_entry(report, "probe_count", stats->probe_count, cmd->probe_ge);

    return report->failed > 0 ? -1 : 0;
}
