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
 *   暴露 sysfs 开关（enable，默认开）、窗口/卡死计数与最近降档档位
 *   （last_speed）只读属性，及自愈演练入口（inject_stuck，root-only 写一次
 *   触发一次：内存构造合成 STUCK 走完整 notifier 降档链，不触碰真实卡）。
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
#include "lciod_histo.h"
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
	u64 window_lat_sum_ns;      /* 本窗口累计请求历时（纳秒，延迟反馈源） */
	u64 window_lat_count;       /* 本窗口计入历时的请求数（求均值分母） */
	u64 window_lat_tail;        /* 本窗口长尾请求数（历时 >= 长尾阈值） */
	u64 total_bytes;            /* 累计有效字节 */
	u64 total_requests;         /* 累计请求数 */
	u64 stuck_count;            /* 累计 STUCK 次数 */
	struct lciod_histo histo;   /* 传输延迟/块大小二维直方图（lock 保护，累计） */
	unsigned int last_speed;    /* 最近降档后速度档位（0 起） */
	struct mmc_card *card;          /* 最近一次事件关联卡片（非空即持引用；锁内替换，旧卡锁外 put） */
	unsigned int ssr_speed_class;   /* 最近解码 SD 速度档 SPEED_CLASS */
	unsigned int ssr_uhs_grade;     /* 最近解码 UHS_SPEED_GRADE */
	unsigned int ssr_valid;         /* SSR 最近读取是否成功（1/0） */
};

static struct lciod_sd_state sd_state;
static struct delayed_work sd_work;
static struct class *lciod_sd_class;
static struct device *lciod_sd_dev;

int lciod_sd_notifier(struct notifier_block *nb, unsigned long action,
		      void *data);

static void lciod_sd_trace_health(u64 bytes, u64 requests, u64 window_ms,
				  u64 total_bytes, u64 total_requests,
				  u64 stuck_count, unsigned int speed_class,
				  unsigned int uhs_grade);
static void lciod_sd_trace_stuck(unsigned int old_speed, unsigned int new_speed,
				 int err);
static void lciod_sd_trace_qos_auto(u64 avg_lat_us, u64 tail_count,
				    u64 threshold_us);
static void lciod_sd_downgrade(struct mmc_card *card, int err);
static void lciod_sd_refresh_ssr(void);

/*
 * lciod_sd_trace_health — 上报一次 10s 窗聚合结果到 LcView
 * @bytes/requests：本窗有效字节/请求数
 * @window_ms：窗口长度（毫秒）
 * @total_bytes/total_requests：累计字节/请求数
 * @stuck_count：累计卡死次数
 * @speed_class/uhs_grade：最近解码 SD 速度档（SSR，A 批二方向 1）
 * 调用上下文：进程上下文（delayed_work）。builder 失败静默降级（cancel）。
 */
static void lciod_sd_trace_health(u64 bytes, u64 requests, u64 window_ms,
				  u64 total_bytes, u64 total_requests,
				  u64 stuck_count, unsigned int speed_class,
				  unsigned int uhs_grade)
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
	rc |= lcview_builder_add_int(b, (int64_t)speed_class);
	rc |= lcview_builder_add_int(b, (int64_t)uhs_grade);

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
 * lciod_sd_trace_qos_auto — 上报一次窗口延迟触发自动分级事件到 LcView
 * @avg_lat_us：本窗口平均请求延迟（微秒）
 * @tail_count：本窗口长尾（>= LCIOD_SD_LAT_TAIL_NS）请求数
 * @threshold_us：长尾阈值（微秒）
 *
 * 触发条件：10s 窗内长尾计数 > 0（窗口到期时判定）。事件为 daemon
 * IoQosManager 自动档的观测锚点（daemon 读 window_lat_avg_us/window_lat_tail
 * 决定收紧/放松 bg 限速）。调用上下文：进程上下文（delayed_work）。
 */
static void lciod_sd_trace_qos_auto(u64 avg_lat_us, u64 tail_count,
				    u64 threshold_us)
{
	struct lcview_builder *b;
	int rc;

	b = lcview_builder_start(LCVIEW_EVENT_QOS_AUTO_ADJUST, LCVIEW_LEVEL_WARN);
	if (!b)
		return;

	/* 字段顺序必须与 lcview_events.json id=18 的 fields 顺序一致 */
	rc = lcview_builder_add_int(b, (int64_t)avg_lat_us);
	rc |= lcview_builder_add_int(b, (int64_t)tail_count);
	rc |= lcview_builder_add_int(b, (int64_t)threshold_us);

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
	struct mmc_card *old_card = NULL;
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
		/*
		 * C 批一：延迟/块大小直方图入桶（elapsed=nd->duration_ns，
		 * bytes=nd->bytes）。当前持 sd_state.lock（irqsave），入桶无需
		 * 再加锁；方向 LECHAO_SD_DIR_READ→读，其余→写。
		 */
		lciod_histo_add(&sd_state.histo,
				(nd->direction == LECHAO_SD_DIR_READ) ?
					LCIOD_HISTO_DIR_READ : LCIOD_HISTO_DIR_WRITE,
				nd->bytes, nd->duration_ns);
		/* 延迟反馈源：累计窗口历时/计数与长尾计数（QoS 自动档输入） */
		sd_state.window_lat_sum_ns += nd->duration_ns;
		sd_state.window_lat_count++;
		if (LCIOD_SD_LAT_IS_TAIL(nd->duration_ns))
			sd_state.window_lat_tail++;
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

	/*
	 * 记录最近关联卡片：先 get_device 持引用，锁内替换指针，旧卡在锁外
	 * put（不在 spinlock 内触发 device release）。演练合成事件
	 * （synthetic）的 card 为临时内存卡，禁止写入 sd_state.card——否则
	 * kfree 后 SSR 刷新线程会 READ_ONCE 到悬空指针。
	 */
	if (nd->card && !nd->synthetic) {
		get_device(&nd->card->dev);
		spin_lock_irqsave(&sd_state.lock, flags);
		old_card = sd_state.card;
		WRITE_ONCE(sd_state.card, nd->card);
		spin_unlock_irqrestore(&sd_state.lock, flags);
	}

	/* 降档需解引用 card：放锁外（仍 atomic），仅 WRITE_ONCE 赋值 */
	if (nd->category == LECHAO_SD_EVENT_STUCK && nd->card)
		lciod_sd_downgrade(nd->card, nd->err);

	/* 旧卡引用锁外释放（SSR 此后读到的是新卡，旧卡不会悬空） */
	if (old_card)
		put_device(&old_card->dev);

	return NOTIFY_OK;
}

struct notifier_block lciod_sd_nb = {
	.notifier_call = lciod_sd_notifier,
};

/*
 * lciod_sd_refresh_ssr — 读取并解码 SD Status Register 速度档
 *
 * 调用上下文：进程上下文（delayed_work 10s 窗）。读最近关联卡片；仅对
 * SD 卡（mmc_card_sd）读取。读取期间 get_device 持 card 引用防拔卡，
 * 完成后 put_device 释放。读取失败仅 ratelimited 告警并置 ssr_valid=0，
 * 不阻断窗口上报（CXX-002：错误经 sysfs ssr_valid 可见，不静默吞噬）。
 */
static void lciod_sd_refresh_ssr(void)
{
	struct mmc_card *card;
	struct lciod_sd_ssr d;
	unsigned int raw_ssr[LCIOD_SD_SSR_WORDS];
	unsigned long flags;

	/*
	 * 与 notifier 的指针替换同锁取卡并 get_device：sd_state.card 虽由
	 * notifier 持引用，但若先 READ_ONCE 再 get_device，notifier 可能在
	 * 两步之间换卡并 put 旧卡，旧卡引用归零即已释放（UAF）。锁内取引用
	 * 保证拿到的是仍被 sd_state 持有的卡。
	 */
	spin_lock_irqsave(&sd_state.lock, flags);
	card = sd_state.card;
	if (card)
		get_device(&card->dev);
	spin_unlock_irqrestore(&sd_state.lock, flags);

	if (!card)
		return;
	if (!mmc_card_sd(card)) {
		put_device(&card->dev);
		return;
	}

	if (lechao_mmc_read_sd_status(card, raw_ssr) == 0) {
		lciod_sd_decode_ssr(raw_ssr, &d);
		spin_lock_irqsave(&sd_state.lock, flags);
		sd_state.ssr_speed_class = d.speed_class;
		sd_state.ssr_uhs_grade = d.uhs_speed_grade;
		sd_state.ssr_valid = 1;
		spin_unlock_irqrestore(&sd_state.lock, flags);
	} else {
		spin_lock_irqsave(&sd_state.lock, flags);
		sd_state.ssr_valid = 0;
		spin_unlock_irqrestore(&sd_state.lock, flags);
		pr_warn_ratelimited(PREFIX "sd: read SD status failed\n");
	}

	put_device(&card->dev);
}

/*
 * lciod_sd_window_work — 10s 窗口到期：快照并上报聚合结果后重排
 *
 * 锁内快照并清零窗口累加器，锁外（进程上下文）经 LcView 上报，避免在
 * 持锁期间调用 builder。上报前先刷新 SSR 速度档（进程上下文可睡眠）。
 * 上报后重新调度下一个 10s 窗口。
 */
static void lciod_sd_window_work(struct work_struct *work)
{
	unsigned long flags;
	u64 bytes, requests, total_bytes, total_requests, stuck;
	u64 lat_avg_us, lat_tail;
	unsigned int speed_class, uhs_grade;

	/* 进程上下文刷新 SSR（含 mmc claim/release host，可睡眠） */
	lciod_sd_refresh_ssr();

	spin_lock_irqsave(&sd_state.lock, flags);
	bytes = sd_state.window_bytes;
	requests = sd_state.window_requests;
	/* 延迟反馈源快照（窗口作用域：窗口到期清零，下窗重新累计） */
	lat_avg_us = lciod_sd_lat_avg_us(sd_state.window_lat_sum_ns,
					 sd_state.window_lat_count);
	lat_tail = sd_state.window_lat_tail;
	sd_state.window_bytes = 0;
	sd_state.window_requests = 0;
	sd_state.window_lat_sum_ns = 0;
	sd_state.window_lat_count = 0;
	sd_state.window_lat_tail = 0;
	total_bytes = sd_state.total_bytes;
	total_requests = sd_state.total_requests;
	stuck = sd_state.stuck_count;
	speed_class = sd_state.ssr_speed_class;
	uhs_grade = sd_state.ssr_uhs_grade;
	spin_unlock_irqrestore(&sd_state.lock, flags);

	lciod_sd_trace_health(bytes, requests, LCIOD_SD_WINDOW_MS,
			      total_bytes, total_requests, stuck,
			      speed_class, uhs_grade);

	/*
	 * 窗口长尾延迟触发自动分级：长尾计数 > 0 时上报 QOS_AUTO_ADJUST
	 * （daemon 自动档观测锚点），携带本窗平均延迟/长尾计数/长尾阈值。
	 */
	if (lat_tail > 0)
		lciod_sd_trace_qos_auto(lat_avg_us, lat_tail,
					LCIOD_SD_LAT_TAIL_NS / 1000ULL);

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

static ssize_t last_speed_show(struct device *dev,
			       struct device_attribute *attr, char *buf)
{
	unsigned long flags;
	unsigned int v;

	spin_lock_irqsave(&sd_state.lock, flags);
	v = sd_state.last_speed;
	spin_unlock_irqrestore(&sd_state.lock, flags);

	return sysfs_emit(buf, "%u\n", v);
}
static DEVICE_ATTR_RO(last_speed);

/*
 * window_lat_avg_us_show — 本窗口平均请求延迟（微秒，只读）
 *
 * daemon IoQosManager 自动档反馈源之一：平均延迟超阈收紧 bg 限速。窗口
 * 到期由 lciod_sd_window_work 清零，故反映"当前窗口"的实时均值。
 */
static ssize_t window_lat_avg_us_show(struct device *dev,
				      struct device_attribute *attr, char *buf)
{
	unsigned long flags;
	u64 v;

	spin_lock_irqsave(&sd_state.lock, flags);
	v = lciod_sd_lat_avg_us(sd_state.window_lat_sum_ns,
				sd_state.window_lat_count);
	spin_unlock_irqrestore(&sd_state.lock, flags);

	return sysfs_emit(buf, "%llu\n", (unsigned long long)v);
}
static DEVICE_ATTR_RO(window_lat_avg_us);

/*
 * window_lat_tail_show — 本窗口长尾请求数（历时 >= 长尾阈值，只读）
 *
 * daemon 自动档反馈源之一：长尾计数 > 0 收紧 bg 限速；窗口到期（计数清零）
 * 后无长尾则放松。亦为 QOS_AUTO_ADJUST 事件的触发判据。
 */
static ssize_t window_lat_tail_show(struct device *dev,
				     struct device_attribute *attr, char *buf)
{
	unsigned long flags;
	u64 v;

	spin_lock_irqsave(&sd_state.lock, flags);
	v = sd_state.window_lat_tail;
	spin_unlock_irqrestore(&sd_state.lock, flags);

	return sysfs_emit(buf, "%llu\n", (unsigned long long)v);
}
static DEVICE_ATTR_RO(window_lat_tail);

/*
 * inject_stuck_store — SD 自愈演练入口（root-only，写一次触发一次）
 *
 * 在内存构造一张临时 SD 卡（起点 DDR50 + 全模式位），构造合成 STUCK 事件
 * 置 synthetic=1 后经 lechao_sd_notify 走**完整 notifier 处理链**：
 * stuck_count 递增 → 依据合成卡 sd_bus_speed 逐级剥 caps 降档 → SD_STUCK
 * 事件上报。**仅内存模拟**：合成卡随本次调用 kzalloc/kfree，全程不触碰
 * 真实 mmc 卡与硬件操作；synthetic 标记确保回调不将合成卡写入 sd_state.card
 * （防 SSR 误读已释放卡）。调用上下文：进程上下文（sysfs store，可睡眠分配）。
 */
static ssize_t inject_stuck_store(struct device *dev,
				  struct device_attribute *attr,
				  const char *buf, size_t count)
{
	struct lechao_sd_notifier_data nd = {0};
	struct lciod_sd_stuck_evt evt;
	struct mmc_card *card;

	card = kzalloc(sizeof(*card), GFP_KERNEL);
	if (!card)
		return -ENOMEM;

	/* 演练起点契约（纯函数，host 单测同源）：合成卡起始档位/模式 */
	lciod_sd_build_stuck_evt(LCIOD_SD_DRILL_START_SPEED,
				 LCIOD_SD_DRILL_START_MODE,
				 LCIOD_SD_DRILL_ERR, &evt);
	card->type = MMC_TYPE_SD;
	card->sd_bus_speed = evt.old_speed;
	card->sw_caps.sd3_bus_mode = evt.old_mode;

	nd.card = card;
	nd.category = LECHAO_SD_EVENT_STUCK;
	nd.err = evt.err;
	nd.synthetic = 1; /* 合成事件：回调不占据 sd_state.card */

	lechao_sd_notify(&nd);

	kfree(card);

	pr_info(PREFIX "sd drill: inject synthetic STUCK (expect speed %u->%u)\n",
		evt.old_speed, evt.new_speed);
	return count;
}
static DEVICE_ATTR_WO(inject_stuck);

/*
 * inject_latency_store — SD 延迟演练入口（root-only，写一次触发一次）
 *
 * 构造一次**慢 COMPLETE** 合成事件（large duration_ns，无关联真实卡）经
 * lechao_sd_notify 走**完整 notifier 处理链**：累计窗口/总字节与请求数 →
 * 延迟/块大小直方图入桶 → 延迟反馈源累计（window_lat_sum_ns/count/tail）。
 * 窗口到期若长尾计数 > 0 即上报 QOS_AUTO_ADJUST，daemon IoQosManager 据此
 * 收紧 bg 限速。**仅内存模拟**：无 card（COMPLETE 路径不解引用卡），全程
 * 不触碰真实 mmc 卡与硬件操作。上下文：进程上下文（sysfs store）。
 */
static ssize_t inject_latency_store(struct device *dev,
				    struct device_attribute *attr,
				    const char *buf, size_t count)
{
	struct lechao_sd_notifier_data nd = {0};

	nd.category = LECHAO_SD_EVENT_COMPLETE;
	nd.direction = LECHAO_SD_DIR_WRITE;
	nd.bytes = LCIOD_SD_DRILL_LAT_BYTES;
	nd.duration_ns = LCIOD_SD_DRILL_LAT_NS;
	nd.synthetic = 1; /* 合成事件：无真实卡，回调不占据 sd_state.card */

	lechao_sd_notify(&nd);

	pr_info(PREFIX "sd drill: inject synthetic slow COMPLETE (lat=%llums bytes=%u)\n",
		(unsigned long long)(LCIOD_SD_DRILL_LAT_NS / 1000000ULL),
		LCIOD_SD_DRILL_LAT_BYTES);
	return count;
}
static DEVICE_ATTR_WO(inject_latency);

static ssize_t ssr_speed_class_show(struct device *dev,
				    struct device_attribute *attr, char *buf)
{
	unsigned long flags;
	unsigned int v;

	spin_lock_irqsave(&sd_state.lock, flags);
	v = sd_state.ssr_speed_class;
	spin_unlock_irqrestore(&sd_state.lock, flags);

	return sysfs_emit(buf, "%u\n", v);
}
static DEVICE_ATTR_RO(ssr_speed_class);

static ssize_t ssr_uhs_grade_show(struct device *dev,
				  struct device_attribute *attr, char *buf)
{
	unsigned long flags;
	unsigned int v;

	spin_lock_irqsave(&sd_state.lock, flags);
	v = sd_state.ssr_uhs_grade;
	spin_unlock_irqrestore(&sd_state.lock, flags);

	return sysfs_emit(buf, "%u\n", v);
}
static DEVICE_ATTR_RO(ssr_uhs_grade);

static ssize_t ssr_valid_show(struct device *dev,
			      struct device_attribute *attr, char *buf)
{
	unsigned long flags;
	unsigned int v;

	spin_lock_irqsave(&sd_state.lock, flags);
	v = sd_state.ssr_valid;
	spin_unlock_irqrestore(&sd_state.lock, flags);

	return sysfs_emit(buf, "%u\n", v);
}
static DEVICE_ATTR_RO(ssr_valid);

static struct attribute *lciod_sd_attrs[] = {
	&dev_attr_enable.attr,
	&dev_attr_window_bytes.attr,
	&dev_attr_window_requests.attr,
	&dev_attr_stuck_count.attr,
	&dev_attr_last_speed.attr,
	&dev_attr_window_lat_avg_us.attr,
	&dev_attr_window_lat_tail.attr,
	&dev_attr_inject_stuck.attr,
	&dev_attr_inject_latency.attr,
	&dev_attr_ssr_speed_class.attr,
	&dev_attr_ssr_uhs_grade.attr,
	&dev_attr_ssr_valid.attr,
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
	sd_state.window_lat_sum_ns = 0;
	sd_state.window_lat_count = 0;
	sd_state.window_lat_tail = 0;
	sd_state.total_bytes = 0;
	sd_state.total_requests = 0;
	sd_state.stuck_count = 0;
	sd_state.last_speed = LCIOD_SD_SPEED_MAX;
	sd_state.card = NULL;
	sd_state.ssr_speed_class = 0;
	sd_state.ssr_uhs_grade = 0;
	sd_state.ssr_valid = 0;
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
 * 与 init 顺序相反：先停窗口工作（阻止新上报）→ 注销 notifier（阻止新卡
 * 指针交换）→ 释放最近关联卡引用 → 拆 sysfs/device/class。上下文：进程
 * 上下文（module_exit）。
 */
static void __exit lciod_sd_exit(void)
{
	struct mmc_card *card;
	unsigned long flags;

	cancel_delayed_work_sync(&sd_work);
	lechao_sd_notifier_unregister(&lciod_sd_nb);

	/* 释放 notifier 持有的最近关联卡引用（get_device 配对释放） */
	spin_lock_irqsave(&sd_state.lock, flags);
	card = sd_state.card;
	WRITE_ONCE(sd_state.card, NULL);
	spin_unlock_irqrestore(&sd_state.lock, flags);
	if (card)
		put_device(&card->dev);

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
