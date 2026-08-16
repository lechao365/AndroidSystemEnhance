#include "expect.h"

#include <stdio.h>

/*
 * 11 类故障的预期值表（与内核监控事件严格对齐）
 *
 * 字段取 -1 = 不校验
 * 字段取 N  = 期望 >= N（fault-verify 按 actual >= expect 校验）
 *
 * F4 (corrupt-cbw-sig) 已删除：CBW 是 Host→Device 方向，Device 无法注入
 * F9 (abort) 重定义为 STALL+TIMEOUT：同时产生 stall + timeout + error + reset
 */
static const struct fault_expect expect_table[FAULT__MAX] = {
    [FAULT_STALL_IN] = {
        .name = "stall-in", .human_desc = "F1: STALL IN endpoint",
        .error_count = 1, .reset_count = 1, .stall_count = 1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_STALL_OUT] = {
        .name = "stall-out", .human_desc = "F2: STALL OUT endpoint",
        .error_count = 1, .reset_count = 1, .stall_count = 1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_TIMEOUT] = {
        .name = "timeout", .human_desc = "F3: No response timeout",
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = 1,
    },
    [FAULT_CORRUPT_CSW_SIG] = {
        .name = "corrupt-csw-sig", .human_desc = "F5: CSW Signature corrupted",
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = 1, .timeout_count = -1,
    },
    [FAULT_CORRUPT_CSW_TAG] = {
        .name = "corrupt-csw-tag", .human_desc = "F6: CSW Tag mismatch",
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = 1, .timeout_count = -1,
    },
    [FAULT_CORRUPT_CSW_STA] = {
        .name = "corrupt-csw-status", .human_desc = "F7: CSW Status = Phase Error",
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = 1, .timeout_count = -1,
    },
    [FAULT_SHORT] = {
        .name = "short", .human_desc = "F8: Data short transfer",
        .error_count = -1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_ABORT] = {
        .name = "abort", .human_desc = "F9: STALL+TIMEOUT composite",
        .error_count = 1, .reset_count = 1, .stall_count = 1,
        .corrupt_count = -1, .timeout_count = 1,
    },
    [FAULT_HOTPLUG] = {
        .name = "hotplug", .human_desc = "F10: VBUS hot-plug cycle",
        .error_count = -1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_DISCONNECT] = {
        .name = "disconnect", .human_desc = "F11: Physical disconnect",
        .error_count = -1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_DEGRADE] = {
        .name = "degrade", .human_desc = "F12: Rate degradation",
        .error_count = -1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
};

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
    printf("Available fault injections (11 types):\n");
    for (int i = 0; i < FAULT__MAX; i++) {
        printf("  %2d. %-20s  %s\n", i, expect_table[i].name,
               expect_table[i].human_desc);
    }
}
