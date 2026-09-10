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
#include <stdbool.h>
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

/*
 * ring_read_fit_check — 判定当前记录能否装入用户缓冲区剩余空间
 *
 * KRN-001：copied_total == 0 且记录放不下时返回 -1（调用方转 -EINVAL），
 * 禁止以返回 0 伪装 EOF（poll 恒报 POLLIN 时消费者会忙轮询/误判关闭）。
 *
 * @return 0 可装入 / 1 放不下但已有已读数据（break 返回部分）/
 *         -1 放不下且无数据（调用方返回 -EINVAL）
 */
int ring_read_fit_check(uint32_t copied_total, uint32_t record_len,
                        uint32_t user_len);

/*
 * ring_read_fit_errno — 首条放不下时 read 应返回的错误码映射
 *
 * KRN-001 收口：copied_total == 0 且首条记录放不下用户缓冲区时，
 * lcview_ring_read 返回 -EMSGSIZE（修复前为 -EINVAL）提示缓冲不足，
 * 禁止返回 0（POSIX 0=EOF 而 poll 恒报 POLLIN，消费者会忙轮询或
 * 误判设备关闭，记录永久滞留卡死 reader）。
 *
 * @fit ring_read_fit_check 的返回：0 可继续 / -1 首条放不下（调用方
 *      只在 fit < 0 时调用本函数；fit == 1 走 break 返回部分不经过此）
 * @return 0（fit == 0）/ -EMSGSIZE（fit < 0）
 */
int ring_read_fit_errno(int fit);

/*
 * ring_overrun_restore_amt — GET_OVERRUN 读清失败后的回加量
 *
 * KRN-018：atomic_xchg 原子"读清" overrun 计数后，copy_to_user 失败时
 * 若直接返回，计数已被清零而用户未收到 → overrun 低估（写路径继续
 * atomic_inc，丢失的增量不可恢复）。修复：失败时把读到的值加回。
 *
 * @read_val atomic_xchg 读到的原值
 * @copy_ok  copy_to_user 是否成功
 * @return 回加量（copy 失败为 read_val，成功为 0）
 */
uint32_t ring_overrun_restore_amt(uint32_t read_val, bool copy_ok);

/*
 * builder_write_fits — builder 字段写入容量检查（含 4B 长度前缀）
 *
 * record 在环中存 4B 长度前缀 + 内容，读侧以
 * record_len > LCVIEW_BUILDER_MAX_SIZE 判损坏并跳过。builder 的写入
 * 检查若只按 data_offset + 字段长对比上限，内容写满 4096 时记录总长
 * = 4 + 4096 = 4100 超限被读侧误判损坏（丢记录）。故检查必须把长度
 * 前缀 4B（= LCVIEW_RING_LEN_PREFIX，与 LCVIEW_LEN_PREFIX_SIZE 同步）
 * 一并计入上限。
 *
 * @data_offset builder 当前数据偏移（含 16B 记录头）
 * @add_len     待写入字段字节数（type + value）
 * @max_size    LCVIEW_BUILDER_MAX_SIZE（单条事件硬上限）
 * @return 0 装得下 / -ENOSPC 超限
 */
int builder_write_fits(uint32_t data_offset, uint32_t add_len,
                       uint32_t max_size);

/*
 * builder_str_field_fits — 变长字段（STRING/BINARY）写入容量检查
 *
 * 变长字段布局：type(1B) + len(2B) + data(data_len B)，字段总长
 * 3 + data_len；连同 4B 记录长度前缀一并计入上限（语义与
 * builder_write_fits 相同，只是把调用点的 total = 3 + data_len 组合
 * 内聚为纯函数，供 host 测试直接判红调用点——此前 add_str/add_binary
 * 只按 data_offset + total 对比上限漏扣前缀，记录总长 4100 被读侧
 * 误判损坏丢弃，且 tests Makefile 只链 logic.c 调用点零覆盖）。
 *
 * @data_offset builder 当前数据偏移（含 16B 记录头）
 * @data_len    变长字段数据字节数（不含 type/len 前缀）
 * @max_size    LCVIEW_BUILDER_MAX_SIZE（单条事件硬上限）
 * @return 0 装得下 / -ENOSPC 超限
 */
int builder_str_field_fits(uint32_t data_offset, uint32_t data_len,
                           uint32_t max_size);

/*
 * ring_corrupt_skip_len — 损坏记录读取跳过的前移量计算
 *
 * 判损坏时（record_len < 前缀 或 > MAX 或 > 环大小）跳过该记录。
 * 记录在环中实际占用 record_len 字节（写侧长度前缀即记录总长），
 * 前移须按 record_len，否则 read_pos 落进记录体中间把后续流撕裂；
 * 仅当 record_len 完全不可信（<前缀 或 >环大小，垃圾前缀）才用
 * 保守默认跳过量（前缀 + 记录头），防止跳过头。
 *
 * @record_len  读到的长度前缀
 * @ring_size   环形缓冲区大小
 * @default_skip 不可信前缀时的保守跳过量（前缀 + 记录头）
 * @return 应前移的字节数
 */
uint32_t ring_corrupt_skip_len(uint32_t record_len, uint32_t ring_size,
                               uint32_t default_skip);

#endif /* LCVIEW_RING_LOGIC_H */
