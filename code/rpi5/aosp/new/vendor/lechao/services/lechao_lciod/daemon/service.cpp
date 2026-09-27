/*
 * ============================================================
 * service.cpp — System Daemon 主实现
 * 所属模块: lechao_lciod (system 分区)
 * 设计目的: 实现 IIoService AIDL 接口，作为 vendor HAL 的代理层，
 *           面向 system_server 和上层 App 暴露 IO 监控服务。
 *
 * 架构角色:
 *   - 持有 vendor HAL 的 Binder 客户端引用（IoHalClient）
 *   - 将 system 层 AIDL 调用转换为 vendor HAL 调用
 *   - 执行字段投影/过滤（省略 enabled/flags 等管理字段）
 *   - 提供计算字段（getAverageRate）
 *   - 后台监控线程：定期读取事件和统计，打印到 logcat
 *
 * 服务名称: system.lechao.lciod.IIoService/default
 * 线程模型: 主线程处理 Binder RPC + 1 个 detach 后台监控线程
 *
 * 注: IoServiceImpl 类声明与纯计算函数在 service.h（供单测/filegroup
 *     复用），进程 main 入口在 main_lciod.cpp（与 lcview 模式对齐）。
 * ============================================================
 */
#include "service.h"

#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <thread>
#include <chrono>
#include <cerrno>
#include <cstring>
#include <algorithm>
#include <unordered_map>
#include <vector>
#include <sys/epoll.h>
#include <unistd.h>
#include <aidl/system/lechao/lciod/BnIoService.h>
#include <aidl/system/lechao/lciod/IIoService.h>
#include <aidl/vendor/lechao/lciod/IIoHal.h>
#include <aidl/vendor/lechao/lciod/IoEvent.h>
#include "hal_client.h"
#include "minor_utils.h"
#include "lechao_log.h"
/* R-14 方向 1：共享事件枚举头（AOSP hal 镜像，与内核真相源 1:1 同步），
 * 事件类型/方向名映射单一事实源，消模块内 switch 硬编码 */
#include "vendor_lechao_usbd-ioctl.h"

#define LOG_TAG "lechao_lciod"
#include <log/log.h>

using namespace ndk;
using aidl::system::lechao::lciod::BnIoService;
using aidl::system::lechao::lciod::IoStats;
using aidl::system::lechao::lciod::IoConfig;
using aidl::system::lechao::lciod::IoEvent;
using VendorIoEvent = aidl::vendor::lechao::lciod::IoEvent;
using lechao::lciod::ParseMinorFromPath;

/* LCD-002：readIoEvent timeout 首层钳位上限（与 HAL 侧
 * device_io.h kMaxReadEventTimeoutMs 同值——HAL 侧为最终防线） */
static const int kMaxReadEventTimeoutMs = 1000;

/*
 * R-12 方向 2：事件日志专属 tag + 可配置级别。
 * 原 LC_ALOGD("event: ...") 受 persist.vendor.lechao.loglevel 控制，生产默认
 * 关闭 → 事件被过滤静默（新用户态旧内核 ENOTTY、传输异常等无日志可判，
 * 故障无感）。改专属 tag lechao_lciod_event：默认 INFO 生产级别可见（不再
 * 被过滤），debugVerbose 开启时提升 DEBUG 显示更多细节——事件不再静默。
 */
static inline int EventLogLevel()
{
    return ::lechao::debugVerbose() ? ANDROID_LOG_DEBUG : ANDROID_LOG_INFO;
}
#define EVENT_ALOG(...) __android_log_print(EventLogLevel(), "lechao_lciod_event", __VA_ARGS__)

/* --- 纯计算函数（声明见 service.h，独立于 binder 环境可单测） --- */

int64_t ComputeAverageRate(uint64_t readBytes, uint64_t writeBytes,
                           uint64_t readNs, uint64_t writeNs) {
    // 中间量用 __uint128_t：total/totalNs 累计约 17GiB 时 total*1e9 超出
    // uint64 上限回绕致速率失真，128 位中间量消除溢出（CXX-002 边界防御：
    // 溢出/回绕属资源生命周期与边界类，字节序才是 CXX-001）
    __uint128_t total = static_cast<__uint128_t>(readBytes) + writeBytes;
    __uint128_t totalNs = static_cast<__uint128_t>(readNs) + writeNs;
    if (totalNs > 0)
        return static_cast<int64_t>(total * 1000000000ULL / totalNs);
    return 0;
}

uint64_t ComputeKbRate(uint64_t bytes, uint64_t ns) {
    if (ns == 0) return 0;
    // 同 ComputeAverageRate：bytes 累计约 17GiB 时 bytes*1e9 溢出，
    // 128 位中间量防止速率失真
    __uint128_t rate = (static_cast<__uint128_t>(bytes) * 1000000000ULL) / ns / 1024ULL;
    return static_cast<uint64_t>(rate);
}

uint64_t ComputeWindowKbRate(uint64_t currBytes, uint64_t currNs, uint64_t prevBytes,
                             uint64_t prevNs)
{
    // 回退语义：无快照（新接入 prev=0）或计数回绕（curr < prev，容器/环
    // 重置）时，窗口增量负值无意义，返回全程累计速率等同旧行为；
    // 否则返回窗口增量差分速率（近 10s 即时吞吐）。
    bool valid = currBytes >= prevBytes && currNs >= prevNs;
    if (!valid)
        return ComputeKbRate(currBytes, currNs);
    return ComputeKbRate(currBytes - prevBytes, currNs - prevNs);
}

uint64_t ComputeErrorRate(uint64_t errorCount, uint64_t ioCount) {
    // R-14 方向 2：读/写方向 IO 错误率（‰）。总 IO = 成功 IO + 错误数，
    // 错误占比×1000，128 位中间量防 errorCount*1000 溢出（CXX-002）。
    __uint128_t total = static_cast<__uint128_t>(ioCount) + errorCount;
    if (total == 0)
        return 0;
    return static_cast<uint64_t>(static_cast<__uint128_t>(errorCount) * 1000ULL / total);
}

/* --- 字段投影纯函数（声明见 service.h，独立于 binder 环境可单测） --- */

void ProjectSystemIoStats(const aidl::vendor::lechao::lciod::IoStats& vstats,
                          aidl::system::lechao::lciod::IoStats* out) {    out->vid = vstats.vid;
    out->pid = vstats.pid;
    out->vendor = vstats.vendor;
    out->product = vstats.product;
    out->readBytes = vstats.readBytes;
    out->readNs = vstats.readNs;
    out->readCmds = vstats.readCmds;
    out->writeBytes = vstats.writeBytes;
    out->writeNs = vstats.writeNs;
    out->writeCmds = vstats.writeCmds;
    out->errorCount = vstats.errorCount;
    out->resetCount = vstats.resetCount;
    out->stallCount = vstats.stallCount;
    out->corruptCount = vstats.corruptCount;
    out->timeoutCount = vstats.timeoutCount;
    out->readErrorCount = vstats.readErrorCount;   /* R-14 方向 2：读方向错误分项 */
    out->writeErrorCount = vstats.writeErrorCount; /* R-14 方向 2：写方向错误分项 */
    out->probeCount = vstats.probeCount;
    out->disconnectCount = vstats.disconnectCount;
    out->degradeCount = vstats.degradeCount;
    out->eventDropCount = vstats.eventDropCount;  /* LCD-012：事件丢弃数透出 */
    out->lastTransportLatencyNs = vstats.lastTransportLatencyNs;
    out->lastEventTsNs = vstats.lastEventTsNs;
    out->lastEventType = vstats.lastEventType;
    /* 省略管理字段：currentRate / enabled / flags 不暴露给上层 */
}

void ProjectSystemIoConfig(const aidl::vendor::lechao::lciod::IoConfig& vcfg,
                           aidl::system::lechao::lciod::IoConfig* out) {
    out->enabled = vcfg.enabled;
    out->flags = vcfg.flags;
}

void ProjectSystemIoEvent(const aidl::vendor::lechao::lciod::IoEvent& vev,
                          aidl::system::lechao::lciod::IoEvent* out) {
    out->timestampNs = vev.timestampNs;
    out->eventType = vev.eventType;
    out->eventValue = vev.eventValue;
    out->dataDirection = vev.dataDirection;
    out->status = vev.status;
    out->valid = vev.valid;
    /* R-14 方向 1/4：v3 追加字段 1:1 直传 */
    out->wallTimeNs = vev.wallTimeNs;
    out->opcode = vev.opcode;
    out->lba = vev.lba;
    out->bytes = vev.bytes;
    out->retry = vev.retry;
}

void IoServiceImpl::start() {
    start_monitor();
}

/*
 * listDeviceMinors — 返回所有在线设备的 minor 编号列表
 * 调用 HAL listDevices()，将路径列表转换为 minor 编号列表。
 */
ndk::ScopedAStatus IoServiceImpl::listDeviceMinors(std::vector<int32_t>* _aidl_return) {
    auto hal = hal_client_.get();
    if (!hal) { LC_ALOGW("listDeviceMinors: HAL not connected"); return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
    std::vector<std::string> devices;
    auto status = hal->listDevices(&devices);
    if (!status.isOk()) { LC_ALOGW("listDeviceMinors: listDevices failed"); return status; }
    _aidl_return->clear();
    for (auto& path : devices) {
        int32_t minor = -1;
        /* LCD-020：解析失败静默跳过会让"路径格式漂移"表现为空列表，
         * 至少留 debug 日志供诊断（不升级告警：混合设备名是预期可能） */
        if (ParseMinorFromPath(path, &minor))
            _aidl_return->push_back(minor);
        else
            LC_ALOGD("listDeviceMinors: unparsable device path: %s", path.c_str());
    }
    return ndk::ScopedAStatus::ok();
}

/*
 * getAverageRate — 计算指定设备的平均传输速率
 * 公式与除零防护收敛到 ComputeAverageRate 纯函数（service.h，供单测）
 */
ndk::ScopedAStatus IoServiceImpl::getAverageRate(int32_t in_deviceMinor, int64_t* _aidl_return) {
    auto hal = hal_client_.get();
    if (!hal) { LC_ALOGW("getAverageRate: HAL not connected"); *_aidl_return = 0; return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }

    aidl::vendor::lechao::lciod::IoStats stats;
    auto status = hal->getStats(in_deviceMinor, &stats);
    if (!status.isOk()) { LC_ALOGW("getAverageRate: getStats failed"); *_aidl_return = 0; return status; }

    *_aidl_return = ComputeAverageRate(stats.readBytes, stats.writeBytes,
                                       stats.readNs, stats.writeNs);
    return ndk::ScopedAStatus::ok();
}

/*
 * getIoStats — 获取指定设备的统计快照（投影版）
 *
 * 字段投影: vendor IoStats → system IoStats
 *   - 直传: vid/pid/vendor/product/所有计数器/延迟/时间戳
 *   - 省略: currentRate（通过 getAverageRate 按需计算）
 *           enabled/flags（管理字段，不暴露给上层）
 *           peakRate（仅 degrade check 内部使用）
 */
ndk::ScopedAStatus IoServiceImpl::getIoStats(int32_t in_deviceMinor, IoStats* _aidl_return) {
    *_aidl_return = {};
    auto hal = hal_client_.get();
    if (!hal) { LC_ALOGW("getIoStats: HAL not connected"); return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
    aidl::vendor::lechao::lciod::IoStats vstats;
    auto status = hal->getStats(in_deviceMinor, &vstats);
    if (!status.isOk()) { LC_ALOGW("getIoStats: getStats failed"); return status; }
    ProjectSystemIoStats(vstats, _aidl_return);
    return ndk::ScopedAStatus::ok();
}

/* resetIoState — 代理转发到 HAL resetState() */
ndk::ScopedAStatus IoServiceImpl::resetIoState(int32_t in_deviceMinor) {
    auto hal = hal_client_.get();
    if (!hal) { LC_ALOGW("resetIoState: HAL not connected"); return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
    return hal->resetState(in_deviceMinor);
}

/* getIoConfig — 代理转发到 HAL getConfig() */
ndk::ScopedAStatus IoServiceImpl::getIoConfig(int32_t in_deviceMinor, IoConfig* _aidl_return) {
    *_aidl_return = {};
    auto hal = hal_client_.get();
    if (!hal) { LC_ALOGW("getIoConfig: HAL not connected"); return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
    aidl::vendor::lechao::lciod::IoConfig vcfg;
    auto status = hal->getConfig(in_deviceMinor, &vcfg);
    if (!status.isOk()) { LC_ALOGW("getIoConfig: getConfig failed"); return status; }
    ProjectSystemIoConfig(vcfg, _aidl_return);
    return ndk::ScopedAStatus::ok();
}

/* setIoConfig — 代理转发到 HAL setConfig() */
ndk::ScopedAStatus IoServiceImpl::setIoConfig(int32_t in_deviceMinor, const IoConfig& in_config, bool* _aidl_return) {
    auto hal = hal_client_.get();
    if (!hal) { LC_ALOGW("setIoConfig: HAL not connected"); *_aidl_return = false; return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
    aidl::vendor::lechao::lciod::IoConfig vcfg;
    vcfg.enabled = in_config.enabled;
    vcfg.flags = in_config.flags;
    return hal->setConfig(in_deviceMinor, vcfg, _aidl_return);
}

/* readIoEvent — 代理转发到 HAL readEvent()，1:1 字段直传。
 * LCD-002：timeout 首层钳位（HAL 侧 clamp_read_timeout_ms 为最终防线）——
 * 公开 binder 接口的负值/超大超时不得透传（-1 永久阻塞、INT_MAX 阻塞
 * 约天级，daemon 单线程 binder 池即被占死） */
ndk::ScopedAStatus IoServiceImpl::readIoEvent(int32_t in_deviceMinor, int32_t in_timeoutMs, IoEvent* _aidl_return) {
    *_aidl_return = {};
    auto hal = hal_client_.get();
    if (!hal) { LC_ALOGW("readIoEvent: HAL not connected"); return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
    int timeoutMs = in_timeoutMs < 0 ? 0 : in_timeoutMs;
    if (timeoutMs > kMaxReadEventTimeoutMs) timeoutMs = kMaxReadEventTimeoutMs;
    aidl::vendor::lechao::lciod::IoEvent vev;
    auto status = hal->readEvent(in_deviceMinor, timeoutMs, &vev);
    if (!status.isOk()) { LC_ALOGW("readIoEvent: readEvent failed"); return status; }
    ProjectSystemIoEvent(vev, _aidl_return);
    return ndk::ScopedAStatus::ok();
}

/*
 * start_monitor — 启动后台监控线程
 *
 * 线程行为（R-16 P4 方向 4：epoll 多路复用改造）:
 *   - 每 10s（200 tick）刷新设备列表：经 dupEventFd 获取各设备内核事件
 *     fd 并 EPOLL_CTL_ADD 注册到 epoll（内核 .poll 支持 epoll 多路复用）
 *   - 主循环 epoll_wait(50ms) 统一等待所有设备事件就绪——替代原
 *     "每 50ms 节拍 + N 设备串行 readEvent(50)" 的串行阻塞（N 设备空转
 *     时原单轮 = 50ms + N×50ms，epoll 后统一单次 50ms 等待，事件到达
 *     即返回）
 *   - 就绪 fd 反查 minor，调 hal->readEvent(minor, 0) 非阻塞拉取（事件
 *     就绪即返回，不占 50ms）；EPOLLHUP（设备断开）DEL + close fd
 *   - 每 10s（200 tick）打印统计信息（monitor: minor= 日志格式保留，
 *     lciod-liveness 验收判据依赖）；差分时间桶吞吐逻辑原样保留
 *   - 单设备失败仅跳过，不中断本轮其他设备
 *   - 线程 detach，随进程生命周期自动终止
 *
 * NOTE: detach 线程无独立退出条件，但本进程为 oneshot 服务，
 * 进程退出时线程自动终止，不存在生命周期风险。若将来改为
 * 常驻服务，需改为 std::thread 成员 + join 析构。
 */
void IoServiceImpl::start_monitor() {
    std::thread([this]() {
        int tick = 0;
        std::vector<int32_t> deviceMinors;  /* 当前活跃设备 minor 列表（按 HAL 返回顺序，已排序） */
        /* R-16 P4 方向 4：记录上一次成功的 HAL 实例——实例变化（重启/重连）
         * 时立即重建 epoll，避免 200 tick 重建前的 fd 失效窗口事件溢出 */
        std::shared_ptr<aidl::vendor::lechao::lciod::IIoHal> lastHal = nullptr;
        /* R-16 P4 方向 4 修复：设备热插拔待重连队列。EPOLLHUP（设备断开/
         * authorized 切换）时 fd 失效，设备重连后须重新 dupEventFd 注册——
         * 仅靠 200 tick 重建会留最长 10s 的消费空窗（重连后的初始化 IO
         * 事件全部溢出，event_drop 飙升判红）。pendingRedup 在每轮循环
         * 尝试重新注册，设备一重连立即恢复消费。 */
        std::vector<int32_t> pendingRedup;
        /* R-16 P4 方向 4：minor → epoll 注册的事件 fd 映射（fd 由 dupEventFd
         * 取得，HAL 侧持久 fd 的副本；HAL 重启后全部失效，由 200 tick 重建
         * 兜底） */
        std::unordered_map<int32_t, int> minorToFd;

        /*
         * R-12 方向 3：10 秒 tick 差分时间桶吞吐。原统计用 ComputeKbRate
         * (累计 bytes / 累计 ns) 算的是接入以来全程平均速率——某个 10s 窗口
         * 变慢会被长期均值稀释，无法定位变慢时刻。改差分桶：每 200 tick
         * 保存本 tick 的累计字节/耗时快照，下个统计 tick 用差值算窗口吞吐
         * （近 10s 即时速率），变慢时刻直接反映在窗口速率跌落上。首 tick
         * 无快照时回退累计值（等同原行为，不产生假低谷）。
         */
        struct TickSnapshot
        {
            uint64_t readBytes = 0, readNs = 0, writeBytes = 0, writeNs = 0;
        };
        std::unordered_map<int32_t, TickSnapshot> tickSnap;

        /* R-16 P4 方向 4：epoll 多路复用——单次 epoll_wait(50ms) 统一等待
         * 全部设备事件，替代串行 readEvent 的 N×50ms 阻塞累加。 */
        int epfd = epoll_create1(EPOLL_CLOEXEC);
        if (epfd < 0) {
            LC_ALOGE("monitor: epoll_create1 failed: %s", strerror(errno));
            return;
        }

        /* LCD-018：绝对时间对齐调度。epoll_wait 超时 50ms 作为固定节拍，
         * 事件就绪时提前返回（不等待整 50ms），空闲时精确 50ms——周期不再
         * 因 N 设备串行阻塞漂移。 */
        using clock = std::chrono::steady_clock;
        const auto kTickPeriod = std::chrono::milliseconds(50);
        auto next_tick = clock::now() + kTickPeriod;

        /* 启动前立即 refresh 一次，避免首轮空转或误读 minor=0 */
        auto hal = hal_client_.get();
        if (hal) {
            std::vector<std::string> devices;
            if (hal->listDevices(&devices).isOk()) {
                for (auto& path : devices) {
                    int32_t minor = -1;
                    if (ParseMinorFromPath(path, &minor))
                        deviceMinors.push_back(minor);
                }
            }
        }

        /* R-16 P4 方向 4：每 200 tick 重建 epoll 注册表（刷新设备列表 +
         * 重新 dupEventFd，HAL 重启/设备插拔后 fd 失效由重建兜底）。
         * force_redup=true 时强制重新 dupEventFd（HAL 实例变化场景：旧 fd
         * 指向已失效的内核 file 结构，即使 fd 数值仍 >=0 也不能复用） */
        auto rebuild_epoll = [&](const std::shared_ptr<aidl::vendor::lechao::lciod::IIoHal>& h,
                                 bool force_redup = false) {
            std::vector<std::string> devices;
            auto dev_status = h->listDevices(&devices);
            std::vector<int32_t> newMinors;
            if (dev_status.isOk()) {
                for (auto& path : devices) {
                    int32_t minor = -1;
                    if (ParseMinorFromPath(path, &minor))
                        newMinors.push_back(minor);
                }
            }
            /* 移除已离线 minor 的 fd */
            for (auto& [minor, fd] : minorToFd) {
                if (std::find(newMinors.begin(), newMinors.end(), minor) ==
                    newMinors.end()) {
                    epoll_ctl(epfd, EPOLL_CTL_DEL, fd, nullptr);
                    ::close(fd);
                }
            }
            std::unordered_map<int32_t, int> newMap;
            for (int32_t minor : newMinors) {
                auto it = minorToFd.find(minor);
                if (!force_redup && it != minorToFd.end() && it->second >= 0) {
                    newMap[minor] = it->second;  /* 保留仍在线设备的 fd */
                    continue;
                }
                /* force_redup 时旧 fd 须先移出 epoll 并关闭，防残留监听 */
                if (it != minorToFd.end() && it->second >= 0) {
                    epoll_ctl(epfd, EPOLL_CTL_DEL, it->second, nullptr);
                    ::close(it->second);
                }
                ndk::ScopedFileDescriptor sfd;
                auto st = h->dupEventFd(minor, &sfd);
                if (!st.isOk() || sfd.get() < 0) {
                    LC_ALOGW("monitor: dupEventFd failed for minor=%d: %s", minor,
                             st.getDescription().c_str());
                    continue;
                }
                int fd = sfd.get();
                struct epoll_event ev{};
                ev.events = EPOLLIN;
                ev.data.fd = fd;
                if (epoll_ctl(epfd, EPOLL_CTL_ADD, fd, &ev) < 0) {
                    LC_ALOGW("monitor: epoll_ctl ADD failed for minor=%d fd=%d: %s",
                             minor, fd, strerror(errno));
                    ::close(fd);
                    continue;
                }
                sfd.set(-1);  /* 所有权移交 epoll 表 */
                newMap[minor] = fd;
            }
            minorToFd = std::move(newMap);
            deviceMinors = std::move(newMinors);
        };

        while (true) {
            std::this_thread::sleep_until(next_tick);   /* LCD-018：固定节拍 */
            next_tick += kTickPeriod;
            tick++;
            hal = hal_client_.get();
            if (!hal) {
                /* LCD-007：日志节流（200 tick 一次 = 10s 一条）——HAL 故障
                 * 期间每 50ms 一条 WARNING 会以 1200 条/分钟淹没 logcat，
                 * 掩盖其他关键日志（与 hal_client 重连失败节流同策略） */
                if (tick % 200 == 0)
                    LC_ALOGW("monitor: HAL not connected, skipping cycle (tick=%d)", tick);
                continue;
            }

            /* R-16 P4 方向 4 修复：HAL 实例变化（重启/重连）时立即重建
             * epoll 注册表——原实现仅在每 200 tick（10s）重建，HAL 重启后
             * daemon 持有的旧 dup fd 指向已失效的内核 file 结构，事件
             * wake_up 队列失联，重建前窗口内事件全部溢出（event_drop 飙升，
             * lciod_check --mode stats/delta 判红）。hal_client 重连后 get()
             * 返回新实例（shared_ptr 地址变化），据此即时触发 rebuild。 */
            if (hal != lastHal) {
                lastHal = hal;
                rebuild_epoll(hal, /*force_redup=*/true);
            }

            /* 每 200 tick（10s）刷新设备列表 + 重建 epoll 注册表 */
            if (tick % 200 == 0)
                rebuild_epoll(hal);

            /* R-16 P4 方向 4 修复：设备断开时若恰逢 200 tick rebuild，
             * deviceMinors 会被清空（设备不在线 listDevices 返回空），此处
             * continue 会把 pendingRedup 处理与兜底轮询一并跳过，daemon 最长
             * 10s 不消费 → 重连窗口事件溢出（event_drop 飙升）。故不再因
             * deviceMinors 为空提前 continue——epoll_wait 空表仅 50ms 超时，
             * pendingRedup 仍每轮尝试重连注册，兜底轮询随 deviceMinors 更新
             * 自动恢复覆盖。 */

            /* R-16 P4 方向 4 修复：处理待重连队列——设备热插拔（EPOLLHUP）
             * 后每轮尝试重新 dupEventFd 注册，设备一重连立即恢复消费。
             * 未重连时 dupEventFd 返回 ENODEV，留在队列下轮再试。
             *
             * 消费策略：设备重连后的初始化事件须及时读走，否则 ring 溢出
             * （event_drop 飙升）。这里先经 hal->readEvent(0) 消费（HAL
             * readEvent 内部 resolve_device + 惰性 reopen，设备一在线即
             * 可读，不依赖 epoll 就绪信号），再尝试 dupEventFd 恢复 epoll
             * 注册。设备未恢复时 readEvent 返回 -ENODEV，留在队列。 */
            if (!pendingRedup.empty()) {
                auto it = pendingRedup.begin();
                while (it != pendingRedup.end()) {
                    int32_t pminor = *it;
                    /* 先消费事件（HAL 内部自动 reopen，防重连窗口溢出） */
                    VendorIoEvent vev;
                    auto rst = hal->readEvent(pminor, 0, &vev);
                    if (!rst.isOk()) {
                        ++it;  /* 设备未恢复（-ENODEV），下轮再试 */
                        continue;
                    }
                    /* 设备已恢复：重新 dupEventFd 注册 epoll（恢复及时性） */
                    ndk::ScopedFileDescriptor sfd;
                    auto st = hal->dupEventFd(pminor, &sfd);
                    if (!st.isOk() || sfd.get() < 0) {
                        ++it;  /* dup 失败，下轮再试（事件仍经 readEvent 消费） */
                        continue;
                    }
                    int nfd = sfd.get();
                    struct epoll_event nev{};
                    nev.events = EPOLLIN;
                    nev.data.fd = nfd;
                    if (epoll_ctl(epfd, EPOLL_CTL_ADD, nfd, &nev) < 0) {
                        /* 设备断开时 DEL 可能未真正移除旧 fd（EPOLLHUP 后
                         * fd 状态异常），fd 号复用后 ADD 报 EEXIST——先幂等
                         * DEL 再重试 ADD，消除残留注册 */
                        if (errno == EEXIST) {
                            epoll_ctl(epfd, EPOLL_CTL_DEL, nfd, nullptr);
                            if (epoll_ctl(epfd, EPOLL_CTL_ADD, nfd, &nev) == 0) {
                                sfd.set(-1);
                                minorToFd[pminor] = nfd;
                                if (std::find(deviceMinors.begin(), deviceMinors.end(),
                                              pminor) == deviceMinors.end())
                                    deviceMinors.push_back(pminor);
                                LC_ALOGI("monitor: device minor=%d reconnected via pendingRedup (after DEL)", pminor);
                                it = pendingRedup.erase(it);
                                continue;
                            }
                        }
                        LC_ALOGW("monitor: epoll_ctl ADD failed for minor=%d nfd=%d: %s",
                                 pminor, nfd, strerror(errno));
                        ::close(nfd);
                        ++it;
                        continue;
                    }
                    sfd.set(-1);  /* 所有权移交 epoll 表 */
                    minorToFd[pminor] = nfd;
                    if (std::find(deviceMinors.begin(), deviceMinors.end(),
                                  pminor) == deviceMinors.end())
                        deviceMinors.push_back(pminor);
                    LC_ALOGI("monitor: device minor=%d reconnected via pendingRedup", pminor);
                    it = pendingRedup.erase(it);
                }
            }

            /* R-16 P4 方向 4：epoll 统一等待（50ms 超时 = 节拍），就绪 fd
             * 非阻塞拉取事件。替代原逐设备串行 readEvent(50) 阻塞。 */
            constexpr int kMaxEvents = 16;
            struct epoll_event ready[kMaxEvents];
            int nready = epoll_wait(epfd, ready, kMaxEvents, 50);
            if (nready < 0) {
                if (errno == EINTR)
                    continue;
                int saved = errno;
                LC_ALOGE("monitor: epoll_wait failed: %s (epfd=%d)", strerror(saved), epfd);
                /* CXX-004：epoll 致命错误（EBADF 等）4 步退出——置线程不可
                 * 达前以 ERROR 日志 + exit(1) 交 init 重启（进程为 oneshot，
                 * 无 alive 标志/等待者需通知，ERROR+exit 即完整退出协议） */
                std::exit(1);
            }

            /* 就绪 fd → minor 反查 → 非阻塞拉取（timeout=0） */
            for (int i = 0; i < nready; i++) {
                int fd = ready[i].data.fd;
                int32_t minor = -1;
                for (auto& [m, f] : minorToFd) {
                    if (f == fd) { minor = m; break; }
                }
                if (minor < 0)
                    continue;  /* fd 已过期（重建竞态），跳过 */

                if (ready[i].events & (EPOLLHUP | EPOLLERR)) {
                    LC_ALOGW("monitor: device minor=%d fd=%d disconnected", minor, fd);
                    epoll_ctl(epfd, EPOLL_CTL_DEL, fd, nullptr);
                    ::close(fd);
                    minorToFd.erase(minor);
                    /* 加入待重连队列：设备重连后立即重新注册（不等 200 tick
                     * 重建），消除消费空窗内的事件溢出 */
                    if (std::find(pendingRedup.begin(), pendingRedup.end(), minor) ==
                        pendingRedup.end())
                        pendingRedup.push_back(minor);
                    continue;
                }
                if (!(ready[i].events & EPOLLIN))
                    continue;

                VendorIoEvent vev;
                auto ev_status = hal->readEvent(minor, 0, &vev);
                if (ev_status.isOk() && vev.valid) {
                    /* R-14 方向 1：事件类型/方向名取自共享事件枚举头
                     * （vendor_lechao_usbd-ioctl.h），不再 switch 硬编码数值 */
                    const char *type_name = vendor_lechao_usbd_event_type_name(
                        static_cast<uint32_t>(vev.eventType));
                    const char *dir = vendor_lechao_usbd_data_direction_name(
                        static_cast<uint8_t>(vev.dataDirection));

                    /* R-12 方向 2：专属 tag 可配置级别（INFO 生产可见，
                     * debug 时 DEBUG），事件不再静默。
                     * R-14 方向 1/3/4：事件日志带 SCSI 上下文（opcode/lba/
                     * bytes/retry）、wall 双时间戳与降级基线/阈值（RATE_DEGRADED
                     * 时 eventValue=当前速率、status=阈值、lba=基线） */
                    EVENT_ALOG("event: minor=%d type=%s(%d) val=%d dir=%s "
                               "status=%d mono=%llu wall=%llu opcode=%d lba=%llu "
                               "bytes=%d retry=%d", minor,
                               type_name, vev.eventType, vev.eventValue, dir,
                               vev.status,
                               (unsigned long long)vev.timestampNs,
                               (unsigned long long)vev.wallTimeNs,
                               vev.opcode, (unsigned long long)vev.lba,
                               vev.bytes, vev.retry);
                } else if (!ev_status.isOk()) {
                    /* 单设备失败仅告警，继续下一个设备 */
                    LC_ALOGW("monitor: readEvent failed for minor=%d: %s", minor,
                          ev_status.getDescription().c_str());
                }
            }

            /* R-16 P4 方向 4 修复：全设备兜底轮询——epoll 依赖 fd 就绪信号，
             * 设备断开/重连（authorized 切换）时 fd 失效后 epoll 不再触发，
             * 重连后的初始化事件无人消费致 ring 溢出。此处对全部在线设备
             * 无条件补一次 readEvent(0)（非阻塞）：HAL readEvent 内部
             * resolve_device + 惰性 reopen，设备重连后自动恢复消费；正常态
             * 空 ring 快速返回（binder 本地调用开销可忽略）。epoll 负责
             * 及时唤醒（事件到达即读），此兜底负责完备性（热插拔/竞态下
             * 保证消费），双保险消除消费空窗。 */
            for (int32_t minor : deviceMinors) {
                VendorIoEvent vev;
                auto ev_status = hal->readEvent(minor, 0, &vev);
                if (ev_status.isOk() && vev.valid) {
                    const char *type_name = vendor_lechao_usbd_event_type_name(
                        static_cast<uint32_t>(vev.eventType));
                    const char *dir = vendor_lechao_usbd_data_direction_name(
                        static_cast<uint8_t>(vev.dataDirection));
                    EVENT_ALOG("event: minor=%d type=%s(%d) val=%d dir=%s "
                               "status=%d mono=%llu wall=%llu opcode=%d lba=%llu "
                               "bytes=%d retry=%d", minor,
                               type_name, vev.eventType, vev.eventValue, dir,
                               vev.status,
                               (unsigned long long)vev.timestampNs,
                               (unsigned long long)vev.wallTimeNs,
                               vev.opcode, (unsigned long long)vev.lba,
                               vev.bytes, vev.retry);
                }
                /* 热插拔期间 readEvent 返回 -ENODEV 属正常（设备未恢复），
                 * 静默跳过，不刷告警 */
            }

            /* 每 200 tick（10s）打印统计信息 */
            if (tick % 200 == 0) {
                for (int32_t minor : deviceMinors) {
                    aidl::vendor::lechao::lciod::IoStats stats;
                    auto st_status = hal->getStats(minor, &stats);
                    if (!st_status.isOk()) {
                        ALOGW("monitor: getStats failed for minor=%d", minor);
                        /* 统计失败不清快照——下 tick 差分仍基于本 tick，
                         * 避免失败窗口被误计为 0 速率假低谷 */
                        continue;  /* 跳过该设备统计，继续下一个 */
                    }

                    /*
                     * R-12 方向 3：差分时间桶吞吐。取本 tick 累计与上一
                     * 统计 tick 快照之差为窗口增量，算近 10s 即时速率；
                     * 新接入/回绕回退全程累计（ComputeWindowKbRate 内敛）。
                     * 旧实现累计平均会把变慢窗口摊平，无法定位跌落时刻。
                     */
                    auto it = tickSnap.find(minor);
                    uint64_t rb = stats.readBytes, rn = stats.readNs;
                    uint64_t wb = stats.writeBytes, wn = stats.writeNs;
                    uint64_t prevRb = 0, prevRn = 0, prevWb = 0, prevWn = 0;
                    if (it != tickSnap.end())
                    {
                        prevRb = it->second.readBytes;
                        prevRn = it->second.readNs;
                        prevWb = it->second.writeBytes;
                        prevWn = it->second.writeNs;
                    }
                    tickSnap[minor] = {rb, rn, wb, wn};

                    /* 计算 KB/s 速率（换算核心收敛到 ComputeKbRate/ComputeWindowKbRate） */
                    uint64_t read_rate = ComputeWindowKbRate(rb, rn, prevRb, prevRn);
                    uint64_t write_rate = ComputeWindowKbRate(wb, wn, prevWb, prevWn);
                    /* R-14 方向 2：读/写方向 IO 错误率（‰）——错误数/该方向总 IO */
                    uint64_t read_err_rate = ComputeErrorRate(stats.readErrorCount,
                                                              stats.readCmds);
                    uint64_t write_err_rate = ComputeErrorRate(stats.writeErrorCount,
                                                               stats.writeCmds);

                    ALOGI("monitor: minor=%d read_rate=%llu KB/s, write_rate=%llu KB/s, "
                          "rx_pkts=%lld, tx_pkts=%lld, event_drop=%lld, "
                          "read_err_rate=%llu‰, write_err_rate=%llu‰",
                          minor,
                          (unsigned long long)read_rate, (unsigned long long)write_rate,
                          (long long)stats.readCmds, (long long)stats.writeCmds,
                          (long long)stats.eventDropCount,
                          (unsigned long long)read_err_rate,
                          (unsigned long long)write_err_rate);
                }
            }
        }
    }).detach();
}
