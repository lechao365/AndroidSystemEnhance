/*
 * ============================================================
 * service.h — System Daemon 服务类声明
 * 所属模块: lechao_lciod (system 分区)
 * 设计目的: 将 IoServiceImpl 类声明与纯计算函数从 service.cpp
 *           抽出，供单元测试（tests/Service_test.cpp）与 filegroup
 *           复用源码（main 移至 main_lciod.cpp）。
 *
 * 类职责详见 service.cpp 头注释：
 *   - 代理转发 system 层 AIDL 调用到 vendor HAL
 *   - 字段投影/过滤（省略 enabled/flags 等管理字段）
 *   - 后台监控线程（start_monitor）
 * ============================================================
 */
#ifndef _LECHAO_LCIOD_SERVICE_H
#define _LECHAO_LCIOD_SERVICE_H

#include <aidl/system/lechao/lciod/BnIoService.h>
#include <aidl/system/lechao/lciod/IoConfig.h>
#include <aidl/system/lechao/lciod/IoEvent.h>
#include <aidl/system/lechao/lciod/IoStats.h>
#include <aidl/vendor/lechao/lciod/IoConfig.h>
#include <aidl/vendor/lechao/lciod/IoEvent.h>
#include <aidl/vendor/lechao/lciod/IoStats.h>
#include <cstdint>
#include <vector>
#include "hal_client.h"

/*
 * ComputeAverageRate — getAverageRate 的核心公式（纯函数，供单测）
 * 公式: (readBytes + writeBytes) * 1e9 / (readNs + writeNs)，单位字节/秒
 * 除零防护: totalNs == 0 时返回 0（CXX-002 边界防御）
 */
int64_t ComputeAverageRate(uint64_t readBytes, uint64_t writeBytes,
                           uint64_t readNs, uint64_t writeNs);

/*
 * ComputeKbRate — 监控线程速率换算核心（纯函数，供单测）
 * 公式: bytes * 1e9 / ns / 1024，单位 KB/s
 * 除零防护: ns == 0 时返回 0
 */
uint64_t ComputeKbRate(uint64_t bytes, uint64_t ns);

/*
 * ProjectSystemIoStats — vendor IoStats → system IoStats 字段投影（纯函数，供单测）
 * 直传 21 字段；省略管理字段 currentRate / enabled / flags
 * （currentRate 经 getAverageRate 按需计算，enabled/flags 不暴露给上层）。
 * 回归防护：字段串位/漏投影在此判红。
 */
void ProjectSystemIoStats(const aidl::vendor::lechao::lciod::IoStats& vstats,
                          aidl::system::lechao::lciod::IoStats* out);

/*
 * ProjectSystemIoConfig — vendor IoConfig → system IoConfig 投影（纯函数，供单测）
 */
void ProjectSystemIoConfig(const aidl::vendor::lechao::lciod::IoConfig& vcfg,
                           aidl::system::lechao::lciod::IoConfig* out);

/*
 * ProjectSystemIoEvent — vendor IoEvent → system IoEvent 投影（纯函数，供单测）
 * 1:1 字段直传。
 */
void ProjectSystemIoEvent(const aidl::vendor::lechao::lciod::IoEvent& vev,
                          aidl::system::lechao::lciod::IoEvent* out);

/*
 * IoServiceImpl — IIoService AIDL 接口的实现类
 *
 * 核心职责:
 *   1) 代理转发: 将 system 层调用转换为 vendor HAL 调用
 *   2) 字段投影: vendor IoStats → system IoStats（省略管理字段）
 *   3) 后台监控: start() 启动独立线程，定期轮询事件和统计
 */
class IoServiceImpl : public aidl::system::lechao::lciod::BnIoService {
public:
    IoServiceImpl() = default;

    /*
     * start — 在 main() 服务注册成功后显式启动后台监控线程
     * 确保注册失败时不会留下 detach 线程。
     */
    void start();

    ndk::ScopedAStatus listDeviceMinors(std::vector<int32_t>* _aidl_return) override;
    ndk::ScopedAStatus getAverageRate(int32_t in_deviceMinor, int64_t* _aidl_return) override;
    ndk::ScopedAStatus getIoStats(int32_t in_deviceMinor,
                                  aidl::system::lechao::lciod::IoStats* _aidl_return) override;
    ndk::ScopedAStatus resetIoState(int32_t in_deviceMinor) override;
    ndk::ScopedAStatus getIoConfig(int32_t in_deviceMinor,
                                   aidl::system::lechao::lciod::IoConfig* _aidl_return) override;
    ndk::ScopedAStatus setIoConfig(int32_t in_deviceMinor,
                                   const aidl::system::lechao::lciod::IoConfig& in_config,
                                   bool* _aidl_return) override;
    ndk::ScopedAStatus readIoEvent(int32_t in_deviceMinor, int32_t in_timeoutMs,
                                   aidl::system::lechao::lciod::IoEvent* _aidl_return) override;

private:
    IoHalClient hal_client_; /* HAL Binder 客户端封装 */

    /*
     * start_monitor — 启动后台监控线程
     * 每 50ms 轮询事件、每 10s（200 tick）刷新设备列表并打印统计；
     * 单设备失败不中断本轮；线程 detach 随进程生命周期终止。
     */
    void start_monitor();
};

#endif  // _LECHAO_LCIOD_SERVICE_H
