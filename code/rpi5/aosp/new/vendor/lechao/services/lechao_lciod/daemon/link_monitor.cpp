/*
 * ============================================================
 * link_monitor.cpp — 全局链路事件监控实现（R2 方向 5+6）
 * 所属模块: lechao_lciod (system 分区 daemon)
 * 设计目的: 消费内核全局链路节点 /dev/vendor_lechao_usbd_link 的事件流
 *           （LINK_CONNECT/LINK_DISCONNECT/LINK_OVERCURRENT，lciod_link
 *           订阅 hub notifier 转出），按 busnum/port 聚合输出 JSON 行；
 *           周期采集 RPi get_throttled 供电降频值，规则一（纯函数
 *           ApplyPowerSuspectRule）：掉线/过流事件 5s 窗口内 throttled
 *           非零 → 该事件标 power_suspect（供电不足归因）。
 *
 * 线程模型: LinkMonitorRun() 作为 detach 线程由 IoServiceImpl::start()
 *           启动，独立于既有 4 分片 per-device 监控（start_monitor），
 *           互不影响；生命周期随进程（非 oneshot，init 自动重启）。
 *
 * 事件字段语义（与内核 lciod_link 打包契约一致）:
 *   opcode  : (busnum&0xffff)<<16 | (port&0xffff)，用户态拆分
 *   event_type: 7=LINK_CONNECT 8=LINK_DISCONNECT 9=LINK_OVERCURRENT
 *   event_value: 掉线原因/枚举失败阶段分类（reason）
 *   status  : err（内核 errno）
 *   lba     : duration_ns（事件时长）
 *   bytes   : count（过流计数/枚举耗尽重试次数）
 *   timestamp_ns: ktime_get_ns() = CLOCK_MONOTONIC，与用户态
 *                 clock_gettime(CLOCK_MONOTONIC) 同一时间基直接比较
 *
 * 字节序契约（CXX-001/LCD-004）: 事件经 read 裸拷，结构体由共享头
 *   vendor_lechao_usbd-ioctl.h 维护（含大端编译守卫），同机同序契约；
 *   opcode 位拆分按主机序与内核打包一致。
 * ============================================================
 */
#include "link_monitor.h"

#include <sys/epoll.h>
#include <sys/ioctl.h>
#include <fcntl.h>
#include <unistd.h>
#include <glob.h>
#include <ctime>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <algorithm>
#include <chrono>
#include <string>
#include <thread>
#include <unordered_map>

#include "vendor_lechao_usbd-ioctl.h"
#include "lechao_log.h"

#define LOG_TAG "lechao_lciod"
#include <log/log.h>

namespace lechao {
namespace lciod {

/* 链路事件节点路径（内核 lciod_link.c misc_register 创建，devnode 0600） */
static const char* kLinkDevPath = "/dev/vendor_lechao_usbd_link";

/* EPOLLHUP/EPOLLERR/读致命错误后的重开退避：1s 起指数翻倍，封顶 30s */
static constexpr int kInitBackoffMs = 1000;
static constexpr int kMaxBackoffMs = 30000;

/* throttled 周期采样间隔（1s，RPi 供电降频 sysfs） */
static constexpr uint64_t kThrottlePeriodNs = 1000000000ULL;

/*
 * 事件日志专属 tag + 可配置级别（与 service.cpp EVENT_ALOG 同款语义）：
 * 链路事件经 lechao_lciod_event 专属 tag 输出，默认 INFO 生产可见，
 * debugVerbose 开启时提升 DEBUG 显示更多细节。
 */
static inline int EventLogLevel()
{
    return ::lechao::debugVerbose() ? ANDROID_LOG_DEBUG : ANDROID_LOG_INFO;
}
#define EVENT_ALOG(...) __android_log_print(EventLogLevel(), "lechao_lciod_event", __VA_ARGS__)

/* --- 规则一：供电不足归因（纯函数，声明见 link_monitor.h） --- */

bool ApplyPowerSuspectRule(uint64_t event_mono_ns, uint64_t now_mono_ns,
                           uint64_t throttled, uint32_t event_type)
{
    /* 仅链路掉线/过流事件参与供电归因（CXX-003：事件类型白名单，非链路
     * 事件如 STALL 一律不标） */
    if (event_type != VENDOR_LECHAO_USBD_EVENT_LINK_DISCONNECT &&
        event_type != VENDOR_LECHAO_USBD_EVENT_LINK_OVERCURRENT)
        return false;
    /* throttled 采样为 0（供电正常）→ 不标 */
    if (throttled == 0)
        return false;
    /* 事件时间戳晚于当前（时钟异常/回拨）→ 按窗口外处理；先判大小再相减
     * 防无符号回绕（CXX-002 整数边界防御） */
    if (now_mono_ns < event_mono_ns)
        return false;
    /* 窗口判定：now - event <= 5s（恰在 5s 边界计窗口内） */
    if (now_mono_ns - event_mono_ns > kPowerSuspectWindowNs)
        return false;
    return true;
}

/* --- throttled 采集 --- */

/*
 * FindThrottledPath — 探测可用的 get_throttled sysfs 路径
 * RPi4/RPi5 供电降频路径：优先精确尝试 soc:firmware 路径（RPi4/5 常见
 * 设备树），再 glob 兜底扫描 soc 平台目录下名为 get_throttled 的节点。
 * 返回空串表示平台无此节点（throttled 恒按 0 处理）。
 */
static std::string FindThrottledPath()
{
    static const char* kCandidates[] = {
        "/sys/devices/platform/soc/soc:firmware/get_throttled",
    };
    for (const char* p : kCandidates) {
        if (access(p, F_OK) == 0)
            return p;
    }
    glob_t gl;
    if (glob("/sys/devices/platform/soc/*/get_throttled", 0, nullptr, &gl) == 0) {
        std::string found;
        if (gl.gl_pathc > 0 && gl.gl_pathv[0])
            found = gl.gl_pathv[0];
        globfree(&gl);
        if (!found.empty())
            return found;
    }
    return "";
}

/*
 * SampleThrottled — 读取 get_throttled 十六进制值（bit0 欠压、bit1 频率
 * 受限等，非零即供电异常在位）。文件打开/解析失败仅告警一次不崩溃，读
 * 不到按 0 处理（CXX-002：throttled 为辅助信号，不因缺失拖垮监控线程）。
 */
static uint64_t SampleThrottled()
{
    static std::string path = FindThrottledPath();
    static bool warned = false;
    if (path.empty()) {
        if (!warned) {
            warned = true;
            LC_ALOGW("linkmon: get_throttled path not found, throttled=0");
        }
        return 0;
    }
    FILE* f = fopen(path.c_str(), "r");
    if (!f) {
        int saved = errno;
        if (!warned) {
            warned = true;
            LC_ALOGW("linkmon: open %s failed: %s, throttled=0",
                     path.c_str(), strerror(saved));
        }
        errno = saved;
        return 0;
    }
    char buf[32] = {0};
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    if (n == 0)
        return 0;
    buf[n] = '\0';
    unsigned long long val = 0;
    if (sscanf(buf, "%llx", &val) == 1) {
        /* 读取成功：允许后续失败再次告警一次 */
        warned = false;
        return static_cast<uint64_t>(val);
    }
    return 0;
}

/* --- 事件聚合 --- */

/* per-link 聚合状态（key = busnum<<32 | port） */
struct LinkState {
    uint64_t event_count = 0;       /* 该链路累计链路事件数（聚合计数） */
    uint32_t last_event_type = 0;   /* 最近事件类型 */
    uint32_t last_event_value = 0;  /* 最近事件 event_value（掉线原因/阶段） */
    int32_t  last_status = 0;       /* 最近事件 status（err） */
    uint16_t last_vid = 0;          /* 最近事件 VID（GET_LINK_STATS 快照） */
    uint16_t last_pid = 0;          /* 最近事件 PID */
    uint32_t last_bytes = 0;        /* 最近事件 bytes（过流计数等） */
    uint64_t last_duration_ns = 0;  /* 最近事件 duration（lba） */
    uint64_t last_ts_ns = 0;        /* 最近事件 mono 时间戳 */
};

/* MonoNowNs — CLOCK_MONOTONIC 当前时间（ns），与内核 timestamp_ns 同基 */
static uint64_t MonoNowNs()
{
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0) {
        int saved = errno;
        LC_ALOGE("linkmon: clock_gettime(CLOCK_MONOTONIC) failed: %s", strerror(saved));
        errno = saved;
        return 0;
    }
    /* CXX-002：单调时钟 tv_sec 为系统运行秒数，*1e9 在 uint64 内安全 */
    return static_cast<uint64_t>(ts.tv_sec) * 1000000000ULL
         + static_cast<uint64_t>(ts.tv_nsec);
}

/*
 * ReadLinkStats — GET_LINK_STATS ioctl 快照（取最近一次事件 vid/pid）
 * 返回 false（内核未实现该 ioctl / 驱动版本漂移）时调用方按 vid/pid=0
 * 降级，事件流不受影响（CXX-002：失败留 debug 日志不静默）。
 */
static bool ReadLinkStats(int fd, struct vendor_lechao_usbd_link_stats* out)
{
    memset(out, 0, sizeof(*out));
    if (ioctl(fd, VENDOR_LECHAO_USBD_IOC_GET_LINK_STATS, out) < 0) {
        int saved = errno;
        LC_ALOGD("linkmon: GET_LINK_STATS ioctl failed: %s", strerror(saved));
        errno = saved;
        return false;
    }
    return true;
}

/*
 * EmitLinkEventJson — 手动拼装 JSON 聚合行（不引入外部 JSON 库）
 * 字段值全为数字与固定枚举名（vendor_lechao_usbd_event_type_name 返回
 * 静态表字符串），无外部字符串输入，无注入风险（CXX-003 输入防御）。
 * 输出: 事件日志专属 tag 一行 JSON；power_suspect 事件再打醒目 ERROR 日志。
 */
static void EmitLinkEventJson(const struct vendor_lechao_usbd_event& ev,
                              const LinkState& st, uint32_t busnum, uint32_t port,
                              uint16_t vid, uint16_t pid, bool suspect,
                              uint64_t throttled)
{
    const char* type_name = vendor_lechao_usbd_event_type_name(ev.event_type);
    char line[384];
    int n = snprintf(line, sizeof(line),
        "{\"busnum\":%u,\"port\":%u,\"type_name\":\"%s\","
        "\"event_type\":%u,\"event_value\":%u,\"status\":%d,"
        "\"vid\":%u,\"pid\":%u,\"count\":%llu,\"duration\":%llu,"
        "\"power_suspect\":%d}",
        busnum, port, type_name,
        ev.event_type, ev.event_value, ev.status,
        vid, pid,
        (unsigned long long)st.event_count,
        (unsigned long long)st.last_duration_ns,
        suspect ? 1 : 0);
    /* CXX-002：snprintf 截断则整行丢弃，不打半截 JSON */
    if (n < 0 || n >= static_cast<int>(sizeof(line)))
        return;
    EVENT_ALOG("%s", line);
    if (suspect) {
        LC_ALOGE("linkmon: power_suspect busnum=%u port=%u type=%s throttled=0x%llx",
                 busnum, port, type_name, (unsigned long long)throttled);
    }
}

/*
 * ProcessLinkEvent — 单条链路事件：按 busnum/port 聚合 + 规则一归因 + JSON
 * busnum/port 从 event.opcode 拆分：busnum=(opcode>>16)&0xffff, port=opcode&0xffff。
 */
static void ProcessLinkEvent(const struct vendor_lechao_usbd_event& ev, int fd,
                             uint64_t throttled,
                             std::unordered_map<uint64_t, LinkState>& links)
{
    uint32_t busnum = (static_cast<uint32_t>(ev.opcode) >> 16) & 0xffff;
    uint32_t port = static_cast<uint32_t>(ev.opcode) & 0xffff;

    uint64_t now_ns = MonoNowNs();
    bool suspect = ApplyPowerSuspectRule(ev.timestamp_ns, now_ns, throttled,
                                         ev.event_type);

    /* vid/pid：经 GET_LINK_STATS 取最近一次事件快照；ioctl 不可用降级 0 */
    struct vendor_lechao_usbd_link_stats stats;
    uint16_t vid = 0, pid = 0;
    if (ReadLinkStats(fd, &stats)) {
        vid = stats.last_vid;
        pid = stats.last_pid;
    }

    /* 按 busnum/port 聚合到 per-link 状态（CXX-003：先 find 判存在再更新） */
    uint64_t key = (static_cast<uint64_t>(busnum) << 32) | port;
    auto it = links.find(key);
    if (it == links.end())
        it = links.emplace(key, LinkState{}).first;
    LinkState& st = it->second;
    st.event_count++;
    st.last_event_type = ev.event_type;
    st.last_event_value = ev.event_value;
    st.last_status = ev.status;
    st.last_vid = vid;
    st.last_pid = pid;
    st.last_bytes = ev.bytes;
    st.last_duration_ns = ev.lba;
    st.last_ts_ns = ev.timestamp_ns;

    EmitLinkEventJson(ev, st, busnum, port, vid, pid, suspect, throttled);
}

/*
 * DrainLinkEvents — EPOLLIN 就绪后排空链路事件环形缓冲
 * 仿 hal/device_io.cpp read_event 排空语义：O_NONBLOCK 下循环 read 至
 * EAGAIN，逐条聚合输出（与 HAL"只取最新"不同——链路事件需全量聚合
 * 计数，逐条消费不丢弃）。
 * 返回 true 表示 fd 健康；false 表示读到致命错误，交由主循环 close+退避重开。
 */
static bool DrainLinkEvents(int fd, std::unordered_map<uint64_t, LinkState>& links,
                            uint64_t throttled)
{
    struct vendor_lechao_usbd_event ev;
    ssize_t n;
    while (true) {
        n = read(fd, &ev, sizeof(ev));
        if (n == static_cast<ssize_t>(sizeof(ev))) {
            if (ev.valid)   /* 占位/无效条目跳过，不计数 */
                ProcessLinkEvent(ev, fd, throttled, links);
            continue;
        }
        if (n < 0 && errno == EINTR)
            continue;   /* 信号打断：重试本次 read */
        break;
    }
    if (n < 0 && errno == EAGAIN)
        return true;    /* 环形缓冲已排空（非阻塞语义） */
    if (n < 0) {
        int saved = errno;
        LC_ALOGE("linkmon: read link events failed: %s (fd=%d)", strerror(saved), fd);
        errno = saved;
        return false;   /* fd 失效，交由主循环关闭重开 */
    }
    /* n==0（EOF）或短读（记录不完整）视为 fd 异常，交由主循环重开（CXX-002
     * 错误显式化，不吞为"排空完成"——对齐 device_io read_event 的 EIO 语义） */
    LC_ALOGW("linkmon: short/EOF read %zd bytes (expect %zu), fd=%d reopen",
             n, sizeof(ev), fd);
    return false;
}

/* --- 线程入口 --- */

void LinkMonitorRun()
{
    int epfd = epoll_create1(EPOLL_CLOEXEC);
    if (epfd < 0) {
        int saved = errno;
        LC_ALOGE("linkmon: epoll_create1 failed: %s", strerror(saved));
        /* CXX-004：链路监控是长生命周期关键线程，epoll 创建失败即致命——
         * ERROR 日志 + exit(1) 交非 oneshot init 自动重启 */
        errno = saved;
        std::exit(1);
    }

    int fd = -1;
    int backoff_ms = kInitBackoffMs;
    bool open_failed_warned = false;
    std::unordered_map<uint64_t, LinkState> links;
    uint64_t throttled = 0;
    uint64_t next_throttle_ns = MonoNowNs() + kThrottlePeriodNs;

    while (true) {
        /* 退避重开：链路节点可能晚于 daemon 创建（内核模块加载/驱动探测
         * 时序），打开失败仅告警一次 + 指数退避重试，不崩溃（CXX-002） */
        if (fd < 0) {
            fd = open(kLinkDevPath, O_RDWR | O_CLOEXEC);
            if (fd >= 0) {
                /* 非阻塞：排空依赖 read 至 EAGAIN 终止（仿 device_io
                 * read_event 语义） */
                int flags = fcntl(fd, F_GETFL, 0);
                if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) {
                    int saved = errno;
                    LC_ALOGE("linkmon: fcntl O_NONBLOCK failed: %s", strerror(saved));
                    ::close(fd);
                    fd = -1;
                    errno = saved;
                    std::this_thread::sleep_for(std::chrono::milliseconds(backoff_ms));
                    backoff_ms = std::min(backoff_ms * 2, kMaxBackoffMs);
                    continue;
                }
                struct epoll_event ev{};
                ev.events = EPOLLIN;
                ev.data.fd = fd;
                if (epoll_ctl(epfd, EPOLL_CTL_ADD, fd, &ev) < 0) {
                    int saved = errno;
                    LC_ALOGE("linkmon: epoll_ctl ADD failed: %s", strerror(saved));
                    ::close(fd);
                    fd = -1;
                    errno = saved;
                    std::this_thread::sleep_for(std::chrono::milliseconds(backoff_ms));
                    backoff_ms = std::min(backoff_ms * 2, kMaxBackoffMs);
                    continue;
                }
                backoff_ms = kInitBackoffMs;   /* 打开成功：退避归零 */
                open_failed_warned = false;
                LC_ALOGI("linkmon: link node %s opened", kLinkDevPath);
            } else {
                int saved = errno;
                if (!open_failed_warned) {
                    open_failed_warned = true;
                    LC_ALOGW("linkmon: open %s failed: %s (retry with backoff)",
                             kLinkDevPath, strerror(saved));
                }
                errno = saved;
                std::this_thread::sleep_for(std::chrono::milliseconds(backoff_ms));
                backoff_ms = std::min(backoff_ms * 2, kMaxBackoffMs);
                continue;
            }
        }

        /* 周期采集 throttled（1s，单调时钟对齐） */
        uint64_t now_ns = MonoNowNs();
        if (now_ns >= next_throttle_ns) {
            throttled = SampleThrottled();
            next_throttle_ns = now_ns + kThrottlePeriodNs;
        }

        struct epoll_event ready[8];
        int nready = epoll_wait(epfd, ready, 8, 1000);
        if (nready < 0) {
            if (errno == EINTR)
                continue;
            int saved = errno;
            LC_ALOGE("linkmon: epoll_wait failed: %s (epfd=%d)", strerror(saved), epfd);
            /* CXX-004：epoll 致命错误（非 EINTR）4 步退出——本独立线程无
             * alive 标志/等待者需通知，ERROR 日志 + exit(1) 即完整退出协议，
             * 交非 oneshot init 自动重启 */
            errno = saved;
            std::exit(1);
        }
        for (int i = 0; i < nready; i++) {
            if (ready[i].events & (EPOLLHUP | EPOLLERR)) {
                LC_ALOGW("linkmon: link node fd=%d closed by kernel, reopen with backoff",
                         fd);
                epoll_ctl(epfd, EPOLL_CTL_DEL, fd, nullptr);
                ::close(fd);
                fd = -1;
                std::this_thread::sleep_for(std::chrono::milliseconds(backoff_ms));
                backoff_ms = std::min(backoff_ms * 2, kMaxBackoffMs);
                continue;
            }
            if (!(ready[i].events & EPOLLIN))
                continue;
            if (!DrainLinkEvents(fd, links, throttled)) {
                epoll_ctl(epfd, EPOLL_CTL_DEL, fd, nullptr);
                ::close(fd);
                fd = -1;
                std::this_thread::sleep_for(std::chrono::milliseconds(backoff_ms));
                backoff_ms = std::min(backoff_ms * 2, kMaxBackoffMs);
            }
        }
    }
}

}  // namespace lciod
}  // namespace lechao
