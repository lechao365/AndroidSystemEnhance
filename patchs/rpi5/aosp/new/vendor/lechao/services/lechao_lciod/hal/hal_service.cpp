/*
 * ============================================================
 * hal_service.cpp — Vendor HAL 进程主实现
 * 所属模块: lechao_lciod (vendor 分区)
 * 设计目的: 实现 IIoHal AIDL 接口，作为内核驱动的用户态代理。
 *
 * 架构角色:
 *   - 通过 device_io.h 封装的 ioctl/read 与内核驱动交互
 *   - 维护设备节点路径到 minor 编号的映射缓存（mDeviceMap）
 *   - 为 system daemon (service.cpp) 提供 Binder RPC 接口
 *
 * 关键设计点:
 *   1) mDeviceMap 缓存: 避免每次 AIDL 调用都重新枚举设备，
 *      同时跟踪设备在线/离线状态。
 *   2) extract_minor(): 从设备节点路径解析出 minor 编号，
 *      作为 mDeviceMap 的键（如 "/dev/vendor_lechao_usbd0" → minor=0）
 *   3) 字段映射: struct vendor_lechao_usbd_stats → IoStats parcelable，
 *      逐字段拷贝（含字符串 vendor/product 的 memcpy）。
 *   4) readEvent() 使用持久化 fd: 事件读取需要保持 fd 打开，
 *      存储在 DeviceEntry.fd 中，设备移除时自动关闭并标记 -1。
 * ============================================================
 */
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <aidl/vendor/lechao/lciod/BnIoHal.h>
#include <aidl/vendor/lechao/lciod/IIoHal.h>
#include "device_io.h"
#include <android-base/logging.h>
#include "lechao_log.h"
#include <unordered_map>
#include <array>
#include <cinttypes>

using namespace ndk;
using aidl::vendor::lechao::lciod::BnIoHal;
using aidl::vendor::lechao::lciod::IoStats;
using aidl::vendor::lechao::lciod::IoConfig;
using aidl::vendor::lechao::lciod::IoEvent;

/*
 * extract_minor — 从设备节点路径解析出 minor 编号
 * @path: 设备节点路径，如 "/dev/vendor_lechao_usbd0"
 * 返回: minor 编号（整数），-1 表示路径格式无效
 *
 * 解析逻辑: 去掉前缀 "/dev/vendor_lechao_usbd" 后剩余部分转为整数。
 * 示例: "/dev/vendor_lechao_usbd0" → suffix="0" → minor=0
 *       "/dev/vendor_lechao_usbd1" → suffix="1" → minor=1
 */
static int extract_minor(const std::string& path) {
    const char prefix[] = "/dev/vendor_lechao_usbd";
    if (path.compare(0, sizeof(prefix) - 1, prefix) != 0) {
        LC_LOGW("extract_minor: invalid path '" << path << "'");
        return -1;
    }
    std::string suffix = path.substr(sizeof(prefix) - 1);
    return std::atoi(suffix.c_str());
}

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
class IoHalImpl : public BnIoHal {
public:
    IoHalImpl() {
        refresh_devices();
    }

    ~IoHalImpl() {
        /* 析构时关闭所有持久化 fd */
        for (auto& [minor, entry] : mDeviceMap)
            ::close_device(entry.fd);
    }

    /*
     * refresh_devices — 刷新设备节点缓存
     *
     * 逻辑:
     *   1) 调用 list_devices() 获取当前在线设备列表
     *   2) 构建 newMap，保留仍在线且路径不变的设备 entry（含 fd）
     *   3) 关闭已离线设备的 fd，将其从 map 中移除
     *   4) 替换 mDeviceMap 为 newMap
     *
     * 此方法不再主动 open_device，新设备 fd 初始为 -1，
     * 由 readEvent() 按需懒打开。这样设备拔出后可立即从 map
     * 中清除，避免后续对已消失设备节点进行无意义的 open 重试。
     */
    void refresh_devices() {
        std::vector<std::string> current = ::list_devices();
        std::unordered_map<int, DeviceEntry> newMap;

        for (auto& path : current) {
            int minor = extract_minor(path);
            if (minor < 0) { LC_LOGW("refresh_devices: skipping invalid minor"); continue; }

            auto old = mDeviceMap.find(minor);
            if (old != mDeviceMap.end() && old->second.path == path) {
                /* 设备仍在线且路径不变 → 复用已有 entry（含 fd） */
                newMap[minor] = old->second;
            } else {
                /* 新设备或路径变化 → 创建 entry，fd 留给 readEvent 懒打开 */
                DeviceEntry entry;
                entry.path = path;
                entry.fd = -1;
                newMap[minor] = entry;
            }
        }

        /* 关闭已离线设备的 fd（不在新列表中的设备已被移除） */
        for (auto& [minor, entry] : mDeviceMap) {
            if (newMap.find(minor) == newMap.end())
                ::close_device(entry.fd);
        }

        mDeviceMap = std::move(newMap);
    }

    /*
     * resolve_device — 根据 minor 编号查找设备缓存条目
     * 返回: DeviceEntry* 指针，nullptr 表示设备不在线
     */
    DeviceEntry* resolve_device(int minor) {
        auto it = mDeviceMap.find(minor);
        return (it != mDeviceMap.end()) ? &it->second : nullptr;
    }

    /*
     * listDevices — 返回所有在线设备的路径列表
     * AIDL 接口实现，返回设备节点路径而非 minor 编号。
     */
    ndk::ScopedAStatus listDevices(std::vector<std::string>* _aidl_return) override {
        refresh_devices();
        _aidl_return->clear();
        for (auto& [minor, entry] : mDeviceMap)
            _aidl_return->push_back(entry.path);
        return ndk::ScopedAStatus::ok();
    }

    /*
     * getStats — 获取指定设备的传输统计快照
     *
     * 字段映射: struct vendor_lechao_usbd_stats → IoStats
     *   - 整数字段直接赋值
     *   - 字符串 vendor/product 用 memcpy 拷贝（AIDL String 内部为 char[]）
     *
     * 注意: 每次调用临时打开 fd，调用后立即关闭（不占用持久 fd）
     */
    ndk::ScopedAStatus getStats(int32_t in_deviceMinor, IoStats* _aidl_return) override {
        auto* entry = resolve_device(in_deviceMinor);
        if (!entry) { LC_LOGW("getStats: device not found for minor"); return ndk::ScopedAStatus::ok(); }

        /* 临时打开 fd，避免占用 readEvent 的持久 fd */
        int fd = ::open_device(entry->path.c_str());
        if (fd < 0) { LC_LOGE("getStats: open device failed: " << strerror(errno)); return ndk::ScopedAStatus::ok(); }

        struct vendor_lechao_usbd_stats raw;
        int ret = ::get_stats(fd, &raw);
        close(fd);
        if (ret < 0) { LC_LOGE("getStats: ioctl GET_STATS failed: " << strerror(errno)); return ndk::ScopedAStatus::ok(); }

        /* --- 字段映射: raw → _aidl_return --- */
        _aidl_return->vid = raw.vid;
        _aidl_return->pid = raw.pid;
        _aidl_return->vendor.assign(raw.vendor, strnlen(raw.vendor, sizeof(raw.vendor)));
        _aidl_return->product.assign(raw.product, strnlen(raw.product, sizeof(raw.product)));
        _aidl_return->readBytes = raw.read_bytes;
        _aidl_return->readNs = raw.read_ns;
        _aidl_return->readCmds = raw.read_cmds;
        _aidl_return->writeBytes = raw.write_bytes;
        _aidl_return->writeNs = raw.write_ns;
        _aidl_return->writeCmds = raw.write_cmds;
        _aidl_return->errorCount = raw.error_count;
        _aidl_return->resetCount = raw.reset_count;
        _aidl_return->stallCount = raw.stall_count;
        _aidl_return->corruptCount = raw.corrupt_count;
        _aidl_return->timeoutCount = raw.timeout_count;
        _aidl_return->probeCount = raw.probe_count;
        _aidl_return->disconnectCount = raw.disconnect_count;
        _aidl_return->degradeCount = raw.degrade_count;
        _aidl_return->lastTransportLatencyNs = raw.last_transport_latency_ns;
        _aidl_return->currentRate = raw.current_rate;
        _aidl_return->lastEventTsNs = raw.last_event_ts_ns;
        _aidl_return->lastEventType = raw.last_event_type;
        _aidl_return->enabled = raw.enabled;
        _aidl_return->flags = raw.flags;
        return ndk::ScopedAStatus::ok();
    }

    /*
     * resetState — 重置指定设备的内核端统计计数器
     * 临时打开 fd，执行 IOC_RESET_STATE 后关闭。
     */
    ndk::ScopedAStatus resetState(int32_t in_deviceMinor) override {
        auto* entry = resolve_device(in_deviceMinor);
        if (!entry) { LC_LOGW("resetState: device not found for minor"); return ndk::ScopedAStatus::ok(); }

        int fd = ::open_device(entry->path.c_str());
        if (fd < 0) { LC_LOGE("resetState: open device failed: " << strerror(errno)); return ndk::ScopedAStatus::ok(); }

        int ret = ::reset_state(fd);
        close(fd);
        if (ret < 0) LC_LOGE("resetState: ioctl RESET_STATE failed: " << strerror(errno));
        return ndk::ScopedAStatus::ok();
    }

    /*
     * getConfig — 获取指定设备的运行时配置
     */
    ndk::ScopedAStatus getConfig(int32_t in_deviceMinor, IoConfig* _aidl_return) override {
        auto* entry = resolve_device(in_deviceMinor);
        if (!entry) { LC_LOGW("getConfig: device not found"); return ndk::ScopedAStatus::ok(); }

        int fd = ::open_device(entry->path.c_str());
        if (fd < 0) { LC_LOGE("getConfig: open failed: " << strerror(errno)); return ndk::ScopedAStatus::ok(); }

        struct vendor_lechao_usbd_config raw;
        int cfg_ret = ::get_config(fd, &raw);
        if (cfg_ret != 0)
            LC_LOGE("getConfig: ioctl GET_CONFIG failed: " << strerror(errno));
        if (cfg_ret == 0) {
            _aidl_return->enabled = raw.enabled;
            _aidl_return->flags = raw.flags;
        }
        close(fd);
        return ndk::ScopedAStatus::ok();
    }

    /*
     * setConfig — 设置指定设备的运行时配置
     * 返回 bool 表示内核是否接受配置。
     */
    ndk::ScopedAStatus setConfig(int32_t in_deviceMinor, const IoConfig& in_config, bool* _aidl_return) override {
        auto* entry = resolve_device(in_deviceMinor);
        if (!entry) { LC_LOGW("setConfig: device not found"); *_aidl_return = false; return ndk::ScopedAStatus::ok(); }

        int fd = ::open_device(entry->path.c_str());
        if (fd < 0) { LC_LOGE("setConfig: open failed: " << strerror(errno)); *_aidl_return = false; return ndk::ScopedAStatus::ok(); }

        struct vendor_lechao_usbd_config raw;
        raw.enabled = in_config.enabled;
        raw.flags = in_config.flags;
        int set_ret = ::set_config(fd, &raw);
        if (set_ret != 0)
            LC_LOGE("setConfig: ioctl SET_CONFIG failed: " << strerror(errno));
        *_aidl_return = (set_ret == 0);
        close(fd);
        return ndk::ScopedAStatus::ok();
    }

    /*
     * readEvent — 从内核事件缓冲区读取最新一条事件
     *
     * 与其他方法不同，此方法使用持久化 fd（entry->fd），
     * 因为事件读取需要 poll+read 连续操作，不能每次重新打开。
     *
     * 设备移除检测: 当 read 返回 ENODEV/EIO 时，关闭 fd 并标记 -1，
     * 下次调用时尝试重新打开（设备可能重新 probe）。
     */
    ndk::ScopedAStatus readEvent(int32_t in_deviceMinor, int32_t in_timeoutMs, IoEvent* _aidl_return) override {
        /* 先刷新设备列表，将已消失的设备从 map 中移除 */
        refresh_devices();

        auto* entry = resolve_device(in_deviceMinor);
        if (!entry) { LC_LOGW("readEvent: device not found"); _aidl_return->valid = false; return ndk::ScopedAStatus::ok(); }

        /* 检查持久 fd 是否有效，无效时尝试重新打开 */
        if (entry->fd < 0) {
            LC_LOGD("readEvent: reopening persistent fd");
            entry->fd = ::open_device(entry->path.c_str());
        }
        if (entry->fd < 0) { LC_LOGE("readEvent: reopen failed: " << strerror(errno)); _aidl_return->valid = false; return ndk::ScopedAStatus::ok(); }

        struct vendor_lechao_usbd_event raw;
        int ret = ::read_event(entry->fd, &raw, in_timeoutMs);

        if (ret < 0) {
            if (errno != ETIMEDOUT && errno != EAGAIN) {
                LC_LOGE("readEvent: read_event failed: " << strerror(errno));
                if (errno == ENODEV || errno == EIO) {
                    LC_LOGW("readEvent: device removed (errno=" << errno << ")");
                    ::close_device(entry->fd);
                    entry->fd = -1;
                }
            }
            _aidl_return->valid = false;
        } else {
            /* 字段映射: raw → _aidl_return */
            _aidl_return->timestampNs = raw.timestamp_ns;
            _aidl_return->eventType = raw.event_type;
            _aidl_return->eventValue = raw.event_value;
            _aidl_return->dataDirection = raw.data_direction;
            _aidl_return->status = raw.status;
            _aidl_return->valid = raw.valid;
        }
        return ndk::ScopedAStatus::ok();
    }

private:
    /* 设备节点缓存: minor → {path, fd} */
    std::unordered_map<int, DeviceEntry> mDeviceMap;
};

/*
 * main — HAL 进程入口
 *
 * 职责:
 *   1) 初始化 Android logging
 *   2) 创建 IoHalImpl 实例并注册为 Binder 服务
 *   3) 进入 Binder 线程池等待 RPC 调用
 *
 * 服务名称: vendor.lechao.lciod.IIoHal/default
 * 线程数: 1（单线程处理，避免并发 ioctl 冲突）
 */
int main() {
    android::base::InitLogging(nullptr, android::base::LogdLogger(android::base::SYSTEM));
    android::base::SetDefaultTag("lechao_lciod_hal");

    ABinderProcess_setThreadPoolMaxThreadCount(1);
    auto service = ndk::SharedRefBase::make<IoHalImpl>();
    const std::string instance = "default";
    const std::string name = std::string("vendor.lechao.lciod.IIoHal/") + instance;

    binder_status_t status = AServiceManager_addService(
        service->asBinder().get(), name.c_str());
    if (status != STATUS_OK) {
        LOG(ERROR) << "Failed to register " << name << ": " << status;
        return 1;
    }
    LOG(INFO) << "Registered " << name;
    ABinderProcess_joinThreadPool();
    LOG(ERROR) << "joinThreadPool returned unexpectedly";
    return 1;
}