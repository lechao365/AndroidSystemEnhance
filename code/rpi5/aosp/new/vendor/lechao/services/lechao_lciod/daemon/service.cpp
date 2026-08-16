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
 * ============================================================
 */
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

/*
 * IoServiceImpl — IIoService AIDL 接口的实现类
 *
 * 核心职责:
 *   1) 代理转发: 将 system 层调用转换为 vendor HAL 调用
 *   2) 字段投影: vendor IoStats → system IoStats（省略管理字段）
 *   3) 后台监控: start_monitor() 启动独立线程，定期轮询事件和统计
 */
class IoServiceImpl : public BnIoService {
public:
    /*
     * 构造函数仅做初始化，不启动监控线程。
     * 监控线程由 main() 在服务注册成功后显式调用 start() 启动，
     * 避免注册失败时 detach 线程已无法回收。
     */
    IoServiceImpl() = default;

    /*
     * start — 在 main() 服务注册成功后显式启动后台监控线程
     * 确保注册失败时不会留下 detach 线程。
     */
    void start() {
        start_monitor();
    }

    /*
     * listDeviceMinors — 返回所有在线设备的 minor 编号列表
     * 调用 HAL listDevices()，将路径列表转换为 minor 编号列表。
     */
    ndk::ScopedAStatus listDeviceMinors(std::vector<int32_t>* _aidl_return) override {
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
     * 公式: (readBytes + writeBytes) * 1e9 / (readNs + writeNs)
     * 单位: 字节/秒
     */
    ndk::ScopedAStatus getAverageRate(int32_t in_deviceMinor, int64_t* _aidl_return) override {
        auto hal = hal_client_.get();
        if (!hal) { LC_ALOGW("getAverageRate: HAL not connected"); *_aidl_return = 0; return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }

        aidl::vendor::lechao::lciod::IoStats stats;
        auto status = hal->getStats(in_deviceMinor, &stats);
        if (!status.isOk()) { LC_ALOGW("getAverageRate: getStats failed"); *_aidl_return = 0; return status; }

        uint64_t total = stats.readBytes + stats.writeBytes;
        uint64_t totalNs = stats.readNs + stats.writeNs;
        if (totalNs > 0)
            *_aidl_return = static_cast<int64_t>(total * 1000000000ULL / totalNs);
        else
            *_aidl_return = 0;
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
    ndk::ScopedAStatus getIoStats(int32_t in_deviceMinor, IoStats* _aidl_return) override {
        *_aidl_return = {};
        auto hal = hal_client_.get();
        if (!hal) { LC_ALOGW("getIoStats: HAL not connected"); return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
        aidl::vendor::lechao::lciod::IoStats vstats;
        auto status = hal->getStats(in_deviceMinor, &vstats);
        if (!status.isOk()) { LC_ALOGW("getIoStats: getStats failed"); return status; }
        _aidl_return->vid = vstats.vid;
        _aidl_return->pid = vstats.pid;
        _aidl_return->vendor = vstats.vendor;
        _aidl_return->product = vstats.product;
        _aidl_return->readBytes = vstats.readBytes;
        _aidl_return->readNs = vstats.readNs;
        _aidl_return->readCmds = vstats.readCmds;
        _aidl_return->writeBytes = vstats.writeBytes;
        _aidl_return->writeNs = vstats.writeNs;
        _aidl_return->writeCmds = vstats.writeCmds;
        _aidl_return->errorCount = vstats.errorCount;
        _aidl_return->resetCount = vstats.resetCount;
        _aidl_return->stallCount = vstats.stallCount;
        _aidl_return->corruptCount = vstats.corruptCount;
        _aidl_return->timeoutCount = vstats.timeoutCount;
        _aidl_return->probeCount = vstats.probeCount;
        _aidl_return->disconnectCount = vstats.disconnectCount;
        _aidl_return->degradeCount = vstats.degradeCount;
        _aidl_return->lastTransportLatencyNs = vstats.lastTransportLatencyNs;
        _aidl_return->lastEventTsNs = vstats.lastEventTsNs;
        _aidl_return->lastEventType = vstats.lastEventType;
        return ndk::ScopedAStatus::ok();
    }

    /* resetIoState — 代理转发到 HAL resetState() */
    ndk::ScopedAStatus resetIoState(int32_t in_deviceMinor) override {
        auto hal = hal_client_.get();
        if (!hal) { LC_ALOGW("resetIoState: HAL not connected"); return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
        return hal->resetState(in_deviceMinor);
    }

    /* getIoConfig — 代理转发到 HAL getConfig() */
    ndk::ScopedAStatus getIoConfig(int32_t in_deviceMinor, IoConfig* _aidl_return) override {
        *_aidl_return = {};
        auto hal = hal_client_.get();
        if (!hal) { LC_ALOGW("getIoConfig: HAL not connected"); return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
        aidl::vendor::lechao::lciod::IoConfig vcfg;
        auto status = hal->getConfig(in_deviceMinor, &vcfg);
        if (!status.isOk()) { LC_ALOGW("getIoConfig: getConfig failed"); return status; }
        _aidl_return->enabled = vcfg.enabled;
        _aidl_return->flags = vcfg.flags;
        return ndk::ScopedAStatus::ok();
    }

    /* setIoConfig — 代理转发到 HAL setConfig() */
    ndk::ScopedAStatus setIoConfig(int32_t in_deviceMinor, const IoConfig& in_config, bool* _aidl_return) override {
        auto hal = hal_client_.get();
        if (!hal) { LC_ALOGW("setIoConfig: HAL not connected"); *_aidl_return = false; return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
        aidl::vendor::lechao::lciod::IoConfig vcfg;
        vcfg.enabled = in_config.enabled;
        vcfg.flags = in_config.flags;
        return hal->setConfig(in_deviceMinor, vcfg, _aidl_return);
    }

    /* readIoEvent — 代理转发到 HAL readEvent()，1:1 字段直传 */
    ndk::ScopedAStatus readIoEvent(int32_t in_deviceMinor, int32_t in_timeoutMs, IoEvent* _aidl_return) override {
        *_aidl_return = {};
        auto hal = hal_client_.get();
        if (!hal) { LC_ALOGW("readIoEvent: HAL not connected"); return ndk::ScopedAStatus::fromServiceSpecificError(-ENODEV); }
        aidl::vendor::lechao::lciod::IoEvent vev;
        auto status = hal->readEvent(in_deviceMinor, in_timeoutMs, &vev);
        if (!status.isOk()) { LC_ALOGW("readIoEvent: readEvent failed"); return status; }
        _aidl_return->timestampNs = vev.timestampNs;
        _aidl_return->eventType = vev.eventType;
        _aidl_return->eventValue = vev.eventValue;
        _aidl_return->dataDirection = vev.dataDirection;
        _aidl_return->status = vev.status;
        _aidl_return->valid = vev.valid;
        return ndk::ScopedAStatus::ok();
    }

private:
    IoHalClient hal_client_; /* HAL Binder 客户端封装 */

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
    void start_monitor() {
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

                        /* 计算 KB/s 速率 */
                        auto calc_rate = [](long bytes, long ns) -> uint64_t {
                            if (ns == 0) return 0;
                            return (static_cast<uint64_t>(bytes) * 1000000000ULL)
                                   / static_cast<uint64_t>(ns) / 1024ULL;
                        };
                        uint64_t read_rate = calc_rate(stats.readBytes, stats.readNs);
                        uint64_t write_rate = calc_rate(stats.writeBytes, stats.writeNs);

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
};

/*
 * main — System Daemon 入口
 *
 * 职责:
 *   1) 创建 IoServiceImpl 实例并注册为 Binder 服务
 *   2) 启动后台监控线程（在构造函数中）
 *   3) 进入 Binder 线程池等待 RPC 调用
 *
 * 服务名称: system.lechao.lciod.IIoService/default
 */
int main() {
    ABinderProcess_setThreadPoolMaxThreadCount(1);

    auto service = ndk::SharedRefBase::make<IoServiceImpl>();
    const std::string instance = "default";
    const std::string name = std::string("system.lechao.lciod.IIoService/") + instance;

    binder_status_t status = AServiceManager_addService(
        service->asBinder().get(), name.c_str());
    if (status != STATUS_OK) {
        ALOGE("Failed to register %s: %d", name.c_str(), status);
        return 1;
    }
    ALOGI("Registered %s", name.c_str());

    /* 服务注册成功后才启动后台监控线程 */
    service->start();

    ABinderProcess_joinThreadPool();
    return 1;
}