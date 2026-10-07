/* ============================================================
 * lciod_link.h — LcIod 全局链路节点内部头文件
 *
 * 【文件用途】
 *   定义全局 USB hub 链路节点（lciod_link）的对外接口、掉线原因/
 *   枚举失败阶段分类枚举（与 lechao_hub_notifier.h 对齐）以及把
 *   lechao_hub_notifier_data 打包进 vendor_lechao_usbd_event 的
 *   静态辅助函数。本文件仅供内核模块内部使用（不直接与用户态
 *   共享 ABI，用户态 ABI 见 lciod_usbd-ioctl.h 的 v4 扩展）。
 *
 * 【所属模块】
 *   Lechao vendor USB 链路事件维测子系统（LcIod / R2 方向 3）
 *
 * 【文件关系】
 *   - lciod_link.c：实现全局链路节点（char 设备 + notifier 回调）
 *   - lciod_usbd.c：主模块，module_init/module_exit 调用 lciod_link_init/
 *                   lciod_link_exit 接线
 *   - lechao_hub_notifier.h：全局 hub 链路事件原子 notifier 链头
 *     （drivers/usb/core，R2 方向 1 产物），本文件依赖其枚举与接口签名
 *   - lciod_usbd-ioctl.h：v4 共享 ABI（LINK_* 事件类型 + link_stats +
 *                         GET_LINK_STATS）
 * ============================================================ */

#ifndef _LCIOD_LINK_H
#define _LCIOD_LINK_H

#include <linux/types.h>
#include <linux/ktime.h>
#include <linux/string.h>
#include <linux/notifier.h>
#include "lechao_hub_notifier.h"
#include "lciod_usbd-ioctl.h"

/*
 * 掉线原因分类（值对齐 lechao_hub_disconnect_reason）
 *
 * 供用户态解析 LINK_DISCONNECT 事件（vendor_lechao_usbd_event.event_value）
 * 时使用；与 lechao_hub_notifier.h 的 lechao_hub_disconnect_reason 数值
 * 完全一致，禁止单侧改值。
 */
enum lciod_link_disconnect_reason {
	LCIOD_LINK_DISC_NORMAL      = 0, /* 正常拔出 */
	LCIOD_LINK_DISC_OVERCURRENT = 1, /* 过流后掉线 */
	LCIOD_LINK_DISC_ENUM_FAIL   = 2, /* 枚举失败后掉线 */
	LCIOD_LINK_DISC_ERROR       = 3, /* 传输/复位错误 */
	LCIOD_LINK_DISC_UNKNOWN     = 4, /* 未知原因 */
};

/*
 * 枚举失败阶段分类（值对齐 lechao_hub_enum_stage）
 *
 * 供用户态解析 LINK_DISCONNECT 事件（event_value）中携带的枚举失败
 * 阶段（ENUM_FAIL 分类）时使用；与 lechao_hub_notifier.h 的
 * lechao_hub_enum_stage 数值完全一致，禁止单侧改值。
 */
enum lciod_link_enum_stage {
	LCIOD_LINK_ENUM_STAGE_RESET     = 1, /* hub_port_reset 失败 */
	LCIOD_LINK_ENUM_STAGE_ENABLE    = 2, /* hub_enable_device 失败 */
	LCIOD_LINK_ENUM_STAGE_SET_ADDR  = 3, /* SET_ADDRESS 失败 */
	LCIOD_LINK_ENUM_STAGE_GET_DESCR = 4, /* 读设备描述符失败 */
	LCIOD_LINK_ENUM_STAGE_SPEED     = 5, /* 复位速度变化异常 */
};

/*
 * extern notifier_block — 注册到 lechao_hub_notifier_register 的通知块
 *
 * lciod_link.c 定义并初始化（notifier_call = lciod_link_notifier）。
 * 由 lciod_link_init() 注册、lciod_link_exit() 注销。
 */
extern struct notifier_block lciod_link_nb;

/*
 * lciod_link_event_type_from_cat — hub 事件分类 → LcIod 事件类型映射
 * @category: lechao_hub_event_cat 分类值（0~3）
 *
 * 映射规则（ABI v4）：
 *   CONNECT    (0) → VENDOR_LECHAO_USBD_EVENT_LINK_CONNECT (7)
 *   DISCONNECT (1) → VENDOR_LECHAO_USBD_EVENT_LINK_DISCONNECT (8)
 *   ENUM_FAIL  (2) → VENDOR_LECHAO_USBD_EVENT_LINK_DISCONNECT (8)
 *                    （枚举失败即链路未建立/不可用，视同掉线事件；
 *                    阶段细码经 event_value 透传，link_stats 单独
 *                    累计 enum_fail_count，用户态可据此区分）
 *   OVERCURRENT(3) → VENDOR_LECHAO_USBD_EVENT_LINK_OVERCURRENT (9)
 */
static inline u32 lciod_link_event_type_from_cat(u8 category)
{
	switch (category) {
	case LECHAO_HUB_EVENT_CONNECT:
		return VENDOR_LECHAO_USBD_EVENT_LINK_CONNECT;
	case LECHAO_HUB_EVENT_OVERCURRENT:
		return VENDOR_LECHAO_USBD_EVENT_LINK_OVERCURRENT;
	case LECHAO_HUB_EVENT_DISCONNECT:
	case LECHAO_HUB_EVENT_ENUM_FAIL:
	default:
		return VENDOR_LECHAO_USBD_EVENT_LINK_DISCONNECT;
	}
}

/*
 * lciod_link_pack_event — 把 hub notifier 载荷打包成 LcIod 事件记录
 * @nd:  lechao_hub_notifier_data（hub.c 经 atomic notifier 发射）
 * @ev:  出参，填充后的 vendor_lechao_usbd_event（调用方提供存储）
 *
 * 字段映射（ABI v4，契约对齐）：
 *   event_type  = lciod_link_event_type_from_cat(category)
 *   event_value = reason（掉线原因/枚举失败阶段细码）
 *   status      = err（errno）
 *   data_direction = VENDOR_LECHAO_USBD_DIR_NONE
 *   opcode      = (busnum & 0xffff) << 16 | (port & 0xffff)（用户态拆分）
 *   lba         = duration_ns（事件时长）
 *   bytes       = count（过流计数/枚举耗尽重试次数）
 *   timestamp_ns = ktime_get_ns()（mono）；wall_time_ns = ktime_get_real_ns()
 *   valid       = 1
 * 无内核 API 依赖，可在 notifier 回调（atomic 上下文）直接内联调用。
 */
static inline void lciod_link_pack_event(
	const struct lechao_hub_notifier_data *nd,
	struct vendor_lechao_usbd_event *ev)
{
	memset(ev, 0, sizeof(*ev));
	ev->timestamp_ns = ktime_get_ns();
	ev->wall_time_ns = ktime_get_real_ns();
	ev->event_type = lciod_link_event_type_from_cat(nd->category);
	ev->event_value = nd->reason;
	ev->status = nd->err;
	ev->data_direction = VENDOR_LECHAO_USBD_DIR_NONE;
	ev->opcode = ((u32)(nd->busnum & 0xffff) << 16) |
		     (u32)(nd->port & 0xffff);
	ev->lba = nd->duration_ns;
	ev->bytes = nd->count;
	ev->valid = 1;
}

/*
 * lciod_link_init — 初始化全局链路节点
 *
 * 依次：初始化全局状态（锁/wq/环/stats）→ alloc_chrdev_region →
 * cdev_init/cdev_add → class_create（devnode 0600）→ device_create →
 * lechao_hub_notifier_register。任一步失败逐级回滚已建资源并返回负
 * errno。调用上下文：进程上下文（module_init）。
 */
int lciod_link_init(void);

/*
 * lciod_link_exit — 销毁全局链路节点
 *
 * 与 init 顺序相反：先注销 hub notifier（阻止新事件）→ 置 event_shutdown
 * 并唤醒等待者（read 返回 EOF）→ 依次销毁 device/class/cdev/chrdev region。
 * 调用上下文：进程上下文（module_exit）。
 */
void lciod_link_exit(void);

#endif /* _LCIOD_LINK_H */
