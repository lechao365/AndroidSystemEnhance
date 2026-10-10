/* ============================================================
 * lciod_sd.c — LcIod SD/MMC 块层健康监控实现
 *
 * 【设计目的】
 *   订阅 drivers/mmc/core 的全局 SD/MMC 块层健康事件原子 notifier 链
 *   （lechao_sd_notifier.h，A 批一方向 1 产物）：
 *     - COMPLETE：在 10s 窗内累加有效传输字节与请求数，窗口到期经 LcView
 *       上报 LCVIEW_EVENT_SD_HEALTH（窗口字节/请求数 + 累计值 + 卡死数）；
 *     - STUCK：依据 card->sd_bus_speed 逐级降档并剥除高于新档的模式位
 *       （纯函数见 lciod_sd.h），上报 LCVIEW_EVENT_SD_STUCK。
 *   暴露 sysfs 开关（enable，默认开）与窗口/卡死计数只读属性。
 *
 * 【线程/上下文安全（CXX-001~004）】
 *   - notifier 回调运行在 atomic notifier 链上下文（blk-mq 完成路径可能为
 *     软中断）：仅自旋锁内累加，禁止睡眠/阻塞分配；LcView builder 走
 *     GFP_ATOMIC 预分配池（与 vendor_lechao_usbd-stats.c 同模式）。
 *   - card 字段写入用 WRITE_ONCE，读取用 READ_ONCE：降档发生在 atomic
 *     上下文，与 mmc 核心重协商并发时避免撕裂。
 *   - 窗口累加/总计共用 sd_state.lock（irqsave），delayed_work 与 notifier
 *     同一把锁，无跨锁序死锁。
 * ============================================================ */

#include "lciod_sd.h"
#include "lechao_sd_notifier.h"
#include "lcview_internal.h"
#include "lcview_events.h"
#include "kernel_lechao_log.h"

#include <linux/module.h>
#include <linux/kernel.h>
#include <linux/init.h>
#include <linux/errno.h>
#include <linux/notifier.h>
#include <linux/spinlock.h>
#include <linux/workqueue.h>
#include <linux/ktime.h>
#include <linux/device.h>
#include <linux/sysfs.h>
#include <linux/slab.h>
#include <linux/string.h>
#include <linux/mmc/card.h>

#define PREFIX KERNEL_USB_TAG ": "

/* 聚合窗口长度（毫秒）：10s 窗 */
#define LCIOD_SD_WINDOW_MS   10000u

/* sysfs / class / 设备名 */
#define LCIOD_SD_NAME        "lciod_sd"

/*
 * SD 健康监控窗口状态
 *
 * window_* 为本 10s 窗累加值（窗口到期上报后清零）；total_* 为模块加载
 * 起累计值；stuck_count 为累计 STUCK 次数；last_speed 为最近一次降档后
 * 的速度档位。全部由 lock（irqsave）保护。
 */
struct lciod_sd_state {
	spinlock_t lock;            /* 保护下列全部字段（irqsave） */
	bool enabled;               /* sysfs 开关，默认 true（开） */
	u64 window_bytes;           /* 本窗口累计有效字节 */
	u64 window_requests;        /* 本窗口累计请求数 */
	u64 total_bytes;            /* 累计有效字节 */
	u64 total_requests;         /* 累计请求数 */
	u64 stuck_count;            /* 累计 STUCK 次数 */
	unsigned int last_speed;    /* 最近降档后速度档位（0 起） */
};

static struct lciod_sd_state sd_state;
static struct delayed_work sd_work;
static struct class *lciod_sd_class;
static struct device *lciod_sd_dev;

int lciod_sd_notifier(struct notifier_block *nb, unsigned long action,
		      void *data);

static void lciod_sd_trace_health(u64 bytes, u64 requests, u64 window_ms,
				  u64 total_bytes, u64 total_requests,
				  u64 stuck_count);
static void lciod_sd_trace_stuck(unsigned int old_speed, unsigned int new_speed,
				 int err);
static void lciod_sd_downgrade(struct mmc_card *card, int err);

/*
 * lciod_sd_trace_health — 上报一次 10s 窗聚合结果到 LcView
 * @bytes/requests：本窗有效字节/请求数
 * @window_ms：窗口长度（毫秒）
 * @total_bytes/total_requests：累计字节/请求数
 * @stuck_count：累计卡死次数
 * 调用上下文：进程上下文（delayed_work）。builder 失败静默降级（cancel）。
 */
static void lciod_sd_trace_health(u64 bytes, u64 requests, u64 window_ms,
				  u64 total_bytes, u64 total_requests,
				  u64 stuck_count)
{
	struct lcview_builder *b;
	int rc;

	b = lcview_builder_start(LCVIEW_EVENT_SD_HEALTH, LCVIEW_LEVEL_INFO);
	if (!b)
		return;

	/* 字段顺序必须与 lcview_events.json id=15 的 fields 顺序一致 */
	rc = lcview_builder_add_int(b, (int64_t)bytes);
	rc |= lcview_builder_add_int(b, (int64_t)requests);
	rc |= lcview_builder_add_int(b, (int64_t)window_ms);
	rc |= lcview_builder_add_int(b, (int64_t)total_bytes);
	rc |= lcview_builder_add_int(b, (int64_t)total_requests);
	rc |= lcview_builder_add_int(b, (int64_t)stuck_count);

	if (rc || lcview_builder_commit(b, &lcview_ring))
		lcview_builder_cancel(b);
}

/*
 * lciod_sd_trace_stuck — 上报一次 STUCK 降档事件到 LcView
 * @old_speed/new_speed：降档前/后速度档位
 * @err：STUCK 错误码
 * 调用上下文：atomic notifier 上下文（builder 走 GFP_ATOMIC 池）。
 */
static void lciod_sd_trace_stuck(unsigned int old_speed, unsigned int new_speed,
				 int err)
{
	struct lcview_builder *b;
	int rc;

	b = lcview_builder_start(LCVIEW_EVENT_SD_STUCK, LCVIEW_LEVEL_WARN);
	if (!b)
		return;

	/* 字段顺序必须与 lcview_events.json id=16 的 fields 顺序一致 */
	rc = lcview_builder_add_int(b, (int64_t)old_speed);
	rc |= lcview_builder_add_int(b, (int64_t)new_speed);
	rc |= lcview_builder_add_int(b, (int64_t)err);
	rc |= lcview_builder_add_int(b, (int64_t)READ_ONCE(sd_state.stuck_count));

	if (rc || lcview_builder_commit(b, &lcview_ring))
		lcview_builder_cancel(b);
}

/*
 * lciod_sd_downgrade — 依据当前 sd_bus_speed 逐级剥 caps 降档
 * @card：STUCK 关联卡片（notifier 载荷携带）
 * @err：STUCK 错误码
 *
 * 仅对 SD 卡（mmc_card_sd）生效：new_speed = next_lower_speed(speed)；
 * sw_caps.sd3_bus_mode = strip_modes_above(mode, new_speed)。已至最低档且
 * 模式位无可剥时直接返回。card 字段用 WRITE_ONCE 写入（atomic 上下文与
 * mmc 核心并发），本函数不触发立即重协商——降档后的档位/掩码在下一次
 * 卡初始化（mmc_sd_init_card）时被采用。
 * 调用上下文：atomic notifier 上下文，不可睡眠。
 */
static void lciod_sd_downgrade(struct mmc_card *card, int err)
{
	unsigned int old_speed, new_speed, old_mode, new_mode;

	if (!mmc_card_sd(card))
		return;

	old_speed = READ_ONCE(card->sd_bus_speed);
	new_speed = lciod_sd_next_lower_speed(old_speed);
	old_mode = READ_ONCE(card->sw_caps.sd3_bus_mode);
	new_mode = lciod_sd_strip_modes_above(old_mode, new_speed);

	if (new_speed == old_speed && new_mode == old_mode)
		return; /* 已至最低档且无更高模式位，无可降 */

	WRITE_ONCE(card->sd_bus_speed, new_speed);
	WRITE_ONCE(card->sw_caps.sd3_bus_mode, new_mode);
	WRITE_ONCE(sd_state.last_speed, new_speed);

	pr_warn_ratelimited(PREFIX "sd stuck: speed %u->%u mode 0x%x->0x%x err=%d\n",
			    old_speed, new_speed, old_mode, new_mode, err);

	lciod_sd_trace_stuck(old_speed, new_speed, err);
}

/*
 * lciod_sd_notifier — SD/MMC 块层事件 notifier 回调
 * @nb：通知块（lciod_sd_nb）
 * @action：事件分类（lechao_sd_event_cat）
 * @data：struct lechao_sd_notifier_data 指针
 *
 * COMPLETE 累加窗口/总计字节与请求数；STUCK 累计卡死次数并触发降档。
 * 调用上下文：atomic notifier 链，不可睡眠。返回 NOTIFY_OK；数据非法
 * 或开关关闭返回 NOTIFY_DONE。
 */
int lciod_sd_notifier(struct notifier_block *nb, unsigned long action,
		      void *data)
{
	struct lechao_sd_notifier_data *nd = data;
	unsigned long flags;

	if (WARN_ON_ONCE(!nd))
		return NOTIFY_DONE;

	spin_lock_irqsave(&sd_state.lock, flags);
	if (!sd_state.enabled) {
		spin_unlock_irqrestore(&sd_state.lock, flags);
		return NOTIFY_DONE;
	}

	switch (nd->category) {
	case LECHAO_SD_EVENT_COMPLETE:
		sd_state.window_bytes += nd->bytes;
		sd_state.window_requests++;
		sd_state.total_bytes += nd->bytes;
		sd_state.total_requests++;
		break;
	case LECHAO_SD_EVENT_STUCK:
		sd_state.stuck_count++;
		break;
	default:
		/* CXX-004：未知分类不静默吞，ratelimited 防日志风暴 */
		pr_warn_ratelimited(PREFIX "sd unknown event category=%u\n",
				    nd->category);
		break;
	}
	spin_unlock_irqrestore(&sd_state.lock, flags);

	/* 降档需解引用 card：放锁外（仍 atomic），仅 WRITE_ONCE 赋值 */
	if (nd->category == LECHAO_SD_EVENT_STUCK && nd->card)
		lciod_sd_downgrade(nd->card, nd->err);

	return NOTIFY_OK;
}

struct notifier_block lciod_sd_nb = {
	.notifier_call = lciod_sd_notifier,
};

/*
 * lciod_sd_window_work — 10s 窗口到期：快照并上报聚合结果后重排
 *
 * 锁内快照并清零窗口累加器，锁外（进程上下文）经 LcView 上报，避免在
 * 持锁期间调用 builder。上报后重新调度下一个 10s 窗口。
 */
static void lciod_sd_window_work(struct work_struct *work)
{
	unsigned long flags;
	u64 bytes, requests, total_bytes, total_requests, stuck;

	spin_lock_irqsave(&sd_state.lock, flags);
	bytes = sd_state.window_bytes;
	requests = sd_state.window_requests;
	sd_state.window_bytes = 0;
	sd_state.window_requests = 0;
	total_bytes = sd_state.total_bytes;
	total_requests = sd_state.total_requests;
	stuck = sd_state.stuck_count;
	spin_unlock_irqrestore(&sd_state.lock, flags);

	lciod_sd_trace_health(bytes, requests, LCIOD_SD_WINDOW_MS,
			      total_bytes, total_requests, stuck);

	schedule_delayed_work(&sd_work, msecs_to_jiffies(LCIOD_SD_WINDOW_MS));
}

/* ---- sysfs：开关（默认开）与只读计数 ---- */

static ssize_t enable_show(struct device *dev, struct device_attribute *attr,
			   char *buf)
{
	return sysfs_emit(buf, "%u\n",
			  READ_ONCE(sd_state.enabled) ? 1u : 0u);
}

static ssize_t enable_store(struct device *dev, struct device_attribute *attr,
			    const char *buf, size_t count)
{
	unsigned int val;
	unsigned long flags;

	if (kstrtouint(buf, 0, &val))
		return -EINVAL;

	spin_lock_irqsave(&sd_state.lock, flags);
	sd_state.enabled = (val != 0);
	spin_unlock_irqrestore(&sd_state.lock, flags);

	return count;
}
static DEVICE_ATTR_RW(enable);

static ssize_t window_bytes_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	unsigned long flags;
	u64 v;

	spin_lock_irqsave(&sd_state.lock, flags);
	v = sd_state.window_bytes;
	spin_unlock_irqrestore(&sd_state.lock, flags);

	return sysfs_emit(buf, "%llu\n", (unsigned long long)v);
}
static DEVICE_ATTR_RO(window_bytes);

static ssize_t window_requests_show(struct device *dev,
				    struct device_attribute *attr, char *buf)
{
	unsigned long flags;
	u64 v;

	spin_lock_irqsave(&sd_state.lock, flags);
	v = sd_state.window_requests;
	spin_unlock_irqrestore(&sd_state.lock, flags);

	return sysfs_emit(buf, "%llu\n", (unsigned long long)v);
}
static DEVICE_ATTR_RO(window_requests);

static ssize_t stuck_count_show(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	unsigned long flags;
	u64 v;

	spin_lock_irqsave(&sd_state.lock, flags);
	v = sd_state.stuck_count;
	spin_unlock_irqrestore(&sd_state.lock, flags);

	return sysfs_emit(buf, "%llu\n", (unsigned long long)v);
}
static DEVICE_ATTR_RO(stuck_count);

static struct attribute *lciod_sd_attrs[] = {
	&dev_attr_enable.attr,
	&dev_attr_window_bytes.attr,
	&dev_attr_window_requests.attr,
	&dev_attr_stuck_count.attr,
	NULL,
};

static const struct attribute_group lciod_sd_attr_group = {
	.attrs = lciod_sd_attrs,
};

/*
 * lciod_sd_init — 初始化 SD/MMC 健康监控
 *
 * 顺序：初始化状态/工作 → 建 class/device（sysfs 节点）→ 建属性组 →
 * 注册 notifier → 起 10s 窗。任一步失败逐级回滚已建资源。上下文：进程
 * 上下文（module_init）。
 */
static int __init lciod_sd_init(void)
{
	int ret;

	spin_lock_init(&sd_state.lock);
	sd_state.enabled = true; /* 默认开 */
	sd_state.window_bytes = 0;
	sd_state.window_requests = 0;
	sd_state.total_bytes = 0;
	sd_state.total_requests = 0;
	sd_state.stuck_count = 0;
	sd_state.last_speed = LCIOD_SD_SPEED_MAX;
	INIT_DELAYED_WORK(&sd_work, lciod_sd_window_work);

	lciod_sd_class = class_create(LCIOD_SD_NAME);
	if (IS_ERR(lciod_sd_class)) {
		ret = PTR_ERR(lciod_sd_class);
		pr_err(PREFIX "sd: class_create failed (%d)\n", ret);
		return ret;
	}

	lciod_sd_dev = device_create(lciod_sd_class, NULL, MKDEV(0, 0), NULL,
				     LCIOD_SD_NAME);
	if (IS_ERR(lciod_sd_dev)) {
		ret = PTR_ERR(lciod_sd_dev);
		pr_err(PREFIX "sd: device_create failed (%d)\n", ret);
		class_destroy(lciod_sd_class);
		return ret;
	}

	ret = sysfs_create_group(&lciod_sd_dev->kobj, &lciod_sd_attr_group);
	if (ret) {
		pr_err(PREFIX "sd: sysfs_create_group failed (%d)\n", ret);
		device_destroy(lciod_sd_class, MKDEV(0, 0));
		class_destroy(lciod_sd_class);
		return ret;
	}

	ret = lechao_sd_notifier_register(&lciod_sd_nb);
	if (ret) {
		pr_err(PREFIX "sd: notifier register failed (%d)\n", ret);
		sysfs_remove_group(&lciod_sd_dev->kobj, &lciod_sd_attr_group);
		device_destroy(lciod_sd_class, MKDEV(0, 0));
		class_destroy(lciod_sd_class);
		return ret;
	}

	schedule_delayed_work(&sd_work, msecs_to_jiffies(LCIOD_SD_WINDOW_MS));

	pr_info(PREFIX "sd health monitor ready, window=%ums\n",
		LCIOD_SD_WINDOW_MS);
	return 0;
}

/*
 * lciod_sd_exit — 销毁 SD/MMC 健康监控
 *
 * 与 init 顺序相反：先停窗口工作（阻止新上报）→ 注销 notifier → 拆
 * sysfs/device/class。上下文：进程上下文（module_exit）。
 */
static void __exit lciod_sd_exit(void)
{
	cancel_delayed_work_sync(&sd_work);
	lechao_sd_notifier_unregister(&lciod_sd_nb);
	sysfs_remove_group(&lciod_sd_dev->kobj, &lciod_sd_attr_group);
	device_destroy(lciod_sd_class, MKDEV(0, 0));
	class_destroy(lciod_sd_class);

	pr_info(PREFIX "sd health monitor unloaded\n");
}

module_init(lciod_sd_init);
module_exit(lciod_sd_exit);

MODULE_LICENSE("GPL");
MODULE_AUTHOR("Lechao");
MODULE_DESCRIPTION("SD/MMC Block-layer Health Monitor for Lechao Vendor");
MODULE_VERSION("1.0");
