/*
 * ============================================================
 * usbd_device.c — USB 设备节点底层 IO 操作实现
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 封装 open/close/ioctl/poll/read 系统调用，
 *           返回 -errno 而非 -1，方便调用者区分错误类型。
 * ============================================================
 */
#include "usbd_device.h"

#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <unistd.h>

/*
 * usbd_open — 打开设备节点
 * 使用 O_RDWR 模式（需要写入配置），失败时返回 -errno。
 */
int usbd_open(const char *path)
{
    int fd = open(path, O_RDWR);
    if (fd < 0)
        return -errno;
    return fd;
}

/*
 * usbd_close — 安全关闭设备节点
 * fd < 0 时跳过。
 */
void usbd_close(int fd)
{
    if (fd >= 0)
        close(fd);
}

/*
 * usbd_get_stats — 通过 IOC_GET_STATS ioctl 获取统计快照
 * 先 memset 清零，再 ioctl 读取，失败返回 -errno。
 */
int usbd_get_stats(int fd, struct vendor_lechao_usbd_stats *stats)
{
    memset(stats, 0, sizeof(*stats));
    if (ioctl(fd, VENDOR_LECHAO_USBD_IOC_GET_STATS, stats) < 0)
        return -errno;
    return 0;
}

/*
 * usbd_reset_state — 通过 IOC_RESET_STATE ioctl 重置计数器
 */
int usbd_reset_state(int fd)
{
    if (ioctl(fd, VENDOR_LECHAO_USBD_IOC_RESET_STATE) < 0)
        return -errno;
    return 0;
}

/*
 * usbd_get_config — 通过 IOC_GET_CONFIG ioctl 获取配置
 */
int usbd_get_config(int fd, struct vendor_lechao_usbd_config *config)
{
    memset(config, 0, sizeof(*config));
    if (ioctl(fd, VENDOR_LECHAO_USBD_IOC_GET_CONFIG, config) < 0)
        return -errno;
    return 0;
}

/*
 * usbd_set_config — 通过 IOC_SET_CONFIG ioctl 写入配置
 */
int usbd_set_config(int fd, const struct vendor_lechao_usbd_config *config)
{
    if (ioctl(fd, VENDOR_LECHAO_USBD_IOC_SET_CONFIG, config) < 0)
        return -errno;
    return 0;
}

/*
 * usbd_read_event — poll + read 单条事件
 *
 * 实现流程:
 *   1) poll(fd, POLLIN, timeout_ms) 等待数据就绪
 *      - poll 返回 < 0: 系统错误 → 返回 -errno
 *      - poll 返回  0: 超时 → 返回 -ETIMEDOUT
 *   2) read() 读取 sizeof(*event) 字节
 *      - read 返回 < 0: 系统错误 → 返回 -errno
 *      - read 返回不足 sizeof: 数据不完整 → 返回 -EIO
 *   3) 返回 0 表示成功
 *
 * 事件消费语义（显式文档化，R-18 P5 方向 2）:
 *   本实现每次调用只读取一条事件，不排空内核环形缓冲区。
 *   这是有意设计而非缺陷：usb-verify 是校验工具，event wait/check
 *   依赖逐条读取来匹配指定类型（fv_wait_for_event 循环读取非匹配
 *   事件直到命中 expect_event_type）。若排空只保留最新，中间被跳过
 *   的非匹配事件会丢失，导致匹配失败。
 *   与 AOSP HAL 版（device_io.cpp read_event 循环排空、只保留最新）
 *   的差异是消费场景不同：HAL 为生产监控取最新状态，本工具为测试
 *   校验逐条断言。两者语义差异为显式设计决策，勿贸然统一。
 */
int usbd_read_event(int fd, struct vendor_lechao_usbd_event *event, int timeout_ms)
{
    struct pollfd pfd;
    pfd.fd = fd;
    pfd.events = POLLIN;

    int pret = poll(&pfd, 1, timeout_ms);
    if (pret < 0)
        return -errno;      /* poll 系统调用失败 */
    if (pret == 0)
        return -ETIMEDOUT;  /* 超时，无数据 */

    ssize_t n = read(fd, event, sizeof(*event));
    if (n < 0)
        return -errno;      /* read 系统调用失败 */
    if ((size_t)n < sizeof(*event))
        return -EIO;        /* 读取不完整（设备异常） */

    return 0;
}
