/*
 * lcview_ring_logic.h — LcView 环形缓冲区纯索引逻辑（内核与 host 单测共用）
 *
 * 从 lcview_ring.c 抽出的环形缓冲区核心算法：可写空间计算、跨尾部环绕
 * memcpy、最旧记录驱逐。这些逻辑只依赖缓冲区大小与读写指针，不依赖任何
 * 内核 API（spinlock/vmalloc/atomic/pr_*），因此：
 *   - 内核侧：lcview_ring.c 经薄包装调用，行为与原实现一致
 *   - host 侧：tests/lcview_ring_host_test.c 直接编译本文件 + lcview_ring_logic.c
 *     做单元测试，无需 KUnit、无需内核头（内核 API 已剥离，等价 shim 语义）
 *
 * 长度前缀固定 4 字节（= lcview_internal.h 的 LCVIEW_LEN_PREFIX_SIZE，
 * 此处为保持纯 C 不引内核头而写死，两者须保持同步）。
 */

#ifndef LCVIEW_RING_LOGIC_H
#define LCVIEW_RING_LOGIC_H

#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdint.h>
#include <stddef.h>
#endif

/* 环形缓冲区记录长度前缀字节数（与 LCVIEW_LEN_PREFIX_SIZE 同步，恒 4） */
#define LCVIEW_RING_LEN_PREFIX 4

/*
 * ring_avail_write_core — 计算可写入的空闲字节数
 *
 * 预留 1 字节区分空/满（write_pos == read_pos 为空，满条件为
 * write_pos + 1 == read_pos 环绕）。纯标量计算，无副作用。
 */
uint32_t ring_avail_write_core(uint32_t size, uint32_t write_pos,
                               uint32_t read_pos);

/*
 * ring_memcpy_out_core — 从环形缓冲区读取 len 字节到线性内存
 *
 * 处理跨缓冲区尾部换行：pos + len 超过 size 时先拷 pos..size-1，
 * 再拷 0..剩余，两段 memcpy 避免逐字节 % 取模的性能劣化。
 */
void ring_memcpy_out_core(const uint8_t *buf, uint32_t size, uint8_t *dst,
                          uint32_t pos, uint32_t len);

/*
 * ring_memcpy_in_core — 从线性内存写入 len 字节到环形缓冲区
 *
 * 与 ring_memcpy_out_core 对称，处理 wrap-around 分两段写入。
 */
void ring_memcpy_in_core(uint8_t *buf, uint32_t size, uint32_t pos,
                         const uint8_t *src, uint32_t len);

/*
 * ring_evict_one_core — 驱逐（跳过）一条最旧记录，推进 read_pos
 *
 * 从 read_pos 读取 4 字节长度前缀（处理跨尾部换行），推进
 * read_pos = (read_pos + old_len) % size。防御损坏记录（old_len 为 0 或
 * > size）时用 default_record_len 保守跳过，避免推进过多致永久错乱。
 *
 * @buf/@{size}       环形缓冲区内存与大小
 * @read_pos          入/出：驱逐后推进到的读指针
 * @write_pos         写指针（read_pos == write_pos 表示环空，不驱逐）
 * @default_record_len 损坏记录时的保守跳过长度
 * @out_len           出参（可为 NULL）：被驱逐记录读取到的原始长度
 *                    （损坏时为坏值，供调用方打警告日志）
 * @return 0 未驱逐（环空）/ 1 正常驱逐 / 2 损坏记录按 default 跳过
 */
int ring_evict_one_core(uint8_t *buf, uint32_t size, uint32_t *read_pos,
                        uint32_t write_pos, uint32_t default_record_len,
                        uint32_t *out_len);

#endif /* LCVIEW_RING_LOGIC_H */
