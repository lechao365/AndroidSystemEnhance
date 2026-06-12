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

    b = lcview_builder_start(LCVIEW_EVENT_USB_PROBE, LCVIEW_LEVEL_INFO);
    if (!b)
        return;
    lcview_builder_add_int(b, (int64_t)device_index);
    lcview_builder_add_int(b, (int64_t)vid);
    lcview_builder_add_int(b, (int64_t)pid);
    lcview_builder_add_str(b, vendor);
    lcview_builder_add_str(b, product);
    lcview_builder_commit(b, &lcview_ring);
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

    b = lcview_builder_start(LCVIEW_EVENT_USB_DISCONNECT, LCVIEW_LEVEL_INFO);
    if (!b)
        return;
    lcview_builder_add_int(b, (int64_t)device_index);
    lcview_builder_commit(b, &lcview_ring);
}

/*
 * 驱动名称 — 用于字符设备名、sysfs class 名
 * 最终设备节点路径：/dev/vendor_lechao_usbd0 ~ /dev/vendor_lechao_usbd15
 */
#define VENDOR_LECHAO_USBD_NAME "vendor_lechao_usbd"

/*
 * vendor_lechao_usbd_devnode — 自定义设备节点权限
 *
 * 设置 /dev/vendor_lechao_usbdN 节点为 0666 权限（所有用户可读写）。
 * 为什么这样做：因为 USB 设备监控需求通常来自普通用户态进程
 * （非 root），0666 避免了 sudo 或 udev 规则的额外配置。
 * 返回 NULL 表示使用内核默认的 devtmpfs 节点名。
 */
static char *vendor_lechao_usbd_devnode(const struct device *dev, umode_t *mode)
{
    if (mode)
        *mode = 0666;
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
 * 如果没有事件可读，进程进入可中断睡眠等待。
 *
 * 为什么 read() 返回固定大小的 struct vendor_lechao_usbd_event：
 * 环形缓冲区中的每个条目都是定长的，用户态必须提供足够的
 * 缓冲区。这种方式简化了内核态的实现，避免了可变长度编码。
 *
 * 为什么有 EPOLLHUP / event_shutdown 机制：
 * 当 USB 设备断开或模块卸载时，等待 read() 的进程应被唤醒
 * 并得到 EOF（返回 0），而不是永远阻塞。event_shutdown 标志
 * 和 EPOLLHUP 共同实现这个"优雅关闭"协议。
 */
static ssize_t vendor_lechao_usbd_read(struct file *file, char __user *buf,
                                       size_t count, loff_t *ppos)
{
    struct vendor_lechao_usbd_device *dev = file->private_data;
    struct vendor_lechao_usbd_event ev;
    unsigned long flags;
    int ret;

    LC_DBG("read: count=%zu\n", count);

    if (count < sizeof(ev)) {
        pr_warn(PREFIX "read: buffer too small (%zu < %zu)\n", count, sizeof(ev));
        return -EINVAL;
    }

    for (;;) {
        ret = wait_event_interruptible(dev->event_wq,
                dev->event_head != dev->event_tail || READ_ONCE(dev->event_shutdown));
        if (ret)
            return ret;

        spin_lock_irqsave(&dev->event_lock, flags);
        if (dev->event_head != dev->event_tail) {
            ev = dev->event_buf[dev->event_tail];
            dev->event_tail = (dev->event_tail + 1) % VENDOR_LECHAO_USBD_EVENT_BUF_SIZE;
            spin_unlock_irqrestore(&dev->event_lock, flags);
            break;
        }
        if (READ_ONCE(dev->event_shutdown)) {
            spin_unlock_irqrestore(&dev->event_lock, flags);
            return 0;
        }
        spin_unlock_irqrestore(&dev->event_lock, flags);
    }

    if (copy_to_user(buf, &ev, sizeof(ev))) {
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
 *   EPOLLHUP — 设备已断开或模块卸载（读将返回 EOF）
 *
 * 为什么不使用 EPOLLERR：断开是预期行为，不是错误。
 */
static __poll_t vendor_lechao_usbd_poll(struct file *file, poll_table *wait)
{
    struct vendor_lechao_usbd_device *dev = file->private_data;
    __poll_t mask = 0;

    poll_wait(file, &dev->event_wq, wait);

    if (READ_ONCE(dev->event_shutdown))
        mask |= EPOLLHUP;
    else if (dev->event_head != dev->event_tail)
        mask |= EPOLLIN | EPOLLRDNORM;

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
    rate_dev->enabled = !!rate_dev->config.enabled;
    rate_dev->stats.enabled = rate_dev->config.enabled;
    rate_dev->stats.flags = rate_dev->config.flags;
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
    rate_dev->transport_start_time = ktime_set(0, 0);
    rate_dev->transport_active = false;
    rate_dev->last_degrade_window_start = ktime_set(0, 0);
    rate_dev->stats.last_event_type = VENDOR_LECHAO_USBD_EVENT_NONE;

    if (!us->pusb_dev) {
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
void vendor_lechao_usbd_device_add_to_list(struct vendor_lechao_usbd_device *rate_dev)
{
    int ret;

    atomic_notifier_chain_register(&rate_dev->us->notifier, &rate_dev->nb);

    cdev_init(&rate_dev->cdev, &vendor_lechao_usbd_fops);
    rate_dev->cdev.owner = THIS_MODULE;
    ret = cdev_add(&rate_dev->cdev, MKDEV(vendor_lechao_usbd_major, rate_dev->minor), 1);
    if (ret) {
        pr_err(PREFIX "cdev_add failed: %d\n", ret);
        atomic_notifier_chain_unregister(&rate_dev->us->notifier, &rate_dev->nb);
        kref_put(&rate_dev->kref, vendor_lechao_usbd_device_release);
        return;
    }

    rate_dev->dev = device_create(vendor_lechao_usbd_class, NULL, 
                                   MKDEV(vendor_lechao_usbd_major, rate_dev->minor),
                                   rate_dev, VENDOR_LECHAO_USBD_NAME "%d", rate_dev->minor);
    if (IS_ERR(rate_dev->dev)) {
        pr_err(PREFIX "device_create failed: %ld\n", PTR_ERR(rate_dev->dev));
        cdev_del(&rate_dev->cdev);
        atomic_notifier_chain_unregister(&rate_dev->us->notifier, &rate_dev->nb);
        kref_put(&rate_dev->kref, vendor_lechao_usbd_device_release);
        return;
    }

    list_add_tail(&rate_dev->list, &vendor_lechao_usbd_devices);

    pr_info(PREFIX "registered device %s (VID:%04x PID:%04x \"%s\" \"%s\")\n",
            dev_name(rate_dev->dev), rate_dev->stats.vid, rate_dev->stats.pid,
            rate_dev->stats.vendor, rate_dev->stats.product);

    /* LcView: trace USB device PROBE (vid/pid/vendor/product) */
    lcview_trace_probe(rate_dev->minor, rate_dev->stats.vid, rate_dev->stats.pid,
                       rate_dev->stats.vendor, rate_dev->stats.product);
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

        new_dev = vendor_lechao_usbd_device_alloc(us);
        if (IS_ERR(new_dev)) {
            pr_warn(PREFIX "failed to alloc device: %ld\n",
                    PTR_ERR(new_dev));
            break;
        }

        mutex_lock(&vendor_lechao_usbd_mutex);
        list_for_each_entry(pos, &vendor_lechao_usbd_devices, list) {
            if (pos->us == us) {
                found = true;
                break;
            }
        }
        if (!found)
            vendor_lechao_usbd_device_add_to_list(new_dev);
        else
            kref_put(&new_dev->kref, vendor_lechao_usbd_device_release);
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
 *   USB 接口的驱动名匹配 "usb-storage"，再通过 dev_get_drvdata
 *   获取 Scsi_Host，最后转为 us_data。这是一条间接但完整路径。
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

    for (i = 0; i < udev->actconfig->desc.bNumInterfaces; i++) {
        intf = udev->actconfig->interface[i];
        if (!intf || !intf->dev.driver)
            continue;

        if (strcmp(intf->dev.driver->name, "usb-storage") != 0)
            continue;

        void *drvdata = dev_get_drvdata(&intf->dev);
        struct Scsi_Host *shost = drvdata ? scsi_host_get(drvdata) : NULL;
        if (!shost)
            continue;

        struct us_data *us = host_to_us(shost);
        if (!us || !us->notifier.head) {
            scsi_host_put(shost);
            continue;
        }

        struct vendor_lechao_usbd_device *pos;
        struct vendor_lechao_usbd_device *new_dev = NULL;
        bool found = false;

        mutex_lock(&vendor_lechao_usbd_mutex);
        list_for_each_entry(pos, &vendor_lechao_usbd_devices, list) {
            if (pos->us == us) {
                found = true;
                break;
            }
        }
        mutex_unlock(&vendor_lechao_usbd_mutex);

        if (found) {
            scsi_host_put(shost);
            continue;
        }

        new_dev = vendor_lechao_usbd_device_alloc(us);
        if (IS_ERR(new_dev)) {
            pr_warn(PREFIX "failed to alloc device: %ld\n", PTR_ERR(new_dev));
            scsi_host_put(shost);
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
            vendor_lechao_usbd_device_add_to_list(new_dev);
        } else {
            kref_put(&new_dev->kref, vendor_lechao_usbd_device_release);
        }
        mutex_unlock(&vendor_lechao_usbd_mutex);

        scsi_host_put(shost);
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
