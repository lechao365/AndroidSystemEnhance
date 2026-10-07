/* SPDX-License-Identifier: GPL-2.0 */
/*
 * ============================================================
 * uas-notifier.h — UAS 驱动 notifier 接口（R1 UAS 维测）
 * ============================================================
 *
 * 【设计目的】
 *   为 Lechao vendor USB 监控驱动（vendor_lechao_usbd）提供 UAS
 *   （USB Attached SCSI）传输事件的实时推送通道，与 usb-storage 的
 *   notifier 接口（usb.h）同构：UAS 驱动在关键传输节点发射事件，
 *   vendor 驱动注册 atomic_notifier_block 接收并处理。
 *
 * 【事件类型】
 *   复用 usb.h 的 usb_stor_notifier_event 枚举，保证 LcIod 侧处理
 *   逻辑与 BOT 协议统一（协议统一，零 ABI bump）。START/END 语义与
 *   usb-storage 一致：END 携带 duration_ns（usb 侧测得），错误类事件
 *   携带 result/status 码。
 *
 * 【通知链类型】
 *   每个 uas_dev_info 实例内嵌一个 atomic_notifier_head
 *   （devinfo->notifier，见 uas.c），vendor 驱动注册到 per-device 链
 *   上。全部为 atomic notifier chain，回调不可睡眠。
 *
 * 【厂商生命周期链】
 *   uas_register_vendor_notifier 注册到 UAS 驱动的全局 blocking 链，
 *   接收 DEVICE_PROBE / DEVICE_DISCONNECT（与 usb_stor_register_vendor_
 *   notifier 同构，见 uas.c）。
 *
 * 【调用上下文】
 *   全部为 notifier chain（atomic 或 blocking），调用方按 usb.h 约定：
 *   - per-device 链（uas_notifier_call）：中断/自旋锁上下文
 *   - 厂商链（DEVICE_PROBE/DISCONNECT）：进程上下文
 * ============================================================
 */
#ifndef _UAS_NOTIFIER_H
#define _UAS_NOTIFIER_H

#include <linux/notifier.h>
#include "usb.h"

struct uas_dev_info;
struct scsi_cmnd;

/*
 * struct uas_notifier_data — UAS notifier 事件载荷
 *
 * 【字段来源】
 *   devinfo：UAS 设备实例指针，用于 notifier 回调定位设备
 *   srb：     SCSI 命令指针，用于提取传输方向、数据长度
 *   result：  传输层返回码（USB_STOR_XFER_xxx 或 transport result）
 *   status：  URB 状态码（如 -EPIPE/-EOVERFLOW/-ECONNRESET）
 *   data_direction：数据传输方向（1=读, 2=写, 0=无）
 *   data_len：请求的数据长度（scsi_bufflen）
 *   duration_ns：传输耗时（仅在 TRANSPORT_END 时填充）
 *
 * 【使用场景】
 *   vendor_lechao_usbd_uas_handle_event 通过 nd->srb 获取传输信息，
 *   通过 nd->result/status 判断错误类型，通过 nd->duration_ns 计算
 *   latency（uas 侧在命令始末差值测时长，见 lciod_usbd-stats.c）。
 */
struct uas_notifier_data {
	struct uas_dev_info *devinfo;   /* UAS 设备实例指针 */
	struct scsi_cmnd *srb;          /* SCSI 命令指针（可能为 NULL） */
	int result;                     /* 传输层返回码（USB_STOR_XFER_xxx 或 transport result） */
	int status;                     /* URB 状态码（原始 errno） */
	u8 data_direction;              /* 数据方向：1=DMA_FROM_DEVICE(读), 2=DMA_TO_DEVICE(写), 0=无 */
	u32 data_len;                   /* 请求传输的数据长度（字节） */
	u64 duration_ns;                /* 传输耗时（纳秒，仅 TRANSPORT_END 填充） */
};

/*
 * uas_notifier_init — 初始化 uas_dev_info 的原子通知链头
 * @devinfo: UAS 设备实例
 *
 * 在 uas_probe 中调用（scsi_add_host 成功后），将 devinfo->notifier
 * 初始化为空的原子通知链。实现见 uas.c（struct uas_dev_info 定义于
 * uas.c，无法在头文件内内联）。
 */
void uas_notifier_init(struct uas_dev_info *devinfo);

/*
 * uas_notifier_call — 向设备的所有 notifier 回调发射事件
 * @devinfo: UAS 设备实例
 * @event:   事件类型（见 usb_stor_notifier_event 枚举）
 * @data:    事件载荷（struct uas_notifier_data *）
 *
 * 遍历 devinfo->notifier 链上的所有 notifier_block，依次调用其
 * notifier_call。返回值：最后一个回调的返回值。实现见 uas.c。
 */
int uas_notifier_call(struct uas_dev_info *devinfo,
		      unsigned long event, void *data);

/*
 * uas_notifier_register / uas_notifier_unregister — 注册/注销 per-device
 * notifier 到 uas_dev_info 内嵌的通知链
 *
 * uas_dev_info 结构体定义于 uas.c（未在头文件公开），vendor 驱动无法
 * 直接访问 devinfo->notifier，故封装为注册 API。BOT 侧 us_data 公开，
 * 直接用 atomic_notifier_chain_register；UAS 侧必须走本 API。
 * 实现见 uas.c（委托 atomic_notifier_chain_register/unregister）。
 */
int uas_notifier_register(struct uas_dev_info *devinfo, struct notifier_block *nb);
int uas_notifier_unregister(struct uas_dev_info *devinfo, struct notifier_block *nb);

/*
 * uas_register_vendor_notifier / uas_unregister_vendor_notifier —
 * UAS 厂商级生命周期通知链注册/注销
 *
 * 与 usb_stor_register_vendor_notifier 同构：vendor_lechao_usbd 在
 * 模块加载时注册此链，接收 UAS 设备的 DEVICE_PROBE/DISCONNECT 事件。
 * 实现见 uas.c（全局 blocking notifier head + EXPORT_SYMBOL_GPL）。
 */
int uas_register_vendor_notifier(struct notifier_block *nb);
int uas_unregister_vendor_notifier(struct notifier_block *nb);

#endif /* _UAS_NOTIFIER_H */
