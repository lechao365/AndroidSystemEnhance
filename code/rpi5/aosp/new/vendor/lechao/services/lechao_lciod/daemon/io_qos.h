/*
 * ============================================================
 * io_qos.h — SD 卡写 QoS 限速（R4 方向 1/2/3/5）
 * 所属模块: lechao_lciod (system 分区 daemon)
 * 设计目的: 通过 cgroup（v2 io.max / v1 blkio.throttle）对指定块设备
 *           （默认 SD 卡 mmcblk0，sysprop sys.lechao.lciod.ioqos_dev 可
 *           覆盖）施加写带宽限速。档位由 sysprop sys.lechao.lciod.ioqos_level
 *           驱动（top/fg/bg/off），IoServiceImpl::start() 启动周期线程每
 *           1s 轮询应用（绝对时间对齐，仿 link_monitor 单调时钟范式）。
 *
 * 纯函数（本头文件声明，io_qos.cpp 实现，供单测直接调用）:
 *   ParseIoQosLevel / LevelToBytesPerSec / DetectCgroupVersion /
 *   BuildIoMaxLine / BuildBlkioLine / ReadDeviceMajorMinor
 * 成员（IoQosManager，可注入 cgroup 根路径供单测用临时目录模拟）:
 *   EnsureGroup / ApplyLevel（幂等）/ MovePidToGroup / PollAndApply /
 *   RunLoop / stop
 *
 * 安全性: 所有外部输入（sysprop 档位值、设备号、pid）前置校验
 *         防御（CXX-003）；失败路径 ERROR 留痕不静默（CXX-004）。
 * ============================================================
 */
#ifndef _LECHAO_LCIOD_IO_QOS_H
#define _LECHAO_LCIOD_IO_QOS_H

#include <atomic>
#include <cstdint>
#include <string>

namespace lechao {
namespace lciod {

/* SD 卡写 QoS 档位枚举（sys.lechao.lciod.ioqos_level 取值） */
enum class IoQosLevel {
    kOff,  /* 关闭限速（等同不限，v2 写 max / v1 写 0） */
    kTop,  /* 不限速档 */
    kFg,   /* 前台档：16MiB/s */
    kBg,   /* 后台档：8MiB/s */
};

/* 档位带宽常量（字节/秒）：kFg=16MiB/s，kBg=8MiB/s */
constexpr uint64_t kIoQosFgBytesPerSec = 16ULL * 1024ULL * 1024ULL;  /* 16777216 */
constexpr uint64_t kIoQosBgBytesPerSec = 8ULL * 1024ULL * 1024ULL;   /* 8388608 */

/*
 * ParseIoQosLevel — 解析 ioqos_level sysprop 档位值（纯函数，供单测）
 * 语义: "top"/"fg"/"bg"/"off"（或空串视为 off）→ 对应档位返回 true；
 *       未知值返回 false 且 out 不写入（CXX-003 外部输入防御）。
 */
bool ParseIoQosLevel(const std::string& val, IoQosLevel* out);

/*
 * LevelToBytesPerSec — 档位 → 每秒写字节数（纯函数，供单测）
 * kTop=0（不限，v2 写 max / v1 写 0），kFg=16777216，kBg=8388608；
 * kOff 等同 kTop 返回 0（不限）。
 */
uint64_t LevelToBytesPerSec(IoQosLevel lvl);

/* cgroup 版本探测结果 */
enum class CgroupVersion {
    kV2,          /* 统一层级（cgroup.controllers 含 io，io.max 可用） */
    kV1,          /* 经典层级（blkio 目录存在，blkio.throttle.* 可用） */
    kUnsupported, /* 两者都不满足 */
};

/*
 * DetectCgroupVersion — 探测真实 /sys/fs/cgroup 的 cgroup 版本（纯函数）
 * v2 判据: /sys/fs/cgroup/cgroup.controllers 存在且内容含 "io"；
 * v1 判据: /sys/fs/cgroup/blkio 目录存在。
 * 注: IoQosManager 内部基于注入的 root_ 做同规则探测（单测可模拟）。
 */
CgroupVersion DetectCgroupVersion();

/*
 * BuildIoMaxLine — 构造 cgroup v2 io.max 行（纯函数，供单测）
 * 格式: "MAJ:MIN rbps=N wbps=N"；bytes=0（不限）时 "MAJ:MIN rbps=max wbps=max"。
 */
std::string BuildIoMaxLine(const std::string& majmin, uint64_t bytes);

/*
 * BuildBlkioLine — 构造 cgroup v1 blkio.throttle 行（纯函数，供单测）
 * 格式: "MAJ:MIN N"；bytes=0（不限）时 "MAJ:MIN 0"。
 */
std::string BuildBlkioLine(const std::string& majmin, uint64_t bytes);

/*
 * ReadDeviceMajorMinor — 读取 /sys/block/<dev>/dev 的 MAJ:MIN（纯函数，供单测）
 * 成功写入 out（如 "179:0"）返回 true；文件缺失/格式非法返回 false。
 */
bool ReadDeviceMajorMinor(const std::string& sysblock_dev_path, std::string* out);

/*
 * IoQosManager — SD 卡写 QoS 限速管理器
 *
 * 职责:
 *   1) 建组（EnsureGroup）: v2 <root>/lechao_bg，v1 <root>/blkio/lechao_bg，
 *      已存在幂等（mkdir 返回 EEXIST 视为成功）
 *   2) 应用档位（ApplyLevel）: 幂等——仅档位或设备变化才写 io.max /
 *      blkio.throttle.{read,write}_bps_device；写成功留痕 logcat JSON
 *   3) 迁移 pid（MovePidToGroup）: 追加写 cgroup.procs（v2）/ tasks（v1）
 *   4) 周期轮询（PollAndApply + RunLoop）: 读 ioqos_level / ioqos_dev
 *      sysprop，值变化时 ApplyLevel；RunLoop 提供 stop_ 退出标志供单测/析构
 *
 * 构造参数 cgroup_root 可注入（默认 /sys/fs/cgroup），所有读写均基于该
 * 根路径，保证单测可用 mkdtemp 临时目录模拟而不触碰真实 cgroup。
 */
class IoQosManager {
public:
    explicit IoQosManager(const std::string& cgroup_root = "/sys/fs/cgroup");
    ~IoQosManager();

    /* 建 lechao_bg 限速组；已存在幂等；cgroup 版本不可探测返回 false */
    bool EnsureGroup();

    /*
     * ApplyLevel — 应用档位到指定设备（幂等）
     * 仅当 (档位, 设备 MAJ:MIN) 相对上次已应用值发生变化时才写限速文件；
     * 写成功留痕 logcat JSON 事件。失败返回 false 且不更新 lastApplied
     * 状态（下轮重试，CXX-002 错误路径不污染状态）。
     */
    bool ApplyLevel(IoQosLevel lvl, const std::string& majmin);

    /* 迁移指定 pid 到 lechao_bg 限速组（追加一行 pid） */
    bool MovePidToGroup(int pid);

    /*
     * PollAndApply — 单周期轮询：读 ioqos_level/ioqos_dev sysprop，
     * 解析档位 + 确定设备（ioqos_dev 覆盖默认 mmcblk0）后 ApplyLevel。
     */
    void PollAndApply();

    /*
     * RunLoop — 周期循环线程入口（IoServiceImpl::start() 以
     * std::thread(&IoQosManager::RunLoop, &io_qos_).detach() 启动）
     * 每 1s 绝对时间对齐 PollAndApply（steady_clock sleep_until，
     * 单调时钟防漂移，仿 link_monitor next_throttle_ns 范式）；
     * stop_ 置位后安全退出（供单测/析构）。
     */
    void RunLoop();

    /* 置 stop_ 退出标志（RunLoop 在下一个周期边界退出） */
    void stop();

private:
    /* 解析 v1 blkio 实际挂载根（<root>/blkio 或真机 /dev/blkio），均不可用返回空串 */
    std::string BlkioRoot() const;

    std::string root_;                  /* cgroup 根路径（可注入） */
    CgroupVersion version_ = CgroupVersion::kUnsupported;  /* 懒探测缓存 */
    IoQosLevel lastAppliedLevel_ = IoQosLevel::kOff;   /* 幂等记录：上次档位 */
    std::string lastAppliedMajMin_;                     /* 幂等记录：上次设备 */
    std::atomic<bool> stop_{false};                     /* RunLoop 退出标志 */
    bool unsupportedWarned_ = false;    /* cgroup 不可用告警去重 */
    bool noDeviceWarned_ = false;       /* 设备缺失告警去重 */
    std::string lastLevelProp_;         /* 上次 ioqos_level 原始值（告警去重） */
};

}  // namespace lciod
}  // namespace lechao

#endif  // _LECHAO_LCIOD_IO_QOS_H
