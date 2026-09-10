/* ============================================================
 * lciod_read_logic.c — LcIod 读路径纯逻辑决策实现
 * 无内核 API 依赖，host 单测与内核共用同一实现（防漂移）。
 * ============================================================ */

#include "lciod_read_logic.h"

int lciod_nonblock_read_decision(int ring_empty, int shutdown)
{
    /* KRN-004：先判 empty 再判 shutdown——非空 ring 先 drain 事件
     * （shutdown 不越过非空判定），空环才按 shutdown 分流 EOF/EAGAIN */
    if (ring_empty)
        return shutdown ? 0 : 1;
    return -1;
}

int lciod_event_tail_rollback_ok(uint32_t tail_after, uint32_t consumed_pos,
                                 uint32_t buf_size)
{
    return tail_after == (consumed_pos + 1) % buf_size;
}
