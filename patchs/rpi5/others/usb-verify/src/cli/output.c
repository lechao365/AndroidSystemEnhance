/*
 * ============================================================
 * output.c — CLI 输出格式化实现
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 实现统计/配置/事件/断言报告的文本表格和 JSON 输出。
 *
 * 格式约定:
 *   文本表格: "%-28s %s\n" 格式，第一列字段名，第二列值
 *   JSON: 标准的缩进 JSON，键名使用 snake_case
 * ============================================================
 */
#include "output.h"

#include <stdio.h>
#include <string.h>

/*
 * event_type_name — 将事件类型枚举转换为可读字符串
 * 用于事件输出中的 event_type 字段显示。
 */
static const char *event_type_name(uint32_t type)
{
    switch (type) {
    case VENDOR_LECHAO_USBD_EVENT_NONE:            return "none";
    case VENDOR_LECHAO_USBD_EVENT_TRANSPORT_ERROR: return "transport_error";
    case VENDOR_LECHAO_USBD_EVENT_STALL:           return "stall";
    case VENDOR_LECHAO_USBD_EVENT_DATA_CORRUPT:    return "data_corrupt";
    case VENDOR_LECHAO_USBD_EVENT_TIMEOUT:         return "timeout";
    case VENDOR_LECHAO_USBD_EVENT_RESET:           return "reset";
    default:                                        return "unknown";
    }
}

/*
 * output_stats — 输出统计快照
 * 支持两种格式: 文本表格和 JSON
 */
int output_stats(const struct vendor_lechao_usbd_stats *s, int json)
{
    if (json) {
        printf("{\n");
        printf("  \"read_bytes\": %llu,\n", (unsigned long long)s->read_bytes);
        printf("  \"write_bytes\": %llu,\n", (unsigned long long)s->write_bytes);
        printf("  \"read_cmds\": %llu,\n", (unsigned long long)s->read_cmds);
        printf("  \"write_cmds\": %llu,\n", (unsigned long long)s->write_cmds);
        printf("  \"error_count\": %llu,\n", (unsigned long long)s->error_count);
        printf("  \"reset_count\": %llu,\n", (unsigned long long)s->reset_count);
        printf("  \"probe_count\": %llu,\n", (unsigned long long)s->probe_count);
        printf("  \"disconnect_count\": %llu,\n", (unsigned long long)s->disconnect_count);
        printf("  \"degrade_count\": %llu,\n", (unsigned long long)s->degrade_count);
        printf("  \"stall_count\": %llu,\n", (unsigned long long)s->stall_count);
        printf("  \"corrupt_count\": %llu,\n", (unsigned long long)s->corrupt_count);
        printf("  \"timeout_count\": %llu,\n", (unsigned long long)s->timeout_count);
        printf("  \"current_rate\": %llu,\n", (unsigned long long)s->current_rate);
        printf("  \"peak_rate\": %llu,\n", (unsigned long long)s->peak_rate);
        printf("  \"last_transport_latency_ns\": %llu,\n", (unsigned long long)s->last_transport_latency_ns);
        printf("  \"last_event_ts_ns\": %llu,\n", (unsigned long long)s->last_event_ts_ns);
        printf("  \"vid\": %u,\n", (unsigned)s->vid);
        printf("  \"pid\": %u,\n", (unsigned)s->pid);
        printf("  \"vendor\": \"%s\",\n", s->vendor);
        printf("  \"product\": \"%s\",\n", s->product);
        printf("  \"enabled\": %u,\n", (unsigned)s->enabled);
        printf("  \"flags\": %u\n", (unsigned)s->flags);
        printf("}\n");
    } else {
        printf("%-28s %s\n", "FIELD", "VALUE");
        printf("%-28s %llu\n", "read_bytes", (unsigned long long)s->read_bytes);
        printf("%-28s %llu\n", "write_bytes", (unsigned long long)s->write_bytes);
        printf("%-28s %llu\n", "read_cmds", (unsigned long long)s->read_cmds);
        printf("%-28s %llu\n", "write_cmds", (unsigned long long)s->write_cmds);
        printf("%-28s %llu\n", "error_count", (unsigned long long)s->error_count);
        printf("%-28s %llu\n", "reset_count", (unsigned long long)s->reset_count);
        printf("%-28s %llu\n", "probe_count", (unsigned long long)s->probe_count);
        printf("%-28s %llu\n", "disconnect_count", (unsigned long long)s->disconnect_count);
        printf("%-28s %llu\n", "degrade_count", (unsigned long long)s->degrade_count);
        printf("%-28s %llu\n", "stall_count", (unsigned long long)s->stall_count);
        printf("%-28s %llu\n", "corrupt_count", (unsigned long long)s->corrupt_count);
        printf("%-28s %llu\n", "timeout_count", (unsigned long long)s->timeout_count);
        printf("%-28s %llu\n", "current_rate", (unsigned long long)s->current_rate);
        printf("%-28s %llu\n", "peak_rate", (unsigned long long)s->peak_rate);
        printf("%-28s %llu\n", "last_transport_latency_ns", (unsigned long long)s->last_transport_latency_ns);
        printf("%-28s %llu\n", "last_event_ts_ns", (unsigned long long)s->last_event_ts_ns);
        printf("%-28s %04x:%04x\n", "vid:pid", (unsigned)s->vid, (unsigned)s->pid);
        printf("%-28s %s\n", "vendor", s->vendor);
        printf("%-28s %s\n", "product", s->product);
        printf("%-28s %u\n", "enabled", (unsigned)s->enabled);
        printf("%-28s 0x%08x\n", "flags", (unsigned)s->flags);
    }
    return 0;
}

/* output_config — 输出配置（enabled + flags） */
int output_config(const struct vendor_lechao_usbd_config *c, int json)
{
    if (json) {
        printf("{\n");
        printf("  \"enabled\": %u,\n", (unsigned)c->enabled);
        printf("  \"flags\": %u\n", (unsigned)c->flags);
        printf("}\n");
    } else {
        printf("%-12s %s\n", "FIELD", "VALUE");
        printf("%-12s %u\n", "enabled", (unsigned)c->enabled);
        printf("%-12s 0x%08x\n", "flags", (unsigned)c->flags);
    }
    return 0;
}

/*
 * output_event — 输出事件（带错误处理）
 * @rc: usbd_read_event 返回值，非 0 时输出错误
 */
int output_event(const struct vendor_lechao_usbd_event *ev, int json, int rc)
{
    if (rc != 0) {
        if (json)
            printf("{\"error\": true, \"rc\": %d}\n", rc);
        else
            printf("Event read failed: %d\n", rc);
        return rc;
    }
    if (json) {
        printf("{\n");
        printf("  \"timestamp_ns\": %llu,\n", (unsigned long long)ev->timestamp_ns);
        printf("  \"event_type\": \"%s\",\n", event_type_name(ev->event_type));
        printf("  \"event_value\": %u,\n", (unsigned)ev->event_value);
        printf("  \"status\": %d,\n", ev->status);
        printf("  \"data_direction\": %u,\n", (unsigned)ev->data_direction);
        printf("  \"valid\": %u\n", (unsigned)ev->valid);
        printf("}\n");
    } else {
        printf("%-18s %s\n", "FIELD", "VALUE");
        printf("%-18s %llu\n", "timestamp_ns", (unsigned long long)ev->timestamp_ns);
        printf("%-18s %s\n", "event_type", event_type_name(ev->event_type));
        printf("%-18s %u\n", "event_value", (unsigned)ev->event_value);
        printf("%-18s %d\n", "status", ev->status);
        printf("%-18s %u\n", "data_direction", (unsigned)ev->data_direction);
        printf("%-18s %u\n", "valid", (unsigned)ev->valid);
    }
    return 0;
}

/*
 * output_check_report — 输出断言报告
 * 文本格式: 表格（FIELD/ACTUAL/EXPECTED/PASS）
 * JSON 格式: 包含 total/failed/passed/entries[]
 */
int output_check_report(const struct fv_check_report *report, int json)
{
    if (json) {
        printf("{\n");
        printf("  \"total\": %d,\n", report->count);
        printf("  \"failed\": %d,\n", report->failed);
        printf("  \"passed\": %s,\n", report->failed == 0 ? "true" : "false");
        printf("  \"entries\": [\n");
        for (int i = 0; i < report->count; i++) {
            const struct fv_check_entry *e = &report->entries[i];
            printf("    {\"field\": \"%s\", \"actual\": %llu, \"expected\": %llu, \"passed\": %s}%s\n",
                   e->field_name,
                   (unsigned long long)e->actual,
                   (unsigned long long)e->expected,
                   e->passed ? "true" : "false",
                   i < report->count - 1 ? "," : "");
        }
        printf("  ]\n");
        printf("}\n");
    } else {
        printf("%-20s %-12s %-12s %s\n", "FIELD", "ACTUAL", "EXPECTED", "PASS");
        for (int i = 0; i < report->count; i++) {
            const struct fv_check_entry *e = &report->entries[i];
            printf("%-20s %-12llu %-12llu %s\n",
                   e->field_name,
                   (unsigned long long)e->actual,
                   (unsigned long long)e->expected,
                   e->passed ? "OK" : "FAIL");
        }
        printf("Result: %d/%d checks passed (%s)\n",
               report->count - report->failed, report->count,
               report->failed == 0 ? "PASS" : "FAIL");
    }
    return 0;
}

/*
 * output_degrade_check — 降级检查报告（内置计算逻辑）
 *
 * 此函数与 fv_check_degrade() 功能重叠，但直接在 output 层完成计算。
 * 三项检查:
 *   1) rate_drop: peak_rate - current_rate
 *   2) latency_rise: last_transport_latency_ns
 *   3) stall_count: stall_count
 *
 * 检查结果填充到本地 report 后委托给 output_check_report 输出。
 */
int output_degrade_check(const struct vendor_lechao_usbd_stats *stats,
                         const struct fv_command *cmd, int json)
{
    struct fv_check_report report;
    memset(&report, 0, sizeof(report));

    printf("=== Degrade Check ===\n");
    if (cmd->rate_drop_ge > 0) {
        uint64_t drop = 0;
        if (stats->peak_rate > stats->current_rate)
            drop = stats->peak_rate - stats->current_rate;
        if (report.count < FV_MAX_CHECK_ENTRIES) {
            struct fv_check_entry *e = &report.entries[report.count++];
            e->field_name = "rate_drop";
            e->actual = drop;
            e->expected = cmd->rate_drop_ge;
            e->passed = (drop >= cmd->rate_drop_ge) ? 1 : 0;
            if (!e->passed) report.failed++;
        }
    }
    if (cmd->latency_rise_ge > 0) {
        if (report.count < FV_MAX_CHECK_ENTRIES) {
            struct fv_check_entry *e = &report.entries[report.count++];
            e->field_name = "latency_rise";
            e->actual = stats->last_transport_latency_ns;
            e->expected = cmd->latency_rise_ge;
            e->passed = (stats->last_transport_latency_ns >= cmd->latency_rise_ge) ? 1 : 0;
            if (!e->passed) report.failed++;
        }
    }
    if (cmd->stall_ge > 0) {
        if (report.count < FV_MAX_CHECK_ENTRIES) {
            struct fv_check_entry *e = &report.entries[report.count++];
            e->field_name = "stall_count";
            e->actual = stats->stall_count;
            e->expected = cmd->stall_ge;
            e->passed = (stats->stall_count >= cmd->stall_ge) ? 1 : 0;
            if (!e->passed) report.failed++;
        }
    }

    return output_check_report(&report, json);
}
