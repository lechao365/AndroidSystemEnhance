#ifndef EXPECT_H
#define EXPECT_H

#include <stdint.h>
#include "faults.h"

/* 12 类故障的预期值表
 * 字段说明：
 *   - error_count, reset_count, stall_count, corrupt_count, timeout_count
 *     取值: -1 = 不校验该字段；其他 = 期望值（fault-verify 按 actual>=expect 比对）
 */
struct fault_expect {
    const char    *name;        /* JSON 中的 fault 字段值 */
    const char    *human_desc;  /* 人类可读描述 */
    int            error_count;
    int            reset_count;
    int            stall_count;
    int            corrupt_count;
    int            timeout_count;
};

/* 输出 JSON 到 stdout（与 fault-verify --expect 格式一致） */
void expect_output_by_id(enum fault_id id);

/* 供命令行 --list-failures 列出全部故障 ID 与描述 */
void expect_list_all(void);

#endif
