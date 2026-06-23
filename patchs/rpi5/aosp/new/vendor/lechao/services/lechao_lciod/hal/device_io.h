/*
 * ============================================================
 * device_io.h — USB 设备节点底层 IO 操作封装
 * 所属模块: lechao_lciod (HAL 层)
 * 设计目的: 封装对内核驱动 /dev/vendor_lechao_usbd* 的
 *           open/close/ioctl/poll/read 操作，为上层 hal_service
 *           提供简洁的 C 风格 API。
 *
 * 所有函数返回 0 表示成功，负值表示失败（errno 保留在全局变量中）。
 * 调用者应先 list_devices() 枚举设备路径，再 open_device() 获取 fd，
 * 然后通过 fd 执行 get_stats/reset_state/get_config/set_config/read_event。
 * ============================================================
 */
#ifndef _LECHAO_LCIOD_DEVICE_IO_H
#define _LECHAO_LCIOD_DEVICE_IO_H

#include "vendor_lechao_usbd-ioctl.h"
#include <string>
#include <vector>

/*
 * open_device — 打开 USB 设备节点（带重试）
 * @path:        设备节点路径，如 "/dev/vendor_lechao_usbd0"
 * @max_retries: 最大重试次数（默认 3，传 0 则使用默认值）
 * @delay_ms:    每次重试间隔（默认 50ms）
 * 返回: >= 0 为有效 fd，-1 表示失败
 *
 * 默认 3 × 50ms = 最长 150ms，适合 HAL 前台调用（getStats 等）。
 * readEvent 的持久 fd 懒打开可显式传入更大的重试参数。
 */
int open_device(const char *path, int max_retries = 0, int delay_ms = 0);

/*
 * close_device — 关闭 USB 设备节点
 * @fd: 设备文件描述符，-1 时安全跳过
 */
void close_device(int fd);

/*
 * get_stats — 获取设备传输统计快照
 * @fd: 设备 fd
 * @stats: 输出参数，调用前不需初始化（函数内部 memset 清零）
 * 返回: 0 成功，-1 失败（ioctl 错误）
 */
int get_stats(int fd, struct vendor_lechao_usbd_stats *stats);

/*
 * reset_state — 重置设备统计计数器
 * @fd: 设备 fd
 * 返回: 0 成功，-1 失败
 */
int reset_state(int fd);

/*
 * get_config — 获取设备运行时配置
 * @fd: 设备 fd
 * @config: 输出参数
 * 返回: 0 成功，-1 失败
 */
int get_config(int fd, struct vendor_lechao_usbd_config *config);

/*
 * set_config — 设置设备运行时配置
 * @fd: 设备 fd
 * @config: 输入参数，要写入内核的配置
 * 返回: 0 成功，-1 失败
 */
int set_config(int fd, const struct vendor_lechao_usbd_config *config);

/*
 * read_event — 从内核事件环形缓冲区读取最新一条事件
 * @fd: 设备 fd（需保持打开，用于 poll/read）
 * @event: 输出参数，接收最新事件
 * @timeout_ms: poll 超时时间（毫秒），0 表示非阻塞
 * 返回: 0 成功（至少读到一条事件），-1 失败或超时
 *
 * 实现细节：先 poll 等待数据就绪，然后循环 read 排空缓冲区，
 * 只保留最后一条（最新）事件。中间事件被丢弃并打印警告。
 */
int read_event(int fd, struct vendor_lechao_usbd_event *event, int timeout_ms);

/*
 * list_devices — 枚举系统中所有匹配 /dev/vendor_lechao_usbd* 的设备节点
 * 返回: 设备路径列表，如 ["/dev/vendor_lechao_usbd0"]
 *       使用 glob(3) 模式匹配，无设备时返回空列表
 */
std::vector<std::string> list_devices();

#endif
