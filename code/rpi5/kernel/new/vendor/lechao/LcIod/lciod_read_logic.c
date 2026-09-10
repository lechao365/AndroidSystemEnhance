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

uint32_t lciod_event_ring_push(uint32_t head, uint32_t tail, uint32_t buf_size,
                               uint32_t *new_tail, int *dropped)
{
    /* 与内核 vendor_lechao_usbd_event_push 同构：先写入再推进 head，
     * head 追上 tail（环满）时丢弃最旧事件并推进 tail（overflow）。 */
    uint32_t h = (head + 1) % buf_size;
    uint32_t t = tail;
    int drop = 0;

    if (h == tail) {
        drop = 1;
        t = (tail + 1) % buf_size;
    }
    if (new_tail)
        *new_tail = t;
    if (dropped)
        *dropped = drop;
    return h;
}
