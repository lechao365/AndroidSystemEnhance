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
#include <linux/errno.h>
#else
#include <string.h>
#include <errno.h>
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

/*
 * ring_read_fit_check — 判定当前记录能否装入用户缓冲区剩余空间
 *
 * KRN-001 语义收口：copied_total == 0 且首条记录放不下用户缓冲区时，
 * read 不得返回 0（POSIX 0 = EOF，而 poll 恒报 POLLIN——LT-epoll 消费者
 * 忙轮询或误判设备关闭，记录永久滞留卡死后续 read），必须报 -EINVAL。
 *
 * @copied_total 本次 read 已拷贝给用户态的字节数
 * @record_len   当前记录总长（含 4B 长度前缀）
 * @user_len     用户缓冲区总长
 * @return 0 可装入继续拷贝 / 1 放不下但已有数据（返回已读部分）/
 *         -1 放不下且无数据（调用方返回 -EINVAL）
 *
 * 两参数均 ≤ ring->size ≤ 4MB，uint32 求和无溢出。
 */
int ring_read_fit_check(uint32_t copied_total, uint32_t record_len,
                        uint32_t user_len)
{
    if (copied_total + record_len <= user_len)
        return 0;
    return (copied_total == 0) ? -1 : 1;
}

int ring_read_fit_errno(int fit)
{
    return (fit == 0) ? 0 : -EMSGSIZE;
}

uint32_t ring_overrun_restore_amt(uint32_t read_val, bool copy_ok)
{
    return copy_ok ? 0 : read_val;
}

int builder_write_fits(uint32_t data_offset, uint32_t add_len,
                       uint32_t max_size)
{
    if (data_offset + LCVIEW_RING_LEN_PREFIX + add_len <= max_size)
        return 0;
    return -ENOSPC;
}

int builder_str_field_fits(uint32_t data_offset, uint32_t data_len,
                           uint32_t max_size)
{
    /* 变长字段总长 = type(1B) + len(2B) + data，再计入 4B 记录前缀 */
    return builder_write_fits(data_offset, 3 + data_len, max_size);
}

uint32_t ring_corrupt_skip_len(uint32_t record_len, uint32_t ring_size,
                               uint32_t default_skip)
{
    if (record_len < LCVIEW_RING_LEN_PREFIX || record_len > ring_size)
        return default_skip;
    return record_len;
}
