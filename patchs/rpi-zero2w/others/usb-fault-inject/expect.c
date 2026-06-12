#include "expect.h"

#include <stdio.h>

/* 12 类故障的预期值表（与 10.01.02 内核监控事件严格对齐）
 *
 * 字段取 -1 = 不校验
 * 字段取 0  = 期望为 0（与实际为 0 比对）
 * 字段取 N  = 期望 >= N（fault-verify 按 actual >= expect 校验）
 *
 * 关键映射：
 *   - STALL  → stall_count++ + TRANSPORT_ERROR + RESET
 *   - TIMEOUT → timeout_count++ + TRANSPORT_ERROR + RESET
 *   - CBW/CSW 损坏 → corrupt_count++ + TRANSPORT_ERROR + RESET
 *   - 短传输 → corrupt_count++ + TRANSPORT_ERROR (无 RESET，Host 直接重传)
 *   - ABORT  → TRANSPORT_ERROR + RESET
 *   - HOTPLUG → DEVICE_DISCONNECT + DEVICE_PROBE（计数由设备节点存在性判断）
 *   - DEGRADE → TRANSPORT_END（速率减半，error_count 不增加）
 */
static const struct fault_expect expect_table[FAULT__MAX] = {
    [FAULT_STALL] = {
        .name = "stall", .human_desc = "STALL endpoint",
        .error_count = 1, .reset_count = 1, .stall_count = 1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_TIMEOUT] = {
        .name = "timeout", .human_desc = "No response timeout",
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = 1,
    },
    [FAULT_CORRUPT_CBW_SIG] = {
        .name = "corrupt-cbw-sig", .human_desc = "CBW Signature corrupted",
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = 1, .timeout_count = -1,
    },
    [FAULT_CORRUPT_CSW_SIG] = {
        .name = "corrupt-csw-sig", .human_desc = "CSW Signature corrupted",
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = 1, .timeout_count = -1,
    },
    [FAULT_CORRUPT_CSW_TAG] = {
        .name = "corrupt-csw-tag", .human_desc = "CSW Tag mismatch",
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = 1, .timeout_count = -1,
    },
    [FAULT_CORRUPT_CSW_STA] = {
        .name = "corrupt-csw-status", .human_desc = "CSW Status = Phase Error",
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = 1, .timeout_count = -1,
    },
    [FAULT_SHORT] = {
        .name = "short", .human_desc = "Data short transfer",
        .error_count = 1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = 1, .timeout_count = -1,
    },
    [FAULT_ABORT] = {
        .name = "abort", .human_desc = "Bulk ABORT (ERR PID)",
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_HOTPLUG] = {
        .name = "hotplug", .human_desc = "VBUS hot-plug cycle",
        .error_count = -1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_DISCONNECT] = {
        .name = "disconnect", .human_desc = "Physical disconnect",
        .error_count = -1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_DEGRADE] = {
        .name = "degrade", .human_desc = "Rate degradation",
        .error_count = -1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
};

/* 输出单条 JSON 预期值到 stdout
 * 格式: {"fault":"stall","expect":{"error_count":1,"reset_count":1,"stall_count":1}}
 */
void expect_output_by_id(enum fault_id id)
{
    if (id < 0 || id >= FAULT__MAX) {
        fprintf(stderr, "expect_output_by_id: invalid fault id %d\n", id);
        return;
    }
    const struct fault_expect *e = &expect_table[id];
    printf("{\"fault\":\"%s\"", e->name);

    printf(",\"expect\":{");
    int comma = 0;
    if (e->error_count >= 0) {
        printf("%s\"error_count\":%d", comma ? "," : "", e->error_count);
        comma = 1;
    }
    if (e->reset_count >= 0) {
        printf("%s\"reset_count\":%d", comma ? "," : "", e->reset_count);
        comma = 1;
    }
    if (e->stall_count >= 0) {
        printf("%s\"stall_count\":%d", comma ? "," : "", e->stall_count);
        comma = 1;
    }
    if (e->corrupt_count >= 0) {
        printf("%s\"corrupt_count\":%d", comma ? "," : "", e->corrupt_count);
        comma = 1;
    }
    if (e->timeout_count >= 0) {
        printf("%s\"timeout_count\":%d", comma ? "," : "", e->timeout_count);
        comma = 1;
    }
    printf("}}\n");
    fflush(stdout);
}

void expect_list_all(void)
{
    printf("Available fault injections (12 types):\n");
    for (int i = 0; i < FAULT__MAX; i++) {
        printf("  %2d. %-20s  %s\n", i, expect_table[i].name,
               expect_table[i].human_desc);
    }
}
