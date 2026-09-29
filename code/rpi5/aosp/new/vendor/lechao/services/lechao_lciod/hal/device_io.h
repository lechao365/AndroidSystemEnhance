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
 * clamp_read_timeout_ms — readEvent 超时入参钳位（LCD-002 纯函数）
 * @timeout_ms: 上层透传的原始超时值（不可信，binder 公开接口）
 * 返回: <0 钳为 0（非阻塞）；> kMax 裁到 kMax。
 *
 * 背景：timeoutMs 从 IIoService/IIoHal 公开 binder 接口一路透传到
 * poll()，-1 即永久阻塞、INT_MAX 阻塞约 24.8 天；HAL 侧 binder 线程池
 * 仍为 1 线程，单次恶意/失误调用即瘫痪整条监控链路（"活着但不工作"）。
 * HAL 侧为最终防线，daemon 侧为首层防御（R-17 后 daemon binder 池扩至
 * 4 线程，读阻塞不再占死全部 RPC，但长阻塞仍拖累监控分片）。
 */
static const int kMaxReadEventTimeoutMs = 1000;
int clamp_read_timeout_ms(int timeout_ms);

/*
 * read_event — 从内核事件环形缓冲区读取最新一条事件
 * @fd: 设备 fd（需保持打开，用于 poll/read）
 * @event: 输出参数，接收最新事件
 * @timeout_ms: poll 超时时间（毫秒），0 表示非阻塞
 * @dropped: 可选输出参数（可为 NULL），排空时被丢弃的中间事件条数——
 *   R-11 方向 4 丢弃显式化：保留"只取最新"语义，同时把丢弃计数透出，
 *   使事件完整性可见（调用方日志/监控据此感知积压）。
 * 返回: 0 成功（至少读到一条事件），-1 失败或超时
 *
 * 实现细节：先 poll 等待数据就绪，然后循环 read 排空缓冲区，
 * 只保留最后一条（最新）事件。中间事件被丢弃并打印警告，
 * 丢弃条数（count-1）经 @dropped 输出（若提供）。
 */
int read_event(int fd, struct vendor_lechao_usbd_event *event, int timeout_ms,
               uint32_t *dropped = nullptr);

/*
 * list_devices — 枚举系统中所有匹配 /dev/vendor_lechao_usbd* 的设备节点
 * 返回: 设备路径列表，如 ["/dev/vendor_lechao_usbd0"]
 *       使用 glob(3) 模式匹配，无设备时返回空列表
 *
 * R-16 P4 方向 3：仅作冷启动 bootstrap / 兜底（HAL 设备感知主通道
 * 已改为订阅内核 uevent，热路径不再依赖 glob）。
 */
std::vector<std::string> list_devices();

/*
 * open_uevent_socket — 打开 NETLINK_KOBJECT_UEVENT 订阅 socket
 * 返回: >= 0 为 uevent fd，-1 失败（errno 保留）
 *
 * 订阅内核 uevent（R-16 P4 方向 3）：HAL 设备上下线即时感知的增量
 * 通道。绑定 nl_groups=1（KOBJECT_UEVENT），过滤子系统
 * vendor_lechao_usbd 的设备 add/remove 事件，替代周期 glob 全量扫描。
 */
int open_uevent_socket();

/*
 * read_uevent — 读取一条 uevent 消息
 * @fd:   uevent fd（open_uevent_socket 返回）
 * @buf:  接收缓冲区
 * @len:  缓冲区大小（建议 >= 8192，内核 uevent 最大 2048 + 边界余量）
 * 返回: 读取字节数；-1 失败（errno 保留）
 */
ssize_t read_uevent(int fd, char* buf, size_t len);

/*
 * UeventInfo — 解析后的 uevent 事件载荷（方向 3）
 * action:    add/remove 等动作
 * subsystem: uevent 子系统名（过滤键）
 * devname:   /dev/ 下的设备节点名（如 vendor_lechao_usbd0）
 */
struct UeventInfo {
    std::string action;
    std::string subsystem;
    std::string devname;
};

/*
 * parse_uevent — 解析 uevent 消息为结构化载荷（纯解析，无 netlink 依赖）
 * @buf:   uevent 原始消息（NUL 分隔的环境变量序列）
 * @len:   消息长度
 * @out:   输出解析结果
 * 返回: true 解析成功（含 action/subsystem/devname 三字段），false 解析失败
 *
 * 设计为纯函数供 host 单测（DeviceIo_test）覆盖，不依赖真实 netlink。
 */
bool parse_uevent(const char* buf, size_t len, UeventInfo* out);

#endif
