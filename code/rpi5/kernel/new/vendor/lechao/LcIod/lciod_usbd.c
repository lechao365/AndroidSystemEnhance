/*
 * ============================================================
 * vendor_lechao_usbd.c — USB 存储速率监控主模块
 *
 * 所属模块：Lechao USB 存储速率监控驱动 (VENDOR_LECHAO_USBD)
 *
 * 设计目的：
 *   本文件是驱动的主入口，负责以下核心功能：
 *
 *   1. 字符设备层
 *      - 注册一组 /dev/vendor_lechao_usbdN 字符设备（最多 16 个）
 *      - 实现 open/read/poll/release/unlocked_ioctl
 *      - read() 提供异步事件推送通道（环形缓冲区）
 *      - ioctl() 提供 GET_STATS/GET_CONFIG/SET_CONFIG 接口
 *      - poll() 支持 select/epoll 多路复用
 *
 *   2. 设备生命周期管理
 *      - 通过 usb_stor_register_vendor_notifier() 注册厂商通知链
 *      - 回调 vendor_lechao_usbd_vendor_notifier 处理 PROBE/DISCONNECT
 *      - 每个 usb-storage us_data 实例对应一个 device 结构体
 *      - 使用 kref 引用计数确保 fd 持有期间设备安全
 *
 *   3. 热插拔支持
 *      - 模块初始化时扫描所有已连接的 usb-storage 设备
 *      - 后续插入/移除通过 vendor notifier 动态处理
 *
 *   4. LcView 结构化打点
 *      - USB 设备 PROBE/DISCONNECT 时上送结构化事件日志
 *
 * 线程安全设计：
 *   - vendor_lechao_usbd_mutex — 保护全局设备链表（进程上下文）
 *   - per-device lock          — 保护 stats 和状态位（自旋锁，notifier 上下文）
 *   - per-device event_lock    — 保护事件环形缓冲区（自旋锁）
 *   - RCU/atomic 操作          — removing/event_shutdown 使用 READ_ONCE/WRITE_ONCE
 * ============================================================
 */

#include "lciod_usbd.h"
#include "lciod_read_logic.h"
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/usb.h>
#include <linux/poll.h>
#include <scsi/scsi_host.h>
#include "lcview_events.h"
#include "lcview_internal.h"
#include "kernel_lechao_log.h"

#define PREFIX KERNEL_USB_TAG ": "

int usbd_debug = 0;
module_param_named(debug, usbd_debug, int, 0644);
MODULE_PARM_DESC(debug, "Enable verbose diagnostic logging (0=off, 1=on)");

#define LC_DBG(fmt, ...) do { if (usbd_debug) pr_info(PREFIX "[D] " fmt, ##__VA_ARGS__); } while (0)

/*
 * LcView 结构化打点 — USB 设备 PROBE 事件
 *
 * 当一个新的 USB 存储设备被探测到时调用，生成一条包含以下
 * 字段的结构化日志事件：
 *   - device_index：次设备号（在内核中的唯一索引）
 *   - vid：USB Vendor ID（如 0x0781 代表 SanDisk）
 *   - pid：USB Product ID
 *   - vendor：制造商字符串（从 USB 描述符读取）
 *   - product：产品名
 *
 * 为什么需要这个事件：
 *   用户态监控程序可以通过 LcView 时间线精确知道每个 USB 设备
 *   何时插入、是什么设备，从而将后续的传输事件关联到具体设备。
 */
static void lcview_trace_probe(int device_index, u16 vid, u16 pid,
                               const char *vendor, const char *product)
{
    struct lcview_builder *b;
    int rc;

    b = lcview_builder_start(LCVIEW_EVENT_USB_PROBE, LCVIEW_LEVEL_INFO);
    if (!b)
        return;
    rc = lcview_builder_add_int(b, (int64_t)device_index);
    rc |= lcview_builder_add_int(b, (int64_t)vid);
    rc |= lcview_builder_add_int(b, (int64_t)pid);
    rc |= lcview_builder_add_str(b, vendor);
    rc |= lcview_builder_add_str(b, product);
    /* KRN-016：add 失败聚合处理——任一字段缺失都会使用户态
     * 按 schema 解析错位，整体丢弃事件而非发射残缺记录。*/
    if (rc || lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}

/*
 * LcView 结构化打点 — USB 设备 DISCONNECT 事件
 *
 * 当 USB 存储设备断开时调用，记录哪个设备被拔出。
 * 与 lcview_trace_probe 配对使用，形成完整的设备生命周期记录。
 */
static void lcview_trace_disconnect(int device_index)
{
    struct lcview_builder *b;
    int rc;

    b = lcview_builder_start(LCVIEW_EVENT_USB_DISCONNECT, LCVIEW_LEVEL_INFO);
    if (!b)
        return;
    rc = lcview_builder_add_int(b, (int64_t)device_index);
    /* KRN-016：add 失败聚合处理——任一字段缺失都会使用户态
     * 按 schema 解析错位，整体丢弃事件而非发射残缺记录。*/
    if (rc || lcview_builder_commit(b, &lcview_ring))
        lcview_builder_cancel(b);
}

/*
 * 驱动名称 — 用于字符设备名、sysfs class 名
 * 最终设备节点路径：/dev/vendor_lechao_usbd0 ~ /dev/vendor_lechao_usbd15
 */
#define VENDOR_LECHAO_USBD_NAME "vendor_lechao_usbd"

/*
 * vendor_lechao_usbd_devnode — 自定义设备节点权限
 *
 * R-08 方向 1：收紧 /dev/vendor_lechao_usbdN 节点为 0600（仅 owner system
 * 可读写）。原 0666 所有用户可读写——devnode 暴露 ioctl GET_STATS 等监控
 * 接口与底层传输状态，任意 app 可读属权限过宽（信息泄露面）。收紧后：
 *   - DAC 层：仅 system:system（HAL 运行 uid）可读写，普通 app/shell 拒绝
 *   - SELinux 层：devnode 标 lechao_lciod_hal_device，te 只放行 lechao_lciod_hal
 *     域 + shell 域（监控取数特批，见 lechao_lciod_hal.te / lechao_lciod.te）
 * 返回 NULL 表示使用内核默认的 devtmpfs 节点名。
 */
static char *vendor_lechao_usbd_devnode(const struct device *dev, umode_t *mode)
{
    if (mode)
        *mode = 0600;
    return NULL;
}

static int vendor_lechao_usbd_usb_dev_scan(struct usb_device *udev, void *data);

/*
 * 最大设备数 — 限制为 16
 * 对应次设备号范围：0~15。选择 16 是因为 Raspberry Pi 的
 * USB 控制器端口有限，同时在驱动的 IDA 分配器上设定了
 * 明确的上限，防止资源耗尽。
 */
#define VENDOR_LECHAO_USBD_MAX_DEVICES 16

/*
 * 全局状态
 *
 * vendor_lechao_usbd_major   — 字符设备主设备号（动态分配）
 * vendor_lechao_usbd_class   — sysfs class，用于设备自动节点创建
 * vendor_lechao_usbd_ida     — 次设备号 ID 分配器
 * vendor_lechao_usbd_devices — 所有活跃设备的链表头
 * vendor_lechao_usbd_mutex   — 保护设备链表的互斥锁
 *
 * 为什么用 IDA 而非简单计数器：IDA 可以回收释放的次设备号，
 * 避免频繁插拔后次设备号无限增长。
 */
static int vendor_lechao_usbd_major;
static struct class *vendor_lechao_usbd_class;
static DEFINE_IDA(vendor_lechao_usbd_ida);
static LIST_HEAD(vendor_lechao_usbd_devices);
static DEFINE_MUTEX(vendor_lechao_usbd_mutex);

/*
 * vendor_lechao_usbd_open — 字符设备 open 回调
 *
 * 从 inode 中恢复指向 vendor_lechao_usbd_device 的指针
 * （通过 container_of 从内嵌的 cdev 成员推算）。
 * 在增加 kref 引用计数之前检查设备是否正在被移除，
 * 防止在设备断开和 open 的竞态条件下访问已释放的内存。
 *
 * 为什么用 kref_get_unless_zero 而非 kref_get：
 * 当 kref 降为 0 时，设备正在被释放，此时不能再增加引用。
 *
 * 【事件消费语义（LCD-008）】本驱动未限制单打开：event_tail 是
 * per-device 共享状态，多读者并发 open 时构成共享消费队列——
 * 每条事件被随机分配给其中一个读者（不复制、不回放）。当前
 * 实际单读者由 sepolicy 保证（lechao_lciod_hal_device 的 chr_file
 * 访问仅授予 HAL domain）。若未来出现多读者需求，须改为 per-fd
 * 消费位或事件复制，不能依赖现有语义。
 */
static int vendor_lechao_usbd_open(struct inode *inode, struct file *file)
{
    struct vendor_lechao_usbd_device *rate_dev;

    rate_dev = container_of(inode->i_cdev, struct vendor_lechao_usbd_device, cdev);

    if (READ_ONCE(rate_dev->removing)) {
        pr_warn(PREFIX "open: device removing\n");
        return -ENODEV;
    }

    if (!kref_get_unless_zero(&rate_dev->kref)) {
        pr_warn(PREFIX "open: device gone (kref)\n");
        return -ENODEV;
    }

    file->private_data = rate_dev;
    return 0;
}

/*
 * vendor_lechao_usbd_release — 字符设备 release 回调
 *
 * 释放 kref 引用。当最后一个 fd 关闭时，kref 降为 0，
 * 触发 vendor_lechao_usbd_device_release 回调释放内存。
 */
static int vendor_lechao_usbd_release(struct inode *inode, struct file *file)
{
    struct vendor_lechao_usbd_device *rate_dev = file->private_data;
    kref_put(&rate_dev->kref, vendor_lechao_usbd_device_release);
    return 0;
}

/*
 * vendor_lechao_usbd_read — 字符设备 read 回调
 *
 * 从设备的环形事件缓冲区中读取一条事件记录（固定大小）。
 * 如果没有事件可读：
 *   - 阻塞模式（默认）：进程进入可中断睡眠等待
 *   - 非阻塞模式（O_NONBLOCK）：立即返回 -EAGAIN
 *
 * 为什么 read() 返回固定大小的 struct vendor_lechao_usbd_event：
 * 环形缓冲区中的每个条目都是定长的，用户态必须提供足够的
 * 缓冲区。这种方式简化了内核态的实现，避免了可变长度编码。
 *
 * 为什么有 EPOLLHUP / event_shutdown 机制：
 * 当 USB 设备断开或模块卸载时，等待 read() 的进程应被唤醒
 * 并得到 EOF（返回 0），而不是永远阻塞。event_shutdown 标志
 * 和 EPOLLHUP 共同实现这个"优雅关闭"协议。
 *
 * 断连后 ring 中可能仍有未消费的尾部事件；read() 允许先 drain
 * 这些事件（返回事件），ring 清空后再返回 0 (EOF)。
 */
static ssize_t vendor_lechao_usbd_read(struct file *file, char __user *buf,
                                       size_t count, loff_t *ppos)
{
    struct vendor_lechao_usbd_device *dev = file->private_data;
    struct vendor_lechao_usbd_event ev;
    uint32_t consumed_pos;  /* 本次读取的事件槽位（读取前 tail），供回滚守卫 */
    unsigned long flags;
    int decision;

    LC_DBG("read: count=%zu\n", count);

    if (count < sizeof(ev)) {
        pr_warn(PREFIX "read: buffer too small (%zu < %zu)\n", count, sizeof(ev));
        return -EINVAL;
    }

    /*
     * R-05 方向 2：read 路径统一 poll 驱动 + 非阻塞 read 循环。
     *
     * 原实现把"O_NONBLOCK 快速路径采样"与"阻塞 for 循环消费"分成两段：
     *   - 快速路径锁内采样 empty/shutdown 后解锁，decision==-1（非空）时
     *     落入 for 循环；
     *   - for 循环第一件事 wait_event_interruptible 等新事件，而 ring 里
     *     的数据可能已在"采样 → wait"窗口内被并发读者消费（poll 报过
     *     POLLIN 就绪、HAL 排空循环连续 read），wait 条件不满足即挂起等
     *     新事件——但上层 poll 已判定就绪才来 read，read 却阻塞 → HAL
     *     排空循环挂死（poll(0) 认为还有数据，read 卡住等永不来的新事件）。
     *
     * 修复：read 统一为非阻塞语义（不论 f_flags）——上层 poll() 负责等
     * 就绪，read 只做"锁内取一条非空事件返回，空环按 decision 分流 EOF/
     * EAGAIN"。消除采样与消费两段式竞态：单锁临界区内完成 empty 采样 +
     * 事件消费，poll 就绪判定与 read 消费原子一致，HAL 排空循环读不到
     * 就绪事件即 -EAGAIN 退出、由 poll 重新等，不会挂死。
     *
     * 语义变更：阻塞 fd 的 read 不再阻塞（原 wait_event 行为移除）。对
     * 本驱动唯一消费者 HAL（read_event 恒先 poll 后 read 排空）无影响；
     * 其它阻塞 read 调用方需自行 poll/select 等就绪——与字符设备
     * "poll 就绪后 read 不阻塞"的惯用法一致。
     */
    spin_lock_irqsave(&dev->event_lock, flags);
    if (READ_ONCE(dev->event_head) != READ_ONCE(dev->event_tail))
    {
        ev = dev->event_buf[dev->event_tail];
        consumed_pos = dev->event_tail;
        dev->event_tail = (dev->event_tail + 1) % VENDOR_LECHAO_USBD_EVENT_BUF_SIZE;
        spin_unlock_irqrestore(&dev->event_lock, flags);
    }
    else
    {
        bool empty = true;
        bool shutdown = READ_ONCE(dev->event_shutdown);
        spin_unlock_irqrestore(&dev->event_lock, flags);
        /*
         * KRN-004：判定逻辑抽至 lciod_read_logic.c（host 单测覆盖
         * 四象限语义）。空环时分流：1→-EAGAIN（空环重试）、0→0（EOF）。
         * 非空分支不会到此处（上方已消费）。
         */
        decision = lciod_nonblock_read_decision(empty, shutdown);
        if (decision == 1)
            return -EAGAIN;
        return 0;
    }

    if (copy_to_user(buf, &ev, sizeof(ev))) {
        /*
         * KRN-009：copy_to_user 失败时回滚 event_tail，事件留在环中
         * 供下次重试（原实现事件已消费但用户未收到——静默丢失）。
         * 回滚必须带守卫（lciod_event_tail_rollback_ok）：期间写者驱逐
         * （head 追上 tail 推进 tail，见 lciod_usbd-stats.c event_push）
         * 会覆盖该槽位，无守卫回滚得 tail==head 判空致事件清零或重复
         * 消费——仅当 tail 未被驱逐推进时才回滚（与 lcview_ring.c
         * KRN-003 读指针回滚守卫同构）。判定逻辑抽至 lciod_read_logic.c。
         */
        spin_lock_irqsave(&dev->event_lock, flags);
        if (lciod_event_tail_rollback_ok(dev->event_tail, consumed_pos,
                                         VENDOR_LECHAO_USBD_EVENT_BUF_SIZE))
            dev->event_tail = consumed_pos;
        spin_unlock_irqrestore(&dev->event_lock, flags);
        pr_err(PREFIX "read: copy_to_user failed\n");
        return -EFAULT;
    }
    return sizeof(ev);
}

/*
 * vendor_lechao_usbd_poll — 字符设备 poll/select 回调
 *
 * 支持 select() 和 epoll 多路复用机制。用户态监控程序
 * 可以在一个线程中同时 poll 多个 /dev/vendor_lechao_usbdN 设备。
 *
 * 返回的掩码语义：
 *   EPOLLIN | EPOLLRDNORM — 有事件可读
 *   EPOLLHUP — 设备已断开或模块卸载（ring 排空后 read 返回 EOF）
 *
 * 为什么 ring 非空与 shutdown 可叠加（而非 else if）：
 *   断连后 ring 中可能仍有未消费的尾部事件。若 shutdown 时
 *   直接屏蔽 EPOLLIN，用户态只能看到 HUP 而不会继续 drain，
 *   尾部事件会丢失。两者叠加后，用户态会先读到全部尾部事件，
 *   ring 清空后才表现为纯 HUP。read() 语义与此一致。
 *
 * 为什么不使用 EPOLLERR：断开是预期行为，不是错误。
 */
static __poll_t vendor_lechao_usbd_poll(struct file *file, poll_table *wait)
{
    struct vendor_lechao_usbd_device *dev = file->private_data;
    __poll_t mask = 0;

    poll_wait(file, &dev->event_wq, wait);

    if (READ_ONCE(dev->event_head) != READ_ONCE(dev->event_tail))
        mask |= EPOLLIN | EPOLLRDNORM;
    if (READ_ONCE(dev->event_shutdown))
        mask |= EPOLLHUP;

    return mask;
}

/*
 * vendor_lechao_usbd_apply_config_locked — 应用运行时配置
 * @rate_dev: 目标设备实例
 * @cfg:      用户态传入的新配置
 *
 * 将用户态通过 IOC_SET_CONFIG 传入的配置原子应用到设备。
 * 同时同步 config 到 stats.enabled 和 stats.flags，确保
 * GET_STATS 返回的数据反映最新配置状态。
 *
 * 调用上下文：必须持有 rate_dev->lock 自旋锁（irqsave 版本）。
 * !!cfg->enabled 使用双重否定将任意非零值规范化为 0/1。
 */
static void vendor_lechao_usbd_apply_config_locked(
    struct vendor_lechao_usbd_device *rate_dev,
    const struct vendor_lechao_usbd_config *cfg)
{
    rate_dev->config.enabled = !!cfg->enabled;
    memset(rate_dev->config.reserved, 0, sizeof(rate_dev->config.reserved));
    rate_dev->config.flags = cfg->flags;
    /*
     * R-06 方向 1：enabled 写侧 WRITE_ONCE 与 handle_event 锁外 READ_ONCE 配对
     * （无锁读侧依赖 disable 语义的可见性，持锁写亦须显式内存序标记）。
     */
    WRITE_ONCE(rate_dev->enabled, !!rate_dev->config.enabled);
    rate_dev->stats.enabled = rate_dev->config.enabled;
    rate_dev->stats.flags = rate_dev->config.flags;

    /*
     * R-05 方向 3：disable 路径清理 transport_active（消 notifier 早退后
     * END 残留）。
     *
     * 背景：transport_active 在 TRANSPORT_START 置位（lciod_usbd-stats.c
     * handle_event）、TRANSPORT_END 消费后清零。当配置 enabled=false 时，
     * handle_event 在锁外早退（`if (!rate_dev->enabled) return NOTIFY_DONE;`），
     * 中间的传输不会收到 END —— transport_active 残留 true。
     *
     * 后果：disable 期间传输中断 → 重新 enable 后，若无新 START 先到，
     * 残留的 transport_active 会把不配对的 END（或下一轮首个 END）误当
     * 正常传输处理：错误累计延迟/字节、发射无 START 配对的 END trace，
     * 污染统计与事件流（check_lcview_events 双向契约认为 END 必有 START）。
     *
     * 修复：enabled 由 1→0（disable）时重置传输状态机（transport_active/
     * 起始时间/错误标志），与 vendor_lechao_usbd_do_reset 的 transport 段
     * 一致——disable 语义即"停止追踪传输"，残留状态须随 disable 清空。
     * 保持持锁（本函数调用方已持 rate_dev->lock）与 do_reset 同锁域。
     */
    if (!rate_dev->config.enabled)
    {
        rate_dev->transport_active = false;
        rate_dev->transport_start_time = ktime_set(0, 0);
        rate_dev->last_transport_error = false;
        rate_dev->last_transport_latency_ns = 0;
        rate_dev->stats.last_transport_latency_ns = 0;
    }
}

/*
 * vendor_lechao_usbd_fill_stats_locked — 填充统计快照用于返回用户态
 * @rate_dev: 目标设备实例
 * @stats:    输出缓冲区（栈上临时变量，随后 copy_to_user）
 *
 * 从设备结构体拷贝 stats，并补充 config 中最新状态。
 * last_transport_latency_ns 单独从 rate_dev 取值而非 stats 中的副本，
 * 因为 stats 中的值可能在 TRANSPORT_END 时未被更新（如传输出错时）。
 *
 * 调用上下文：必须持有 rate_dev->lock 自旋锁（irqsave 版本）。
 */
static void vendor_lechao_usbd_fill_stats_locked(
    struct vendor_lechao_usbd_device *rate_dev,
    struct vendor_lechao_usbd_stats *stats)
{
    memcpy(stats, &rate_dev->stats, sizeof(*stats));
    /* R-06 方向 3：event_drop_count 从 atomic 计数读取（原子读，与 event_lock
     * 域自增跨锁域），覆盖 memcpy 带入的陈旧 ABI 值。 */
    stats->event_drop_count = atomic64_read(&rate_dev->event_drop_cnt);
    stats->last_transport_latency_ns = rate_dev->last_transport_latency_ns;
    stats->enabled = rate_dev->config.enabled;
    stats->flags = rate_dev->config.flags;

    if (!rate_dev->last_event.valid) {
        stats->last_event_ts_ns = 0;
        stats->last_event_type = VENDOR_LECHAO_USBD_EVENT_NONE;
    }
}

/*
 * vendor_lechao_usbd_reset_state_locked — 重置设备统计状态
 * @rate_dev: 目标设备实例
 *
 * IOC_RESET_STATE ioctl 的内部实现。委托给 vendor_lechao_usbd_do_reset()
 * 执行实际清零操作。
 *
 * 调用上下文：必须持有 rate_dev->lock 自旋锁（irqsave 版本）。
 * 为什么加一层包装而非直接调用 do_reset：保持 ioctl 分发层与
 * stats 引擎的解耦，未来可在 reset 前后添加额外逻辑（如日志）。
 */
static void vendor_lechao_usbd_reset_state_locked(
    struct vendor_lechao_usbd_device *rate_dev)
{
    vendor_lechao_usbd_do_reset(rate_dev);
}

/*
 * vendor_lechao_usbd_ioctl — 字符设备 unlocked_ioctl 回调
 */
static long vendor_lechao_usbd_ioctl(struct file *file, unsigned int cmd,
                             unsigned long arg)
{
    struct vendor_lechao_usbd_device *rate_dev = file->private_data;
    void __user *argp = (void __user *)arg;
    unsigned long flags;

    if (READ_ONCE(rate_dev->removing)) {
        pr_warn(PREFIX "ioctl: device removing\n");
        return -ENODEV;
    }

    switch (cmd) {
    case VENDOR_LECHAO_USBD_IOC_GET_STATS: {
        struct vendor_lechao_usbd_stats stats;

        spin_lock_irqsave(&rate_dev->lock, flags);
        vendor_lechao_usbd_fill_stats_locked(rate_dev, &stats);
        spin_unlock_irqrestore(&rate_dev->lock, flags);

        if (copy_to_user(argp, &stats, sizeof(stats))) {
            pr_err(PREFIX "ioctl cmd 0x%x: copy failed\n", cmd);
            return -EFAULT;
        }
        return 0;
    }
    case VENDOR_LECHAO_USBD_IOC_RESET_STATE:
        spin_lock_irqsave(&rate_dev->lock, flags);
        vendor_lechao_usbd_reset_state_locked(rate_dev);
        spin_unlock_irqrestore(&rate_dev->lock, flags);
        return 0;
    case VENDOR_LECHAO_USBD_IOC_GET_CONFIG: {
        struct vendor_lechao_usbd_config cfg;

        spin_lock_irqsave(&rate_dev->lock, flags);
        cfg = rate_dev->config;
        spin_unlock_irqrestore(&rate_dev->lock, flags);

        if (copy_to_user(argp, &cfg, sizeof(cfg))) {
            pr_err(PREFIX "ioctl cmd 0x%x: copy failed\n", cmd);
            return -EFAULT;
        }
        return 0;
    }
    case VENDOR_LECHAO_USBD_IOC_SET_CONFIG: {
        struct vendor_lechao_usbd_config cfg;

        if (copy_from_user(&cfg, argp, sizeof(cfg))) {
            pr_err(PREFIX "ioctl cmd 0x%x: copy failed\n", cmd);
            return -EFAULT;
        }

        spin_lock_irqsave(&rate_dev->lock, flags);
        vendor_lechao_usbd_apply_config_locked(rate_dev, &cfg);
        spin_unlock_irqrestore(&rate_dev->lock, flags);
        return 0;
    }
    default:
        pr_warn(PREFIX "unknown ioctl cmd=0x%x\n", cmd);
        return -ENOTTY;
    }
}


/*
 * vendor_lechao_usbd_fops — 字符设备文件操作集
 *
 * 注册了 open/release/read/poll/unlocked_ioctl。
 */
static const struct file_operations vendor_lechao_usbd_fops = {
    .owner          = THIS_MODULE,
    .open           = vendor_lechao_usbd_open,
    .release        = vendor_lechao_usbd_release,
    .read           = vendor_lechao_usbd_read,
    .poll           = vendor_lechao_usbd_poll,
    .unlocked_ioctl = vendor_lechao_usbd_ioctl,
};

/*
 * vendor_lechao_usbd_device_release — kref 引用计数归零回调
 *
 * 当最后一个字符设备 fd 关闭且 USB 设备已断开时调用。
 * 回收次设备号到 IDA 池，并释放设备结构体内存。
 *
 * 为什么不在 device_remove 中直接 kfree：
 * 如果有用户态进程持有通过 open() 获取的 fd，需要等它 close
 * 后才能释放。kref 机制完美解决这个"谁最后谁清理"的问题。
 */
void vendor_lechao_usbd_device_release(struct kref *kref)
{
    struct vendor_lechao_usbd_device *rate_dev =
        container_of(kref, struct vendor_lechao_usbd_device, kref);
    ida_free(&vendor_lechao_usbd_ida, rate_dev->minor);
    kfree(rate_dev);
}

/*
 * vendor_lechao_usbd_device_alloc — 分配和初始化 per-device 结构体
 *
 * 步骤：
 *   1. kzalloc 分配零初始化结构体
 *   2. ida_alloc_max 分配次设备号（0~15）
 *   3. 初始化字段：us_data 指针、notifier 回调、自旋锁、wq、kref
 *   4. 读取 USB 设备描述符（VID/PID/制造商/产品名），存入 stats
 *
 * 为什么读取 VID/PID/vendor/product：
 *   这些信息在后续的 IOCTL GET_STATS 中返回给用户态，
 * 方便监控程序识别设备身份。在分配时就读取，而不是在
 * ioctl 时实时读取，因为 ioctl 上下文中 usb_string() 可能
 * 因设备睡眠状态不可用。
 */
struct vendor_lechao_usbd_device *vendor_lechao_usbd_device_alloc(struct us_data *us)
{
    struct vendor_lechao_usbd_device *rate_dev;
    int minor;

    rate_dev = kzalloc(sizeof(*rate_dev), GFP_KERNEL);
    if (!rate_dev)
        return ERR_PTR(-ENOMEM);

    minor = ida_alloc_max(&vendor_lechao_usbd_ida, VENDOR_LECHAO_USBD_MAX_DEVICES - 1, 
                          GFP_KERNEL);
    if (minor < 0) {
        kfree(rate_dev);
        return ERR_PTR(minor);
    }

    rate_dev->minor = minor;
    rate_dev->us = us;
    rate_dev->nb.notifier_call = vendor_lechao_usbd_handle_event;
    rate_dev->removing = false;
    rate_dev->enabled = true;
    rate_dev->config.enabled = 1;
    memset(rate_dev->config.reserved, 0, sizeof(rate_dev->config.reserved));
    rate_dev->config.flags = 0;
    spin_lock_init(&rate_dev->lock);
    spin_lock_init(&rate_dev->event_lock);
    init_waitqueue_head(&rate_dev->event_wq);
    kref_init(&rate_dev->kref);
    rate_dev->stats.enabled = rate_dev->config.enabled;
    rate_dev->stats.flags = rate_dev->config.flags;
    rate_dev->stats.probe_count = 1;
    atomic64_set(&rate_dev->event_drop_cnt, 0);
    rate_dev->transport_start_time = ktime_set(0, 0);
    rate_dev->transport_active = false;
    rate_dev->last_degrade_window_start = ktime_set(0, 0);
    rate_dev->stats.last_event_type = VENDOR_LECHAO_USBD_EVENT_NONE;

    if (!us->pusb_dev) {
        ida_free(&vendor_lechao_usbd_ida, minor);
        kfree(rate_dev);
        return ERR_PTR(-ENODEV);
    }

    rate_dev->stats.vid = us->pusb_dev->descriptor.idVendor;
    rate_dev->stats.pid = us->pusb_dev->descriptor.idProduct;
    
    memset(rate_dev->stats.vendor, 0, sizeof(rate_dev->stats.vendor));
    memset(rate_dev->stats.product, 0, sizeof(rate_dev->stats.product));
    
    if (us->pusb_dev->descriptor.iManufacturer) {
        if (usb_string(us->pusb_dev, us->pusb_dev->descriptor.iManufacturer,
                       rate_dev->stats.vendor, sizeof(rate_dev->stats.vendor)) < 0)
            rate_dev->stats.vendor[0] = '\0';
    }
    if (us->pusb_dev->descriptor.iProduct) {
        if (usb_string(us->pusb_dev, us->pusb_dev->descriptor.iProduct,
                       rate_dev->stats.product, sizeof(rate_dev->stats.product)) < 0)
            rate_dev->stats.product[0] = '\0';
    }

    /*
     * R-06 方向 4：PROBE 入链前复查 us 存活。
     *
     * usb_string() 可睡眠，从 device_alloc 进入 usb_string 到返回这段窗口内
     * 若发生物理拔出，USB core 会先把 usb_device->state 置为
     * USB_STATE_NOTATTACHED（usb-storage 的 quiesce_and_remove_host 同样以此
     * 判断设备已消失），随后 usb_stor_disconnect 的 release_everything 才释放
     * us_data。若此时仍把 rate_dev 入链，add_to_list 之后对 us->notifier /
     * us->pusb_dev 的访问将落在已释放的 us_data 上（UAF 滞留僵尸设备）。
     * 因此 usb_string 完成后、返回前复查设备存活，发现已拔出则自释放并
     * 返回 -ENODEV，调用方不得入链。
     */
    if (us->pusb_dev->state == USB_STATE_NOTATTACHED)
    {
        pr_warn(PREFIX "device unplugged during probe alloc, aborting\n");
        ida_free(&vendor_lechao_usbd_ida, minor);
        kfree(rate_dev);
        return ERR_PTR(-ENODEV);
    }

    return rate_dev;
}

/*
 * vendor_lechao_usbd_device_add_to_list — 注册设备到全局列表
 *
 * 执行以下有序步骤（任何一步失败都回滚前序操作）：
 *   1. 注册 notifier 到 us_data 的原子通知链
 *   2. 初始化并注册字符设备（cdev_init + cdev_add）
 *   3. 创建 sysfs 设备节点（device_create）
 *   4. 加入全局设备链表
 *   5. 打印日志 + LcView 打点
 *
 * 为什么步骤顺序很重要：
 *   notifier 必须在字符设备可用之前注册，确保用户态在 open()
 *   之前不会遗漏任何传输事件。但 notifier 可能在注册后立即被
 *   调用（如果 usb-storage 正在传输中），因此需要保证设备结构
 *   体已经初始化完成。
 */
/*
 * R-06 方向 2：add_to_list 改返回错误码——notifier 注册 / cdev_add /
 * device_create 任一失败都上报调用方（0 成功 / 负 errno），失败路径内部
 * 回滚已做操作并 kref_put 释放 rate_dev（调用方不得再引用）。
 */
int vendor_lechao_usbd_device_add_to_list(struct vendor_lechao_usbd_device *rate_dev)
{
    int ret;

    ret = atomic_notifier_chain_register(&rate_dev->us->notifier, &rate_dev->nb);
    if (ret)
    {
        /*
         * 返回值检查：-EEXIST 表示 us_data 上已注册同名 notifier（重复 PROBE
         * 或 us_data 异常复用），本设备未注册成功，无资源可回滚，直接上报。
         */
        pr_err(PREFIX "notifier_chain_register failed: %d\n", ret);
        kref_put(&rate_dev->kref, vendor_lechao_usbd_device_release);
        return ret;
    }

    cdev_init(&rate_dev->cdev, &vendor_lechao_usbd_fops);
    rate_dev->cdev.owner = THIS_MODULE;
    ret = cdev_add(&rate_dev->cdev, MKDEV(vendor_lechao_usbd_major, rate_dev->minor), 1);
    if (ret) {
        pr_err(PREFIX "cdev_add failed: %d\n", ret);
        atomic_notifier_chain_unregister(&rate_dev->us->notifier, &rate_dev->nb);
        kref_put(&rate_dev->kref, vendor_lechao_usbd_device_release);
        return ret;
    }

    rate_dev->dev = device_create(vendor_lechao_usbd_class, NULL, 
                                   MKDEV(vendor_lechao_usbd_major, rate_dev->minor),
                                   rate_dev, VENDOR_LECHAO_USBD_NAME "%d", rate_dev->minor);
    if (IS_ERR(rate_dev->dev)) {
        ret = PTR_ERR(rate_dev->dev);
        pr_err(PREFIX "device_create failed: %d\n", ret);
        cdev_del(&rate_dev->cdev);
        atomic_notifier_chain_unregister(&rate_dev->us->notifier, &rate_dev->nb);
        kref_put(&rate_dev->kref, vendor_lechao_usbd_device_release);
        return ret;
    }

    list_add_tail(&rate_dev->list, &vendor_lechao_usbd_devices);

    pr_info(PREFIX "registered device %s (VID:%04x PID:%04x \"%s\" \"%s\")\n",
            dev_name(rate_dev->dev), rate_dev->stats.vid, rate_dev->stats.pid,
            rate_dev->stats.vendor, rate_dev->stats.product);

    /* LcView: trace USB device PROBE (vid/pid/vendor/product) */
    lcview_trace_probe(rate_dev->minor, rate_dev->stats.vid, rate_dev->stats.pid,
                       rate_dev->stats.vendor, rate_dev->stats.product);
    return 0;
}

/*
 * vendor_lechao_usbd_device_remove — 从系统移除设备（预留，当前未使用）
 *
 * __maybe_unused 标记表示该函数当前未被调用，因为模块的退出路径
 * 和 DISCONNECT 路径各自实现了独立的资源清理逻辑。如果将来需要
 * 独立的移除接口，可以直接使用此函数。
 *
 * 清理序列：
 *   1. 从全局链表删除（mutex 保护）
 *   2. 标记 removing（防止后续 open/new ioctl）
 *   3. 注销 notifier
 *   4. 销毁 sysfs 设备节点
 *   5. 删除字符设备
 *   6. 释放 kref（触发 device_release 如果引用已归零）
 */
static void __maybe_unused vendor_lechao_usbd_device_remove(struct vendor_lechao_usbd_device *rate_dev)
{
    mutex_lock(&vendor_lechao_usbd_mutex);
    list_del(&rate_dev->list);
    WRITE_ONCE(rate_dev->removing, true);
    mutex_unlock(&vendor_lechao_usbd_mutex);

    atomic_notifier_chain_unregister(&rate_dev->us->notifier, &rate_dev->nb);
    device_destroy(vendor_lechao_usbd_class, MKDEV(vendor_lechao_usbd_major, rate_dev->minor));
    cdev_del(&rate_dev->cdev);
    kref_put(&rate_dev->kref, vendor_lechao_usbd_device_release);
}

/*
 * vendor_lechao_usbd_vendor_notifier — 厂商通知链回调
 *
 * 由 usb-storage 在 USB 设备探测/断开时调用。
 * 这是一个进程上下文回调（非原子上下文），因此可以使用
 * mutex_lock 和 kzalloc(GFP_KERNEL)。
 *
 * 为什么需要双重检查（double-check）模式：
 *   第一次检查在 mutex 外执行（无锁遍历），快速路径优化。
 *   第二次检查在 mutex 内执行，避免分配和注册之间的竞态。
 *
 * 处理逻辑：
 *   USB_STOR_NOTIFIER_DEVICE_PROBE：
 *     1. 查重（防止 usb_dev_scan 和 notifier 同时调用）
 *     2. 分配并初始化设备结构体
 *     3. 注册 notifier + 字符设备 + sysfs 节点
 *     4. LcView 打点
 *
 *   USB_STOR_NOTIFIER_DEVICE_DISCONNECT：
 *     1. 从全局链表移除
 *     2. 标记 removing + event_shutdown
 *     3. 唤醒所有等待 read() 的进程
 *     4. 注销 notifier + 销毁设备 + kref_put
 *     5. LcView 打点
 */
static int vendor_lechao_usbd_vendor_notifier(struct notifier_block *nb,
                                     unsigned long action, void *data)
{
    struct us_data *us = data;

    pr_info(PREFIX "vendor notifier called, action=%lu\n", action);

    switch (action) {
    case USB_STOR_NOTIFIER_DEVICE_PROBE:
    {
        struct vendor_lechao_usbd_device *new_dev, *pos;
        bool found = false;

        mutex_lock(&vendor_lechao_usbd_mutex);
        list_for_each_entry(pos, &vendor_lechao_usbd_devices, list) {
            if (pos->us == us) {
                found = true;
                break;
            }
        }
        mutex_unlock(&vendor_lechao_usbd_mutex);

        if (found)
            break;

        /*
         * R-06 方向 4：alloc 与 add_to_list 全程持全局 mutex（alloc 内
         * usb_string 可睡眠，进程上下文 OK）。disconnect 的 DISCONNECT
         * notifier 同样经 blocking_notifier_call_chain 同步调用且在此
         * mutex 上等待——若 PROBE 持锁期间设备拔出，DISCONNECT 阻塞在
         * mutex 上，usb_stor_disconnect 主流程亦阻塞等待 notifier 返回，
         * us_data 在 PROBE 释放锁前不会被 release_everything 释放，故
         * 锁内访问 us 安全；device_alloc 内部已复查 us 存活（NOTATTACHED
         * 即 -ENODEV），复查失败自释放，不存在"已分配未入链"滞留设备，
         * DISCONNECT 侧链表状态始终一致。
         */
        mutex_lock(&vendor_lechao_usbd_mutex);
        found = false;
        list_for_each_entry(pos, &vendor_lechao_usbd_devices, list) {
            if (pos->us == us) {
                found = true;
                break;
            }
        }
        if (!found)
        {
            new_dev = vendor_lechao_usbd_device_alloc(us);
            if (IS_ERR(new_dev))
            {
                pr_warn(PREFIX "failed to alloc device: %ld\n", PTR_ERR(new_dev));
            }
            else
            {
                /* R-06 方向 2：add_to_list 失败已自释放 new_dev，仅记录告警 */
                if (vendor_lechao_usbd_device_add_to_list(new_dev))
                    pr_warn(PREFIX "failed to add device to list\n");
            }
        }
        mutex_unlock(&vendor_lechao_usbd_mutex);
        break;
    }

    case USB_STOR_NOTIFIER_DEVICE_DISCONNECT:
    {
        struct vendor_lechao_usbd_device *pos, *rate_dev = NULL;

        mutex_lock(&vendor_lechao_usbd_mutex);
        list_for_each_entry(pos, &vendor_lechao_usbd_devices, list) {
            if (pos->us == us) {
                unsigned long flags;

                spin_lock_irqsave(&pos->lock, flags);
                pos->stats.disconnect_count++;
                spin_unlock_irqrestore(&pos->lock, flags);
                wake_up_interruptible(&pos->event_wq);
                list_del(&pos->list);
                WRITE_ONCE(pos->removing, true);
                rate_dev = pos;
                break;
            }
        }
        mutex_unlock(&vendor_lechao_usbd_mutex);

        if (rate_dev) {
            WRITE_ONCE(rate_dev->event_shutdown, true);
            wake_up_interruptible(&rate_dev->event_wq);
            atomic_notifier_chain_unregister(&rate_dev->us->notifier,
                                              &rate_dev->nb);
            device_destroy(vendor_lechao_usbd_class, MKDEV(vendor_lechao_usbd_major,
                                                   rate_dev->minor));
            cdev_del(&rate_dev->cdev);
            /* LcView: trace USB device DISCONNECT */
            lcview_trace_disconnect(rate_dev->minor);
            kref_put(&rate_dev->kref, vendor_lechao_usbd_device_release);
        }
        break;
    }
    }

    return NOTIFY_DONE;
}

/*
 * vendor_lechao_usbd_vendor_nb — 厂商通知链 notifier_block
 *
 * 通过 usb_stor_register_vendor_notifier() 注册到 usb-storage 核心模块。
 * 当 usb-storage 探测到新的 USB 存储设备或设备断开时，回调
 * vendor_lechao_usbd_vendor_notifier 处理 PROBE/DISCONNECT 事件。
 */
static struct notifier_block vendor_lechao_usbd_vendor_nb = {
    .notifier_call = vendor_lechao_usbd_vendor_notifier,
};

/*
 * vendor_lechao_usbd_usb_dev_scan — 扫描已有 USB 存储设备
 *
 * 在模块初始化时通过 usb_for_each_dev() 遍历所有已连接的 USB 设备，
 * 为每个设备上的 usb-storage 接口创建对应的监控设备实例。
 *
 * 为什么既要有这个扫描函数，又要有 vendor notifier：
 *   vendor notifier 只处理热插拔事件（模块加载后的插入/移除）。
 *   模块加载时已经存在的 USB 存储设备不会触发 PROBE notifier，
 *   因此需要主动扫描。两者结合实现"全覆盖"：已有的 + 后续热插的。
 *
 * 为什么遍历 USB 接口而非直接匹配 us_data：
 *   usb_for_each_dev 遍历的是 struct usb_device，需要通过
 *   USB 接口的驱动名匹配 "usb-storage"。usb-storage 在 probe 时经
 *   usb_set_intfdata(intf, us) 把接口私有数据设为 us_data*，因此
 *   dev_get_drvdata(&intf->dev) 返回的就是 struct us_data*，
 *   无需再经 Scsi_Host 中转（方向 2：删除 scsi_host_get/host_to_us
 *   误用——drvdata 不是 Scsi_Host，强制转换既类型错又无谓持引用）。
 *
 * 为什么 check us->notifier.head：
 *   确保 us_data 的 notifier 链已经初始化，防止在关键路径上
 *   注册到未初始化的通知链。
 */
static int vendor_lechao_usbd_usb_dev_scan(struct usb_device *udev, void *data)
{
    struct usb_interface *intf;
    int i;

    if (!udev->actconfig)
        return 0;

    for (i = 0; i < udev->actconfig->desc.bNumInterfaces; i++)
    {
        struct us_data *us;
        struct vendor_lechao_usbd_device *pos;
        struct vendor_lechao_usbd_device *new_dev = NULL;
        bool found = false;

        intf = udev->actconfig->interface[i];
        if (!intf || !intf->dev.driver)
            continue;

        if (strcmp(intf->dev.driver->name, "usb-storage") != 0)
            continue;

        /* usb-storage 的 intfdata 即 us_data（probe 时 usb_set_intfdata），
         * 直接取用，删 scsi_host_get 与 host_to_us 的类型误用（方向 2）。 */
        us = dev_get_drvdata(&intf->dev);
        if (!us || !us->notifier.head)
            continue;

        mutex_lock(&vendor_lechao_usbd_mutex);
        list_for_each_entry(pos, &vendor_lechao_usbd_devices, list) {
            if (pos->us == us) {
                found = true;
                break;
            }
        }
        mutex_unlock(&vendor_lechao_usbd_mutex);

        if (found)
            continue;

        new_dev = vendor_lechao_usbd_device_alloc(us);
        if (IS_ERR(new_dev)) {
            pr_warn(PREFIX "failed to alloc device: %ld\n", PTR_ERR(new_dev));
            continue;
        }

        mutex_lock(&vendor_lechao_usbd_mutex);
        list_for_each_entry(pos, &vendor_lechao_usbd_devices, list) {
            if (pos->us == us) {
                found = true;
                break;
            }
        }
        if (!found) {
            /* R-06 方向 2：add_to_list 失败已自释放 new_dev，仅记录告警 */
            if (vendor_lechao_usbd_device_add_to_list(new_dev))
                pr_warn(PREFIX "failed to add device to list\n");
        } else {
            kref_put(&new_dev->kref, vendor_lechao_usbd_device_release);
        }
        mutex_unlock(&vendor_lechao_usbd_mutex);
    }
    return 0;
}

/*
 * vendor_lechao_usbd_monitor_init — 模块初始化入口
 *
 * 执行以下初始化序列：
 *   1. alloc_chrdev_region：动态分配 16 个字符设备号（主设备号自动分配）
 *   2. class_create：创建 sysfs class，配合 devtmpfs 自动创建设备节点
 *   3. 设置 devnode 权限为 0666
 *   4. usb_stor_register_vendor_notifier：注册厂商通知链
 *   5. usb_for_each_dev：扫描所有已存在的 USB 存储设备
 *
 * 为什么步骤 4 和 5 的先后顺序如此重要：
 *   先注册 notifier，再扫描已有设备。这样当扫描过程中有新的
 *   设备插入，notifier 可以捕获到。如果反过来，notifier 注册前
 *   插入的设备会丢失。扫描时的双重检查机制防止了重复注册。
 *
 * 为什么用 alloc_chrdev_region 而非指定主设备号：
 *   避免与内核中已注册的字符驱动冲突，动态分配更安全。
 */
static int __init vendor_lechao_usbd_monitor_init(void)
{
    dev_t devt;
    int ret;

    ret = alloc_chrdev_region(&devt, 0, VENDOR_LECHAO_USBD_MAX_DEVICES, VENDOR_LECHAO_USBD_NAME);
    if (ret < 0) {
        pr_err(PREFIX "failed to allocate chrdev region\n");
        return ret;
    }
    vendor_lechao_usbd_major = MAJOR(devt);

    vendor_lechao_usbd_class = class_create(VENDOR_LECHAO_USBD_NAME);
    if (IS_ERR(vendor_lechao_usbd_class)) {
        pr_err(PREFIX "class_create failed\n");
        unregister_chrdev_region(MKDEV(vendor_lechao_usbd_major, 0), 
                                  VENDOR_LECHAO_USBD_MAX_DEVICES);
        return PTR_ERR(vendor_lechao_usbd_class);
    }
    vendor_lechao_usbd_class->devnode = vendor_lechao_usbd_devnode;

    ret = usb_stor_register_vendor_notifier(&vendor_lechao_usbd_vendor_nb);
    if (ret) {
        class_destroy(vendor_lechao_usbd_class);
        unregister_chrdev_region(MKDEV(vendor_lechao_usbd_major, 0),
                                  VENDOR_LECHAO_USBD_MAX_DEVICES);
        pr_err(PREFIX "failed to register vendor notifier\n");
        return ret;
    }

    usb_for_each_dev(NULL, vendor_lechao_usbd_usb_dev_scan);

    pr_info(PREFIX "vendor patch v1.3 loaded, major=%d\n", vendor_lechao_usbd_major);
    return 0;
}

/*
 * vendor_lechao_usbd_monitor_exit — 模块卸载入口
 *
 * 卸载序列（顺序与 init 相反，保证安全回滚）：
 *   1. 注销厂商 notifier（防止新事件进入）
 *   2. 在 mutex 保护下，将所有设备从链表移出并标记 removing/shutdown
 *      - 同时唤醒等待 read() 的进程，因为它们会看到 event_shutdown
 *      并返回 0 (EOF)
 *   3. 逐一注销 notifier、销毁设备、删除字符设备、释放 kref
 *      - 注意：由于可能仍有进程持有 fd，kref_put 不一定会触发
 *        device_release（延迟到最后 fd close 时）
 *   4. 销毁 sysfs class
 *   5. 释放字符设备号区域
 *
 * 为什么需要把所有设备先摘出链表再逐个清理：
 *   防止在清理过程中有并发的 notifier 调用或 open() 访问
 *   正在被清理的设备。先统一标记 removing，确保无新访问进入。
 */
static void __exit vendor_lechao_usbd_monitor_exit(void)
{
    struct vendor_lechao_usbd_device *devs[VENDOR_LECHAO_USBD_MAX_DEVICES];
    int count = 0;
    struct vendor_lechao_usbd_device *pos;

    usb_stor_unregister_vendor_notifier(&vendor_lechao_usbd_vendor_nb);

    mutex_lock(&vendor_lechao_usbd_mutex);
    while (!list_empty(&vendor_lechao_usbd_devices)) {
        pos = list_first_entry(&vendor_lechao_usbd_devices, struct vendor_lechao_usbd_device, list);
        list_del(&pos->list);
        WRITE_ONCE(pos->removing, true);
        WRITE_ONCE(pos->event_shutdown, true);
        wake_up_interruptible(&pos->event_wq);
        devs[count++] = pos;
        if (WARN_ON(count >= VENDOR_LECHAO_USBD_MAX_DEVICES))
            break;
    }
    mutex_unlock(&vendor_lechao_usbd_mutex);

    while (count > 0) {
        struct vendor_lechao_usbd_device *rate_dev = devs[--count];
        atomic_notifier_chain_unregister(&rate_dev->us->notifier, &rate_dev->nb);
        device_destroy(vendor_lechao_usbd_class, MKDEV(vendor_lechao_usbd_major, rate_dev->minor));
        cdev_del(&rate_dev->cdev);
        kref_put(&rate_dev->kref, vendor_lechao_usbd_device_release);
    }

    class_destroy(vendor_lechao_usbd_class);
    unregister_chrdev_region(MKDEV(vendor_lechao_usbd_major, 0), 
                              VENDOR_LECHAO_USBD_MAX_DEVICES);

    pr_info(PREFIX "module unloaded\n");
}

module_init(vendor_lechao_usbd_monitor_init);
module_exit(vendor_lechao_usbd_monitor_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Lechao");
MODULE_DESCRIPTION("USB Storage Rate Monitor for Lechao Vendor");
MODULE_VERSION("1.0");
