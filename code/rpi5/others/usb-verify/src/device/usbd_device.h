/*
 * ============================================================
 * usbd_device.h — USB 设备节点底层 IO 操作封装
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 封装对内核驱动 /dev/vendor_lechao_usbd* 的
 *           open/close/ioctl/poll/read 操作，为上层 CLI 提供
 *           简洁的 C 风格 API。
 *
 * 与 AOSP 端 device_io.h 的关系:
 *   功能完全等价，但返回值约定不同：
 *   - AOSP 版: 成功返回 0，失败返回 -1（errno 在全局变量中）
 *   - 本版本: 成功返回 0，失败返回 -errno（负的错误码）
 *
 * 所有函数线程安全（无共享状态）。
 * ============================================================
 */
#ifndef USBD_DEVICE_H
#define USBD_DEVICE_H

#include "fv_ioctl_compat.h"

/*
 * usbd_open — 打开 USB 设备节点
 * @path: 设备节点路径，如 "/dev/vendor_lechao_usbd0"
 * 返回: >= 0 为有效 fd，负值为 -errno
 * 使用 O_RDWR 模式打开（需要 ioctl 写入配置）
 */
int usbd_open(const char *path);

/*
 * usbd_close — 关闭 USB 设备节点
 * @fd: 设备 fd，< 0 时安全跳过
 */
void usbd_close(int fd);

/*
 * usbd_get_stats — 获取设备传输统计快照
 * @stats: 输出参数，调用前不需初始化（函数内部 memset 清零）
 * 返回: 0 成功，负值为 -errno
 */
int usbd_get_stats(int fd, struct vendor_lechao_usbd_stats *stats);

/*
 * usbd_reset_state — 重置设备统计计数器
 * 返回: 0 成功，负值为 -errno
 */
int usbd_reset_state(int fd);

/*
 * usbd_get_config — 获取设备运行时配置
 * @config: 输出参数
 * 返回: 0 成功，负值为 -errno
 */
int usbd_get_config(int fd, struct vendor_lechao_usbd_config *config);

/*
 * usbd_set_config — 设置设备运行时配置
 * @config: 输入参数
 * 返回: 0 成功，负值为 -errno
 */
int usbd_set_config(int fd, const struct vendor_lechao_usbd_config *config);

/*
 * usbd_read_event — 从内核事件环形缓冲区读取一条事件
 * @event: 输出参数，接收事件数据
 * @timeout_ms: poll 超时时间（毫秒）
 * 返回: 0 成功，-ETIMEDOUT 超时，其他负值为 -errno
 *
 * 实现流程: poll() 等待 → read() 读取单条事件
 * 注意: 与 AOSP 版不同，此版本只读取一条（不排空缓冲区）
 */
int usbd_read_event(int fd, struct vendor_lechao_usbd_event *event, int timeout_ms);

#endif
