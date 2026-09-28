#ifndef EXPECT_H
#define EXPECT_H

#include <stdint.h>
#include "faults.h"

/*
 * 与内核 event 枚举（vendor_lechao_usbd-ioctl.h / lciod_usbd-ioctl.h）对齐的
 * 镜像枚举（R-18 P5 方向 5 schema gate）。
 *
 * 数值必须与内核真相源 VENDOR_LECHAO_USBD_EVENT_* 严格一致：
 *   内核：NONE=0, TRANSPORT_ERROR=1, STALL=2, DATA_CORRUPT=3,
 *         TIMEOUT=4, RESET=5, RATE_DEGRADED=6
 * 镜像用于 expect_table 的 kernel_event 字段，使故障注入侧声明的
 * "该故障预期触发的内核事件"与内核监控语义对齐；任一侧漂移即被
 * expect_validate_schema() 在启动时判红（防故障语义静默漂移）。
 */
enum fdi_kernel_event {
    FDI_EVENT_NONE            = 0,
    FDI_EVENT_TRANSPORT_ERROR = 1,
    FDI_EVENT_STALL           = 2,
    FDI_EVENT_DATA_CORRUPT    = 3,
    FDI_EVENT_TIMEOUT         = 4,
    FDI_EVENT_RESET           = 5,
    FDI_EVENT_RATE_DEGRADED   = 6,
};

/* 12 类故障的预期值表
 * 字段说明：
 *   - kernel_event: 该故障预期触发的主内核事件类型（enum fdi_kernel_event），
 *                    无事件/纯物理类故障填 FDI_EVENT_NONE
 *   - error_count, reset_count, stall_count, corrupt_count, timeout_count
 *     取值: -1 = 不校验该字段；其他 = 期望值（fault-verify 按 actual>=expect 比对）
 */
struct fault_expect {
    const char    *name;        /* JSON 中的 fault 字段值 */
    const char    *human_desc;  /* 人类可读描述 */
    int            kernel_event; /* 预期主内核事件（enum fdi_kernel_event） */
    int            error_count;
    int            reset_count;
    int            stall_count;
    int            corrupt_count;
    int            timeout_count;
};

/*
 * expect_validate_schema — schema gate 校验（R-18 P5 方向 5）
 *
 * 在启动/列出时校验 expect_table 与内核 event 枚举语义一致：
 *   1) 每个 fault 的 kernel_event 落在合法镜像枚举范围内
 *   2) 每个 fault 的期望字段取值合法（-1 或 >=0）
 *   3) 每个 fault 的 name 与 fault_id_to_name() 一致（防重命名漂移）
 * 返回 0 全部通过，-1 校验失败（调用方应拒绝继续执行）。
 */
int expect_validate_schema(void);

/* 输出 JSON 到 stdout（与 fault-verify --expect 格式一致） */
void expect_output_by_id(enum fault_id id);

/* 供命令行 --list-failures 列出全部故障 ID 与描述 */
void expect_list_all(void);

#endif
