/* SPDX-License-Identifier: GPL-2.0 */
/*
 * ============================================================
 * lechao_hub_notifier.h — 全局 USB hub 链路事件 notifier 接口
 * （R2 链路事件维测与供电归因）
 * ============================================================
 *
 * 【设计目的】
 *   为 Lechao vendor USB 监控驱动（vendor_lechao_usbd / lciod_link）提供
 *   USB hub 链路事件（连接 / 掉线 / 枚举失败 / 端口过流）的全局实时推送
 *   通道。hub.c 在链路关键节点发射事件，lciod_link 模块注册 atomic
 *   notifier_block 订阅，转成 vendor_lechao_usbd_event 环形事件流，供上层
 *   做掉线/过流与 RPi throttled 供电降频的关联归因（power_suspect）。
 *
 * 【通知链类型】
 *   全局原子通知链（atomic_notifier_head，链头 lechao_hub_nh 在 hub.c
 *   定义并 EXPORT_SYMBOL_GPL）。发射点均运行于 hub_event 工作队列（进程
 *   上下文），但回调可能被持有锁调用，故一律为 atomic 链：回调不可睡眠，
 *   禁止 kmalloc/sleep，只能自旋锁 + 赋值/环形推进。
 *
 * 【事件分类与 reason】
 *   - CONNECT：端口连接/枚举成功（vid/pid/duration_ns 有效）
 *   - DISCONNECT：设备断开（reason 为掉线原因分类，见 disconnect_reason）
 *   - ENUM_FAIL：枚举失败（reason 为失败阶段分类，见 enum_stage）
 *   - OVERCURRENT：端口过流（count 为过流计数，与计数并排上报）
 *
 * 【调用上下文】
 *   全部为进程上下文（hub_event / usb_disconnect 锁保护），atomic 链回调
 *   仅做赋值。
 * ============================================================
 */
#ifndef _LECHAO_HUB_NOTIFIER_H
#define _LECHAO_HUB_NOTIFIER_H

#include <linux/notifier.h>
#include <linux/types.h>

/* 全局 USB hub 链路事件分类 */
enum lechao_hub_event_cat {
	LECHAO_HUB_EVENT_CONNECT     = 0, /* 端口连接/枚举成功 */
	LECHAO_HUB_EVENT_DISCONNECT  = 1, /* 设备断开（reason 为掉线原因分类） */
	LECHAO_HUB_EVENT_ENUM_FAIL   = 2, /* 枚举失败（reason 为失败阶段分类） */
	LECHAO_HUB_EVENT_OVERCURRENT = 3, /* 端口过流（count 为过流计数，并排上报） */
};

/* 枚举失败阶段分类（LECHAO_HUB_EVENT_ENUM_FAIL 的 reason） */
enum lechao_hub_enum_stage {
	LECHAO_HUB_ENUM_STAGE_RESET     = 1, /* hub_port_reset 失败 */
	LECHAO_HUB_ENUM_STAGE_ENABLE    = 2, /* hub_enable_device 失败 */
	LECHAO_HUB_ENUM_STAGE_SET_ADDR  = 3, /* SET_ADDRESS 失败 */
	LECHAO_HUB_ENUM_STAGE_GET_DESCR = 4, /* 读设备描述符失败 */
	LECHAO_HUB_ENUM_STAGE_SPEED     = 5, /* 复位速度变化异常 */
};

/* 掉线原因分类（LECHAO_HUB_EVENT_DISCONNECT 的 reason） */
enum lechao_hub_disconnect_reason {
	LECHAO_HUB_DISC_NORMAL      = 0, /* 正常拔出 */
	LECHAO_HUB_DISC_OVERCURRENT = 1, /* 过流后掉线 */
	LECHAO_HUB_DISC_ENUM_FAIL   = 2, /* 枚举失败后掉线 */
	LECHAO_HUB_DISC_ERROR       = 3, /* 传输/复位错误 */
	LECHAO_HUB_DISC_UNKNOWN     = 4, /* 未知原因 */
};

/*
 * struct lechao_hub_notifier_data — hub 链路 notifier 事件载荷
 *
 * 【字段来源】
 *   busnum/port：设备所在 USB 总线号与 hub 端口（1 起）
 *   category/reason：事件分类与细码（见上述枚举）
 *   err：错误码（errno，ENUM_FAIL 时为枚举失败返回值）
 *   vid/pid：设备 VID/PID（le16_to_cpu(udev->descriptor.idVendor/idProduct)）
 *   duration_ns：事件时长（枚举耗时等，无则 0）
 *   count：计数（过流计数 / 枚举耗尽重试次数）
 *
 * 【使用场景】
 *   lciod_link 的 notifier 回调据此打包 vendor_lechao_usbd_event 入环形
 *   缓冲，并累加链路统计（connect/disconnect/enum_fail/overcurrent）。
 *   busnum+port 打包进 opcode 供用户态拆分定位端口。
 */
struct lechao_hub_notifier_data {
	int  busnum;       /* USB 总线号（hdev->bus->busnum） */
	int  port;         /* hub 端口（1 起） */
	u8   category;     /* 事件分类（enum lechao_hub_event_cat） */
	u8   reason;       /* 分类细码：掉线原因 / 枚举失败阶段 */
	int  err;          /* 错误码（errno） */
	u16  vid;          /* 设备 VID（le16_to_cpu(udev->descriptor.idVendor)） */
	u16  pid;          /* 设备 PID */
	u64  duration_ns;  /* 事件时长（枚举耗时等，无则 0） */
	u32  count;        /* 计数（过流计数/枚举耗尽重试次数） */
};

/* 链头在 hub.c 定义并 EXPORT，本头提供操作接口 */
extern struct atomic_notifier_head lechao_hub_nh;

static inline int lechao_hub_notifier_register(struct notifier_block *nb)
{
	return atomic_notifier_chain_register(&lechao_hub_nh, nb);
}

static inline int lechao_hub_notifier_unregister(struct notifier_block *nb)
{
	return atomic_notifier_chain_unregister(&lechao_hub_nh, nb);
}

static inline int lechao_hub_notify(struct lechao_hub_notifier_data *data)
{
	return atomic_notifier_call_chain(&lechao_hub_nh, data->category, data);
}

#endif /* _LECHAO_HUB_NOTIFIER_H */
