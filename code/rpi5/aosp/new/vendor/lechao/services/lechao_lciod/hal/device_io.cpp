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
#include <ctime>
#include <sys/ioctl.h>
#include <sys/socket.h>
#include <linux/netlink.h>
#include <android-base/logging.h>
#include "lechao_log.h"
#include "minor_utils.h"

/* 内核驱动创建的设备节点路径前缀（LCD-014：统一引用 minor_utils.h
 * 的 kUsbdDevPrefix，消除与 glob 模式/解析前缀的双份定义漂移风险） */
#define DEV_PREFIX lechao::lciod::kUsbdDevPrefix

/*
 * list_devices — 使用 glob(3) 枚举所有匹配的设备节点
 * 匹配模式: /dev/vendor_lechao_usbd*
 * 返回路径列表（如 ["/dev/vendor_lechao_usbd0"]），无设备时为空
 *
 * R-16 P4 方向 3：仅作冷启动 bootstrap / 兜底（主通道已改为订阅内核
 * uevent，热路径不再 glob）。
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

/*
 * open_uevent_socket — 打开 NETLINK_KOBJECT_UEVENT 订阅 socket
 *
 * R-16 P4 方向 3：HAL 设备上下线即时感知的增量通道。绑定 nl_groups=1
 * 接收内核广播的 kobject uevent，过滤 SUBSYSTEM=vendor_lechao_usbd 的
 * add/remove 事件，替代周期 glob 全量扫描（热路径去 glob、插入感知延迟
 * 从 ~10s 降至事件到达即感知）。SOCK_CLOEXEC 防 fd 泄漏到子进程。
 */
int open_uevent_socket() {
    int fd = socket(AF_NETLINK, SOCK_DGRAM | SOCK_CLOEXEC,
                    NETLINK_KOBJECT_UEVENT);
    if (fd < 0) {
        int saved = errno;
        LC_LOGE("open_uevent_socket: socket() failed: " << strerror(saved));
        errno = saved;
        return -1;
    }

    struct sockaddr_nl addr;
    memset(&addr, 0, sizeof(addr));
    addr.nl_family = AF_NETLINK;
    addr.nl_pid = static_cast<uint32_t>(getpid());  /* 本进程专属端口 */
    addr.nl_groups = 1;                             /* KOBJECT_UEVENT 多播组 */
    if (bind(fd, reinterpret_cast<struct sockaddr*>(&addr), sizeof(addr)) < 0) {
        int saved = errno;
        LC_LOGE("open_uevent_socket: bind() failed: " << strerror(saved));
        close(fd);
        errno = saved;
        return -1;
    }

    /* R-16 P4 方向 4 修复：netlink socket 必须设 O_NONBLOCK——on_uevent_readable
     * 循环 recv 读至 EAGAIN 作为排空终止条件，若 fd 阻塞则首条 uevent 读走后
     * 再循环 recv 会阻塞等待下一条，HAL 主线程卡在 recv，binder fd 饿死
     * （事务无人处理 → 服务从 servicemanager 注销 → daemon 卡死在 binder
     * ioctl，热插拔后 event_drop 飙升）。设备热插拔触发 uevent 风暴时该
     * 路径必然命中。 */
    int flags = fcntl(fd, F_GETFL, 0);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) {
        int saved = errno;
        LC_LOGE("open_uevent_socket: fcntl O_NONBLOCK failed: " << strerror(saved));
        close(fd);
        errno = saved;
        return -1;
    }
    return fd;
}

/*
 * read_uevent — 读取一条 uevent 消息
 * 返回: 读取字节数；-1 失败（errno 保留）
 */
ssize_t read_uevent(int fd, char* buf, size_t len) {
    ssize_t n = recv(fd, buf, len, 0);
    if (n < 0) {
        int saved = errno;
        /* EAGAIN/EINTR 是"暂无消息"的正常语义，不视为故障 */
        if (saved != EAGAIN && saved != EINTR)
            LC_LOGW("read_uevent: recv failed: " << strerror(saved));
        errno = saved;
        return -1;
    }
    return n;
}

/*
 * parse_uevent — 解析 uevent 消息为结构化载荷（纯解析，供 host 单测）
 *
 * uevent 线材格式：首行 "ACTION@DEVPATH"（如 add@/devices/...），随后
 * 为 "KEY=VALUE" 形式的环境变量序列，行间 NUL 分隔，末尾双 NUL 结束。
 * 本函数提取 ACTION（首行 @ 前缀）与 SUBSYSTEM/DEVNAME 环境变量。
 * 防御：非 NUL 分隔/缺字段/越界一律返回 false（CXX-003）。
 */
bool parse_uevent(const char* buf, size_t len, UeventInfo* out) {
    if (!buf || !out || len == 0)
        return false;

    std::string action;
    std::string subsystem;
    std::string devname;

    /* 首行：ACTION@DEVPATH（@ 前缀即动作，如 "add"） */
    size_t i = 0;
    size_t at = std::string::npos;
    for (; i < len; i++) {
        if (buf[i] == '\0')
            break;
        if (buf[i] == '@' && at == std::string::npos)
            at = i;
    }
    if (i == 0 || i >= len || at == std::string::npos || at == 0)
        return false;
    action.assign(buf, at);

    /* 环境变量序列：KEY=VALUE，NUL 分隔，遇空串结束 */
    while (i < len) {
        /* 跳过行首（首行已消费）；后续行从 i+1 开始 */
        i++;
        if (i >= len)
            break;
        size_t j = i;
        while (j < len && buf[j] != '\0')
            j++;
        if (j == i)  /* 双 NUL：消息结束 */
            break;
        std::string kv(buf + i, j - i);
        i = j;
        size_t eq = kv.find('=');
        if (eq == std::string::npos)
            continue;
        std::string key = kv.substr(0, eq);
        std::string val = kv.substr(eq + 1);
        if (key == "SUBSYSTEM")
            subsystem = val;
        else if (key == "DEVNAME")
            devname = val;
    }

    if (action.empty() || subsystem.empty() || devname.empty())
        return false;

    out->action = action;
    out->subsystem = subsystem;
    out->devname = devname;
    return true;
}

/*
 * 设备节点打开重试参数：默认 3 次 × 50ms（总最长 150ms）
 *
 * 历史值为 10 × 200ms（2s），在 HAL 单线程模型下会严重阻塞
 * getStats/listDevices 等前台调用。新默认值将阻塞时间压到 150ms，
 * 调用方可在需要更长等待的场景（如 readEvent 的持久 fd 懒打开）
 * 显式传入更大的 max_retries / delay_ms。
 */
#define OPEN_RETRY_MAX_DEFAULT    3
#define OPEN_RETRY_DELAY_MS_DEFAULT 50

/*
 * open_device — 带重试的设备节点打开
 * @path:      设备节点路径
 * @max_retries: 最大重试次数（LCD-013：<=0 一律取默认 3 次，与
 *               device_io.h "传 0 则使用默认值" 注释对齐，不存在
 *               "不重试" 语义）
 * @delay_ms:  每次重试间隔（<=0 取默认 50ms）
 *
 * 返回: >= 0 为有效 fd，-1 表示全部重试失败（errno 保留最后一次错误）
 */
int open_device(const char *path, int max_retries, int delay_ms) {
    if (max_retries <= 0) max_retries = OPEN_RETRY_MAX_DEFAULT;
    if (delay_ms  <= 0) delay_ms  = OPEN_RETRY_DELAY_MS_DEFAULT;
    int fd = -1;
    int last_errno = 0;
    for (int i = 0; i < max_retries; i++) {
        fd = open(path, O_RDONLY);
        if (fd >= 0)
            return fd;
        /* LCD-021：每次失败先把 errno 承接进 last_errno——LC_LOGD 展开会调
         * debugVerbose()/strerror/流操作，污染 errno；循环外据 last_errno
         * 打日志并还原，勿读被污染的当前 errno */
        last_errno = errno;
        LC_LOGD("open: attempt " << (i + 1) << "/" << max_retries << " failed: " << strerror(last_errno));
        if (i + 1 < max_retries && delay_ms > 0)
            usleep(delay_ms * 1000);
    }
    LC_LOGE("Cannot open " << path << " after " << max_retries
               << " retries: " << strerror(last_errno));
    errno = last_errno;  // 还原 errno（strerror 可能改），return 后上层取到正确错误码
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
    if (ret < 0) {
        int saved = errno;  // strerror 可能改 errno，先存并 return 前还原
        LC_LOGE("get_stats: ioctl failed: " << strerror(saved));
        errno = saved;
    }
    return ret;
}

/*
 * reset_state — 通过 IOC_RESET_STATE ioctl 重置内核端计数器
 * 返回: 0 成功，-1 失败
 */
int reset_state(int fd) {
    int ret = ioctl(fd, VENDOR_LECHAO_USBD_IOC_RESET_STATE);
    if (ret < 0) {
        int saved = errno;  // strerror 可能改 errno，先存并 return 前还原
        LC_LOGE("reset_state: ioctl failed: " << strerror(saved));
        errno = saved;
    }
    return ret;
}

/*
 * get_config — 通过 IOC_GET_CONFIG ioctl 获取运行时配置
 * 返回: 0 成功，-1 失败
 */
int get_config(int fd, struct vendor_lechao_usbd_config *config) {
    int ret = ioctl(fd, VENDOR_LECHAO_USBD_IOC_GET_CONFIG, config);
    if (ret < 0) {
        int saved = errno;  // strerror 可能改 errno，先存并 return 前还原
        LC_LOGE("get_config: ioctl failed: " << strerror(saved));
        errno = saved;
    }
    return ret;
}

/*
 * set_config — 通过 IOC_SET_CONFIG ioctl 写入运行时配置
 * 返回: 0 成功，-1 失败
 */
int set_config(int fd, const struct vendor_lechao_usbd_config *config) {
    int ret = ioctl(fd, VENDOR_LECHAO_USBD_IOC_SET_CONFIG, (void *)config);
    if (ret < 0) {
        int saved = errno;  // strerror 可能改 errno，先存并 return 前还原
        LC_LOGE("set_config: ioctl failed: " << strerror(saved));
        errno = saved;
    }
    return ret;
}

/*
 * clamp_read_timeout_ms — readEvent 超时入参钳位（LCD-002，声明见 .h）
 * 负值钳 0（非阻塞），超上限裁到 kMaxReadEventTimeoutMs
 */
int clamp_read_timeout_ms(int timeout_ms) {
    if (timeout_ms < 0)
        return 0;
    if (timeout_ms > kMaxReadEventTimeoutMs)
        return kMaxReadEventTimeoutMs;
    return timeout_ms;
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
 * R-11 方向 4：丢弃显式化——被丢弃的中间事件条数经 @dropped 透出
 * （可为 NULL），调用方日志/监控据此感知积压，事件完整性可见。
 *
 * 返回: 0 成功（至少读到 1 条），-1 超时或读取失败
 */
int read_event(int fd, struct vendor_lechao_usbd_event *event, int timeout_ms, uint32_t *dropped)
{
    struct pollfd pfd = { .fd = fd, .events = POLLIN };
    /* LCD-016：poll EINTR 重试而非报错——信号打断是瞬时噪声，原实现
     * 直接 -1 会被上层白名单外判为真实错误层层上抛，monitor 50ms 轮询
     * 在任何信号到达时误报一条 readEvent failed。
     * 修复（方向 6）：重试按剩余时间扣减——原实现每次都用原始
     * timeout_ms 重新起算，多次 EINTR 叠加会让实际等待突破 clamp 上限
     * kMaxReadEventTimeoutMs，阻塞 monitor 周期。以单调时钟累计已耗时，
     * 剩余 = clamp 后 timeout 扣减，扣完为 0 不再延长。 */
    struct timespec t_start, t_now;
    int remaining = timeout_ms;
    int ret;
    clock_gettime(CLOCK_MONOTONIC, &t_start);
    do {
        ret = poll(&pfd, 1, remaining);
        if (ret < 0 && errno == EINTR) {
            clock_gettime(CLOCK_MONOTONIC, &t_now);
            long long elapsed_ms =
                (t_now.tv_sec - t_start.tv_sec) * 1000LL +
                (t_now.tv_nsec - t_start.tv_nsec) / 1000000LL;
            remaining = timeout_ms - (int)elapsed_ms;
            if (remaining < 0)
                remaining = 0;
        }
    } while (ret < 0 && errno == EINTR);
    if (ret < 0) {
        /* LCD-021：poll 失败先存 saved 再打日志还原——LC_LOGW 无条件展开，
         * strerror/流操作必执行，污染 errno；调用方 readEvent 靠 errno 区分
         * 暂无事件（ETIMEDOUT/EAGAIN）与真故障，据 ENODEV/EIO 决定关 fd */
        int saved = errno;
        LC_LOGW("read_event: poll failed: " << strerror(saved));
        errno = saved;
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
    int saved_errno = 0;
    while ((n = read(fd, &tmp, sizeof(tmp))) == (ssize_t)sizeof(tmp)) {
        *event = tmp;
        count++;
        ret = poll(&pfd, 1, 0);
        if (ret <= 0)
            break;
    }
    if (n < 0)
        saved_errno = errno;
    else if (n == 0 || n < (ssize_t)sizeof(tmp))
        // read 返回 0（EOF，设备关闭）或短读（记录不完整）属异常，置 EIO；
        // 原实现落 EAGAIN 会把设备关闭伪装成"暂无事件"（上层白名单内判绿）
        saved_errno = EIO;

    if (count > 1)
    {
        /* R-11 方向 4：丢弃条数透出（非空指针时），日志保留供现场追溯 */
        if (dropped)
            *dropped = (uint32_t)(count - 1);
        LC_LOGW("read_event: drained " << count << " events from kernel, "
                     << (count - 1) << " dropped");
    }
    else if (dropped)
    {
        /* 单条/无丢弃路径也确定性置 0，调用方避免读到残留值 */
        *dropped = 0;
    }
    if (count > 0) return 0;

    errno = saved_errno ? saved_errno : EAGAIN;
    return -1;
}
