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
#include <linux/math64.h>
#include <scsi/scsi_cmnd.h>
#include "lcview_events.h"
#include "lcview_internal.h"
#include "kernel_lechao_log.h"

#define PREFIX KERNEL_USB_TAG ": "
#define VENDOR_LECHAO_USBD_DEGRADE_WINDOW_NS NSEC_PER_SEC  /* degrade 检测窗口大小：1 秒 */

extern int usbd_debug;
#define LC_DBG(fmt, ...) do { if (usbd_debug) pr_info(PREFIX "[D] " fmt, ##__VA_ARGS__); } while (0)

/*
 * vendor_lechao_usbd_dir_to_u8 — 将 SCSI 数据方向枚举转换为 ABI 编码
 * @sc_data_direction: SCSI 命令的数据方向（DMA_FROM_DEVICE / DMA_TO_DEVICE / DMA_NONE）
 *
 * 返回值：1=读, 2=写, 0=无数据
 * 用于填充 vendor_lechao_usbd_event.data_direction 字段。
 */
static inline u8 vendor_lechao_usbd_dir_to_u8(int sc_data_direction)
{
    switch (sc_data_direction) {
    case DMA_FROM_DEVICE:
        return 1;
    case DMA_TO_DEVICE:
        return 2;
    default:
        return 0;
    }
}

/*
 * vendor_lechao_usbd_record_last_event_locked — 记录最近一条异常事件
 * @rate_dev: 目标设备实例
 * @type:     事件类型（见 vendor_lechao_usbd_event_type 枚举）
 * @value:    事件附加值（如 result 码）
 * @status:   原始错误码
 * @dir:      数据传输方向（0/1/2）
 *
 * 更新 rate_dev->last_event 和 stats 中的 last_event_ts_ns/last_event_type。
 * 这些信息随后通过 IOC_GET_STATS 返回给用户态。
 *
 * 调用上下文：必须持有 rate_dev->lock 自旋锁。
 */
static inline void vendor_lechao_usbd_record_last_event_locked(
    struct vendor_lechao_usbd_device *rate_dev,
    u32 type, u32 value, s32 status, u8 dir)
{
    u64 now = ktime_get_ns();

    rate_dev->last_event.timestamp_ns = now;
    rate_dev->last_event.event_type = type;
    rate_dev->last_event.event_value = value;
    rate_dev->last_event.status = status;
    rate_dev->last_event.data_direction = dir;
    rate_dev->last_event.valid = 1;
    memset(rate_dev->last_event.reserved, 0, sizeof(rate_dev->last_event.reserved));

    rate_dev->stats.last_event_ts_ns = now;
    rate_dev->stats.last_event_type = type;
}

/*
 * vendor_lechao_usbd_update_current_rate_locked — 计算瞬时传输速率
 * @rate_dev:   目标设备实例
 * @bytes:      本次传输的有效字节数
 * @elapsed_ns: 本次传输的耗时（纳秒）
 *
 * 计算公式：rate = bytes * NSEC_PER_SEC / elapsed_ns（字节/秒）
 * 同时更新 peak_rate（历史最高值）。
 *
 * 调用上下文：必须持有 rate_dev->lock 自旋锁。
 */
static inline void vendor_lechao_usbd_update_current_rate_locked(
    struct vendor_lechao_usbd_device *rate_dev,
    u64 bytes, u64 elapsed_ns)
{
    u64 current_rate = 0;

    if (elapsed_ns)
        current_rate = div64_u64(bytes * NSEC_PER_SEC, elapsed_ns);

    rate_dev->stats.current_rate = current_rate;
    if (current_rate > rate_dev->stats.peak_rate)
        rate_dev->stats.peak_rate = current_rate;
}

/*
 * vendor_lechao_usbd_update_degrade_context_locked — 滑动窗口 degrade 判定
 * @rate_dev: 目标设备实例
 * @bytes:    本次传输的有效字节数
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
 *   在确认 degraded 后单点执行，避免双计数。
 *
 * 调用上下文：必须持有 rate_dev->lock 自旋锁。
 */
static inline bool vendor_lechao_usbd_update_degrade_context_locked(
    struct vendor_lechao_usbd_device *rate_dev,
    u64 bytes)
{
    ktime_t now = ktime_get();
    u64 window_ns;
    u64 baseline_rate;

    if (!rate_dev->last_degrade_window_start) {
        rate_dev->last_degrade_window_start = now;
        rate_dev->last_degrade_window_bytes = bytes;
        return false;
    }

    window_ns = ktime_to_ns(ktime_sub(now, rate_dev->last_degrade_window_start));
    if (window_ns < VENDOR_LECHAO_USBD_DEGRADE_WINDOW_NS) {
        rate_dev->last_degrade_window_bytes += bytes;
        return false;
    }

    baseline_rate = div64_u64(rate_dev->last_degrade_window_bytes * NSEC_PER_SEC,
                              window_ns);
    bool degraded = (baseline_rate > 0 &&
                     rate_dev->stats.current_rate < div64_u64(baseline_rate, 2));

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
 * 调用上下文：spin_unlock 后调用，可睡眠（lcview_builder_start 使用 GFP_ATOMIC）。
 * 如果 builder_start 失败（如 ring buffer 满），事件被静默丢弃。
 */

/*
 * lcview_trace_transport_start — 发射传输开始事件
 * @rate_dev:     目标设备实例
 * @srb:          SCSI 命令（用于提取方向和数据长度）
 * @device_index: 次设备号（用于用户态关联设备）
 *
 * 字段：device_index, direction, data_len
 */
static void lcview_trace_transport_start(struct vendor_lechao_usbd_device *rate_dev,
                                         struct scsi_cmnd *srb, int device_index)
{
    struct lcview_builder *b;
    int dir;

    if (!srb)
        return;

    dir = vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction);

    b = lcview_builder_start(LCVIEW_EVENT_USB_TRANSPORT_START, LCVIEW_LEVEL_DEBUG);
    if (!b)
        return;
    lcview_builder_add_int(b, (int64_t)device_index);
    lcview_builder_add_int(b, (int64_t)dir);
    lcview_builder_add_int(b, (int64_t)scsi_bufflen(srb));
    if (lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}

/*
 * lcview_trace_transport_end — 发射传输结束事件
 * 字段：device_index, direction, bytes, elapsed_ns, was_error
 */
static void lcview_trace_transport_end(struct vendor_lechao_usbd_device *rate_dev,
                                       struct scsi_cmnd *srb, int device_index,
                                       u64 bytes, u64 elapsed_ns, int was_error)
{
    struct lcview_builder *b;
    int dir;

    if (!srb)
        return;

    dir = vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction);

    b = lcview_builder_start(LCVIEW_EVENT_USB_TRANSPORT_END, LCVIEW_LEVEL_INFO);
    if (!b)
        return;
    lcview_builder_add_int(b, (int64_t)device_index);
    lcview_builder_add_int(b, (int64_t)dir);
    lcview_builder_add_int(b, (int64_t)bytes);
    lcview_builder_add_int(b, (int64_t)elapsed_ns);
    lcview_builder_add_int(b, (int64_t)was_error);
    if (lcview_builder_commit(b, &lcview_ring))
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

    b = lcview_builder_start(LCVIEW_EVENT_USB_TRANSPORT_ERROR, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    lcview_builder_add_int(b, (int64_t)device_index);
    lcview_builder_add_int(b, (int64_t)dir);
    lcview_builder_add_int(b, (int64_t)result);
    if (lcview_builder_commit(b, &lcview_ring))
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

    b = lcview_builder_start(LCVIEW_EVENT_USB_RESET, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    lcview_builder_add_int(b, (int64_t)device_index);
    if (lcview_builder_commit(b, &lcview_ring))
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

    b = lcview_builder_start(LCVIEW_EVENT_USB_STALL, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    lcview_builder_add_int(b, (int64_t)device_index);
    lcview_builder_add_int(b, (int64_t)status);
    if (lcview_builder_commit(b, &lcview_ring))
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

    b = lcview_builder_start(LCVIEW_EVENT_USB_TIMEOUT, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    lcview_builder_add_int(b, (int64_t)device_index);
    lcview_builder_add_int(b, (int64_t)status);
    if (lcview_builder_commit(b, &lcview_ring))
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

    b = lcview_builder_start(LCVIEW_EVENT_USB_DATA_CORRUPT, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    lcview_builder_add_int(b, (int64_t)device_index);
    lcview_builder_add_int(b, (int64_t)status);
    if (lcview_builder_commit(b, &lcview_ring))
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

    b = lcview_builder_start(LCVIEW_EVENT_USB_RATE_DEGRADED, LCVIEW_LEVEL_WARN);
    if (!b)
        return;
    lcview_builder_add_int(b, (int64_t)device_index);
    lcview_builder_add_int(b, (int64_t)latency_ns);
    if (lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}

/*
 * vendor_lechao_usbd_event_push — 推送事件到环形缓冲区
 * @dev:    目标设备实例
 * @type:   事件类型
 * @value:  事件附加值
 * @status: 原始错误码
 * @dir:    数据传输方向
 *
 * 将事件写入环形缓冲区并唤醒等待 read() 的进程。
 * 如果缓冲区已满（head 追上 tail），丢弃最旧的事件并打印告警日志
 * （使用 ratelimited 防止日志风暴）。
 *
 * 调用上下文：可从 atomic notifier 调用，不可睡眠。
 * event_lock 是 irqsave 自旋锁，确保与 read() 端的安全并发。
 */
static inline void vendor_lechao_usbd_event_push(
    struct vendor_lechao_usbd_device *dev,
    u32 type, u32 value, s32 status, u8 dir)
{
    struct vendor_lechao_usbd_event ev;
    unsigned long flags;

    ev.timestamp_ns = ktime_get_ns();
    ev.event_type = type;
    ev.event_value = value;
    ev.status = status;
    ev.data_direction = dir;
    ev.valid = 1;
    memset(ev.reserved, 0, sizeof(ev.reserved));

    spin_lock_irqsave(&dev->event_lock, flags);
    dev->event_buf[dev->event_head] = ev;
    dev->event_head = (dev->event_head + 1) % VENDOR_LECHAO_USBD_EVENT_BUF_SIZE;
    if (dev->event_head == dev->event_tail) {
        pr_warn_ratelimited(PREFIX "event_push ring overflow, dropped old event\n");
        /* 统计丢弃事件数，归 event_lock 保护域；fill_stats 在 dev->lock 下读取为原子读 */
        dev->stats.event_drop_count++;
        dev->event_tail = (dev->event_tail + 1) % VENDOR_LECHAO_USBD_EVENT_BUF_SIZE;
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
    rate_dev->stats.event_drop_count = 0;
    rate_dev->transport_start_time = ktime_set(0, 0);
    rate_dev->transport_active = false;
    rate_dev->last_transport_latency_ns = 0;
    rate_dev->last_transport_error = false;
    rate_dev->last_degrade_window_start = ktime_set(0, 0);
    rate_dev->last_degrade_window_bytes = 0;
    memset(&rate_dev->last_event, 0, sizeof(rate_dev->last_event));
    rate_dev->last_event.event_type = VENDOR_LECHAO_USBD_EVENT_NONE;
    rate_dev->stats.enabled = rate_dev->config.enabled;
    rate_dev->stats.flags = rate_dev->config.flags;

    pr_debug(PREFIX "reset done\n");
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
 *   TRANSPORT_START：
 *     - 记录传输开始时间，设置 transport_active=true，清除上次错误标志
 *   TRANSPORT_ERROR：
 *     - error_count++，标记 last_transport_error，记录+推送事件
 *   STALL：
 *     - stall_count++，记录+推送事件
 *   TIMEOUT：
 *     - timeout_count++，记录+推送事件
 *   DATA_CORRUPT：
 *     - corrupt_count++，记录+推送事件
 *   TRANSPORT_END：
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
    u64 bytes = 0;
    u64 elapsed_ns = 0;
    bool degraded = false;
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
    if (!rate_dev->enabled)
        return NOTIFY_DONE;

    trace.device_index = rate_dev->minor;

    spin_lock_irqsave(&rate_dev->lock, flags);

    switch (event) {
    case USB_STOR_NOTIFIER_TRANSPORT_START:
        rate_dev->transport_start_time = ktime_get();
        rate_dev->transport_active = true;
        rate_dev->last_transport_error = false;
        trace.dir = srb ? vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction) : 0;
        LC_DBG("TRANSPORT_START: dir=%d bytes=%u\n", trace.dir,
               srb ? scsi_bufflen(srb) : 0);
        break;

    case USB_STOR_NOTIFIER_TRANSPORT_ERROR:
        rate_dev->stats.error_count++;
        rate_dev->last_transport_error = true;
        trace.dir = srb ? vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction) : 0;
        trace.result = nd ? nd->result : 0;
        vendor_lechao_usbd_record_last_event_locked(rate_dev,
            VENDOR_LECHAO_USBD_EVENT_TRANSPORT_ERROR,
            (u32)trace.result, trace.result, (u8)trace.dir);
        vendor_lechao_usbd_event_push(rate_dev,
            VENDOR_LECHAO_USBD_EVENT_TRANSPORT_ERROR,
            (u32)trace.result, trace.result, (u8)trace.dir);
        break;

    case USB_STOR_NOTIFIER_STALL:
        rate_dev->stats.stall_count++;
        /* 优先从 srb 取方向；TIMEOUT 等分支 transport.c 未填 nd->data_direction */
        trace.dir = srb ? (int)vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction)
                        : (nd ? (int)nd->data_direction : 0);
        trace.status = nd ? nd->status : 0;
        vendor_lechao_usbd_record_last_event_locked(rate_dev,
            VENDOR_LECHAO_USBD_EVENT_STALL,
            0, trace.status, (u8)trace.dir);
        vendor_lechao_usbd_event_push(rate_dev,
            VENDOR_LECHAO_USBD_EVENT_STALL,
            0, trace.status, (u8)trace.dir);
        break;

    case USB_STOR_NOTIFIER_TIMEOUT:
        rate_dev->stats.timeout_count++;
        /* transport.c 的 TIMEOUT 发射点未填 nd->data_direction，fallback srb */
        trace.dir = srb ? (int)vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction)
                        : (nd ? (int)nd->data_direction : 0);
        trace.status = nd ? nd->status : 0;
        vendor_lechao_usbd_record_last_event_locked(rate_dev,
            VENDOR_LECHAO_USBD_EVENT_TIMEOUT,
            0, trace.status, (u8)trace.dir);
        vendor_lechao_usbd_event_push(rate_dev,
            VENDOR_LECHAO_USBD_EVENT_TIMEOUT,
            0, trace.status, (u8)trace.dir);
        break;

    case USB_STOR_NOTIFIER_DATA_CORRUPT:
        rate_dev->stats.corrupt_count++;
        trace.dir = srb ? (int)vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction)
                        : (nd ? (int)nd->data_direction : 0);
        trace.status = nd ? nd->status : 0;
        vendor_lechao_usbd_record_last_event_locked(rate_dev,
            VENDOR_LECHAO_USBD_EVENT_DATA_CORRUPT,
            0, trace.status, (u8)trace.dir);
        vendor_lechao_usbd_event_push(rate_dev,
            VENDOR_LECHAO_USBD_EVENT_DATA_CORRUPT,
            0, trace.status, (u8)trace.dir);
        break;

    case USB_STOR_NOTIFIER_TRANSPORT_END:
        trace.dir = srb ? vendor_lechao_usbd_dir_to_u8(srb->sc_data_direction) : 0;
        if (!rate_dev->transport_active) {
            rate_dev->last_transport_error = false;
            break;
        }

        /* 优先消费 usb-storage 核心侧已测得的传输耗时，仅在缺失时 fallback */
        elapsed_ns = (nd && nd->duration_ns) ? nd->duration_ns
                     : ktime_to_ns(ktime_sub(ktime_get(),
                                             rate_dev->transport_start_time));
        trace.was_error = rate_dev->last_transport_error ? 1 : 0;

        if (srb && !rate_dev->last_transport_error) {
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

                if (prev_rate > 0 && rate_dev->stats.current_rate < prev_rate)
                    degraded = true;
                if (prev_latency > 0 && elapsed_ns > prev_latency)
                    degraded = true;
            }

            /* 滑动窗口判定结果合并入 degraded，统计/事件/trace 统一在下方单点执行 */
            degraded = degraded ||
                       vendor_lechao_usbd_update_degrade_context_locked(rate_dev, bytes);
            trace.ev_bytes = bytes;
            trace.ev_elapsed_ns = elapsed_ns;
        }

        /* degrade 统计/事件/trace 的唯一出口，避免双计数 */
        if (degraded) {
            rate_dev->stats.degrade_count++;
            vendor_lechao_usbd_record_last_event_locked(rate_dev,
                VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED,
                0, 0, 0);
            vendor_lechao_usbd_event_push(rate_dev,
                VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED,
                0, 0, 0);
        }

        rate_dev->last_transport_latency_ns = elapsed_ns;
        rate_dev->stats.last_transport_latency_ns = elapsed_ns;

        rate_dev->stats.last_update = ktime_get_ns();
        rate_dev->last_transport_error = false;
        rate_dev->transport_active = false;
        rate_dev->transport_start_time = ktime_set(0, 0);
        break;

    case USB_STOR_NOTIFIER_RESET:
        rate_dev->stats.reset_count++;
        vendor_lechao_usbd_record_last_event_locked(rate_dev,
            VENDOR_LECHAO_USBD_EVENT_RESET, 0, 0, 0);
        vendor_lechao_usbd_event_push(rate_dev,
            VENDOR_LECHAO_USBD_EVENT_RESET, 0, 0, 0);
        break;

    default:
        break;
    }

    spin_unlock_irqrestore(&rate_dev->lock, flags);

    switch (event) {
    case USB_STOR_NOTIFIER_TRANSPORT_START:
        lcview_trace_transport_start(rate_dev, srb, trace.device_index);
        break;
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

int vendor_lechao_usbd_stats_init(void)
{
    return 0;
}

void vendor_lechao_usbd_stats_exit(void)
{
}
