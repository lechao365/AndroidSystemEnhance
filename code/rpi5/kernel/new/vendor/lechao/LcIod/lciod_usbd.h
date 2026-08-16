/*
 * ============================================================
 * vendor_lechao_usbd.h — USB 存储速率监控驱动内部头文件
 * ============================================================
 *
 * 【文件用途】
 *   定义驱动内部使用的 per-device 结构体和跨编译单元共享的函数原型。
 *   本文件仅供内核驱动内部使用，不与用户态共享（用户态 ABI 见
 *   vendor_lechao_usbd-ioctl.h）。
 *
 * 【所属模块】
 *   Lechao vendor USB 存储监控子系统 (VENDOR_LECHAO_USBD)
 *
 * 【文件关系】
 *   - vendor_lechao_usbd.c：主模块，实现字符设备和设备生命周期管理
 *   - vendor_lechao_usbd-stats.c：统计引擎，实现 notifier 回调和事件处理
 *   - vendor_lechao_usbd-ioctl.h：用户态共享的 ABI 定义
 *   - usb.h：usb-storage 核心头文件，定义 notifier 事件枚举和 us_data
 * ============================================================
 */

#ifndef _VENDOR_LECHAO_USBD_INTERNAL_H
#define _VENDOR_LECHAO_USBD_INTERNAL_H

#include <linux/notifier.h>
#include <linux/cdev.h>
#include <linux/spinlock.h>
#include <linux/kref.h>
#include <linux/ktime.h>
#include <linux/wait.h>
#include "usb.h"
#include "lciod_usbd-ioctl.h"

struct us_data;
struct scsi_cmnd;

/*
 * struct vendor_lechao_usbd_device — 单个 USB 存储设备的监控实例
 *
 * 【生命周期】
 *   - 创建：usb_dev_scan 或 PROBE notifier → device_alloc → device_add_to_list
 *   - 销毁：DISCONNECT notifier 或模块卸载 → 标记 removing → kref_put
 *   - 引用计数：open() 时 kref_get，close() 时 kref_put；最后一个引用
 *     归零时触发 device_release 回收内存
 *
 * 【锁层次】（按获取顺序排列，防止死锁）
 *   1. vendor_lechao_usbd_mutex（全局互斥锁，保护设备链表）
 *   2. per-device lock（自旋锁，保护 stats 和状态位，notifier 上下文使用）
 *   3. per-device event_lock（自旋锁，保护事件环形缓冲区）
 *
 * 【与用户态 ABI 的关系】
 *   stats 字段通过 IOC_GET_STATS 拷贝到用户态的 vendor_lechao_usbd_stats。
 *   config 字段通过 IOC_GET_CONFIG/SET_CONFIG 与用户态双向同步。
 *   event_buf 中的事件通过 read() 系统调用推送到用户态。
 */
struct vendor_lechao_usbd_device {
    struct list_head list;           /* 全局设备链表节点（vendor_lechao_usbd_devices） */
    struct us_data *us;              /* 指向 usb-storage 核心的 us_data 实例，用于 notifier 注册/注销 */
    struct vendor_lechao_usbd_stats stats;       /* 传输统计快照（通过 lock 自旋锁保护） */
    struct vendor_lechao_usbd_config config;     /* 运行时配置（enabled/flags，通过 lock 自旋锁保护） */
    struct vendor_lechao_usbd_event last_event;  /* 最近一条异常事件记录（通过 lock 自旋锁保护） */
    struct notifier_block nb;        /* 注册到 us_data->notifier 的通知块，回调为 handle_event */
    struct cdev cdev;                /* 字符设备实例，关联 /dev/vendor_lechao_usbdN */
    struct device *dev;              /* sysfs 设备对象，用于 devtmpfs 自动创建节点 */
    struct kref kref;                /* 引用计数：open() 增加，close() 减少，归零时释放内存 */
    int minor;                       /* 次设备号（0~15），由 IDA 分配，对应设备节点后缀 */
    spinlock_t lock;                 /* 保护 stats、config、last_event、transport_* 等状态字段 */
    ktime_t transport_start_time;    /* 当前传输的开始时间戳（TRANSPORT_START 时设置） */
    bool transport_active;           /* 是否有传输正在进行（TRANSPORT_START→TRANSPORT_END 之间为 true） */
    ktime_t last_degrade_window_start; /* degrade 检测窗口的起始时间（用于滑动窗口速率对比） */
    u64 last_degrade_window_bytes;     /* 上一个 degrade 检测窗口内传输的字节数 */
    u64 last_transport_latency_ns;     /* 最近一次传输延迟（纳秒），用于 degrade 判定 */
    bool last_transport_error;         /* 当前传输周期内是否发生过错误（TRANSPORT_END 时检查） */
    bool removing;                     /* 设备正在被移除（READ_ONCE/WRITE_ONCE 访问，防止 open 竞态） */
    bool enabled;                      /* 监控是否启用（与 config.enabled 同步） */

    struct vendor_lechao_usbd_event event_buf[VENDOR_LECHAO_USBD_EVENT_BUF_SIZE]; /* 事件环形缓冲区 */
    unsigned int event_head;          /* 环形缓冲区写入位置（push 时推进） */
    unsigned int event_tail;          /* 环形缓冲区读取位置（read 时推进） */
    spinlock_t event_lock;            /* 保护 event_buf/head/tail 的自旋锁 */
    wait_queue_head_t event_wq;       /* 等待队列：read() 阻塞在此，push/断开时唤醒 */
    bool event_shutdown;              /* 事件通道关闭标志（设备断开或模块卸载时设为 true，read() 返回 EOF） */
};

/*
 * vendor_lechao_usbd_do_reset — 重置设备统计状态
 * @rate_dev: 目标设备实例
 *
 * 清零所有累计计数器和快照字段，保留 config 和设备标识不变。
 * 调用上下文：必须持有 rate_dev->lock 自旋锁。
 */
void vendor_lechao_usbd_do_reset(struct vendor_lechao_usbd_device *rate_dev);

/*
 * vendor_lechao_usbd_stats_init / _exit — 统计子模块初始化/清理
 * 当前为空实现，预留未来扩展（如 procfs/debugfs 注册）。
 */
int vendor_lechao_usbd_stats_init(void);
void vendor_lechao_usbd_stats_exit(void);

/*
 * vendor_lechao_usbd_handle_event — notifier 回调入口
 * @nb:  通知块（通过 container_of 获取 rate_dev）
 * @event: 事件类型（见 usb_stor_notifier_event 枚举）
 * @data:  事件载荷（struct usb_stor_notifier_data）
 *
 * 处理 usb-storage 核心发射的所有传输事件，更新统计、判定 degrade、
 * 推送事件到环形缓冲区、发射 LcView trace。
 * 调用上下文：原子上下文（atomic notifier chain），不可睡眠。
 * 返回值：NOTIFY_OK 表示事件已处理。
 */
int vendor_lechao_usbd_handle_event(struct notifier_block *nb,
                               unsigned long event, void *data);

/*
 * vendor_lechao_usbd_device_release — kref 归零回调
 * @kref: 内嵌在 vendor_lechao_usbd_device 中的引用计数
 *
 * 回收次设备号到 IDA 池并释放设备结构体内存。
 * 调用上下文：进程上下文（close() 系统调用路径），可睡眠。
 */
void vendor_lechao_usbd_device_release(struct kref *kref);

/*
 * vendor_lechao_usbd_device_alloc — 分配并初始化 per-device 结构体
 * @us: usb-storage 的 us_data 实例
 *
 * 返回值：成功返回设备指针，失败返回 ERR_PTR(errno)。
 * 调用上下文：进程上下文（可睡眠，使用 GFP_KERNEL）。
 */
struct vendor_lechao_usbd_device *vendor_lechao_usbd_device_alloc(struct us_data *us);

/*
 * vendor_lechao_usbd_device_add_to_list — 注册设备到全局列表
 * @rate_dev: 已分配的设备实例
 *
 * 依次注册 notifier → cdev → sysfs 节点 → 加入链表 → LcView 打点。
 * 调用上下文：进程上下文（持有全局 mutex）。
 */
void vendor_lechao_usbd_device_add_to_list(struct vendor_lechao_usbd_device *rate_dev);

#endif /* _VENDOR_LECHAO_USBD_INTERNAL_H */
