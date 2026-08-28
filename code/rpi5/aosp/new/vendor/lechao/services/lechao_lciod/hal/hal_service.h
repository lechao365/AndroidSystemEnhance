/*
 * ============================================================
 * hal_service.h — Vendor HAL 服务类声明
 * 所属模块: lechao_lciod (vendor 分区)
 * 设计目的: 将 IoHalImpl 类声明从 hal_service.cpp 抽出，
 *           供单元测试（tests/HalService_test.cpp）与 filegroup
 *           复用源码（main 移至 main_lciod_hal.cpp，与 lcview
 *           LcView.h 模式对齐）。
 *
 * 类职责详见 hal_service.cpp 头注释：
 *   - 通过 device_io.h 封装的 ioctl/read 与内核驱动交互
 *   - 维护 minor → DeviceEntry 映射缓存（mDeviceMap）
 *   - 为 system daemon 提供 Binder RPC 接口
 * ============================================================
 */
#ifndef _LECHAO_LCIOD_HAL_SERVICE_H
#define _LECHAO_LCIOD_HAL_SERVICE_H

#include <aidl/vendor/lechao/lciod/BnIoHal.h>
#include <string>
#include <unordered_map>
#include <vector>

/*
 * DeviceEntry — 设备节点缓存条目
 * 用于跟踪每个 minor 编号对应的设备路径和持久化 fd。
 * readEvent() 需要持久 fd（不能每次都重新打开），
 * 其他方法（getStats/resetState/getConfig/setConfig）则每次临时打开。
 */
struct DeviceEntry {
    std::string path; /* 设备节点路径，如 "/dev/vendor_lechao_usbd0" */
    int fd = -1;      /* 持久化 fd，用于 readEvent poll/read；-1 表示已关闭 */
};

/*
 * IoHalImpl — IIoHal AIDL 接口的实现类
 *
 * mDeviceMap: unordered_map<int minor → DeviceEntry>
 *   - 缓存所有在线设备的信息
 *   - refresh_devices() 定期更新（每次 AIDL 调用前）
 *   - 设备移除时关闭 fd 并从 map 中剔除
 *
 * NOTE: mDeviceMap 无线程保护，但 HAL 进程仅启用 1 个 Binder 线程
 * (ABinderProcess_setThreadPoolMaxThreadCount(1))，所有 AIDL 调用
 * 串行执行，无并发访问风险。
 */
class IoHalImpl : public aidl::vendor::lechao::lciod::BnIoHal {
public:
    IoHalImpl();
    ~IoHalImpl() override;

    /*
     * refresh_devices — 刷新设备节点缓存
     * 保留仍在线且路径不变的 entry（含 fd），关闭已离线设备的 fd，
     * 新设备 fd 初始 -1 由 readEvent() 懒打开。
     */
    void refresh_devices();

    /*
     * resolve_device — 根据 minor 编号查找设备缓存条目
     * 返回: DeviceEntry* 指针，nullptr 表示设备不在线
     */
    DeviceEntry* resolve_device(int minor);

    ndk::ScopedAStatus listDevices(std::vector<std::string>* _aidl_return) override;
    ndk::ScopedAStatus getStats(int32_t in_deviceMinor,
                                aidl::vendor::lechao::lciod::IoStats* _aidl_return) override;
    ndk::ScopedAStatus resetState(int32_t in_deviceMinor) override;
    ndk::ScopedAStatus getConfig(int32_t in_deviceMinor,
                                 aidl::vendor::lechao::lciod::IoConfig* _aidl_return) override;
    ndk::ScopedAStatus setConfig(int32_t in_deviceMinor,
                                 const aidl::vendor::lechao::lciod::IoConfig& in_config,
                                 bool* _aidl_return) override;
    ndk::ScopedAStatus readEvent(int32_t in_deviceMinor, int32_t in_timeoutMs,
                                 aidl::vendor::lechao::lciod::IoEvent* _aidl_return) override;

private:
    /* 设备节点缓存: minor → {path, fd} */
    std::unordered_map<int, DeviceEntry> mDeviceMap;
};

#endif  // _LECHAO_LCIOD_HAL_SERVICE_H
