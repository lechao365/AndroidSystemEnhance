#include "expect.h"

#include <stdio.h>
#include <string.h>

/*
 * 11 类故障的预期值表（与内核监控事件严格对齐）
 *
 * 字段取 -1 = 不校验
 * 字段取 N  = 期望 >= N（fault-verify 按 actual >= expect 校验）
 *
 * kernel_event 为该故障预期触发的主内核事件类型（enum fdi_kernel_event），
 * 与内核 vendor_lechao_usbd event 枚举语义对齐（R-18 P5 方向 5 schema gate）：
 *   - STALL 类   → FDI_EVENT_STALL（内核同时累计 error_count/reset_count）
 *   - TIMEOUT 类 → FDI_EVENT_TIMEOUT
 *   - CORRUPT 类 → FDI_EVENT_DATA_CORRUPT
 *   - 复合/物理  → FDI_EVENT_STALL（abort）/ FDI_EVENT_NONE（short/hotplug/...）
 *
 * F4 (corrupt-cbw-sig) 已删除：CBW 是 Host→Device 方向，Device 无法注入
 * F9 (abort) 重定义为 STALL+TIMEOUT：同时产生 stall + timeout + error + reset
 */
static const struct fault_expect expect_table[FAULT__MAX] = {
    [FAULT_STALL_IN] = {
        .name = "stall-in", .human_desc = "F1: STALL IN endpoint",
        .kernel_event = FDI_EVENT_STALL,
        .error_count = 1, .reset_count = 1, .stall_count = 1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_STALL_OUT] = {
        .name = "stall-out", .human_desc = "F2: STALL OUT endpoint",
        .kernel_event = FDI_EVENT_STALL,
        .error_count = 1, .reset_count = 1, .stall_count = 1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_TIMEOUT] = {
        .name = "timeout", .human_desc = "F3: No response timeout",
        .kernel_event = FDI_EVENT_TIMEOUT,
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = 1,
    },
    [FAULT_CORRUPT_CSW_SIG] = {
        .name = "corrupt-csw-sig", .human_desc = "F5: CSW Signature corrupted",
        .kernel_event = FDI_EVENT_DATA_CORRUPT,
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = 1, .timeout_count = -1,
    },
    [FAULT_CORRUPT_CSW_TAG] = {
        .name = "corrupt-csw-tag", .human_desc = "F6: CSW Tag mismatch",
        .kernel_event = FDI_EVENT_DATA_CORRUPT,
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = 1, .timeout_count = -1,
    },
    [FAULT_CORRUPT_CSW_STA] = {
        .name = "corrupt-csw-status", .human_desc = "F7: CSW Status = Phase Error",
        .kernel_event = FDI_EVENT_DATA_CORRUPT,
        .error_count = 1, .reset_count = 1, .stall_count = -1,
        .corrupt_count = 1, .timeout_count = -1,
    },
    [FAULT_SHORT] = {
        .name = "short", .human_desc = "F8: Data short transfer",
        .kernel_event = FDI_EVENT_NONE,
        .error_count = -1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_ABORT] = {
        .name = "abort", .human_desc = "F9: STALL+TIMEOUT composite",
        .kernel_event = FDI_EVENT_STALL,
        .error_count = 1, .reset_count = 1, .stall_count = 1,
        .corrupt_count = -1, .timeout_count = 1,
    },
    [FAULT_HOTPLUG] = {
        .name = "hotplug", .human_desc = "F10: VBUS hot-plug cycle",
        .kernel_event = FDI_EVENT_NONE,
        .error_count = -1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_DISCONNECT] = {
        .name = "disconnect", .human_desc = "F11: Physical disconnect",
        .kernel_event = FDI_EVENT_NONE,
        .error_count = -1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
    [FAULT_DEGRADE] = {
        .name = "degrade", .human_desc = "F12: Rate degradation",
        .kernel_event = FDI_EVENT_RATE_DEGRADED,
        .error_count = -1, .reset_count = -1, .stall_count = -1,
        .corrupt_count = -1, .timeout_count = -1,
    },
};

/*
 * expect_validate_schema — schema gate 校验
 *
 * 三项一致性检查（防故障语义漂移）：
 *   1) 每个 fault 的 kernel_event 必须落在合法镜像枚举范围
 *      [FDI_EVENT_NONE .. FDI_EVENT_RATE_DEGRADED]（防枚举改坏/越界）
 *   2) 每个 fault 的期望计数字段取值合法：-1（不校验）或 >=0（期望值）
 *   3) 每个 fault 的 name 必须与 fault_id_to_name() 严格一致
 *      （防 expect_table 与 faults.c 命名表重命名后漂移）
 * 返回 0 全部通过，-1 校验失败。
 */
int expect_validate_schema(void)
{
    for (int i = 0; i < FAULT__MAX; i++) {
        const struct fault_expect *e = &expect_table[i];
        if (!e->name || e->name[0] == '\0' ||
            !e->human_desc || e->human_desc[0] == '\0') {
            fprintf(stderr, "[expect] schema FAIL: fault %d name/human_desc 为空\n", i);
            return -1;
        }
        if (e->kernel_event < FDI_EVENT_NONE ||
            e->kernel_event > FDI_EVENT_RATE_DEGRADED) {
            fprintf(stderr, "[expect] schema FAIL: fault %d kernel_event=%d "
                            "超出合法范围\n", i, e->kernel_event);
            return -1;
        }
        if (e->error_count < -1 || e->reset_count < -1 ||
            e->stall_count < -1 || e->corrupt_count < -1 ||
            e->timeout_count < -1) {
            fprintf(stderr, "[expect] schema FAIL: fault %d (%s) 期望字段取值为负\n",
                    i, e->name);
            return -1;
        }
        const char *name = fault_id_to_name((enum fault_id)i);
        if (strcmp(name, e->name) != 0) {
            fprintf(stderr, "[expect] schema FAIL: fault %d name 漂移 "
                            "(faults.c=%s, expect.c=%s)\n", i, name, e->name);
            return -1;
        }
    }
    return 0;
}

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
    printf("Available fault injections (%d types):\n", FAULT__MAX);
    for (int i = 0; i < FAULT__MAX; i++) {
        printf("  %2d. %-20s  %s\n", i, expect_table[i].name,
               expect_table[i].human_desc);
    }
}
