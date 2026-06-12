/*
 * ============================================================
 * device_io.cpp — USB 设备节点底层 IO 操作实现
 * 所属模块: lechao_lciod (HAL 层)
 * 设计目的: 封装对内核驱动 /dev/vendor_lechao_usbd* 的
 *           open/close/ioctl/poll/read 操作。
 * ============================================================
 */
#include "device_io.h"
#include <stdio.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <poll.h>
#include <glob.h>
#include <sys/ioctl.h>
#include <android-base/logging.h>
#include "lechao_log.h"

/* 内核驱动创建的设备节点路径前缀 */
#define DEV_PREFIX "/dev/vendor_lechao_usbd"

/*
 * list_devices — 使用 glob(3) 枚举所有匹配的设备节点
 * 匹配模式: /dev/vendor_lechao_usbd*
 * 返回路径列表（如 ["/dev/vendor_lechao_usbd0"]），无设备时为空
 */
std::vector<std::string> list_devices() {
    glob_t gl;
    char pattern[128];
    snprintf(pattern, sizeof(pattern), "%s*", DEV_PREFIX);
    std::vector<std::string> result;
    if (glob(pattern, 0, NULL, &gl) == 0) {
        for (size_t i = 0; i < gl.gl_pathc; i++)
            result.emplace_back(gl.gl_pathv[i]);
        globfree(&gl);
    }
    return result;
}

/* 设备节点打开重试参数：应对 udev 权限设置延迟等场景 */
#define OPEN_RETRY_MAX    10
#define OPEN_RETRY_DELAY_MS 200

/*
 * open_device — 带重试的设备节点打开
 * 重试 10 次，每次间隔 200ms，应对设备节点短暂不可用的情况。
 * 返回: >= 0 为有效 fd，-1 表示全部重试失败
 */
int open_device(const char *path) {
    int fd = -1;
    for (int i = 0; i < OPEN_RETRY_MAX; i++) {
        fd = open(path, O_RDONLY);
        if (fd >= 0)
            return fd;
        LC_LOGD("open: attempt failed: " << strerror(errno));
        usleep(OPEN_RETRY_DELAY_MS * 1000);
    }
    LOG(ERROR) << "Cannot open " << path << " after " << OPEN_RETRY_MAX
               << " retries: " << strerror(errno);
    return fd;
}

/*
 * close_device — 安全关闭设备节点
 * fd < 0 时跳过（防御性编程）
 */
void close_device(int fd) {
    LC_LOGD("close_device");
    if (fd >= 0)
        close(fd);
}

/*
 * get_stats — 通过 IOC_GET_STATS ioctl 获取统计快照
 * 先 memset 清零输出缓冲区，再 ioctl 读取。
 * 返回: 0 成功，-1 失败（errno 保留）
 */
int get_stats(int fd, struct vendor_lechao_usbd_stats *stats) {
    memset(stats, 0, sizeof(*stats));
    int ret = ioctl(fd, VENDOR_LECHAO_USBD_IOC_GET_STATS, stats);
    if (ret < 0) LC_LOGE("get_stats: ioctl failed: " << strerror(errno));
    return ret;
}

/*
 * reset_state — 通过 IOC_RESET_STATE ioctl 重置内核端计数器
 * 返回: 0 成功，-1 失败
 */
int reset_state(int fd) {
    int ret = ioctl(fd, VENDOR_LECHAO_USBD_IOC_RESET_STATE);
    if (ret < 0) LC_LOGE("reset_state: ioctl failed: " << strerror(errno));
    return ret;
}

/*
 * get_config — 通过 IOC_GET_CONFIG ioctl 获取运行时配置
 * 返回: 0 成功，-1 失败
 */
int get_config(int fd, struct vendor_lechao_usbd_config *config) {
    int ret = ioctl(fd, VENDOR_LECHAO_USBD_IOC_GET_CONFIG, config);
    if (ret < 0) LC_LOGE("get_config: ioctl failed: " << strerror(errno));
    return ret;
}

/*
 * set_config — 通过 IOC_SET_CONFIG ioctl 写入运行时配置
 * 返回: 0 成功，-1 失败
 */
int set_config(int fd, const struct vendor_lechao_usbd_config *config) {
    int ret = ioctl(fd, VENDOR_LECHAO_USBD_IOC_SET_CONFIG, (void *)config);
    if (ret < 0) LC_LOGE("set_config: ioctl failed: " << strerror(errno));
    return ret;
}

/*
 * read_event — 从内核事件环形缓冲区读取最新一条事件
 *
 * 实现流程:
 *   1) poll(fd, POLLIN, timeout_ms) 等待数据就绪
 *   2) 循环 read() 逐条读取，直到缓冲区排空
 *   3) 只保留最后一条事件（最新），中间事件丢弃
 *
 * 丢弃策略：内核事件缓冲区大小有限（32条），用户态消费不及时
 * 时可能积压。保留最新事件确保 HAL 层获取的是最新状态。
 *
 * 返回: 0 成功（至少读到 1 条），-1 超时或读取失败
 */
int read_event(int fd, struct vendor_lechao_usbd_event *event, int timeout_ms) {
    struct pollfd pfd = { .fd = fd, .events = POLLIN };
    int ret = poll(&pfd, 1, timeout_ms);
    if (ret < 0) {
        if (errno != EINTR) LC_LOGW("read_event: poll failed: " << strerror(errno));
        return -1;
    }
    if (ret == 0) {
        errno = ETIMEDOUT;
        return -1;
    }
    if (!(pfd.revents & POLLIN)) {
        errno = EIO;
        return -1;
    }

    struct vendor_lechao_usbd_event tmp;
    ssize_t n;
    int count = 0;
    while ((n = read(fd, &tmp, sizeof(tmp))) == (ssize_t)sizeof(tmp)) {
        *event = tmp;
        count++;
        ret = poll(&pfd, 1, 0);
        if (ret <= 0)
            break;
    }
    if (count > 1)
        LOG(WARNING) << "read_event: drained " << count << " events from kernel, "
                     << (count - 1) << " dropped";
    if (count > 0) return 0;
    errno = EAGAIN;
    return -1;
}
