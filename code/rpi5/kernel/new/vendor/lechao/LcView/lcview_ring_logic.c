/*
 * lcview_ring_logic.c — LcView 环形缓冲区纯索引逻辑实现
 *
 * 与 lcview_ring_logic.h 配套：4 个核心算法函数，纯 C 实现（仅依赖
 * stdint/memcpy），内核与 host 单测共用同一份源码，杜绝复制漂移。
 * 逻辑语义与 lcview_ring.c 原 static 函数一一对应（2026-08-28 抽取）。
 */

#include "lcview_ring_logic.h"

#ifdef __KERNEL__
#include <linux/string.h>
#else
#include <string.h>
#endif

uint32_t ring_avail_write_core(uint32_t size, uint32_t write_pos,
                               uint32_t read_pos)
{
    uint32_t used;

    if (write_pos >= read_pos)
        used = write_pos - read_pos;
    else
        used = size - read_pos + write_pos;
    return size - used - 1;
}

void ring_memcpy_out_core(const uint8_t *buf, uint32_t size, uint8_t *dst,
                          uint32_t pos, uint32_t len)
{
    if (pos + len <= size) {
        memcpy(dst, buf + pos, len);
    } else {
        uint32_t part1 = size - pos;
        memcpy(dst, buf + pos, part1);
        memcpy(dst + part1, buf, len - part1);
    }
}

void ring_memcpy_in_core(uint8_t *buf, uint32_t size, uint32_t pos,
                         const uint8_t *src, uint32_t len)
{
    if (pos + len <= size) {
        memcpy(buf + pos, src, len);
    } else {
        uint32_t part1 = size - pos;
        memcpy(buf + pos, src, part1);
        memcpy(buf, src + part1, len - part1);
    }
}

int ring_evict_one_core(uint8_t *buf, uint32_t size, uint32_t *read_pos,
                        uint32_t write_pos, uint32_t default_record_len,
                        uint32_t *out_len)
{
    uint32_t old_len;
    uint32_t rpos = *read_pos;

    if (out_len)
        *out_len = 0;

    /* 环空（read_pos == write_pos）不驱逐 */
    if (rpos == write_pos)
        return 0;

    /* 读取长度前缀，处理跨尾部换行 */
    if (rpos + LCVIEW_RING_LEN_PREFIX <= size) {
        memcpy(&old_len, buf + rpos, LCVIEW_RING_LEN_PREFIX);
    } else {
        uint32_t part1 = size - rpos;
        memcpy(&old_len, buf + rpos, part1);
        memcpy(((uint8_t *)&old_len) + part1, buf,
               LCVIEW_RING_LEN_PREFIX - part1);
    }

    if (out_len)
        *out_len = old_len;

    /* 防御损坏记录：长度异常时用保守默认长度跳过 */
    if (old_len == 0 || old_len > size) {
        *read_pos = (rpos + default_record_len) % size;
        return 2;
    }

    *read_pos = (rpos + old_len) % size;
    return 1;
}
