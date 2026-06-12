#ifndef RAW_GADGET_H
#define RAW_GADGET_H

#include <stdint.h>
#include <stddef.h>

/* Raw Gadget 封装：直接控制 /dev/raw-gadget 字符设备
 * - 不依赖内核 Gadget 框架（g_mass_storage / ConfigFS）
 * - 通过 ioctl 操纵 USB 描述符、端点、VBUS
 * - 12 类故障的底层操作均封装在此模块
 */
struct raw_gadget;

struct raw_gadget *raw_gadget_open(const char *udc_path);
void raw_gadget_close(struct raw_gadget *rg);

/* 设备配置：设置 VID/PID/厂商/产品字符串 */
int raw_gadget_configure(struct raw_gadget *rg,
                         uint16_t vid, uint16_t pid,
                         const char *vendor, const char *product);

/* 端点 STALL 注入：使 Host 在下一次访问该端点时收到 STALL PID */
int raw_gadget_stall_now(struct raw_gadget *rg, int ep);

/* 端点数据读写：用于 CBW 接收与 CSW/Data 发送 */
int raw_gadget_ep_read(struct raw_gadget *rg, int ep,
                       void *buf, size_t len, int timeout_ms);
int raw_gadget_ep_write(struct raw_gadget *rg, int ep,
                        const void *buf, size_t len);

/* VBUS 控制：模拟热插拔（热插拔故障） */
int raw_gadget_vbus_draw(struct raw_gadget *rg, int mA);

/* Gadget 启动/停止：用于物理断开（disconnect 故障） */
int raw_gadget_run_start(struct raw_gadget *rg);
int raw_gadget_run_stop(struct raw_gadget *rg);

/* Bulk ERR PID 发送：通过 stall-then-clear 模拟 ABORT 序列 */
int raw_gadget_send_bulk_err(struct raw_gadget *rg, int ep);

/* 事件获取：枚举完成、配置选择等（用于等待 Host 完成配置） */
int raw_gadget_event_fetch(struct raw_gadget *rg, int timeout_ms);

int raw_gadget_get_fd(struct raw_gadget *rg);

#endif
