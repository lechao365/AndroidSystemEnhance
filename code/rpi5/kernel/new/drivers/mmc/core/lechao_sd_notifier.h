/* SPDX-License-Identifier: GPL-2.0 */
/*
 * ============================================================
 * lechao_sd_notifier.h — SD/MMC 块层健康事件 notifier 接口（A 批一 SD 健康）
 * ============================================================
 *
 * 【设计目的】
 *   为 Lechao vendor SD 健康监控驱动（lciod_sd）提供块层传输事件的实时推送
 *   通道：block.c 在块请求完成路径（mmc_blk_mq_complete_rq）与 card busy
 *   轮询路径（mmc_blk_card_busy）在关键节点发射事件，vendor 驱动注册
 *   atomic_notifier_block 接收并聚合。
 *
 * 【事件类型】
 *   COMPLETE — 一次块请求完成：携带有效字节（bytes）、历时（duration_ns，
 *     由 req->start_time_ns 差值测得）与传输方向（direction）。
 *   STUCK    — card busy 轮询超时或错误位置位：携带卡片指针（card），供
 *     lciod_sd 依据 sd_bus_speed 逐级剥 caps 降档。
 *
 * 【通知链类型】
 *   全局单条 atomic_notifier_head（lechao_sd_nh），在 block.c 定义并
 *   EXPORT_SYMBOL_GPL。COMPLETE 发射点位于 blk-mq 完成路径（可能为软中断/
 *   进程上下文），STUCK 发射点位于 card busy 轮询（进程上下文），统一按
 *   atomic 链语义：回调不可睡眠，仅做赋值/自旋锁内累加。
 * ============================================================
 */
#ifndef _LECHAO_SD_NOTIFIER_H
#define _LECHAO_SD_NOTIFIER_H

#include <linux/notifier.h>
#include <linux/types.h>

struct mmc_card;

/* 全局 SD/MMC 块层健康事件分类 */
enum lechao_sd_event_cat {
	LECHAO_SD_EVENT_COMPLETE = 0, /* 块请求完成（bytes/duration/direction） */
	LECHAO_SD_EVENT_STUCK    = 1, /* card busy 轮询超时/错误（携带 card） */
};

/* 传输方向（与 rq_data_dir(req) 的 READ/WRITE 语义对齐） */
#define LECHAO_SD_DIR_NONE   0
#define LECHAO_SD_DIR_READ   1
#define LECHAO_SD_DIR_WRITE  2

/*
 * struct lechao_sd_notifier_data — SD/MMC 块层 notifier 事件载荷
 *
 * 【字段来源】
 *   card：事件关联卡片（COMPLETE 取 mq->card，STUCK 取轮询卡片）
 *   category：事件分类（见 enum lechao_sd_event_cat）
 *   direction：传输方向（LECHAO_SD_DIR_*，COMPLETE 有效）
 *   bytes：COMPLETE 本次有效传输字节（mqrq->brq.data.bytes_xfered）
 *   duration_ns：COMPLETE 历时（ktime_get_ns() - req->start_time_ns）
 *   err：STUCK 错误码（__mmc_poll_for_busy 或错误位判定后的 errno）
 *
 * 【使用场景】
 *   lciod_sd 的 notifier 回调据此累加 10s 窗字节/请求数；STUCK 时依据
 *   card->sd_bus_speed 调用纯函数逐级剥 caps 降档（见 lciod_sd.h）。
 */
struct lechao_sd_notifier_data {
	struct mmc_card *card;   /* 事件关联卡片 */
	u8   category;           /* 事件分类（enum lechao_sd_event_cat） */
	u8   direction;          /* 传输方向（LECHAO_SD_DIR_*） */
	u32  bytes;              /* COMPLETE：本次有效传输字节 */
	u64  duration_ns;        /* COMPLETE：历时（纳秒） */
	int  err;                /* STUCK：错误码（errno） */
};

/* 链头在 block.c 定义并 EXPORT，本头提供操作接口 */
extern struct atomic_notifier_head lechao_sd_nh;

static inline int lechao_sd_notifier_register(struct notifier_block *nb)
{
	return atomic_notifier_chain_register(&lechao_sd_nh, nb);
}

static inline int lechao_sd_notifier_unregister(struct notifier_block *nb)
{
	return atomic_notifier_chain_unregister(&lechao_sd_nh, nb);
}

static inline int lechao_sd_notify(struct lechao_sd_notifier_data *data)
{
	return atomic_notifier_call_chain(&lechao_sd_nh, data->category, data);
}

#endif /* _LECHAO_SD_NOTIFIER_H */
