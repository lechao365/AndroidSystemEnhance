/* ============================================================
 * lciod_link.c — LcIod 全局链路节点实现
 *
 * 【设计目的】
 *   订阅 drivers/usb/core 的全局 hub 链路事件原子 notifier 链
 *   （lechao_hub_notifier.h，R2 方向 1 产物），把连接/掉线/枚举失败/
 *   过流事件转成 vendor_lechao_usbd_event 环形事件流，经
 *   /dev/vendor_lechao_usbd_link 字符节点供用户态 epoll/read 消费；
 *   并提供 GET_LINK_STATS ioctl 返回链路事件计数与最近事件快照
 *   （R2 方向 3：链路事件维测与供电归因，AOSP daemon LinkMonitor）。
 *
 * 【线程/上下文安全】
 *   - notifier 回调运行在 atomic notifier 链上下文（hub_event 工作
 *     队列发射，atomic 链禁止睡眠）：只能 spinlock + 环形推进，禁止
 *     kmalloc/sleep/printk 慢路径（pr_warn_ratelimited 可接受）。
 *   - link_state.event_lock（irqsave）保护事件环 + stats 快照；read/
 *     poll/ioctl 与 notifier 回调同一把锁，无跨锁序死锁。
 *   - event_drop_cnt 用 atomic64（与 lciod_usbd-stats.c 同模式），
 *     环形 overflow 时丢弃最旧事件并计数。
 *
 * 【ABI 契约】
 *   read() 返回定长 vendor_lechao_usbd_event（v4，含 LINK_* 类型）；
 *   链路事件经 read 裸拷（同机小端契约，lciod_usbd-ioctl.h 编译守卫）。
 *   本文件自身不 module_init/module_exit，由 lciod_usbd.c 接线。
 * ============================================================ */

#include "lciod_link.h"
#include "lciod_read_logic.h"
#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/bug.h>
#include <linux/errno.h>
#include <linux/ioctl.h>
#include <linux/atomic.h>
#include <linux/cdev.h>
#include <linux/device.h>
#include <linux/fs.h>
#include <linux/spinlock.h>
#include <linux/wait.h>
#include <linux/poll.h>
#include <linux/uaccess.h>
#include "kernel_lechao_log.h"

#define PREFIX KERNEL_USB_TAG ": "

/* 全局链路节点名称（char 设备名 / class 名 / 设备节点名） */
#define LCIOD_LINK_NAME "vendor_lechao_usbd_link"

/*
 * 全局链路节点状态
 *
 * 单节点全局实例，event_lock 同时保护事件环（event_buf/head/tail）与
 * stats（vendor_lechao_usbd_link_stats），确保 notifier 写入与用户态
 * read/ioctl 快照在同一临界区语义下一致。
 */
struct lciod_link_state {
	spinlock_t event_lock;         /* 保护事件环 + stats（irqsave） */
	wait_queue_head_t event_wq;    /* read()/poll() 等待队列 */
	bool event_shutdown;           /* 链路节点关闭标志（模块卸载置 true） */
	atomic64_t event_drop_cnt;     /* 环形溢出丢弃事件计数（atomic 锁域） */
	struct vendor_lechao_usbd_event event_buf[VENDOR_LECHAO_USBD_EVENT_BUF_SIZE];
	unsigned int event_head;       /* 事件环写指针（push 时推进） */
	unsigned int event_tail;       /* 事件环读指针（read 时推进） */
	struct vendor_lechao_usbd_link_stats stats; /* 链路事件统计快照 */
};

static struct lciod_link_state link_state;
static int lciod_link_major;
static dev_t lciod_link_devt;
static struct cdev lciod_link_cdev;
static struct class *lciod_link_class;
static struct device *lciod_link_dev;

/*
 * lciod_link_devnode — 自定义设备节点权限（0600）
 *
 * 与 vendor_lechao_usbd_devnode 同款：链路节点暴露链路事件流与
 * GET_LINK_STATS 接口，仅 system 用户（daemon）可读写。返回 NULL
 * 表示使用内核默认的 devtmpfs 节点名。
 */
static char *lciod_link_devnode(const struct device *dev, umode_t *mode)
{
	if (mode)
		*mode = 0600;
	return NULL;
}

/*
 * lciod_link_notifier — hub 链路事件 notifier 回调
 * @nb:    通知块（lciod_link_nb）
 * @action:事件分类（lehac_hub_event_cat，兼容 lechao_hub_notify 传参）
 * @data:  struct lechao_hub_notifier_data 指针
 *
 * 把 hub 载荷打包成 vendor_lechao_usbd_event 写入全局事件环，同步累计
 * stats 各计数与 last_* 最近事件字段，并唤醒等待 read() 的进程。
 * 调用上下文：atomic notifier 链上下文，不可睡眠。
 * 返回值：NOTIFY_OK 表示已处理；数据非法时 NOTIFY_DONE。
 */
int lciod_link_notifier(struct notifier_block *nb, unsigned long action,
			void *data)
{
	struct lechao_hub_notifier_data *nd = data;
	struct vendor_lechao_usbd_event ev;
	unsigned long flags;

	if (WARN_ON_ONCE(!nd))
		return NOTIFY_DONE;

	lciod_link_pack_event(nd, &ev);

	spin_lock_irqsave(&link_state.event_lock, flags);
	link_state.event_buf[link_state.event_head] = ev;
	{
		unsigned int new_tail;
		int dropped;

		link_state.event_head = lciod_event_ring_push(
			link_state.event_head, link_state.event_tail,
			VENDOR_LECHAO_USBD_EVENT_BUF_SIZE, &new_tail, &dropped);
		if (dropped) {
			pr_warn_ratelimited(PREFIX "link event ring overflow, dropped old event\n");
			atomic64_inc(&link_state.event_drop_cnt);
			link_state.event_tail = new_tail;
		}
	}

	/* stats：分类累计计数（与事件类型映射同一分类源） */
	switch (nd->category) {
	case LECHAO_HUB_EVENT_CONNECT:
		link_state.stats.connect_count++;
		break;
	case LECHAO_HUB_EVENT_DISCONNECT:
		link_state.stats.disconnect_count++;
		break;
	case LECHAO_HUB_EVENT_ENUM_FAIL:
		link_state.stats.enum_fail_count++;
		break;
	case LECHAO_HUB_EVENT_OVERCURRENT:
		link_state.stats.overcurrent_count++;
		break;
	default:
		/* CXX-004：未知分类只记计数不静默吞，ratelimited 防日志风暴 */
		pr_warn_ratelimited(PREFIX "link event unknown category=%d\n",
				    nd->category);
		break;
	}

	/* stats：最近一次事件快照字段 */
	link_state.stats.last_event_ts_ns = ev.timestamp_ns;
	link_state.stats.last_event_type = ev.event_type;
	link_state.stats.last_busnum = (u16)(nd->busnum & 0xffff);
	link_state.stats.last_port = (u16)(nd->port & 0xffff);
	link_state.stats.last_vid = nd->vid;
	link_state.stats.last_pid = nd->pid;
	link_state.stats.last_err = nd->err;
	link_state.stats.last_count = nd->count;
	link_state.stats.last_duration_ns = nd->duration_ns;
	spin_unlock_irqrestore(&link_state.event_lock, flags);

	wake_up_interruptible(&link_state.event_wq);
	return NOTIFY_OK;
}

struct notifier_block lciod_link_nb = {
	.notifier_call = lciod_link_notifier,
};

/*
 * lciod_link_open — 字符设备 open 回调
 *
 * 全局单节点，无 per-fd 状态；open 即放行（单消费者由 sepolicy
 * 保证，与 vendor_lechao_usbd 同语义）。
 */
static int lciod_link_open(struct inode *inode, struct file *file)
{
	return 0;
}

/*
 * lciod_link_release — 字符设备 release 回调
 *
 * 全局单节点，无资源需释放。
 */
static int lciod_link_release(struct inode *inode, struct file *file)
{
	return 0;
}

/*
 * lciod_link_read — 字符设备 read 回调
 *
 * 复用 vendor_lechao_usbd_read 的非阻塞三态语义（poll 驱动）：
 *   - 空环且未关闭 → -EAGAIN（非阻塞重试）
 *   - 空环且已关闭 → 0（EOF）
 *   - 非空 → 锁内取一条事件返回
 * copy_to_user 失败时用 lciod_event_tail_rollback_ok 守卫回滚
 * event_tail（CXX-002：事件留在环中供下次重试，期间被写者驱逐
 * 时禁止回滚，避免事件清零或重复消费）。
 */
static ssize_t lciod_link_read(struct file *file, char __user *buf,
			       size_t count, loff_t *ppos)
{
	struct vendor_lechao_usbd_event ev;
	uint32_t consumed_pos;  /* 本次读取的事件槽位（读取前 tail），供回滚守卫 */
	unsigned long flags;
	int decision;

	if (count < sizeof(ev)) {
		pr_warn(PREFIX "link read: buffer too small (%zu < %zu)\n",
			count, sizeof(ev));
		return -EINVAL;
	}

	spin_lock_irqsave(&link_state.event_lock, flags);
	if (READ_ONCE(link_state.event_head) != READ_ONCE(link_state.event_tail)) {
		ev = link_state.event_buf[link_state.event_tail];
		consumed_pos = link_state.event_tail;
		link_state.event_tail = (link_state.event_tail + 1) %
					VENDOR_LECHAO_USBD_EVENT_BUF_SIZE;
		spin_unlock_irqrestore(&link_state.event_lock, flags);
	} else {
		bool shutdown = READ_ONCE(link_state.event_shutdown);
		spin_unlock_irqrestore(&link_state.event_lock, flags);
		decision = lciod_nonblock_read_decision(1, shutdown);
		if (decision == 1)
			return -EAGAIN;
		return 0;
	}

	if (copy_to_user(buf, &ev, sizeof(ev))) {
		spin_lock_irqsave(&link_state.event_lock, flags);
		if (lciod_event_tail_rollback_ok(link_state.event_tail,
						 consumed_pos,
						 VENDOR_LECHAO_USBD_EVENT_BUF_SIZE))
			link_state.event_tail = consumed_pos;
		spin_unlock_irqrestore(&link_state.event_lock, flags);
		pr_err(PREFIX "link read: copy_to_user failed\n");
		return -EFAULT;
	}
	return sizeof(ev);
}

/*
 * lciod_link_poll — 字符设备 poll/select 回调
 *
 * EPOLLIN | EPOLLRDNORM — 事件环非空（有事件可读）
 * EPOLLHUP — 链路节点已关闭（模块卸载，环排空后 read 返回 EOF）
 * 环非空与 shutdown 可叠加（与 vendor_lechao_usbd_poll 同语义：
 * 关闭前先 drain 尾部事件，不清空后才表现为纯 HUP）。
 */
static __poll_t lciod_link_poll(struct file *file, poll_table *wait)
{
	__poll_t mask = 0;

	poll_wait(file, &link_state.event_wq, wait);

	if (READ_ONCE(link_state.event_head) != READ_ONCE(link_state.event_tail))
		mask |= EPOLLIN | EPOLLRDNORM;
	if (READ_ONCE(link_state.event_shutdown))
		mask |= EPOLLHUP;

	return mask;
}

/*
 * lciod_link_ioctl — 字符设备 unlocked_ioctl 回调
 *
 * 仅处理 VENDOR_LECHAO_USBD_IOC_GET_LINK_STATS：spin_lock_irqsave 下
 * 把 stats 快照到栈上再 copy_to_user（CXX-002：快照先行，copy 失败
 * 不破坏内核状态，直接 -EFAULT）。
 */
static long lciod_link_ioctl(struct file *file, unsigned int cmd,
			     unsigned long arg)
{
	void __user *argp = (void __user *)arg;
	unsigned long flags;

	switch (cmd) {
	case VENDOR_LECHAO_USBD_IOC_GET_LINK_STATS: {
		struct vendor_lechao_usbd_link_stats stats;

		spin_lock_irqsave(&link_state.event_lock, flags);
		stats = link_state.stats;
		spin_unlock_irqrestore(&link_state.event_lock, flags);

		if (copy_to_user(argp, &stats, sizeof(stats))) {
			pr_err(PREFIX "link ioctl cmd 0x%x: copy failed\n", cmd);
			return -EFAULT;
		}
		return 0;
	}
	default:
		pr_warn(PREFIX "link unknown ioctl cmd=0x%x\n", cmd);
		return -ENOTTY;
	}
}

/*
 * lciod_link_fops — 全局链路节点字符设备文件操作集
 */
static const struct file_operations lciod_link_fops = {
	.owner          = THIS_MODULE,
	.open           = lciod_link_open,
	.release        = lciod_link_release,
	.read           = lciod_link_read,
	.poll           = lciod_link_poll,
	.unlocked_ioctl = lciod_link_ioctl,
};

/*
 * lciod_link_init — 初始化全局链路节点
 *
 * 失败路径逐级回滚已建资源：注册链失败时依次销毁
 * device/class/cdev/chrdev region；cdev/class/device 失败时回滚
 * 各自的既有资源。调用上下文：进程上下文（module_init）。
 */
int lciod_link_init(void)
{
	int ret;

	spin_lock_init(&link_state.event_lock);
	init_waitqueue_head(&link_state.event_wq);
	atomic64_set(&link_state.event_drop_cnt, 0);
	link_state.event_head = 0;
	link_state.event_tail = 0;
	link_state.event_shutdown = false;
	memset(&link_state.stats, 0, sizeof(link_state.stats));

	ret = alloc_chrdev_region(&lciod_link_devt, 0, 1, LCIOD_LINK_NAME);
	if (ret < 0) {
		pr_err(PREFIX "link: alloc_chrdev_region failed (%d)\n", ret);
		return ret;
	}
	lciod_link_major = MAJOR(lciod_link_devt);

	cdev_init(&lciod_link_cdev, &lciod_link_fops);
	lciod_link_cdev.owner = THIS_MODULE;
	ret = cdev_add(&lciod_link_cdev, lciod_link_devt, 1);
	if (ret < 0) {
		pr_err(PREFIX "link: cdev_add failed (%d)\n", ret);
		unregister_chrdev_region(lciod_link_devt, 1);
		return ret;
	}

	lciod_link_class = class_create(LCIOD_LINK_NAME);
	if (IS_ERR(lciod_link_class)) {
		ret = PTR_ERR(lciod_link_class);
		pr_err(PREFIX "link: class_create failed (%d)\n", ret);
		cdev_del(&lciod_link_cdev);
		unregister_chrdev_region(lciod_link_devt, 1);
		return ret;
	}
	lciod_link_class->devnode = lciod_link_devnode;

	lciod_link_dev = device_create(lciod_link_class, NULL, lciod_link_devt,
				       NULL, LCIOD_LINK_NAME);
	if (IS_ERR(lciod_link_dev)) {
		ret = PTR_ERR(lciod_link_dev);
		pr_err(PREFIX "link: device_create failed (%d)\n", ret);
		class_destroy(lciod_link_class);
		cdev_del(&lciod_link_cdev);
		unregister_chrdev_region(lciod_link_devt, 1);
		return ret;
	}

	ret = lechao_hub_notifier_register(&lciod_link_nb);
	if (ret) {
		pr_err(PREFIX "link: notifier register failed (%d)\n", ret);
		device_destroy(lciod_link_class, lciod_link_devt);
		class_destroy(lciod_link_class);
		cdev_del(&lciod_link_cdev);
		unregister_chrdev_region(lciod_link_devt, 1);
		return ret;
	}

	pr_info(PREFIX "link node %s ready, major=%d\n",
		LCIOD_LINK_NAME, lciod_link_major);
	return 0;
}

/*
 * lciod_link_exit — 销毁全局链路节点
 *
 * 与 init 顺序相反：先注销 hub notifier 阻止新事件进入，再置
 * event_shutdown 并唤醒等待者（read 排空后返回 EOF），最后销毁
 * device/class/cdev/chrdev region。调用上下文：进程上下文（module_exit）。
 */
void lciod_link_exit(void)
{
	unsigned long flags;

	lechao_hub_notifier_unregister(&lciod_link_nb);

	spin_lock_irqsave(&link_state.event_lock, flags);
	WRITE_ONCE(link_state.event_shutdown, true);
	spin_unlock_irqrestore(&link_state.event_lock, flags);
	wake_up_interruptible(&link_state.event_wq);

	device_destroy(lciod_link_class, lciod_link_devt);
	class_destroy(lciod_link_class);
	cdev_del(&lciod_link_cdev);
	unregister_chrdev_region(lciod_link_devt, 1);

	pr_info(PREFIX "link node %s unloaded\n", LCIOD_LINK_NAME);
}
