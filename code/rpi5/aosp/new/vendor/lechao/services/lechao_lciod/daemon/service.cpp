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
#include <aidl/system/lechao/lciod/BnIoService.h>
#include <aidl/system/lechao/lciod/IIoService.h>
#include <aidl/vendor/lechao/lciod/IIoHal.h>
#include <aidl/vendor/lechao/lciod/IoEvent.h>
#include "hal_client.h"
#include "minor_utils.h"
#include "lechao_log.h"

#define LOG_TAG "lechao_lciod"
#include <log/log.h>

using namespace ndk;
using aidl::system::lechao::lciod::BnIoService;
using aidl::system::lechao::lciod::IoStats;
using aidl::system::lechao::lciod::IoConfig;
using aidl::system::lechao::lciod::IoEvent;
using VendorIoEvent = aidl::vendor::lechao::lciod::IoEvent;
using lechao::lciod::ParseMinorFromPath;

/* --- 纯计算函数（声明见 service.h，独立于 binder 环境可单测） --- */

int64_t ComputeAverageRate(uint64_t readBytes, uint64_t writeBytes,
                           uint64_t readNs, uint64_t writeNs) {
    // 中间量用 __uint128_t：total/totalNs 累计约 17GiB 时 total*1e9 超出
    // uint64 上限回绕致速率失真，128 位中间量消除溢出（CXX-001 数值正确性）
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

/* --- 字段投影纯函数（声明见 service.h，独立于 binder 环境可单测） --- */

void ProjectSystemIoStats(const aidl::vendor::lechao::lciod::IoStats& vstats,
                          aidl::system::lechao::lciod::IoStats* out) {
    out->vid = vstats.vid;
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
    out->probeCount = vstats.probeCount;
    out->disconnectCount = vstats.disconnectCount;
    out->degradeCount = vstats.degradeCount;
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
        if (ParseMinorFromPath(path, &minor))
            _aidl_return->push_back(minor);
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

/* readIoEvent — 代理转发到 HAL readEvent()，1:1 字段直传 */
ndk::ScopedAStatus IoServiceImpl::readIoEvent(int32_t in_deviceMinor, int32_t in_timeoutMs, IoEvent* _aidl_return) {
    *_aidl_return = {};
    auto hal = hal_client_.get();
    if (!hal) { LC_ALOGW("readIoEvent: HAL not connected"); return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
    aidl::vendor::lechao::lciod::IoEvent vev;
    auto status = hal->readEvent(in_deviceMinor, in_timeoutMs, &vev);
    if (!status.isOk()) { LC_ALOGW("readIoEvent: readEvent failed"); return status; }
    ProjectSystemIoEvent(vev, _aidl_return);
    return ndk::ScopedAStatus::ok();
}

/*
 * start_monitor — 启动后台监控线程
 *
 * 线程行为:
 *   - 每 50ms 轮询一次事件（readEvent, 50ms timeout）
 *   - 每 10s（200 个 tick）刷新设备列表和打印统计信息
 *   - 遍历所有活跃设备，不再只监控 devices[0]
 *   - 单设备调用失败仅跳过该设备，不中断本轮其他设备
 *   - 收到有效事件时打印事件详情到 logcat
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

        while (true) {
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
            tick++;
            hal = hal_client_.get();
            if (!hal) { LC_ALOGW("monitor: HAL not connected, skipping cycle"); continue; }

            /* 每 200 tick（10s）刷新设备列表 */
            if (tick % 200 == 0) {
                std::vector<std::string> devices;
                auto dev_status = hal->listDevices(&devices);
                deviceMinors.clear();
                if (dev_status.isOk()) {
                    for (auto& path : devices) {
                        int32_t minor = -1;
                        if (ParseMinorFromPath(path, &minor))
                            deviceMinors.push_back(minor);
                    }
                }
            }

            if (deviceMinors.empty()) continue;

            /* 遍历所有活跃设备，单设备失败不中断本轮 */
            for (int32_t minor : deviceMinors) {
                VendorIoEvent vev;
                auto ev_status = hal->readEvent(minor, 50, &vev);
                if (ev_status.isOk() && vev.valid) {
                    const char *type_name = "UNKNOWN";
                    switch (vev.eventType) {
                        case 1: type_name = "TRANSPORT_ERROR"; break;
                        case 2: type_name = "STALL"; break;
                        case 3: type_name = "DATA_CORRUPT"; break;
                        case 4: type_name = "TIMEOUT"; break;
                        case 5: type_name = "RESET"; break;
                        case 6: type_name = "RATE_DEGRADED"; break;
                    }
                    const char *dir = (vev.dataDirection == 1) ? "READ"
                                      : (vev.dataDirection == 2) ? "WRITE" : "NONE";

                    LC_ALOGD("event: minor=%d type=%s(%d) val=%d dir=%s ts=%llu",
                          minor, type_name, vev.eventType, vev.eventValue, dir,
                          (unsigned long long)vev.timestampNs);
                } else if (!ev_status.isOk()) {
                    /* 单设备失败仅告警，继续下一个设备 */
                    LC_ALOGW("monitor: readEvent failed for minor=%d: %s", minor,
                          ev_status.getDescription().c_str());
                }

                /* 每 200 tick（10s）打印统计信息 */
                if (tick % 200 == 0) {
                    aidl::vendor::lechao::lciod::IoStats stats;
                    auto st_status = hal->getStats(minor, &stats);
                    if (!st_status.isOk()) {
                        ALOGW("monitor: getStats failed for minor=%d", minor);
                        continue;  /* 跳过该设备统计，继续下一个 */
                    }

                    /* 计算 KB/s 速率（换算核心收敛到 ComputeKbRate，除零防护可单测） */
                    uint64_t read_rate = ComputeKbRate(stats.readBytes, stats.readNs);
                    uint64_t write_rate = ComputeKbRate(stats.writeBytes, stats.writeNs);

                    ALOGI("monitor: minor=%d read_rate=%llu KB/s, write_rate=%llu KB/s, "
                          "rx_pkts=%lld, tx_pkts=%lld",
                          minor,
                          (unsigned long long)read_rate, (unsigned long long)write_rate,
                          (long long)stats.readCmds, (long long)stats.writeCmds);
                }
            }
        }
    }).detach();
}
