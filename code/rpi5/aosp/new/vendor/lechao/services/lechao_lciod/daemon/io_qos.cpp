/*
 * ============================================================
 * io_qos.cpp — SD 卡写 QoS 限速实现（R4 方向 1/2/3/5）
 * 所属模块: lechao_lciod (system 分区 daemon)
 * 设计目的: 实现 IoQosManager 与相关纯函数（声明见 io_qos.h）。通过
 *           cgroup（v2 io.max / v1 blkio.throttle）对指定块设备施加写
 *           带宽限速，档位由 sysprop sys.lechao.lciod.ioqos_level 驱动
 *           （top/fg/bg/off），设备号由 sysprop sys.lechao.lciod.ioqos_dev
 *           覆盖（默认 /sys/block/mmcblk0/dev）。
 *
 * 线程模型: IoServiceImpl::start() 以 std::thread(&IoQosManager::RunLoop,
 *           &io_qos_).detach() 启动周期线程（进程非 oneshot，init 自动
 *           重启），每 1s 绝对时间对齐 PollAndApply。
 *
 * 安全性: CXX-003 外部输入防御（sysprop 档位/设备号/pid 前置校验）；
 *         CXX-002 错误路径不污染状态（写失败不更新 lastApplied，下轮重
 *         试）、snprintf 截断丢弃整行；CXX-004 失败路径 ERROR 留痕不静默。
 * 字节序: 本模块无跨进程二进制协议，不涉及 CXX-001 字节序转换。
 * ============================================================
 */
#include "io_qos.h"

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/system_properties.h>
#include <unistd.h>
#include <cerrno>
#include <chrono>
#include <cstdio>
#include <cstring>
#include <thread>

#include "lechao_log.h"

#define LOG_TAG "lechao_lciod"
#include <log/log.h>

namespace lechao {
namespace lciod {

/* 默认限速设备：RPi5 从 SD 卡启动，root 分区在 mmcblk0p2，块设备 mmcblk0 */
static const char* kDefaultBlockDevPath = "/sys/block/mmcblk0/dev";

/*
 * 事件日志专属 tag + 可配置级别（与 service.cpp / link_monitor.cpp
 * EVENT_ALOG 同款语义）：QoS 应用事件经 lechao_lciod_event 专属 tag
 * 输出，默认 INFO 生产可见。
 */
static inline int EventLogLevel()
{
    return ::lechao::debugVerbose() ? ANDROID_LOG_DEBUG : ANDROID_LOG_INFO;
}
#define EVENT_ALOG(...) __android_log_print(EventLogLevel(), "lechao_lciod_event", __VA_ARGS__)

/* --- 纯函数（声明见 io_qos.h，独立于 cgroup 环境可单测） --- */

bool ParseIoQosLevel(const std::string& val, IoQosLevel* out) {
    if (out == nullptr)
        return false;
    if (val == "top") { *out = IoQosLevel::kTop; return true; }
    if (val == "fg")  { *out = IoQosLevel::kFg;  return true; }
    if (val == "bg")  { *out = IoQosLevel::kBg;  return true; }
    if (val == "off" || val.empty()) { *out = IoQosLevel::kOff; return true; }
    /* 未知值一律拒绝（CXX-003 外部输入防御），out 不写入 */
    return false;
}

uint64_t LevelToBytesPerSec(IoQosLevel lvl) {
    switch (lvl) {
        case IoQosLevel::kTop:
        case IoQosLevel::kOff:
            return 0;  /* 不限：v2 写 max / v1 写 0 */
        case IoQosLevel::kFg:
            return kIoQosFgBytesPerSec;  /* 16MiB/s */
        case IoQosLevel::kBg:
            return kIoQosBgBytesPerSec;  /* 8MiB/s */
    }
    return 0;
}

std::string BuildIoMaxLine(const std::string& majmin, uint64_t bytes) {
    char buf[128];
    if (bytes == 0) {
        /* 不限档（kTop/kOff）：v2 写 max */
        snprintf(buf, sizeof(buf), "%s rbps=max wbps=max", majmin.c_str());
    } else {
        snprintf(buf, sizeof(buf), "%s rbps=%llu wbps=%llu", majmin.c_str(),
                 static_cast<unsigned long long>(bytes),
                 static_cast<unsigned long long>(bytes));
    }
    return std::string(buf);
}

std::string BuildBlkioLine(const std::string& majmin, uint64_t bytes) {
    char buf[128];
    if (bytes == 0) {
        /* 不限档（kTop/kOff）：v1 写 0 */
        snprintf(buf, sizeof(buf), "%s 0", majmin.c_str());
    } else {
        snprintf(buf, sizeof(buf), "%s %llu", majmin.c_str(),
                 static_cast<unsigned long long>(bytes));
    }
    return std::string(buf);
}

/*
 * IsValidMajorMinor — 校验 MAJ:MIN 格式（CXX-003 外部输入防御）
 * 格式: <major>:<minor>，两侧均为 1 位以上十进制数字。
 */
static bool IsValidMajorMinor(const std::string& s) {
    size_t colon = s.find(':');
    if (colon == std::string::npos || colon == 0 || colon == s.size() - 1)
        return false;
    for (size_t i = 0; i < s.size(); ++i) {
        if (i == colon)
            continue;
        if (s[i] < '0' || s[i] > '9')
            return false;
    }
    return true;
}

bool ReadDeviceMajorMinor(const std::string& sysblock_dev_path, std::string* out) {
    if (out == nullptr)
        return false;
    FILE* f = fopen(sysblock_dev_path.c_str(), "r");
    if (!f) {
        int saved = errno;
        LC_ALOGD("qos: open %s failed: %s", sysblock_dev_path.c_str(), strerror(saved));
        errno = saved;
        return false;
    }
    char buf[64] = {0};
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    if (n == 0)
        return false;
    buf[n] = '\0';
    /* 去除尾部空白（换行/回车/空格），n 同步收窄防越界 */
    while (n > 0 && (buf[n - 1] == '\n' || buf[n - 1] == '\r' || buf[n - 1] == ' '))
        buf[--n] = '\0';
    std::string val(buf);
    if (!IsValidMajorMinor(val))
        return false;
    *out = val;
    return true;
}

/*
 * ResolveBlkioRoot — 解析 v1 blkio 实际挂载根
 * （IoQosManager 注入临时根时复用，单测可用 mkdtemp 模拟 v1）
 * 优先 <root>/blkio（标准 v1 布局，单测模拟路径）；fallback_dev 为 true
 * 且 /dev/blkio 存在时回落到真机 Android v1 挂载点（RPi5 实测，真实根
 * /sys/fs/cgroup 下无 <root>/blkio）。注入根（单测）不回落真机挂载点，
 * 保证单测隔离（不依赖宿主 /dev/blkio 存在与否）。均不可用返回空串。
 */
static std::string ResolveBlkioRoot(const std::string& root, bool fallback_dev) {
    struct stat st{};
    std::string std_path = root + "/blkio";
    if (stat(std_path.c_str(), &st) == 0 && S_ISDIR(st.st_mode))
        return std_path;
    if (fallback_dev) {
        std::string dev_path = "/dev/blkio";
        if (stat(dev_path.c_str(), &st) == 0 && S_ISDIR(st.st_mode))
            return dev_path;
    }
    return "";
}

/*
 * DetectCgroupVersionFromRoot — 基于给定根路径探测 cgroup 版本
 * （IoQosManager 注入临时根时复用，单测可用 mkdtemp 模拟 v2/v1）
 * v2 判据: <root>/cgroup.controllers 存在且内容含 "io"；
 * v1 判据: <root>/blkio 目录存在；仅真实根（/sys/fs/cgroup）额外回落
 * 真机 /dev/blkio（Android v1 挂载点）。
 */
static CgroupVersion DetectCgroupVersionFromRoot(const std::string& root) {
    std::string controllers_path = root + "/cgroup.controllers";
    FILE* f = fopen(controllers_path.c_str(), "r");
    if (f) {
        char buf[256] = {0};
        size_t n = fread(buf, 1, sizeof(buf) - 1, f);
        fclose(f);
        buf[n] = '\0';
        if (strstr(buf, "io") != nullptr)
            return CgroupVersion::kV2;
    }
    if (!ResolveBlkioRoot(root, root == "/sys/fs/cgroup").empty())
        return CgroupVersion::kV1;
    return CgroupVersion::kUnsupported;
}

CgroupVersion DetectCgroupVersion() {
    return DetectCgroupVersionFromRoot("/sys/fs/cgroup");
}

/* --- 文件写工具（CXX-002 资源生命周期：短写续写、失败显式返回） --- */

/* WriteAll — 循环 write 直至写完（处理短写与 EINTR），失败显式返回 false */
static bool WriteAll(int fd, const std::string& path, const char* data, size_t len) {
    size_t off = 0;
    while (off < len) {
        ssize_t n = write(fd, data + off, len - off);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            int saved = errno;
            LC_ALOGE("qos: write %s failed: %s", path.c_str(), strerror(saved));
            errno = saved;
            return false;
        }
        off += static_cast<size_t>(n);
    }
    return true;
}

/* WriteFileContent — 覆盖写（限速文件 io.max / blkio.throttle.*） */
static bool WriteFileContent(const std::string& path, const std::string& content) {
    int fd = open(path.c_str(), O_WRONLY | O_TRUNC | O_CLOEXEC);
    if (fd < 0) {
        int saved = errno;
        LC_ALOGE("qos: open %s failed: %s", path.c_str(), strerror(saved));
        errno = saved;
        return false;
    }
    bool ok = WriteAll(fd, path, content.data(), content.size());
    int saved = errno;
    close(fd);
    errno = saved;
    return ok;
}

/* AppendFileLine — 追加写（cgroup.procs / tasks，迁移 pid） */
static bool AppendFileLine(const std::string& path, const std::string& line) {
    int fd = open(path.c_str(), O_WRONLY | O_APPEND | O_CLOEXEC);
    if (fd < 0) {
        int saved = errno;
        LC_ALOGE("qos: open %s failed: %s", path.c_str(), strerror(saved));
        errno = saved;
        return false;
    }
    bool ok = WriteAll(fd, path, line.data(), line.size());
    int saved = errno;
    close(fd);
    errno = saved;
    return ok;
}

/* MkdirP — mkdir 建目录，已存在（EEXIST）视为成功（幂等） */
static bool MkdirP(const std::string& path) {
    if (mkdir(path.c_str(), 0755) == 0)
        return true;
    if (errno == EEXIST)
        return true;  /* 已存在：幂等成功 */
    int saved = errno;
    LC_ALOGE("qos: mkdir %s failed: %s", path.c_str(), strerror(saved));
    errno = saved;
    return false;
}

/* --- 档位名 / cgroup 版本名（JSON 留痕用） --- */

static const char* IoQosLevelToString(IoQosLevel lvl) {
    switch (lvl) {
        case IoQosLevel::kOff: return "off";
        case IoQosLevel::kTop: return "top";
        case IoQosLevel::kFg:  return "fg";
        case IoQosLevel::kBg:  return "bg";
    }
    return "unknown";
}

static const char* CgroupVersionToString(CgroupVersion v) {
    switch (v) {
        case CgroupVersion::kV2: return "v2";
        case CgroupVersion::kV1: return "v1";
        case CgroupVersion::kUnsupported: return "unsupported";
    }
    return "unsupported";
}

/* --- IoQosManager 实现 --- */

IoQosManager::IoQosManager(const std::string& cgroup_root)
    : root_(cgroup_root) {
}

IoQosManager::~IoQosManager() {
    /* RunLoop 停产后析构安全（生产 detach 线程随进程生命周期） */
    stop();
}

std::string IoQosManager::BlkioRoot() const {
    return ResolveBlkioRoot(root_, root_ == "/sys/fs/cgroup");
}

bool IoQosManager::EnsureGroup() {
    /* 懒探测 cgroup 版本（可能晚于构造就绪） */
    if (version_ == CgroupVersion::kUnsupported)
        version_ = DetectCgroupVersionFromRoot(root_);
    if (version_ == CgroupVersion::kUnsupported) {
        /* 去重告警：cgroup 不可探测只报一次（CXX-004 故障可见性，不刷屏） */
        if (!unsupportedWarned_) {
            unsupportedWarned_ = true;
            LC_ALOGE("qos: cgroup 版本不可用（root=%s），限速不生效", root_.c_str());
        }
        return false;
    }
    unsupportedWarned_ = false;

    bool ok = false;
    if (version_ == CgroupVersion::kV2) {
        /* v2: 建 <root>/lechao_bg（io.max 由内核在 io 控制器启用时自动生成） */
        ok = MkdirP(root_ + "/lechao_bg");
    } else {
        /* v1: 建 <blkio_root>/lechao_bg（真机 blkio_root=/dev/blkio，Android
         * v1 挂载点；单测注入根时 <root>/blkio）。组目录已存在幂等成功。 */
        std::string blkio_root = BlkioRoot();
        if (blkio_root.empty())
            return false;
        ok = MkdirP(blkio_root + "/lechao_bg");
    }
    if (ok) {
        LC_ALOGI("qos: lechao_bg 组就绪（version=%s root=%s）",
                 CgroupVersionToString(version_), root_.c_str());
    }
    return ok;
}

bool IoQosManager::ApplyLevel(IoQosLevel lvl, const std::string& majmin) {
    /* CXX-003：设备号格式前置校验（ioqos_dev sysprop 属外部输入） */
    if (!IsValidMajorMinor(majmin)) {
        LC_ALOGE("qos: 非法设备号 '%s'（期望 MAJ:MIN），本周期不应用", majmin.c_str());
        return false;
    }
    /* 幂等：档位与设备均未变化时不重复写 */
    if (lvl == lastAppliedLevel_ && majmin == lastAppliedMajMin_)
        return true;
    if (!EnsureGroup())
        return false;

    uint64_t bytes = LevelToBytesPerSec(lvl);
    bool ok = false;
    if (version_ == CgroupVersion::kV2) {
        /* v2: 写 <root>/lechao_bg/io.max（BuildIoMaxLine 返回无换行行，
         * 写入时补换行——与 echo 写 cgroup 语义一致） */
        ok = WriteFileContent(root_ + "/lechao_bg/io.max",
                              BuildIoMaxLine(majmin, bytes) + "\n");
    } else {
        /* v1: 写 read_bps_device 与 write_bps_device 两文件
         * （组在 <blkio_root>/lechao_bg，真机 blkio_root=/dev/blkio） */
        std::string blkio_root = BlkioRoot();
        if (blkio_root.empty())
            return false;
        ok = WriteFileContent(blkio_root + "/lechao_bg/blkio.throttle.read_bps_device",
                              BuildBlkioLine(majmin, bytes) + "\n")
          && WriteFileContent(blkio_root + "/lechao_bg/blkio.throttle.write_bps_device",
                              BuildBlkioLine(majmin, bytes) + "\n");
    }
    if (!ok) {
        /* CXX-002：失败不更新 lastApplied，下轮重试（错误路径不污染状态）；
         * WriteFileContent 内部已 ERROR 留痕（CXX-004） */
        return false;
    }

    lastAppliedLevel_ = lvl;
    lastAppliedMajMin_ = majmin;

    /* 留痕 logcat JSON 事件（EVENT_ALOG 同款，snprintf 手拼，截断丢弃整行） */
    char line[256];
    int n = snprintf(line, sizeof(line),
        "{\"rule\":\"qos\",\"level\":\"%s\",\"dev\":\"%s\","
        "\"limit_bytes_per_s\":%llu,\"cgroup\":\"%s\"}",
        IoQosLevelToString(lvl), majmin.c_str(),
        static_cast<unsigned long long>(bytes), CgroupVersionToString(version_));
    if (n < 0 || n >= static_cast<int>(sizeof(line))) {
        /* CXX-002：snprintf 截断则丢弃整行，不打半截 JSON */
        LC_ALOGE("qos: JSON 留痕截断丢弃整行（level=%s dev=%s）",
                 IoQosLevelToString(lvl), majmin.c_str());
    } else {
        EVENT_ALOG("%s", line);
    }
    LC_ALOGI("qos: apply level=%s dev=%s bytes=%llu version=%s",
             IoQosLevelToString(lvl), majmin.c_str(),
             static_cast<unsigned long long>(bytes), CgroupVersionToString(version_));
    return true;
}

bool IoQosManager::MovePidToGroup(int pid) {
    /* CXX-003：pid 外部输入防御，pid<=0（负 pid / 0）一律拒绝 */
    if (pid <= 0) {
        LC_ALOGE("qos: 非法 pid %d，拒绝迁移到 lechao_bg", pid);
        return false;
    }
    if (!EnsureGroup())
        return false;
    char line[32];
    int n = snprintf(line, sizeof(line), "%d\n", pid);
    if (n < 0 || n >= static_cast<int>(sizeof(line))) {
        LC_ALOGE("qos: pid 行拼装截断（pid=%d）", pid);
        return false;
    }
    std::string path;
    if (version_ == CgroupVersion::kV2) {
        path = root_ + "/lechao_bg/cgroup.procs";   /* v2 迁移文件 */
    } else {
        std::string blkio_root = BlkioRoot();
        if (blkio_root.empty())
            return false;
        path = blkio_root + "/lechao_bg/tasks";     /* v1 迁移文件 */
    }
    return AppendFileLine(path, line);
}

void IoQosManager::PollAndApply() {
    /* PROP_VALUE_MAX 为属性值上限，缓冲按上限开防 __system_property_get
     * 溢出（CXX-002 边界防御） */
    char level_buf[PROP_VALUE_MAX] = {0};
    __system_property_get("sys.lechao.lciod.ioqos_level", level_buf);
    char dev_buf[PROP_VALUE_MAX] = {0};
    __system_property_get("sys.lechao.lciod.ioqos_dev", dev_buf);
    std::string level_val(level_buf);

    IoQosLevel lvl;
    if (!ParseIoQosLevel(level_val, &lvl)) {
        /* CXX-003：非法档位值告警（值变化才刷屏，仿 fault_inject 去重） */
        if (level_val != lastLevelProp_) {
            lastLevelProp_ = level_val;
            LC_ALOGE("qos: 非法 ioqos_level '%s'（期望 top/fg/bg/off/空），本周期不应用",
                     level_val.c_str());
        }
        return;
    }
    lastLevelProp_ = level_val;

    /* 设备确定：ioqos_dev sysprop 覆盖默认 mmcblk0 */
    std::string majmin;
    if (dev_buf[0] != '\0') {
        majmin = dev_buf;
        noDeviceWarned_ = false;
    } else if (ReadDeviceMajorMinor(kDefaultBlockDevPath, &majmin)) {
        noDeviceWarned_ = false;
    } else {
        /* 默认设备不可读且未配置覆盖：告警一次不静默（CXX-004） */
        if (!noDeviceWarned_) {
            noDeviceWarned_ = true;
            LC_ALOGW("qos: 默认设备 %s 不可读且未设置 ioqos_dev，本周期不应用",
                     kDefaultBlockDevPath);
        }
        return;
    }
    /* ApplyLevel 幂等，失败路径内部已 ERROR 留痕（CXX-004） */
    ApplyLevel(lvl, majmin);
}

void IoQosManager::RunLoop() {
    using clock = std::chrono::steady_clock;
    const auto kPeriod = std::chrono::seconds(1);
    while (!stop_.load()) {
        /* 绝对时间对齐 1s 周期（steady_clock 单调，防漂移），仿
         * link_monitor next_throttle_ns 范式：每轮以当前单调时刻锚定 */
        auto now = clock::now();
        std::this_thread::sleep_until(now + kPeriod);
        if (stop_.load())
            break;
        PollAndApply();
    }
}

void IoQosManager::stop() {
    stop_.store(true);
}

}  // namespace lciod
}  // namespace lechao
