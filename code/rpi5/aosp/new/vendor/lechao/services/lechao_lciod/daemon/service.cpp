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
 * 线程模型: 主线程进入 Binder 线程池（R-17 后 4 线程）处理 RPC +
 *           per-minor 分片监控线程池（R-17 后固定 4 分片 worker，见
 *           start_monitor）定期读取事件和统计，打印到 logcat
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
#include <cstdlib>
#include <string>
#include <sys/epoll.h>
#include <sys/system_properties.h>
#include <unistd.h>
#include <aidl/system/lechao/lciod/BnIoService.h>
#include <aidl/system/lechao/lciod/IIoService.h>
#include <aidl/vendor/lechao/lciod/IIoHal.h>
#include <aidl/vendor/lechao/lciod/IoEvent.h>
#include "hal_client.h"
#include "minor_utils.h"
#include "lechao_log.h"
/* R2 方向 5+6：全局链路事件监控（独立于 per-device 分片） */
#include "link_monitor.h"
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

/*
 * ShouldEmitStorm — 规则三触发判定（60s 窗口累计达阈，R3 方向4）
 * 契约: (stall+timeout) >= threshold。
 * 输入防御（CXX-002）: 窗口计数为 60s 内事件数（阈值 10 级），正常累计
 *       远小于 UINT64_MAX，stall+timeout 相加不会回绕；仍先判单边达阈
 *       短路，再求两数之和，防御式避免无符号加法回绕。
 */
bool ShouldEmitStorm(const StormWindow& w, uint64_t threshold) {
    if (w.stall >= threshold || w.timeout >= threshold)
        return true;
    return w.stall + w.timeout >= threshold;
}

/*
 * ParseFaultInjectValue — 解析用户态合成注入 sysprop 值（R3 方向7）
 * 格式: "stall:<n>" 或 "timeout:<n>"；n 为十进制数字（无符号）。
 * 输入防御（CXX-003）: 前缀精确匹配 + 数字段逐字符校验（禁符号/空白/空值），
 *       非该格式返回 false 且 out 不写入。
 * 溢出防御（CXX-002）: n 超 uint64 上限返回 false。
 */
bool ParseFaultInjectValue(const std::string& val, FaultInjectValue* out) {
    if (out == nullptr)
        return false;
    static const std::string kStallPrefix = "stall:";
    static const std::string kTimeoutPrefix = "timeout:";
    bool is_stall = false;
    std::string::size_type prefixLen = 0;
    if (val.compare(0, kStallPrefix.size(), kStallPrefix) == 0) {
        is_stall = true;
        prefixLen = kStallPrefix.size();
    } else if (val.compare(0, kTimeoutPrefix.size(), kTimeoutPrefix) == 0) {
        is_stall = false;
        prefixLen = kTimeoutPrefix.size();
    } else {
        return false;
    }
    const char* num = val.c_str() + prefixLen;
    if (*num == '\0')
        return false;  /* 缺数字段 */
    uint64_t n = 0;
    for (const char* p = num; *p != '\0'; ++p) {
        if (*p < '0' || *p > '9')
            return false;  /* 非数字字符（符号/空白/字母）一律拒绝 */
        uint64_t d = static_cast<uint64_t>(*p - '0');
        if (n > (UINT64_MAX - d) / 10)
            return false;  /* 超 uint64 上限（CXX-002 溢出防御） */
        n = n * 10 + d;
    }
    out->is_stall = is_stall;
    out->count = n;
    return true;
}

/* --- 字段投影纯函数（声明见 service.h，独立于 binder 环境可单测） --- */

void ProjectSystemIoStats(const aidl::vendor::lechao::lciod::IoStats& vstats,
                          aidl::system::lechao::lciod::IoStats* out) {    out->vid = vstats.vid;
    out->pid = vstats.pid;
    out->protocol = vstats.protocol;   /* R1 UAS 维测：传输协议直传（BOT=0/UAS=1） */
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
    /* R2 方向 5+6：启动独立全局链路监控线程（LinkMonitorRun，detach）。
     * 与 start_monitor 的 per-device 4 分片并行，互不影响——链路事件来自
     * 全局节点 /dev/vendor_lechao_usbd_link（lciod_link），非 per-device
     * 通道；线程随进程生命周期终止（进程非 oneshot，init 自动重启）。 */
    std::thread(::lechao::lciod::LinkMonitorRun).detach();
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
 * 约天级，未经钳位可占死 daemon binder 池；R-17 后池 4 线程仍须防御） */
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
 * start_monitor — 启动 per-minor 分片监控线程池
 *
 * R-17 方向 1：per-minor 分片 + 线程池异步化——消 daemon 单线程单点。
 * 原实现 1 个监控线程单线程串行消费全部 minor（epoll 就绪后逐设备
 * readEvent + 全设备兜底轮询 + 全设备统计），任一设备 readEvent 慢
 * （binder 跨进程调用被 HAL 侧卡住）会阻塞整轮其他设备消费，daemon
 * 事件消费呈单线程单点。
 *
 * 改造为固定 kShardCount 个分片 worker 线程（与 daemon Binder 池 4 对齐）：
 *   - 按 minor % kShardCount 分片，每 worker 独立 epoll/注册表/统计快照/
 *     pendingRedup，单设备慢只阻塞其所在分片，其他分片不受影响；
 *   - 每 worker 各自维护 lastHal 检测 HAL 重启即时重建（保留 R-16 修复）；
 *   - 200 tick 重建/统计/pendingRedup/兜底轮询语义在分片内原样保留；
 *   - 线程 detach，随进程生命周期自动终止。
 *
 * 线程行为（R-16 P4 方向 4：epoll 多路复用，分片内）：
 *   - 每 10s（200 tick）刷新设备列表（过滤本分片）：经 dupEventFd 获取
 *     各设备内核事件 fd 并 EPOLL_CTL_ADD 注册到本分片 epoll
 *   - 主循环 epoll_wait(50ms) 统一等待本分片设备事件就绪
 *   - 就绪 fd 反查 minor，调 hal->readEvent(minor, 0) 非阻塞拉取；
 *     EPOLLHUP（设备断开）DEL + close fd + 入 pendingRedup 待重连
 *   - 每 10s（200 tick）打印统计信息（monitor: minor= 日志格式保留，
 *     lciod-liveness 验收判据依赖）；差分时间桶吞吐逻辑原样保留
 *   - 单设备失败仅跳过，不中断本轮其他设备
 *
 * NOTE: detach 线程无独立退出条件，但本进程为**非 oneshot** 服务
 * （LCD-005，init 自动重启），进程退出时线程自动终止，不存在生命周期风险。
 */
void IoServiceImpl::start_monitor() {
    /* R-17 方向 1：分片数固定为 4（与 daemon Binder 池 4 对齐）。USB 设备
     * minor 通常 0..n，按 minor % 4 均匀散列到 4 个独立消费线程。 */
    constexpr int kShardCount = 4;
    for (int shard = 0; shard < kShardCount; shard++) {
        std::thread([this, shard]() {
            /* 本分片归属判定：minor % kShardCount == shard */
            auto belongs = [&](int32_t m) { return (m % kShardCount) == shard; };

            int tick = 0;
            std::vector<int32_t> deviceMinors;  /* 本分片活跃设备 minor 列表 */
            /* R-16 P4 方向 4：记录上一次成功的 HAL 实例——实例变化（重启/重连）
             * 时立即重建 epoll，避免 200 tick 重建前的 fd 失效窗口事件溢出 */
            std::shared_ptr<aidl::vendor::lechao::lciod::IIoHal> lastHal = nullptr;
            /* R-16 P4 方向 4 修复：设备热插拔待重连队列（分片内独立）。 */
            std::vector<int32_t> pendingRedup;
            /* R-16 P4 方向 4：minor → epoll 注册的事件 fd 映射（分片内独立） */
            std::unordered_map<int32_t, int> minorToFd;

            /*
             * R-12 方向 3：10 秒 tick 差分时间桶吞吐（分片内独立快照）。
             */
            struct TickSnapshot
            {
                uint64_t readBytes = 0, readNs = 0, writeBytes = 0, writeNs = 0;
            };
            std::unordered_map<int32_t, TickSnapshot> tickSnap;

            /*
             * R3 方向4：per-minor 60s 滑动窗口风暴计数（规则三）。
             * 6 槽环形桶（每槽 10s）：每 200 tick（10s）推进槽位并计算
             * Δstall/Δtimeout 加入当前槽；窗口累计 = 6 槽之和，达阈值
             * kStormThreshold 触发风暴事件（EVENT_ALOG JSON 行）。
             * 用户态合成注入（方向7）每 50ms tick 读 sysprop
             * sys.lechao.lciod.fault_inject，值变化时把 n 加入当前槽
             * （幂等一次性，不落内核计数）。
             * NOTE: 仅本分片 worker 线程访问，无锁。
             */
            struct StormBucket {
                uint64_t stall = 0;    /* 槽内 STALL 事件数 */
                uint64_t timeout = 0;  /* 槽内 timeout 事件数 */
            };
            struct StormState {
                StormBucket buckets[kStormWindowSlots];  /* 环形槽位 */
                int slot = 0;            /* 当前槽下标（10s 推进一次） */
                uint64_t prevStall = 0;  /* 上一统计 tick 的 stallCount */
                uint64_t prevTimeout = 0;/* 上一统计 tick 的 timeoutCount */
                bool firstSample = true; /* 首次采样只存 prev 不产生 Δ */
                bool stormArmed = true;  /* 窗口累计回落阈值后重新武装 */
            };
            std::unordered_map<int32_t, StormState> stormStates;
            /* 最近一次已处理的注入值（值变化才处理，幂等去重） */
            std::string lastInjectValue;

            /* R-16 P4 方向 4：epoll 多路复用——单次 epoll_wait(50ms) 统一等待
             * 本分片设备事件。 */
            int epfd = epoll_create1(EPOLL_CLOEXEC);
            if (epfd < 0) {
                LC_ALOGE("monitor[%d]: epoll_create1 failed: %s", shard, strerror(errno));
                return;
            }

            /* LCD-018：绝对时间对齐调度。epoll_wait 超时 50ms 作为固定节拍。 */
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
                        if (ParseMinorFromPath(path, &minor) && belongs(minor))
                            deviceMinors.push_back(minor);
                    }
                }
            }

            /* R-16 P4 方向 4：每 200 tick 重建 epoll 注册表（分片内）。
             * force_redup=true 时强制重新 dupEventFd（HAL 实例变化场景）。 */
            auto rebuild_epoll = [&](const std::shared_ptr<aidl::vendor::lechao::lciod::IIoHal>& h,
                                     bool force_redup = false) {
                std::vector<std::string> devices;
                auto dev_status = h->listDevices(&devices);
                std::vector<int32_t> newMinors;
                if (dev_status.isOk()) {
                    for (auto& path : devices) {
                        int32_t minor = -1;
                        if (ParseMinorFromPath(path, &minor) && belongs(minor))
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
                        LC_ALOGW("monitor[%d]: dupEventFd failed for minor=%d: %s", shard,
                                 minor, st.getDescription().c_str());
                        continue;
                    }
                    int fd = sfd.get();
                    struct epoll_event ev{};
                    ev.events = EPOLLIN;
                    ev.data.fd = fd;
                    if (epoll_ctl(epfd, EPOLL_CTL_ADD, fd, &ev) < 0) {
                        LC_ALOGW("monitor[%d]: epoll_ctl ADD failed for minor=%d fd=%d: %s",
                                 shard, minor, fd, strerror(errno));
                        ::close(fd);
                        continue;
                    }
                    sfd.set(-1);  /* 所有权移交 epoll 表 */
                    newMap[minor] = fd;
                }
                minorToFd = std::move(newMap);
                deviceMinors = std::move(newMinors);

                /* R3 方向4：设备离线时清理其风暴窗口状态，防 per-minor
                 * 状态无限增长（CXX-002 生命周期管理） */
                for (auto it = stormStates.begin(); it != stormStates.end();) {
                    if (std::find(deviceMinors.begin(), deviceMinors.end(), it->first) ==
                        deviceMinors.end())
                        it = stormStates.erase(it);
                    else
                        ++it;
                }
            };

            while (true) {
                std::this_thread::sleep_until(next_tick);   /* LCD-018：固定节拍 */
                next_tick += kTickPeriod;
                tick++;
                hal = hal_client_.get();
                if (!hal) {
                    /* LCD-007：日志节流（200 tick 一次 = 10s 一条） */
                    if (tick % 200 == 0)
                        LC_ALOGW("monitor[%d]: HAL not connected, skipping cycle (tick=%d)",
                                 shard, tick);
                    continue;
                }

                /* R-16 P4 方向 4 修复：HAL 实例变化时立即重建本分片 epoll */
                if (hal != lastHal) {
                    lastHal = hal;
                    rebuild_epoll(hal, /*force_redup=*/true);
                }

                /* 每 200 tick（10s）刷新设备列表 + 重建 epoll 注册表 */
                if (tick % 200 == 0)
                    rebuild_epoll(hal);

                /* R3 方向7：用户态合成注入——每 50ms tick 读 sysprop
                 * sys.lechao.lciod.fault_inject（"stall:<n>"/"timeout:<n>"），
                 * 值变化时把 n 直接加进本分片各设备当前风暴桶（幂等一次性，
                 * lastInjectValue 更新；不落内核计数，__system_property_get
                 * 读无需 sepolicy）。 */
                {
                    /* PROP_VALUE_MAX 为属性值上限（92），缓冲区必须按上限开
                     * 防止 __system_property_get 溢出（CXX-002 边界防御） */
                    char injectBuf[PROP_VALUE_MAX] = {0};
                    __system_property_get("sys.lechao.lciod.fault_inject", injectBuf);
                    std::string injectVal(injectBuf);
                    if (injectVal != lastInjectValue) {
                        lastInjectValue = injectVal;
                        if (!injectVal.empty()) {
                            FaultInjectValue fv;
                            if (!ParseFaultInjectValue(injectVal, &fv)) {
                                /* 解析失败：非该格式，忽略并告警一次（值变化才
                                 * 走到这里，天然去重不刷屏；CXX-003 外部输入防御） */
                                LC_ALOGE("storm: invalid fault_inject value '%s' "
                                         "(expect stall:<n> or timeout:<n>)",
                                         injectVal.c_str());
                            } else {
                                for (int32_t minor : deviceMinors) {
                                    StormState& ss =
                                        stormStates.emplace(minor, StormState{}).first->second;
                                    if (fv.is_stall)
                                        ss.buckets[ss.slot].stall += fv.count;
                                    else
                                        ss.buckets[ss.slot].timeout += fv.count;
                                }
                                LC_ALOGI("storm: fault_inject applied %s:%llu to %zu device(s)",
                                         fv.is_stall ? "stall" : "timeout",
                                         (unsigned long long)fv.count, deviceMinors.size());
                            }
                        }
                    }
                }

                /* R-16 P4 方向 4 修复：处理待重连队列（分片内独立） */
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
                                    LC_ALOGI("monitor[%d]: device minor=%d reconnected via pendingRedup (after DEL)",
                                             shard, pminor);
                                    it = pendingRedup.erase(it);
                                    continue;
                                }
                            }
                            LC_ALOGW("monitor[%d]: epoll_ctl ADD failed for minor=%d nfd=%d: %s",
                                     shard, pminor, nfd, strerror(errno));
                            ::close(nfd);
                            ++it;
                            continue;
                        }
                        sfd.set(-1);  /* 所有权移交 epoll 表 */
                        minorToFd[pminor] = nfd;
                        if (std::find(deviceMinors.begin(), deviceMinors.end(),
                                      pminor) == deviceMinors.end())
                            deviceMinors.push_back(pminor);
                        LC_ALOGI("monitor[%d]: device minor=%d reconnected via pendingRedup",
                                 shard, pminor);
                        it = pendingRedup.erase(it);
                    }
                }

                /* R-16 P4 方向 4：epoll 统一等待（50ms 超时 = 节拍） */
                constexpr int kMaxEvents = 16;
                struct epoll_event ready[kMaxEvents];
                int nready = epoll_wait(epfd, ready, kMaxEvents, 50);
                if (nready < 0) {
                    if (errno == EINTR)
                        continue;
                    int saved = errno;
                    LC_ALOGE("monitor[%d]: epoll_wait failed: %s (epfd=%d)",
                             shard, strerror(saved), epfd);
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
                        LC_ALOGW("monitor[%d]: device minor=%d fd=%d disconnected",
                                 shard, minor, fd);
                        epoll_ctl(epfd, EPOLL_CTL_DEL, fd, nullptr);
                        ::close(fd);
                        minorToFd.erase(minor);
                        /* 加入待重连队列：设备重连后立即重新注册 */
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
                        /* R-14 方向 1：事件类型/方向名取自共享事件枚举头 */
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
                    } else if (!ev_status.isOk()) {
                        /* 单设备失败仅告警，继续下一个设备 */
                        LC_ALOGW("monitor[%d]: readEvent failed for minor=%d: %s", shard,
                                 minor, ev_status.getDescription().c_str());
                    }
                }

                /* R-16 P4 方向 4 修复：本分片全设备兜底轮询——epoll 依赖 fd 就绪
                 * 信号，设备断开/重连（authorized 切换）时 fd 失效后 epoll 不再
                 * 触发，重连后的初始化事件无人消费致 ring 溢出。 */
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

                /* 每 200 tick（10s）打印本分片统计信息 */
                if (tick % 200 == 0) {
                    for (int32_t minor : deviceMinors) {
                        aidl::vendor::lechao::lciod::IoStats stats;
                        auto st_status = hal->getStats(minor, &stats);
                        if (!st_status.isOk()) {
                            ALOGW("monitor[%d]: getStats failed for minor=%d", shard, minor);
                            /* 统计失败不清快照——下 tick 差分仍基于本 tick，
                             * 避免失败窗口被误计为 0 速率假低谷 */
                            continue;  /* 跳过该设备统计，继续下一个 */
                        }

                        /*
                         * R-12 方向 3：差分时间桶吞吐（分片内快照）。
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

                        /*
                         * R3 方向4：规则三风暴判定——per-minor 60s 滑动窗口。
                         */
                        StormState& ss =
                            stormStates.emplace(minor, StormState{}).first->second;
                        /* 推进槽位（环形，10s 一格），清空即将覆盖的旧槽 */
                        ss.slot = (ss.slot + 1) % kStormWindowSlots;
                        ss.buckets[ss.slot] = StormBucket{};
                        if (!ss.firstSample) {
                            /* Δ 差分（CXX-002：counter reset——curr<prev 时 Δ=curr） */
                            uint64_t dStall = stats.stallCount >= ss.prevStall
                                ? stats.stallCount - ss.prevStall : stats.stallCount;
                            uint64_t dTimeout = stats.timeoutCount >= ss.prevTimeout
                                ? stats.timeoutCount - ss.prevTimeout : stats.timeoutCount;
                            ss.buckets[ss.slot].stall += dStall;
                            ss.buckets[ss.slot].timeout += dTimeout;
                        }
                        ss.prevStall = stats.stallCount;
                        ss.prevTimeout = stats.timeoutCount;
                        ss.firstSample = false;
                        /* 窗口累计 = 6 槽 stall+timeout 之和（60s 内事件数，
                         * 相加不会回绕，CXX-002） */
                        StormWindow win{0, 0};
                        for (int i = 0; i < kStormWindowSlots; i++) {
                            win.stall += ss.buckets[i].stall;
                            win.timeout += ss.buckets[i].timeout;
                        }
                        if (ShouldEmitStorm(win, kStormThreshold)) {
                            /* 防重复刷：窗口累计回落阈值前只发一次 */
                            if (ss.stormArmed) {
                                ss.stormArmed = false;
                                uint64_t stormCount = win.stall + win.timeout;
                                char line[160];
                                int n = snprintf(line, sizeof(line),
                                    "{\"rule\":\"storm\",\"device\":%d,\"count\":%llu,"
                                    "\"window_s\":%d,\"threshold\":%llu}",
                                    minor, (unsigned long long)stormCount,
                                    kStormWindowSeconds, (unsigned long long)kStormThreshold);
                                if (n < 0 || n >= static_cast<int>(sizeof(line))) {
                                    /* CXX-002：snprintf 截断则丢弃整行，不打半截 JSON */
                                    LC_ALOGE("storm: JSON line truncated, drop (minor=%d)", minor);
                                } else {
                                    EVENT_ALOG("%s", line);
                                    LC_ALOGI("storm: rule triggered minor=%d window60s_count=%llu "
                                             "threshold=%llu", minor,
                                             (unsigned long long)stormCount,
                                             (unsigned long long)kStormThreshold);
                                }
                            }
                        } else {
                            ss.stormArmed = true;  /* 窗口累计回落阈值，允许再次触发 */
                        }

                        uint64_t read_rate = ComputeWindowKbRate(rb, rn, prevRb, prevRn);
                        uint64_t write_rate = ComputeWindowKbRate(wb, wn, prevWb, prevWn);
                        /* R-14 方向 2：读/写方向 IO 错误率（‰） */
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
}
