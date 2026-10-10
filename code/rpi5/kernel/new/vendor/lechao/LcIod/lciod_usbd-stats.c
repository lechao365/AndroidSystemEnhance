/*
 * ============================================================
 * vendor_lechao_usbd-stats.c — USB 存储速率统计引擎与事件处理
 *
 * 【所属模块】Lechao USB 存储速率监控驱动 (VENDOR_LECHAO_USBD)
 *
 * 【文件用途】
 *   实现 notifier 回调函数，处理 usb-storage 核心发射的所有传输事件。
 *   职责包括：
 *   1. 更新累计统计计数器（bytes/cmds/errors/stall/timeout/corrupt/reset）
 *   2. 计算瞬时传输速率并判定性能降级（degrade）
 *   3. 记录最近事件信息（last_event）并推送到环形缓冲区供 read() 读取
 *   4. 发射 LcView 结构化 trace 事件（用于全系统时序分析）
 *
 * 【文件关系】
 *   - vendor_lechao_usbd.c：主模块，注册 notifier 并管理设备生命周期
 *   - vendor_lechao_usbd.h：内部头文件，定义 device 结构体和函数原型
 *   - vendor_lechao_usbd-ioctl.h：用户态 ABI 定义
 *   - usb.h：定义 notifier 事件枚举和 payload 结构体
 *   - lcview_events.h：LcView 事件 ID 定义
 *   - transport.c：usb-storage 传输层，发射 notifier 事件的源头
 *
 * 【线程安全】
 *   handle_event 运行在 atomic notifier chain 上，不可睡眠。
 *   所有 stats 字段的读写都在 rate_dev->lock 自旋锁保护下进行。
 *   event_push 在 event_lock 自旋锁保护下操作环形缓冲区。
 * ============================================================
 */

#include "lciod_usbd.h"
#include "lciod_read_logic.h"
#include "uas-notifier.h"
#include <linux/math64.h>
#include <linux/percpu.h>
#include <linux/sched/clock.h>
#include <scsi/scsi_cmnd.h>
#include "lcview_events.h"
#include "lcview_internal.h"
#include "kernel_lechao_log.h"

#define PREFIX KERNEL_USB_TAG ": "
#define VENDOR_LECHAO_USBD_DEGRADE_WINDOW_NS NSEC_PER_SEC  /* degrade 检测窗口大小：1 秒 */

extern int usbd_debug;
#define LC_DBG(fmt, ...) do { if (usbd_debug) pr_info(PREFIX "[D] " fmt, ##__VA_ARGS__); } while (0)

/*
 * per-CPU 时间戳槽（R-16 P4 方向 2）：降时钟开销
 *
 * 【背景】
 *   原实现每命令多次调用 ktime_get()/ktime_get_ns()：每次走 timekeeping
 *   seqcount 重试循环（含 smp_rmb），I/O 洪水时 per-SCSI-命令累计开销可观。
 *   本模块（stats.c）是 per-命令热路径（END 的 last_update/degrade 窗口、
 *   异常事件的 record/event_push），时钟读数密集。
 *
 * 【设计】
 *   - 每个 CPU 独立缓存一个 mono 时间戳槽（last_mono_ns）+ 缓存时的
 *     sched_clock() 原始值（last_raw）。
 *   - 读取时仅做一次 sched_clock() 差值判断（latch 读 + 减法，免 seqcount
 *     重试），刷新间隔（LCVIEW_TS_REFRESH_NS）内直接返回缓存值，不调
 *     ktime_get_ns()；超阈值才刷新一次真实时钟。
 *   - 每 CPU 独立缓存免 cacheline 争用；mono 语义（CLOCK_MONOTONIC 近似，
 *     sched_clock 与 ktime_get_ns 同源单调）满足事件时间戳/窗口比较用途。
 *   - wall 时间戳（wall_time_ns/timestamp_ns 的 REALTIME 分量）保留实时
 *     ktime_get_real_ns()：受 NTP/adjtime 调整，per-CPU 缓存会放大各 CPU
 *     对调整的感知偏差，破坏跨源（HAL/daemon 日志）时间对齐语义。
 *
 * 【调用上下文】
 *   读取可安全用于 atomic notifier 上下文（无锁、不可睡眠）。
 */
#define LCVIEW_TS_REFRESH_NS (2ULL * NSEC_PER_MSEC)  /* 2ms 刷新阈值 */

struct lcview_ts_slot {
    u64 last_mono_ns;   /* 缓存的 ktime_get_ns() 值（CLOCK_MONOTONIC） */
    u64 last_raw;       /* 缓存时刻的 sched_clock() 原始值 */
};

static DEFINE_PER_CPU(struct lcview_ts_slot, lcview_ts_slots);

/*
 * lcview_ts_mono_ns — 读取 per-CPU 缓存的 mono 时间戳（ns）
 *
 * 刷新间隔内返回缓存值（免 timekeeping seqcount）；间隔内本函数只读
 * per-CPU 槽 + 一次 sched_clock() 差值判断。调用上下文：原子上下文安全。
 */
static inline u64 lcview_ts_mono_ns(void)
{
    struct lcview_ts_slot *slot = this_cpu_ptr(&lcview_ts_slots);
    u64 now_raw = sched_clock();

    if (unlikely(now_raw - slot->last_raw > LCVIEW_TS_REFRESH_NS)) {
        slot->last_raw = now_raw;
        slot->last_mono_ns = ktime_get_ns();
    }
    return slot->last_mono_ns;
}

/*
 * vendor_lechao_usbd_dir_to_u8 — 将 SCSI 数据方向枚举转换为 ABI 编码
 * @sc_data_direction: SCSI 命令的数据方向（DMA_FROM_DEVICE / DMA_TO_DEVICE / DMA_NONE）
 *
 * 返回值：1=读, 2=写, 0=无数据（VENDOR_LECHAO_USBD_DIR_* 常量）
 * 用于填充 vendor_lechao_usbd_event.data_direction 字段。
 */
static inline u8 vendor_lechao_usbd_dir_to_u8(int sc_data_direction)
{
    switch (sc_data_direction) {
    case DMA_FROM_DEVICE:
        return VENDOR_LECHAO_USBD_DIR_READ;
    case DMA_TO_DEVICE:
        return VENDOR_LECHAO_USBD_DIR_WRITE;
    default:
        return VENDOR_LECHAO_USBD_DIR_NONE;
    }
}

/*
 * vendor_lechao_usbd_record_last_event_locked — 记录最近一条异常事件
 * @rate_dev: 目标设备实例
 * @srb:      SCSI 命令上下文（非空时填充 opcode/lba/bytes/retry；
 *            RATE_DEGRADED 等无命令上下文事件传 NULL）
 * @degrade_baseline: RATE_DEGRADED 复用 lba 字段承载的降级基线速率
 *            （bytes/s）；其他事件传 0
 * @type:     事件类型（见 vendor_lechao_usbd_event_type 枚举）
 * @value:    事件附加值（如 result 码 / RATE_DEGRADED 的当前速率）
 * @status:   原始错误码 / RATE_DEGRADED 的降级阈值速率
 * @dir:      数据传输方向（VENDOR_LECHAO_USBD_DIR_*）
 *
 * 更新 rate_dev->last_event 和 stats 中的 last_event_ts_ns/last_event_type。
 * 这些信息随后通过 IOC_GET_STATS 返回给用户态。
 *
 * R-14 方向 4：事件同时记录 mono（timestamp_ns）与 wall（wall_time_ns）
 * 双时间戳，供跨源（内核事件 / HAL / daemon 日志）时序关联。
 *
 * 调用上下文：必须持有 rate_dev->lock 自旋锁。
 */
static inline void vendor_lechao_usbd_record_last_event_locked(
    struct vendor_lechao_usbd_device *rate_dev,
    struct scsi_cmnd *srb,
    u64 degrade_baseline,
    u32 type, u32 value, s32 status, u8 dir)
{
    /* R-16 P4 方向 2：mono 时间读 per-CPU 槽（wall 保留实时——NTP 对齐语义） */
    u64 now = lcview_ts_mono_ns();

    rate_dev->last_event.timestamp_ns = now;
    rate_dev->last_event.wall_time_ns = ktime_get_real_ns();
    rate_dev->last_event.event_type = type;
    rate_dev->last_event.event_value = value;
    rate_dev->last_event.status = status;
    rate_dev->last_event.data_direction = dir;
    rate_dev->last_event.valid = 1;
    memset(rate_dev->last_event.reserved, 0, sizeof(rate_dev->last_event.reserved));
    memset(rate_dev->last_event.reserved2, 0, sizeof(rate_dev->last_event.reserved2));
    if (degrade_baseline) {
        /* RATE_DEGRADED：无 SCSI 命令上下文，lba 复用承载降级基线速率 */
        rate_dev->last_event.opcode = 0;
        rate_dev->last_event.lba = degrade_baseline;
        rate_dev->last_event.bytes = 0;
        rate_dev->last_event.retry = 0;
    } else if (srb) {
        /* cmnd 为定长数组成员（永不空），直接取 opcode */
        rate_dev->last_event.opcode = srb->cmnd[0];
        rate_dev->last_event.lba = scsi_get_lba(srb);
        rate_dev->last_event.bytes = scsi_bufflen(srb) - scsi_get_resid(srb);
        rate_dev->last_event.retry = (u8)srb->retries;
    } else {
        rate_dev->last_event.opcode = 0;
        rate_dev->last_event.lba = 0;
        rate_dev->last_event.bytes = 0;
        rate_dev->last_event.retry = 0;
    }

    rate_dev->stats.last_event_ts_ns = now;
    rate_dev->stats.last_event_type = type;
}

/*
 * vendor_lechao_usbd_rate_from_ns — 速率倒数近似（省 64 位除法）
 * @bytes:      有效字节数
 * @elapsed_ns: 耗时（纳秒）
 *
 * 计算 rate = bytes * NSEC_PER_SEC / elapsed_ns（字节/秒）的近似值。
 *
 * R-11 方向 1：直接 div64_u64 在除数超 32 位时走 libgcc 软件除法慢路径，
 * per-END 每条命令执行 2 次，I/O 洪水时开销可观。本函数把除数按 2 的幂
 * 同步缩放压入 32 位（分子同步缩放保持商近似），用 div_u64（ARM64 udiv
 * 指令快路径）替代 64 位除法；分子先缩到安全范围防 bytes * NSEC_PER_SEC
 * 溢出（CXX-002）。
 */
static inline u64 vendor_lechao_usbd_rate_from_ns(u64 bytes, u64 elapsed_ns)
{
    u64 numer = bytes;
    u64 denom = elapsed_ns;
    u32 den32;

    if (!denom)
        return 0;

    /* 分子防溢出：先缩到 bytes * NSEC_PER_SEC 不溢出 u64 的范围 */
    while (numer > (~0ULL / NSEC_PER_SEC))
    {
        numer >>= 8;
        denom >>= 8;
    }
    /* 除数压入 32 位：正常传输耗时远小于 2^32 ns，循环通常不进入 */
    while (denom >= (1ULL << 32))
    {
        numer >>= 8;
        denom >>= 8;
    }
    den32 = denom ? (u32)denom : 1;
    return div_u64(numer * NSEC_PER_SEC, den32);
}

/*
 * vendor_lechao_usbd_update_current_rate_locked — 计算瞬时传输速率
 * @rate_dev:   目标设备实例
 * @bytes:      本次传输的有效字节数
 * @elapsed_ns: 本次传输的耗时（纳秒）
 *
 * 计算公式：rate = bytes * NSEC_PER_SEC / elapsed_ns（字节/秒）
 * 同时更新 peak_rate（历史最高值）。速率计算经
 * vendor_lechao_usbd_rate_from_ns 倒数近似（R-11 方向 1）。
 *
 * 调用上下文：必须持有 rate_dev->lock 自旋锁。
 */
static inline void vendor_lechao_usbd_update_current_rate_locked(
    struct vendor_lechao_usbd_device *rate_dev,
    u64 bytes, u64 elapsed_ns)
{
    u64 current_rate = 0;

    current_rate = vendor_lechao_usbd_rate_from_ns(bytes, elapsed_ns);

    rate_dev->stats.current_rate = current_rate;
    if (current_rate > rate_dev->stats.peak_rate)
        rate_dev->stats.peak_rate = current_rate;
}

/*
 * vendor_lechao_usbd_update_degrade_context_locked — 滑动窗口 degrade 判定
 * @rate_dev: 目标设备实例
 * @bytes:    本次传输的有效字节数
 * @baseline_rate_out: 判定降级时输出窗口基线速率（bytes/s）；未降级输出 0
 * @threshold_out: 判定降级时输出窗口阈值速率（baseline>>1）；未降级输出 0
 *
 * 【degrade 判定算法】
 *   使用 1 秒滑动窗口对比历史基线速率与当前瞬时速率：
 *   1. 如果是第一次调用或窗口未满（< 1秒），累计字节数并返回 false
 *   2. 窗口满 1 秒后，计算窗口内的基线速率（window_bytes / window_ns）
 *   3. 如果基线速率 > 当前瞬时速率 × 2，认为发生了性能降级，返回 true
 *   4. 重置窗口，开始新一轮累计
 *
 *   倍率阈值 2 的设计考量：USB 传输速率本身有波动（±30% 是正常的），
 *   2 倍阈值可以过滤掉正常的速率抖动，只捕获真正的性能问题
 *   （如 USB 2.0 降级到 Full Speed、线缆接触不良等）。
 *
 * 【职责边界】
 *   本函数仅返回是否判定为降级，不修改 degrade_count、不推送事件、
 *   不发射 lcview trace。所有副作用统一由调用方（TRANSPORT_END 分支）
 *   在确认 degraded 后单点执行，避免双计数。R-14 方向 3：基线与阈值
 *   通过出参回传，供 RATE_DEGRADED 事件携带（判定降级幅度）。
 *
 * 调用上下文：必须持有 rate_dev->lock 自旋锁。
 */
static inline bool vendor_lechao_usbd_update_degrade_context_locked(
    struct vendor_lechao_usbd_device *rate_dev,
    u64 bytes,
    u64 *baseline_rate_out,
    u64 *threshold_out)
{
    /* R-16 P4 方向 2：mono 时间读 per-CPU 槽（降时钟开销） */
    u64 now = lcview_ts_mono_ns();
    u64 window_ns;
    u64 baseline_rate;
    u64 threshold;

    if (!rate_dev->last_degrade_window_start) {
        rate_dev->last_degrade_window_start = now;
        rate_dev->last_degrade_window_bytes = bytes;
        if (baseline_rate_out)
            *baseline_rate_out = 0;
        if (threshold_out)
            *threshold_out = 0;
        return false;
    }

    window_ns = now - rate_dev->last_degrade_window_start;
    if (window_ns < VENDOR_LECHAO_USBD_DEGRADE_WINDOW_NS) {
        rate_dev->last_degrade_window_bytes += bytes;
        if (baseline_rate_out)
            *baseline_rate_out = 0;
        if (threshold_out)
            *threshold_out = 0;
        return false;
    }

    baseline_rate = vendor_lechao_usbd_rate_from_ns(rate_dev->last_degrade_window_bytes, window_ns);
    threshold = baseline_rate >> 1;
    bool degraded = (baseline_rate > 0 && rate_dev->stats.current_rate < threshold);
    if (baseline_rate_out)
        *baseline_rate_out = degraded ? baseline_rate : 0;
    if (threshold_out)
        *threshold_out = degraded ? threshold : 0;

    rate_dev->last_degrade_window_start = now;
    rate_dev->last_degrade_window_bytes = bytes;
    return degraded;
}

/*
 * ---- LcView trace helper 函数组 ----
 *
 * 每个 helper 函数将特定事件类型发射到 LcView ring buffer。
 * 这些事件被用户态 lcview_daemon 读取并用于全系统时序分析。
 *
 * 调用上下文：spin_unlock 后调用，仍处于 SCSI 中断/原子上下文——不可睡眠
 * （lcview_builder_start 使用 GFP_ATOMIC 即为此）。KRN-017：原注释
 * "可睡眠"错误，调用方不得在此路径使用 GFP_KERNEL 等可睡眠分配。
 * 如果 builder_start 失败（如内存不足），事件被静默丢弃（ratelimited 日志）。
 */

/*
 * lcview_trace_transport_end — 发射传输结束事件
 * 字段：device_index, direction, bytes, elapsed_ns, was_error
 */
static void lcview_trace_transport_end(struct vendor_lechao_usbd_device *rate_dev,
                                       struct scsi_cmnd *srb, int device_index,
                                       u64 bytes, u64 elapsed_ns, int was_error)
{
    struct lcview_builder *b;
    int rc;
    int dir;

    if (!srb)
        return;

    dir = vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction);

    b = lcview_builder_start(LCVIEW_EVENT_USB_TRANSPORT_END, LCVIEW_LEVEL_INFO);
    if (!b)
        return;
    rc  = lcview_builder_add_int(b, (int64_t)device_index);
    rc |= lcview_builder_add_int(b, (int64_t)dir);
    rc |= lcview_builder_add_int(b, (int64_t)bytes);
    rc |= lcview_builder_add_int(b, (int64_t)elapsed_ns);
    rc |= lcview_builder_add_int(b, (int64_t)was_error);
    /* KRN-016：add 失败聚合处理——任一字段缺失都会使用户态
     * 按 schema 解析错位，整体丢弃事件而非发射残缺记录。*/
    if (rc || lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}

/*
 * lcview_trace_transport_error — 发射 USB 传输层错误事件
 * 字段：device_index, direction, result
 */
static void lcview_trace_transport_error(struct vendor_lechao_usbd_device *rate_dev,
                                         int device_index, int dir, int result)
{
    struct lcview_builder *b;
    int rc;

    b = lcview_builder_start(LCVIEW_EVENT_USB_TRANSPORT_ERROR, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    rc  = lcview_builder_add_int(b, (int64_t)device_index);
    rc |= lcview_builder_add_int(b, (int64_t)dir);
    rc |= lcview_builder_add_int(b, (int64_t)result);
    /* KRN-016：add 失败聚合处理——任一字段缺失都会使用户态
     * 按 schema 解析错位，整体丢弃事件而非发射残缺记录。*/
    if (rc || lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}

/*
 * lcview_trace_reset — 发射 USB 设备重置事件
 * 字段：device_index
 */
static void lcview_trace_reset(struct vendor_lechao_usbd_device *rate_dev,
                               int device_index)
{
    struct lcview_builder *b;
    int rc;

    b = lcview_builder_start(LCVIEW_EVENT_USB_RESET, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    rc  = lcview_builder_add_int(b, (int64_t)device_index);
    /* KRN-016：add 失败聚合处理——任一字段缺失都会使用户态
     * 按 schema 解析错位，整体丢弃事件而非发射残缺记录。*/
    if (rc || lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}

/*
 * lcview_trace_stall — 发射 USB STALL 事件
 * 字段：device_index, status
 */
static void lcview_trace_stall(struct vendor_lechao_usbd_device *rate_dev,
                               int device_index, int status)
{
    struct lcview_builder *b;
    int rc;

    b = lcview_builder_start(LCVIEW_EVENT_USB_STALL, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    rc  = lcview_builder_add_int(b, (int64_t)device_index);
    rc |= lcview_builder_add_int(b, (int64_t)status);
    /* KRN-016：add 失败聚合处理——任一字段缺失都会使用户态
     * 按 schema 解析错位，整体丢弃事件而非发射残缺记录。*/
    if (rc || lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}

/*
 * lcview_trace_timeout — 发射 USB 传输超时事件
 * 字段：device_index, status
 */
static void lcview_trace_timeout(struct vendor_lechao_usbd_device *rate_dev,
                                 int device_index, int status)
{
    struct lcview_builder *b;
    int rc;

    b = lcview_builder_start(LCVIEW_EVENT_USB_TIMEOUT, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    rc  = lcview_builder_add_int(b, (int64_t)device_index);
    rc |= lcview_builder_add_int(b, (int64_t)status);
    /* KRN-016：add 失败聚合处理——任一字段缺失都会使用户态
     * 按 schema 解析错位，整体丢弃事件而非发射残缺记录。*/
    if (rc || lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}

/*
 * lcview_trace_data_corrupt — 发射数据损坏事件（babble/EOVERFLOW）
 * 字段：device_index, status
 */
static void lcview_trace_data_corrupt(struct vendor_lechao_usbd_device *rate_dev,
                                      int device_index, int status)
{
    struct lcview_builder *b;
    int rc;

    b = lcview_builder_start(LCVIEW_EVENT_USB_DATA_CORRUPT, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    rc  = lcview_builder_add_int(b, (int64_t)device_index);
    rc |= lcview_builder_add_int(b, (int64_t)status);
    /* KRN-016：add 失败聚合处理——任一字段缺失都会使用户态
     * 按 schema 解析错位，整体丢弃事件而非发射残缺记录。*/
    if (rc || lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}

/*
 * lcview_trace_rate_degraded — 发射性能降级事件
 * 字段：device_index, latency_ns
 */
static void lcview_trace_rate_degraded(struct vendor_lechao_usbd_device *rate_dev,
                                       int device_index, u64 latency_ns)
{
    struct lcview_builder *b;
    int rc;

    b = lcview_builder_start(LCVIEW_EVENT_USB_RATE_DEGRADED, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    rc  = lcview_builder_add_int(b, (int64_t)device_index);
    rc |= lcview_builder_add_int(b, (int64_t)latency_ns);
    /* KRN-016：add 失败聚合处理——任一字段缺失都会使用户态
     * 按 schema 解析错位，整体丢弃事件而非发射残缺记录。*/
    if (rc || lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}

/*
 * vendor_lechao_usbd_event_push — 推送事件到环形缓冲区
 * @dev:    目标设备实例
 * @srb:    SCSI 命令上下文（非空时填充 opcode/lba/bytes/retry）
 * @degrade_baseline: RATE_DEGRADED 复用 lba 字段承载的降级基线速率；其他事件传 0
 * @type:   事件类型
 * @value:  事件附加值
 * @status: 原始错误码
 * @dir:    数据传输方向
 *
 * 将事件写入环形缓冲区并唤醒等待 read() 的进程。
 * 如果缓冲区已满（head 追上 tail），丢弃最旧的事件并打印告警日志
 * （使用 ratelimited 防止日志风暴）。
 *
 * R-14 方向 4：事件同时记录 mono（timestamp_ns）与 wall（wall_time_ns）
 * 双时间戳，供跨源时序关联。
 *
 * 调用上下文：可从 atomic notifier 调用，不可睡眠。
 * event_lock 是 irqsave 自旋锁，确保与 read() 端的安全并发。
 */
static inline void vendor_lechao_usbd_event_push(
    struct vendor_lechao_usbd_device *dev,
    struct scsi_cmnd *srb,
    u64 degrade_baseline,
    u32 type, u32 value, s32 status, u8 dir)
{
    struct vendor_lechao_usbd_event ev;
    unsigned long flags;

    memset(&ev, 0, sizeof(ev));
    /* R-16 P4 方向 2：mono 时间读 per-CPU 槽（wall 保留实时——跨源对齐） */
    ev.timestamp_ns = lcview_ts_mono_ns();
    ev.wall_time_ns = ktime_get_real_ns();
    ev.event_type = type;
    ev.event_value = value;
    ev.status = status;
    ev.data_direction = dir;
    ev.valid = 1;
    if (degrade_baseline) {
        /* RATE_DEGRADED：无 SCSI 命令上下文，lba 复用承载降级基线速率 */
        ev.lba = degrade_baseline;
    } else if (srb) {
        /* cmnd 为定长数组成员（永不空），直接取 opcode */
        ev.opcode = srb->cmnd[0];
        ev.lba = scsi_get_lba(srb);
        ev.bytes = scsi_bufflen(srb) - scsi_get_resid(srb);
        ev.retry = (u8)srb->retries;
    }

    spin_lock_irqsave(&dev->event_lock, flags);
    dev->event_buf[dev->event_head] = ev;
    /* KRN-016/方向 1：环推进判定抽至 lciod_read_logic.c（host 单测覆盖
     * 判红）——head 推进 + overflow 丢弃最旧事件由纯函数统一实现，
     * 内核调用点与 host 共用同一源码防漂移（改坏照绿收口）。 */
    {
        unsigned int new_tail;
        int dropped;
        dev->event_head = lciod_event_ring_push(
            dev->event_head, dev->event_tail,
            VENDOR_LECHAO_USBD_EVENT_BUF_SIZE, &new_tail, &dropped);
        if (dropped) {
            pr_warn_ratelimited(PREFIX "event_push ring overflow, dropped old event\n");
            /* R-06 方向 3：丢弃计数改 atomic64_t 自增——写侧在 event_lock 域，
             * 读侧（fill_stats）在 dev->lock 域，两个锁域无同步，普通 ++ 构成
             * 形式化数据竞争；原子自增与原子读消除竞争，且不破坏 ABI 结构。 */
            atomic64_inc(&dev->event_drop_cnt);
            dev->event_tail = new_tail;
        }
    }
    spin_unlock_irqrestore(&dev->event_lock, flags);

    wake_up_interruptible(&dev->event_wq);
}

/*
 * vendor_lechao_usbd_do_reset — 重置传输类统计计数器
 * @rate_dev: 目标设备实例
 *
 * 清零所有传输类累计计数器（bytes/cmds/errors/degrade/stall/timeout/corrupt）
 * 和快照字段（current_rate/peak_rate/latency/last_event）。
 * 同时重置 degrade 检测窗口、transport 状态和 event_drop_count。
 * 保留 config 和设备标识不变。
 *
 * 【生命周期计数器保留策略】
 *   probe_count / disconnect_count 不清零，它们反映设备热插拔历史，
 *   对排查连接不稳定问题至关重要。用户态 reset 仅用于清传输统计。
 *
 * 调用上下文：必须持有 rate_dev->lock 自旋锁。
 */
void vendor_lechao_usbd_do_reset(struct vendor_lechao_usbd_device *rate_dev)
{
    rate_dev->stats.read_bytes = 0;
    rate_dev->stats.write_bytes = 0;
    rate_dev->stats.read_ns = 0;
    rate_dev->stats.write_ns = 0;
    rate_dev->stats.read_cmds = 0;
    rate_dev->stats.write_cmds = 0;
    rate_dev->stats.error_count = 0;
    rate_dev->stats.reset_count = 0;
    /* probe_count / disconnect_count 为设备生命周期计数，reset 不清零 */
    rate_dev->stats.degrade_count = 0;
    rate_dev->stats.current_rate = 0;
    rate_dev->stats.peak_rate = 0;
    rate_dev->stats.last_transport_latency_ns = 0;
    rate_dev->stats.last_event_ts_ns = 0;
    rate_dev->stats.last_event_type = VENDOR_LECHAO_USBD_EVENT_NONE;
    rate_dev->stats.last_update = 0;
    rate_dev->stats.stall_count = 0;
    rate_dev->stats.corrupt_count = 0;
    rate_dev->stats.timeout_count = 0;
    rate_dev->stats.read_error_count = 0;
    rate_dev->stats.write_error_count = 0;
    /* R-06 方向 3：atomic 清零（do_reset 持 dev->lock，与 event_lock 域自增跨锁域，须原子） */
    atomic64_set(&rate_dev->event_drop_cnt, 0);
    rate_dev->stats.event_drop_count = 0;
    rate_dev->last_transport_latency_ns = 0;
    rate_dev->last_transport_error = false;
    rate_dev->last_degrade_window_start = 0;
    rate_dev->last_degrade_window_bytes = 0;
    memset(&rate_dev->last_event, 0, sizeof(rate_dev->last_event));
    rate_dev->last_event.event_type = VENDOR_LECHAO_USBD_EVENT_NONE;
    rate_dev->stats.enabled = rate_dev->config.enabled;
    rate_dev->stats.flags = rate_dev->config.flags;

    pr_debug(PREFIX "reset done\n");
}

/*
 * vendor_lechao_usbd_transport_end_locked — TRANSPORT_END 共用处理（BOT/UAS）
 * @rate_dev:  目标设备实例（调用方必须已持有 rate_dev->lock）
 * @srb:       SCSI 命令上下文（可能为 NULL）
 * @elapsed_ns: 本次传输耗时（纳秒）；BOT 来自 nd->duration_ns（usb 核心侧测得），
 *              UAS 由 handler 用 last_uas_start_ns 自行差值
 * @was_error: 当前传输周期内是否发生过错误（handler 在锁内由
 *              rate_dev->last_transport_error 计算后传入）
 * @degraded_out:         出参：本次是否判定降级（0/1）
 * @degrade_baseline_out: 出参：判定降级时的窗口基线速率（bytes/s），未降级为 0
 * @degrade_threshold_out:出参：判定降级时的窗口阈值速率（bytes/s），未降级为 0
 * @trace_dir_out:        出参：trace 所需数据方向（VENDOR_LECHAO_USBD_DIR_*）
 * @trace_bytes_out:      出参：trace 所需本次有效字节数
 * @trace_elapsed_out:    出参：trace 所需本次耗时（纳秒）
 *
 * 语义覆盖原 BOT handle_event TRANSPORT_END 分支全部副作用（单一出口，
 * 避免 BOT/UAS 双 handler 双计数）：
 *   - 字节累计：read/write bytes/ns/cmds
 *   - 速率更新：update_current_rate_locked（含 peak_rate）
 *   - degrade 判定：瞬时（10% 阈值，KRN-005）+ 滑动窗口（1s 窗口 + 2 倍阈值）
 *   - degraded 副作用：degrade_count++ / record_last_event / event_push
 *   - last_transport_latency_ns 更新（rate_dev + stats 双份）
 *   - stats.last_update 更新、last_transport_error 复位
 * 调用方在锁外根据出参复用发射 lcview trace（lcview_trace_transport_end /
 * lcview_trace_rate_degraded）。
 * 调用上下文：必须持有 rate_dev->lock 自旋锁，不可睡眠。
 */
void vendor_lechao_usbd_transport_end_locked(
    struct vendor_lechao_usbd_device *rate_dev,
    struct scsi_cmnd *srb, u64 elapsed_ns, int was_error,
    bool *degraded_out, u64 *degrade_baseline_out,
    u64 *degrade_threshold_out, int *trace_dir_out,
    u64 *trace_bytes_out, u64 *trace_elapsed_out)
{
    u64 bytes = 0;
    bool degraded = false;
    u64 degrade_baseline = 0;
    u64 degrade_threshold = 0;

    if (trace_dir_out)
        *trace_dir_out = srb ?
            vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction) : 0;
    if (trace_bytes_out)
        *trace_bytes_out = 0;
    if (trace_elapsed_out)
        *trace_elapsed_out = 0;

    if (srb && !was_error) {
        bytes = scsi_bufflen(srb) - scsi_get_resid(srb);

        if (srb->sc_data_direction == DMA_FROM_DEVICE) {
            rate_dev->stats.read_bytes += bytes;
            rate_dev->stats.read_ns += elapsed_ns;
            rate_dev->stats.read_cmds++;
        } else if (srb->sc_data_direction == DMA_TO_DEVICE) {
            rate_dev->stats.write_bytes += bytes;
            rate_dev->stats.write_ns += elapsed_ns;
            rate_dev->stats.write_cmds++;
        }

        {
            u64 prev_rate = rate_dev->stats.current_rate;
            u64 prev_latency = rate_dev->last_transport_latency_ns;

            vendor_lechao_usbd_update_current_rate_locked(rate_dev, bytes, elapsed_ns);

            /*
             * KRN-005：瞬时判定加 10% 幅度阈值。原实现任意幅度下降/
             * 上升即 degraded，而 USB 命令速率逐条波动（±30% 正常），
             * 条件几乎恒真——每命令都推送 RATE_DEGRADED 事件 + trace，
             * I/O 洪水时 WARN 级事件挤占 ring 驱逐正常记录。
             * 真实降级由滑动窗口判定（1s 窗口 + 2 倍阈值）兜底，
             * 瞬时判定仅作快速告警，10% 阈值过滤正常抖动。
             */
            if (prev_rate > 0 &&
                rate_dev->stats.current_rate < prev_rate - prev_rate / 10) {
                degraded = true;
                degrade_baseline = prev_rate;
                degrade_threshold = prev_rate - prev_rate / 10;
            }
            if (prev_latency > 0 &&
                elapsed_ns > prev_latency + prev_latency / 10) {
                degraded = true;
                /* 延迟降级无速率基线，回退用上一瞬时速率作参考 */
                if (!degrade_baseline)
                    degrade_baseline = prev_rate;
                if (!degrade_threshold)
                    degrade_threshold = prev_rate > 0 ?
                                        prev_rate - prev_rate / 10 : 0;
            }
        }

        /* 滑动窗口判定结果合并入 degraded，统计/事件/trace 统一在下方单点执行 */
        {
            u64 w_baseline = 0;
            u64 w_threshold = 0;

            degraded = degraded ||
                       vendor_lechao_usbd_update_degrade_context_locked(
                           rate_dev, bytes, &w_baseline, &w_threshold);
            if (degraded && w_baseline) {
                degrade_baseline = w_baseline;
                degrade_threshold = w_threshold;
            }
        }
        if (trace_bytes_out)
            *trace_bytes_out = bytes;
        if (trace_elapsed_out)
            *trace_elapsed_out = elapsed_ns;
    }

    /* degrade 统计/事件/trace 的唯一出口，避免双计数 */
    if (degraded) {
        rate_dev->stats.degrade_count++;
        /* R-14 方向 3：event_value=当前速率(bytes/s)，status=降级判定
         * 阈值速率，lba 复用承载降级基线速率——消费侧据此判定降级幅度
         * drop_pct=(baseline-current)*100/baseline。降级事件无 SCSI
         * 命令上下文（srb=NULL），opcode/bytes/retry 置 0。 */
        vendor_lechao_usbd_record_last_event_locked(rate_dev, NULL,
            degrade_baseline,
            VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED,
            (u32)rate_dev->stats.current_rate, (s32)degrade_threshold,
            VENDOR_LECHAO_USBD_DIR_NONE);
        vendor_lechao_usbd_event_push(rate_dev, NULL, degrade_baseline,
            VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED,
            (u32)rate_dev->stats.current_rate, (s32)degrade_threshold,
            VENDOR_LECHAO_USBD_DIR_NONE);
    }

    rate_dev->last_transport_latency_ns = elapsed_ns;
    rate_dev->stats.last_transport_latency_ns = elapsed_ns;

    rate_dev->stats.last_update = lcview_ts_mono_ns();
    rate_dev->last_transport_error = false;

    if (degraded_out)
        *degraded_out = degraded;
    if (degrade_baseline_out)
        *degrade_baseline_out = degrade_baseline;
    if (degrade_threshold_out)
        *degrade_threshold_out = degrade_threshold;
}

/*
 * vendor_lechao_usbd_error_event_locked — 错误类事件共用分发（BOT/UAS）
 * @rate_dev:        目标设备实例（调用方必须已持有 rate_dev->lock）
 * @srb:             SCSI 命令上下文（可能为 NULL）
 * @dir:             数据方向（VENDOR_LECHAO_USBD_DIR_*）。已由调用方按各自
 *                   方向来源规则解析——BOT 与 UAS 的方向来源优先级不同
 *                   （BOT 的 TRANSPORT_ERROR 仅取 srb；其余错误分支 srb
 *                   优先、回退 nd->data_direction；UAS 全部 nd 优先、回退
 *                   srb），故方向解析保留在各自 handler，本函数只消费结果，
 *                   不改变任何方向语义。
 * @event_code:      线材事件类型（VENDOR_LECHAO_USBD_EVENT_*）
 * @counter:         该类事件的累计计数指针（error/stall/timeout/corrupt）
 * @mark_last_error: 是否置位 last_transport_error（仅 TRANSPORT_ERROR 为真）
 * @value:           record/event 的 event_value 字段
 * @status:          record/event 的 status 字段
 *
 * 语义与原 BOT/UAS 两 handler 的逐分支实现逐条一致，仅消除重复：
 *   - counter++；可选置 last_transport_error；
 *   - 按 dir 累加 read/write_error_count；
 *   - record_last_event_locked + event_push 各发射一次（不改变发射次数，
 *     不引入双计数）。
 * 调用上下文：必须持有 rate_dev->lock 自旋锁，不可睡眠。
 */
static inline void vendor_lechao_usbd_error_event_locked(
    struct vendor_lechao_usbd_device *rate_dev,
    struct scsi_cmnd *srb, int dir, u32 event_code,
    u64 *counter, bool mark_last_error, u32 value, s32 status)
{
    (*counter)++;
    if (mark_last_error)
        rate_dev->last_transport_error = true;
    if (dir == VENDOR_LECHAO_USBD_DIR_READ)
        rate_dev->stats.read_error_count++;
    else if (dir == VENDOR_LECHAO_USBD_DIR_WRITE)
        rate_dev->stats.write_error_count++;
    vendor_lechao_usbd_record_last_event_locked(rate_dev, srb, 0,
        event_code, value, status, (u8)dir);
    vendor_lechao_usbd_event_push(rate_dev, srb, 0,
        event_code, value, status, (u8)dir);
}

/*
 * vendor_lechao_usbd_reset_event_locked — RESET 事件共用分发（BOT/UAS）
 * @rate_dev: 目标设备实例（调用方必须已持有 rate_dev->lock）
 *
 * 语义与原 BOT/UAS 两 handler 的 RESET 分支逐条一致：
 *   reset_count++；以 NULL srb / DIR_NONE 记录并推送一次 RESET 事件。
 * 调用上下文：必须持有 rate_dev->lock 自旋锁，不可睡眠。
 */
static inline void vendor_lechao_usbd_reset_event_locked(
    struct vendor_lechao_usbd_device *rate_dev)
{
    rate_dev->stats.reset_count++;
    vendor_lechao_usbd_record_last_event_locked(rate_dev, NULL, 0,
        VENDOR_LECHAO_USBD_EVENT_RESET, 0, 0, VENDOR_LECHAO_USBD_DIR_NONE);
    vendor_lechao_usbd_event_push(rate_dev, NULL, 0,
        VENDOR_LECHAO_USBD_EVENT_RESET, 0, 0, VENDOR_LECHAO_USBD_DIR_NONE);
}

/*
 * vendor_lechao_usbd_handle_event — 核心 notifier 回调，处理所有传输事件
 * @nb:    通知块（container_of 获取 rate_dev）
 * @event: 事件类型（见 usb_stor_notifier_event 枚举）
 * @data:  事件载荷（struct usb_stor_notifier_data *）
 *
 * 【整体处理流程】
 *   1. 获取 rate_dev->lock 自旋锁
 *   2. 根据事件类型更新对应的统计计数器
 *   3. 对需要推送的事件，记录 last_event 并推送到环形缓冲区
 *   4. 释放自旋锁
 *   5. 在无锁状态下发射 LcView trace（因为 lcview_builder 可能睡眠）
 *
 * 【每种事件的处理逻辑】
 *   TRANSPORT_ERROR：
 *     - error_count++，标记 last_transport_error，记录+推送事件
 *   STALL：
 *     - stall_count++，记录+推送事件
 *   TIMEOUT：
 *     - timeout_count++，记录+推送事件
 *   DATA_CORRUPT：
 *     - corrupt_count++，记录+推送事件
 *   TRANSPORT_END（R-16 P4 方向 1：START/END 合并为单次带 duration_ns 的
 *     END——usb-storage 核心侧测得耗时填充 nd->duration_ns，直接消费）：
 *     - 计算传输延迟（elapsed_ns），更新 latency
 *     - 如果传输成功：累计 bytes/cmds/ns，计算瞬时速率
 *     - degrade 判定：如果速率下降或延迟上升，设置 degraded 标志
 *     - 如果 degraded：degrade_count++，记录+推送事件
 *     - 更新 last_update 时间戳，重置 transport 状态
 *   RESET：
 *     - reset_count++，记录+推送事件
 *
 * 【degrade 判定规则】（两种条件，满足任一即判定降级）
 *   1. 速率下降：当前瞬时速率 < 上一次的瞬时速率（prev_rate）
 *   2. 延迟上升：本次传输延迟 > 上一次的传输延迟（prev_latency）
 *   另外还会通过 update_degrade_context_locked 做滑动窗口验证
 *
 * 调用上下文：atomic notifier chain，不可睡眠。
 * 返回值：NOTIFY_OK（始终处理完成，不阻止后续 notifier）。
 */
int vendor_lechao_usbd_handle_event(struct notifier_block *nb,
                            unsigned long event, void *data)
{
    struct vendor_lechao_usbd_device *rate_dev;
    struct usb_stor_notifier_data *nd = data;
    struct scsi_cmnd *srb = nd ? nd->srb : NULL;
    unsigned long flags;
    u64 elapsed_ns = 0;
    bool degraded = false;
    /* R-14 方向 3：RATE_DEGRADED 携带的降级基线/阈值速率（bytes/s）。
     * 由 vendor_lechao_usbd_transport_end_locked 经出参回填（原 TRANSPORT_END
     * 分支的瞬时/滑动窗口判定移入共用函数后，record/event_push 已在锁内完成，
     * 本处仅经出参透传，供后续 trace 扩展使用）。 */
    u64 degrade_baseline = 0;
    u64 degrade_threshold = 0;
    struct {
        int device_index;
        int dir;
        int result;
        int was_error;
        int status;
        u64 ev_bytes;
        u64 ev_elapsed_ns;
    } trace = { 0 };

    rate_dev = container_of(nb, struct vendor_lechao_usbd_device, nb);
    /*
     * R-06 方向 1：enabled 锁外读取统一 READ_ONCE——写侧 apply_config_locked
     * 在 dev->lock 下写（WRITE_ONCE 配对），本处 handle_event 顶部在取锁前
     * 裸读，属无同步并发访问（KCSAN 可报 bool 数据竞争）。虽 bool 单字节撕裂
     * 概率极低，但缺内存序保证且 disable 语义（enabled=false 早退）依赖读可见性，
     * 统一 READ_ONCE 消除形式化竞争。
     */
    if (!READ_ONCE(rate_dev->enabled))
        return NOTIFY_DONE;

    trace.device_index = rate_dev->minor;

    spin_lock_irqsave(&rate_dev->lock, flags);

    switch (event) {
    case USB_STOR_NOTIFIER_TRANSPORT_ERROR:
        /* 方向来源（BOT）：此分支 transport.c 仅由 srb 推导，无 nd 回退 */
        trace.dir = srb ? vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction) : 0;
        trace.result = nd ? nd->result : 0;
        vendor_lechao_usbd_error_event_locked(rate_dev, srb, trace.dir,
            VENDOR_LECHAO_USBD_EVENT_TRANSPORT_ERROR,
            &rate_dev->stats.error_count, true,
            (u32)trace.result, trace.result);
        break;

    case USB_STOR_NOTIFIER_STALL:
        /* 方向来源（BOT）：srb 优先；STALL 分支 transport.c 未填
         * nd->data_direction，回退 nd->data_direction */
        trace.dir = srb ? (int)vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction)
                        : (nd ? (int)nd->data_direction : 0);
        trace.status = nd ? nd->status : 0;
        vendor_lechao_usbd_error_event_locked(rate_dev, srb, trace.dir,
            VENDOR_LECHAO_USBD_EVENT_STALL,
            &rate_dev->stats.stall_count, false, 0, trace.status);
        break;

    case USB_STOR_NOTIFIER_TIMEOUT:
        /* 方向来源（BOT）：srb 优先；TIMEOUT 分支 transport.c 未填
         * nd->data_direction，回退 nd->data_direction */
        trace.dir = srb ? (int)vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction)
                        : (nd ? (int)nd->data_direction : 0);
        trace.status = nd ? nd->status : 0;
        vendor_lechao_usbd_error_event_locked(rate_dev, srb, trace.dir,
            VENDOR_LECHAO_USBD_EVENT_TIMEOUT,
            &rate_dev->stats.timeout_count, false, 0, trace.status);
        break;

    case USB_STOR_NOTIFIER_DATA_CORRUPT:
        /* 方向来源（BOT）：srb 优先；回退 nd->data_direction */
        trace.dir = srb ? (int)vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction)
                        : (nd ? (int)nd->data_direction : 0);
        trace.status = nd ? nd->status : 0;
        vendor_lechao_usbd_error_event_locked(rate_dev, srb, trace.dir,
            VENDOR_LECHAO_USBD_EVENT_DATA_CORRUPT,
            &rate_dev->stats.corrupt_count, false, 0, trace.status);
        break;

    case USB_STOR_NOTIFIER_TRANSPORT_END:
        /* R-16 P4 方向 1：START/END 合并为单次带 duration_ns 的 END——
         * usb-storage 核心侧已测得传输耗时并填充 nd->duration_ns，删除
         * 原 transport_start_time fallback（START notifier 已停发，
         * per-device 不再维护起始时间戳）。 */
        elapsed_ns = nd ? nd->duration_ns : 0;
        trace.was_error = rate_dev->last_transport_error ? 1 : 0;

        /* 传输结束共用处理（字节累计/速率/degrade/事件推送）抽至
         * vendor_lechao_usbd_transport_end_locked，与 UAS handler 共用；
         * 出参供下方锁外发射 lcview trace。 */
        vendor_lechao_usbd_transport_end_locked(rate_dev, srb, elapsed_ns,
                                                trace.was_error, &degraded,
                                                &degrade_baseline,
                                                &degrade_threshold,
                                                &trace.dir, &trace.ev_bytes,
                                                &trace.ev_elapsed_ns);
        break;

    case USB_STOR_NOTIFIER_RESET:
        vendor_lechao_usbd_reset_event_locked(rate_dev);
        break;

    default:
        break;
    }

    spin_unlock_irqrestore(&rate_dev->lock, flags);

    switch (event) {
    case USB_STOR_NOTIFIER_TRANSPORT_ERROR:
        lcview_trace_transport_error(rate_dev, trace.device_index,
                                     trace.dir, trace.result);
        break;
    case USB_STOR_NOTIFIER_STALL:
        lcview_trace_stall(rate_dev, trace.device_index, trace.status);
        break;
    case USB_STOR_NOTIFIER_TIMEOUT:
        lcview_trace_timeout(rate_dev, trace.device_index, trace.status);
        break;
    case USB_STOR_NOTIFIER_DATA_CORRUPT:
        lcview_trace_data_corrupt(rate_dev, trace.device_index, trace.status);
        break;
    case USB_STOR_NOTIFIER_TRANSPORT_END:
        lcview_trace_transport_end(rate_dev, srb, trace.device_index,
                                   trace.ev_bytes, trace.ev_elapsed_ns,
                                   trace.was_error);
        if (degraded)
            lcview_trace_rate_degraded(rate_dev, trace.device_index,
                                       elapsed_ns);
        break;
    case USB_STOR_NOTIFIER_RESET:
        lcview_trace_reset(rate_dev, trace.device_index);
        break;
    default:
        break;
    }

    return NOTIFY_OK;
}

/*
 * vendor_lechao_usbd_uas_handle_event — UAS 设备 notifier 回调入口
 * @nb:    通知块（container_of 获取 rate_dev）
 * @event: 事件类型（复用 usb_stor_notifier_event 枚举，见 usb.h）
 * @data:  事件载荷（struct uas_notifier_data *）
 *
 * 与 BOT 的 vendor_lechao_usbd_handle_event 平行，处理 uas.c 经
 * uas_notifier_call 发射的传输事件。与 BOT 的关键差异：
 *   - UAS 核心侧未填 nd->duration_ns（uas_notifier_data.duration_ns 仅
 *     TRANSPORT_END 填充的约定，本实现改由 handler 差值），TRANSPORT_END
 *     的耗时 = lcview_ts_mono_ns() - last_uas_start_ns（TRANSPORT_START
 *     记录的命令起始时间戳）
 *   - TRANSPORT_END 的字节累计/速率/degrade 语义复用
 *     vendor_lechao_usbd_transport_end_locked（与 BOT 共用，单点副作用）
 *   - 错误类事件 data_direction 优先取 uas 侧填充的 nd->data_direction
 *     （uas-notifier.h 已定义该字段），srb 缺失或方向为 NONE 时回退 srb 推导
 *
 * 【锁协议】与 BOT 一致：rate_dev->lock 保护 stats/状态位，lock 外发射
 * lcview trace。调用上下文：atomic notifier chain（uas_notifier_call），
 * 不可睡眠；lcview builder 用现有封装（GFP_ATOMIC 路径由现有代码保证）。
 * 返回值：NOTIFY_OK（始终处理完成，不阻止后续 notifier）。
 */
int vendor_lechao_usbd_uas_handle_event(struct notifier_block *nb,
                                unsigned long event, void *data)
{
    struct vendor_lechao_usbd_device *rate_dev;
    struct uas_notifier_data *nd = data;
    struct scsi_cmnd *srb = nd ? nd->srb : NULL;
    unsigned long flags;
    u64 elapsed_ns = 0;
    int was_error = 0;
    bool degraded = false;
    /* degrade 基线/阈值经 transport_end_locked 出参回填（与 BOT 一致，
     * 供后续 trace 扩展使用） */
    u64 degrade_baseline = 0;
    u64 degrade_threshold = 0;
    struct {
        int device_index;
        int dir;
        int result;
        int status;
        u64 ev_bytes;
        u64 ev_elapsed_ns;
    } trace = { 0 };

    rate_dev = container_of(nb, struct vendor_lechao_usbd_device, nb);

    if (!READ_ONCE(rate_dev->enabled))
        return NOTIFY_DONE;

    trace.device_index = rate_dev->minor;

    spin_lock_irqsave(&rate_dev->lock, flags);

    switch (event) {
    case USB_STOR_NOTIFIER_TRANSPORT_START:
        /* UAS 差值测时长：记录命令起始时间戳（mono ns），TRANSPORT_END 差值 */
        rate_dev->last_uas_start_ns = lcview_ts_mono_ns();
        break;

    case USB_STOR_NOTIFIER_TRANSPORT_END:
        /* UAS 核心侧未填 nd->duration_ns，由 handler 自行差值测时长
         * （BOT 的 duration_ns 由 usb-storage 核心侧填充） */
        if (rate_dev->last_uas_start_ns)
            elapsed_ns = lcview_ts_mono_ns() - rate_dev->last_uas_start_ns;
        was_error = rate_dev->last_transport_error ? 1 : 0;

        /* 与 BOT 共用的传输结束处理（锁内副作用单点完成） */
        vendor_lechao_usbd_transport_end_locked(rate_dev, srb, elapsed_ns,
                                                was_error, &degraded,
                                                &degrade_baseline,
                                                &degrade_threshold,
                                                &trace.dir, &trace.ev_bytes,
                                                &trace.ev_elapsed_ns);
        break;

    case USB_STOR_NOTIFIER_TRANSPORT_ERROR:
        /* 方向来源（UAS）：nd->data_direction 优先，缺失/为 0 时回退 srb */
        trace.dir = (nd && nd->data_direction) ? (int)nd->data_direction
                    : (srb ? (int)vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction) : 0);
        trace.result = nd ? nd->result : 0;
        vendor_lechao_usbd_error_event_locked(rate_dev, srb, trace.dir,
            VENDOR_LECHAO_USBD_EVENT_TRANSPORT_ERROR,
            &rate_dev->stats.error_count, true,
            (u32)trace.result, trace.result);
        break;

    case USB_STOR_NOTIFIER_STALL:
        /* 方向来源（UAS）：nd->data_direction 优先，回退 srb */
        trace.dir = (nd && nd->data_direction) ? (int)nd->data_direction
                    : (srb ? (int)vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction) : 0);
        trace.status = nd ? nd->status : 0;
        vendor_lechao_usbd_error_event_locked(rate_dev, srb, trace.dir,
            VENDOR_LECHAO_USBD_EVENT_STALL,
            &rate_dev->stats.stall_count, false, 0, trace.status);
        break;

    case USB_STOR_NOTIFIER_TIMEOUT:
        /* 方向来源（UAS）：nd->data_direction 优先，回退 srb */
        trace.dir = (nd && nd->data_direction) ? (int)nd->data_direction
                    : (srb ? (int)vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction) : 0);
        trace.status = nd ? nd->status : 0;
        vendor_lechao_usbd_error_event_locked(rate_dev, srb, trace.dir,
            VENDOR_LECHAO_USBD_EVENT_TIMEOUT,
            &rate_dev->stats.timeout_count, false, 0, trace.status);
        break;

    case USB_STOR_NOTIFIER_DATA_CORRUPT:
        /* 方向来源（UAS）：nd->data_direction 优先，回退 srb */
        trace.dir = (nd && nd->data_direction) ? (int)nd->data_direction
                    : (srb ? (int)vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction) : 0);
        trace.status = nd ? nd->status : 0;
        vendor_lechao_usbd_error_event_locked(rate_dev, srb, trace.dir,
            VENDOR_LECHAO_USBD_EVENT_DATA_CORRUPT,
            &rate_dev->stats.corrupt_count, false, 0, trace.status);
        break;

    case USB_STOR_NOTIFIER_RESET:
        vendor_lechao_usbd_reset_event_locked(rate_dev);
        break;

    default:
        break;
    }

    spin_unlock_irqrestore(&rate_dev->lock, flags);

    switch (event) {
    case USB_STOR_NOTIFIER_TRANSPORT_ERROR:
        lcview_trace_transport_error(rate_dev, trace.device_index,
                                     trace.dir, trace.result);
        break;
    case USB_STOR_NOTIFIER_STALL:
        lcview_trace_stall(rate_dev, trace.device_index, trace.status);
        break;
    case USB_STOR_NOTIFIER_TIMEOUT:
        lcview_trace_timeout(rate_dev, trace.device_index, trace.status);
        break;
    case USB_STOR_NOTIFIER_DATA_CORRUPT:
        lcview_trace_data_corrupt(rate_dev, trace.device_index, trace.status);
        break;
    case USB_STOR_NOTIFIER_TRANSPORT_END:
        lcview_trace_transport_end(rate_dev, srb, trace.device_index,
                                   trace.ev_bytes, trace.ev_elapsed_ns,
                                   was_error);
        if (degraded)
            lcview_trace_rate_degraded(rate_dev, trace.device_index,
                                       elapsed_ns);
        break;
    case USB_STOR_NOTIFIER_RESET:
        lcview_trace_reset(rate_dev, trace.device_index);
        break;
    default:
        break;
    }

    return NOTIFY_OK;
}
