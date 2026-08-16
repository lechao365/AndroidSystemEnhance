#ifndef RAW_GADGET_H
#define RAW_GADGET_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

/*
 * Raw Gadget 封装：完全接管 UDC，实现 USB MSD Device
 *
 * 完整生命周期：
 *   raw_gadget_open()  → INIT + RUN
 *   raw_gadget_enumerate() → EVENT_FETCH 枚举循环 + EP_ENABLE
 *   raw_gadget_ep_write/read → BULK 数据传输
 *   raw_gadget_stall/clear_halt → 端点控制
 *   raw_gadget_vbus_draw → VBUS 供电控制
 *   raw_gadget_close() → 释放
 */

struct raw_gadget;

/* 打开 /dev/raw-gadget 并执行 INIT + RUN
 * udc_name: UDC 驱动/设备名（Pi Zero 2W: "20980000.usb"）
 * 返回 NULL 失败 */
struct raw_gadget *raw_gadget_open(const char *udc_name);

/* 关闭并释放 */
void raw_gadget_close(struct raw_gadget *rg);

/* 枚举循环：处理 EP0 控制请求直到 SET_CONFIGURATION
 * 内部完成 EP_ENABLE 并保存端点 handle
 * 返回 0 成功，-1 失败 */
int raw_gadget_enumerate(struct raw_gadget *rg);

/* ===== BULK 端点操作（使用 EP_ENABLE 返回的 handle） ===== */

/* 向 BULK IN 端点写数据（Device → Host） */
int raw_gadget_ep_write(struct raw_gadget *rg, const void *buf, size_t len);

/* 从 BULK OUT 端点读数据（Host → Device） */
int raw_gadget_ep_read(struct raw_gadget *rg, void *buf, size_t len);

/* ===== 端点控制 ===== */

/* STALL 指定端点（ep_addr: 0x81=IN 或 0x02=OUT） */
int raw_gadget_stall_ep(struct raw_gadget *rg, uint8_t ep_addr);

/* CLEAR HALT 指定端点 */
int raw_gadget_clear_halt_ep(struct raw_gadget *rg, uint8_t ep_addr);

/* ===== VBUS / 电源控制 ===== */

/* 设置 VBUS 电流（mA），0 = 拉低模拟断开 */
int raw_gadget_vbus_draw(struct raw_gadget *rg, int mA);

/* ===== 状态查询 ===== */

/* 是否已完成枚举 */
bool raw_gadget_is_enumerated(const struct raw_gadget *rg);

/* 获取原始 fd */
int raw_gadget_get_fd(const struct raw_gadget *rg);

#endif /* RAW_GADGET_H */
