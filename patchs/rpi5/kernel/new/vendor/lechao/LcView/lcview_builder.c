/*
 * lcview_builder.c — LcView 结构化事件构建器（Builder 模式）
 *
 * 本文件实现 Builder 模式的事件记录构造 API，允许内核模块以链式调用方式
 * 构建包含多个字段的结构化事件：
 *
 *   b = lcview_builder_start(LCVIEW_EVENT_USB_CONNECT, LCVIEW_LEVEL_INFO);
 *   if (b) {
 *       lcview_builder_add_int32(b, speed);
 *       lcview_builder_add_str(b, devname);
 *       lcview_builder_commit(b, &lcview_ring);
 *   }
 *
 * 为什么用 Builder 模式而非 printf 风格？
 * 1. 结构化字段：字段是类型自描述的（type + length + value），
 *    用户态解析器无需预知 schema，可遍历所有字段。
 * 2. 零拷贝序列化：字段直接在 b->buf 中组装，commit 时一次性 memcpy
 *    到环形缓冲区，无中间格式化开销。
 * 3. 类型安全：add_int/add_str 等强类型接口编译时检查参数类型，
 *    避免 printf 格式串与参数不匹配的问题。
 *
 * 序列化格式：
 *   [0..15]       — lcview_record_hdr (16B, packed)
 *   [16..]        — TLV 字段序列:
 *     对于固定长度类型 (INT32/INT64/FLOAT)：
 *       type(1B) + value(NB)
 *     对于变长类型 (STRING/BINARY)：
 *       type(1B) + len(2B, uint16_t) + data(len)
 *
 * 上下文安全性：
 * - GFP_ATOMIC 分配：可在中断上下文或 spin_lock 保护区使用
 * - add_* 系列只在 b->buf 上操作，不访问共享数据，无需锁
 * - commit 时调用 lcview_ring_write（持锁写入），之后释放 builder
 */

#include "lcview_internal.h"
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/timekeeping.h>
#include "kernel_lechao_log.h"

extern int lcview_debug;
#define LC_DBG(fmt, ...) do { if (lcview_debug) pr_info(PREFIX "[D] " fmt, ##__VA_ARGS__); } while (0)

#define PREFIX KERNEL_LCVIEW_TAG ": builder: "

/*
 * compute_str_len — 计算字符串长度并截断到 uint16_t 上限
 *
 * 为什么需要长度上限？
 * 序列化格式中字符串长度用 uint16_t (2 字节) 存储，
 * 最大表示 65535 字节。如果源字符串超过此值，直接截断。
 * 实际场景中日志字符串不太可能 > 64KB，此截断仅为防御性编程。
 *
 * 返回 0 对 NULL 字符串也安全调用方无需额外检查 NULL。
 */
static uint16_t compute_str_len(const char *str)
{
    if (!str)
        return 0;
    size_t len = strlen(str);
    return (len > 0xFFFF) ? 0xFFFF : (uint16_t)len;
}

/*
 * lcview_builder_new — 分配并初始化事件构建器
 *
 * 用 kmalloc(GFP_ATOMIC) 分配 4KB+ 大小的对象：
 *   sizeof(lcview_builder) = LCVIEW_BUILDER_MAX_SIZE + 字段 ≈ 4100 字节
 *
 * 为什么用 GFP_ATOMIC？
 * 因为调用者可能持有 spin_lock（如 USB 中断处理函数），
 * 使用 GFP_KERNEL 会导致内核在分配时尝试睡眠（文件系统 I/O），
 * 这在 atomic 上下文中是禁止的（会导致 "scheduling while atomic" BUG）。
 *
 * GFP_ATOMIC 的缺点：
 * - 仅使用空闲页的紧急预留，分配失败概率高于 GFP_KERNEL
 * - 但 4KB 的分配在嵌入式系统上通常不是问题
 * - 失败时调用者只需跳过这条日志，不会造成功能性故障
 *
 * data_offset 初始设为 sizeof(lcview_record_hdr) (16)，
 * 预留头部空间给 commit 时填充。add_* 写字段从偏移 16 开始。
 */
struct lcview_builder *lcview_builder_new(uint16_t event_id, uint8_t level)
{
    struct lcview_builder *b;

    b = kmalloc(sizeof(*b), GFP_ATOMIC);
    if (!b) {
        pr_err(PREFIX "kmalloc failed for event_id=%u\n", event_id);
        return NULL;
    }

    memset(b, 0, sizeof(*b));
    b->event_id = event_id;
    b->level = level;
    b->field_count = 0;
    b->committed = false;

    /*
     * 预留记录头空间 (16B)：
     * buf[0..15] 保留给 lcview_record_hdr
     * buf[16..]  用于字段值的 TLV 序列化
     * commit 时才会把头部信息写入 buf[0..15]
     */
    b->data_offset = sizeof(struct lcview_record_hdr);

    pr_debug(PREFIX "allocated builder event_id=%u level=%u\n",
             event_id, level);

    return b;
}

/*
 * lcview_builder_free — 释放构建器内存
 * KISS 原则：直接 kfree，不做额外清理。
 */
void lcview_builder_free(struct lcview_builder *b)
{
    kfree(b);
}

/*
 * builder_write_field — 通用字段写入函数
 *
 * 对固定长度字段 (INT32/INT64/FLOAT) 调用此函数。
 * 布局：type(1B) + value(val_len B)
 *
 * 为什么不在 builder_write_field 中自动处理 STRING/BINARY 的长度前缀？
 * 因为 STRING/BINARY 的编码格式不同（类型 + 长度 + 数据），
 * 需要额外 2 字节的长度字段，不适合在此统一处理。
 * 字符串和二进制有各自的专用函数。
 */
static int builder_write_field(struct lcview_builder *b,
                               uint8_t type, const void *val, uint32_t val_len)
{
    uint32_t field_total = 1 + val_len; /* type(1B) + value */
    if (b->data_offset + field_total > LCVIEW_BUILDER_MAX_SIZE) {
        LC_DBG("field overflow: remaining=%zu\n",
               (size_t)(LCVIEW_BUILDER_MAX_SIZE - b->data_offset));
        return -ENOSPC;
    }

    b->buf[b->data_offset] = type;
    b->data_offset += 1;
    memcpy(b->buf + b->data_offset, val, val_len);
    b->data_offset += val_len;
    b->field_count++;

    return 0;
}

/* 添加 int64 类型字段 (8 字节) */
int lcview_builder_add_int(struct lcview_builder *b, int64_t val)
{
    return builder_write_field(b, LCVIEW_TYPE_INT64, &val, sizeof(val));
}

/* 添加 int32 类型字段 (4 字节) */
int lcview_builder_add_int32(struct lcview_builder *b, int32_t val)
{
    return builder_write_field(b, LCVIEW_TYPE_INT32, &val, sizeof(val));
}

/*
 * 添加字符串类型字段
 *
 * 编码格式：type(1B) + len(2B, uint16_t big-endian) + data(len B)
 * 为什么字符串用独立的编码而非统一定长字段？
 * 因为字符串长度可变，需要在序列化数据中记录长度以便解析。
 * 使用 2 字节 (uint16_t) 作为长度前缀，最大支持 65535 字节字符串，
 * 对日志场景完全够用。
 *
 * 即使 val 为 NULL，也写入一条空字符串（len=0），
 * 这样解析器不会混淆"字段不存在"和"字段为空字符串"。
 */
int lcview_builder_add_str(struct lcview_builder *b, const char *val)
{
    uint16_t len = compute_str_len(val);
    /* 写入顺序：type(1B) + len(2B) + data */
    uint32_t total = 1 + 2 + len;
    if (b->data_offset + total > LCVIEW_BUILDER_MAX_SIZE) {
        LC_DBG("string overflow: len=%u, rem=%zu\n",
               len, (size_t)(LCVIEW_BUILDER_MAX_SIZE - b->data_offset));
        return -ENOSPC;
    }

    b->buf[b->data_offset] = LCVIEW_TYPE_STRING;
    b->data_offset += 1;
    memcpy(b->buf + b->data_offset, &len, sizeof(len));
    b->data_offset += sizeof(len);
    if (len > 0)
        memcpy(b->buf + b->data_offset, val, len);
    b->data_offset += len;
    b->field_count++;
    return 0;
}

/* 添加 float 类型字段 (4 字节) */
int lcview_builder_add_float(struct lcview_builder *b, uint32_t raw_float)
{
    return builder_write_field(b, LCVIEW_TYPE_FLOAT, &raw_float, sizeof(raw_float));
}

/*
 * 添加二进制 blob 类型字段
 *
 * 编码格式与字符串相同：type(1B) + len(2B) + data(len B)
 * 与字符串的区别：binary 内容是原始字节，不假定是 UTF-8/ASCII 文本，
 * 可能包含 '\0' 字符。
 *
 * 防御性检查：如果 ptr 为 NULL 但 len > 0，返回 -EINVAL，
 * 因为这种情况下 memcpy(ptr) 会导致内核 panic。
 * len=0（空 blob）允许 ptr=NULL 或任意值。
 */
int lcview_builder_add_binary(struct lcview_builder *b,
                              const void *ptr, uint16_t len)
{
    if (!ptr && len > 0)
        return -EINVAL;

    uint32_t total = 1 + 2 + len;
    if (b->data_offset + total > LCVIEW_BUILDER_MAX_SIZE) {
        LC_DBG("binary overflow: len=%u\n", len);
        return -ENOSPC;
    }

    b->buf[b->data_offset] = LCVIEW_TYPE_BINARY;
    b->data_offset += 1;
    memcpy(b->buf + b->data_offset, &len, sizeof(len));
    b->data_offset += sizeof(len);
    if (len > 0)
        memcpy(b->buf + b->data_offset, ptr, len);
    b->data_offset += len;
    b->field_count++;
    return 0;
}

/*
 * lcview_builder_cancel — 取消构建事件并释放资源
 *
 * 调用场景：构建过程中遇到不可恢复的错误，或构建器不再需要。
 * 释放 builder 内存，记录取消事件（用于调试日志流完整性问题）。
 */
void lcview_builder_cancel(struct lcview_builder *b)
{
    pr_debug(PREFIX "cancelled event_id=%u level=%u fields=%u\n",
             b->event_id, b->level, b->field_count);
    lcview_builder_free(b);
}

/*
 * lcview_builder_commit — 提交构建的事件到环形缓冲区
 *
 * 提交过程：
 *   1. 检查 committed 标志（防止重复提交导致双倍计费或数据重复）
 *   2. 填充 lcview_record_hdr：
 *      - magic：魔数 0x4C56，用于用户态解析器校验数据完整性
 *      - timestamp_ns：ktime_get_real_ns() 获取 CLOCK_REALTIME 时间戳，
 *        确保跨设备的日志时间对齐（相对 CLOCK_MONOTONIC，它受 NTP 调整）
 *   3. 将头部 memcpy 到 buf 预留位置（偏移 0）
 *   4. 调用 lcview_ring_write 写入环形缓冲区
 *   5. 写入成功则标记 committed=true 并释放 builder；
 *      失败则保留 builder 供调用方重试或 cancel
 *
 * 为什么失败时不自动释放 builder？
 * 调用方可能希望立即重试（例如环形缓冲区暂时繁忙），
 * 释放后重建 builder 重新 add 所有字段的开销太大。
 * 由调用方决定是重试还是 cancel。
 */
int lcview_builder_commit(struct lcview_builder *b, struct lcview_ring *ring)
{
    struct lcview_record_hdr hdr;
    uint32_t total_len;
    int ret;

    if (b->committed) {
        pr_warn(PREFIX "double commit event_id=%u\n", b->event_id);
        return -EINVAL;
    }

    /* 填充记录头 */
    hdr.magic       = LCVIEW_MAGIC;
    hdr.event_id    = b->event_id;
    hdr.level       = b->level;
    hdr.field_count = b->field_count;
    hdr.reserved    = 0;
    /*
     * 使用 ktime_get_real_ns() 而非 ktime_get_ns()：
     * real time 返回墙上时间，与用户态的 CLOCK_REALTIME 一致，
     * 便于用户态解析时将时间戳转换为可读的时间字符串。
     * 缺点：受 NTP 调整影响可能跳跃。如果日志用于性能分析，
     * 建议改用 ktime_get_ns() (CLOCK_MONOTONIC)。
     */
    hdr.timestamp_ns = ktime_get_real_ns();

    /* 将头写入 buffer 头部预留位置 (buf[0..15]) */
    memcpy(b->buf, &hdr, sizeof(hdr));
    total_len = b->data_offset;

    /*
     * 写入环形缓冲区：
     * - 成功：标记 committed 并释放 builder
     * - 失败：保留 builder 供重试，返回错误码
     */
    ret = lcview_ring_write(ring, b->buf, total_len);
    if (ret == 0) {
        LC_DBG("committed event_id=%u level=%u fields=%u len=%u ts=%llu\n",
                 b->event_id, b->level, b->field_count,
                 total_len, hdr.timestamp_ns);
        b->committed = true;
        lcview_builder_free(b);
    } else {
        pr_warn(PREFIX "commit failed event_id=%u err=%d\n",
                b->event_id, ret);
    }

    return ret;
}
