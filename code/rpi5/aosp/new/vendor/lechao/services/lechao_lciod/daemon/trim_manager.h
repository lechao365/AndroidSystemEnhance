// ============================================================
// trim_manager.h — ext4 碎片清理链（FITRIM）管理器（UP-110 批一）
// 所属模块：LcIod IO 健康监控 — Daemon 层
// 设计目的：两层结构（对齐 block_collector.h）——
//   纯函数层（本头声明，单测直接调用）：空闲率换算、trim 决策（空闲率 +
//   距上次天数）、盘尾滑动分段计算、熔断判定——无 I/O、无状态，可脱离
//   系统独立测试；
//   执行层（TrimManager）：statvfs 取 /data 容量、读/写状态文件
//   data/misc/lechao/lciod/trim-state（禁碰 vold 路径）、分段 FITRIM
//   （32MiB 盘尾滑动、段间 usleep 5ms、300s + 段上限熔断）。
// 安全边界：FITRIM 仅释放文件系统空闲块（无损，不触碰已分配数据），禁块
//   设备裸写（BLKDISCARD）与 dd；开关 sysprop 缺省 0，未开启零动作。
// ============================================================

#pragma once

#include <cstdint>
#include <string>

namespace lechao {
namespace lciod {

// ---- 事件 id（与 config/lcview_events.json id=20 trim 对应） ----
inline constexpr uint16_t kEventTrimId = 20;

// ---- 决策阈值（含取值理由） ----
// 空闲率阈值（万分比）：/data 空闲占比 >= 50% 才值得清理——空闲块占比高
// 时 FITRIM 收益大且耗时可控；过低说明数据密集，清理收益有限且易拖慢 IO。
inline constexpr uint32_t kTrimFreeThresholdBp = 5000;
// 距上次成功 trim 的最小天数：避免频繁清理（FITRIM 会占用块层队列），
// 一周一次足以维持空闲块连续性。
inline constexpr uint32_t kTrimMinDays = 7;

// ---- 执行参数 ----
// 单段大小 32MiB：单次 FITRIM ioctl 覆盖 32MiB 窗口，避免一次性全盘
// FITRIM 长时间占用（可中断/可熔断）。
inline constexpr uint64_t kTrimSegmentBytes = 32ull * 1024 * 1024;
// 段间休眠 5ms：给块层队列喘息，避免 discard 洪峰阻塞前台 IO。
inline constexpr uint32_t kTrimSegmentSleepUs = 5000;
// 时间熔断预算 300s：单次清理总时长上限，超时安全退出（已执行的段保留，
// 未执行的段中止；状态与度量照常上报）。
inline constexpr uint64_t kTrimBudgetMs = 300000;
// 段上限熔断：即便设备巨大，单次最多执行 8192 段（=256GiB @32MiB）后
// 安全退出，防异常设备（超大 disksize/错误 statvfs）导致无界循环。
inline constexpr uint32_t kTrimMaxSegments = 8192;

// ---- 路径与周期 ----
// /data 挂载点：FITRIM 在挂载点目录 fd 上发起（ext4 仅清空闲块，无损）。
inline constexpr const char* kTrimMountPath = "/data";
// 状态目录/文件：lciod 自持路径，禁碰 vold 的 data/misc/vold/last-fstrim。
inline constexpr const char* kTrimStateDir = "/data/misc/lechao/lciod";
inline constexpr const char* kTrimStatePath = "/data/misc/lechao/lciod/trim-state";
// 块设备统计：前后差分（第 11~14 字段 time_in_queue/discard_ios/merges/sectors）。
inline constexpr const char* kTrimBlockStatPath = "/sys/block/mmcblk0/stat";
// 决策/执行轮询周期（秒）：与块采集同 10s 节拍，决策门槛（天数）保证低频。
inline constexpr uint32_t kTrimLoopIntervalSec = 10;

// ---- 纯函数层（单测直接覆盖，无 I/O） ----

// 空闲率换算（万分比，0..10000）。除零防护：total==0 → 0（CXX-002）。
uint32_t ComputeFreeRatioBp(uint64_t free_bytes, uint64_t total_bytes);

// trim 决策（纯函数）：
//   eligible = enabled && (drill || never_trimmed ||
//                          (free_ratio_bp >= free_threshold_bp
//                           && days_since_last >= min_days))
// drill=true 表示演练强制（单段/限段测试钩子），绕过阈值门槛（仍须 enabled）。
// 说明：never_trimmed（当前无状态文件）视为满足天数门槛，允许首次清理。
bool ShouldTrim(bool enabled, bool drill, uint32_t free_ratio_bp,
                uint32_t free_threshold_bp, uint64_t days_since_last,
                uint32_t min_days, bool never_trimmed);

// 设备可分出的段数 = ceil(dev_size / seg_bytes)；seg_bytes==0 或 dev_size==0
// 返回 0（CXX-002 除零/边界防护）。
uint32_t SegmentCount(uint64_t dev_size, uint64_t seg_bytes);

// 盘尾滑动分段的第 idx 段（idx 从 0 起，自设备尾向前滑动）：
//   seg_len = min(seg_bytes, dev_size - idx*seg_bytes)
//   start   = dev_size - (idx+1)*seg_bytes   （末段可能更短，起始对齐 0）
// idx 越界（idx >= SegmentCount）返回 false；seg_bytes==0 或 dev_size==0
// 返回 false（CXX-002 边界防护）。start/len 仅在返回 true 时写入。
bool SegmentAtFromTail(uint64_t dev_size, uint64_t seg_bytes, uint32_t idx,
                       uint64_t* start, uint64_t* len);

// 熔断判定（纯函数）：已耗时 >= 预算 或 已完成段数 >= 段上限 → true（安全退出）。
bool CircuitBreakerTripped(uint64_t elapsed_ms, uint64_t budget_ms,
                           uint32_t segments_done, uint32_t max_segments);

// ---- 执行层 ----

// TrimManager：ext4 FITRIM 执行器。仅运行循环线程调用（无锁）。
class TrimManager {
public:
    struct Result {
        bool ran = false;              // 是否实际发起了 FITRIM
        bool enabled = false;          // 开关态
        bool decision_passed = false;  // 决策通过
        uint64_t trimmed_bytes = 0;    // FITRIM 累计回报已清字节
        uint32_t segments = 0;         // 实际执行段数
        uint64_t duration_ms = 0;      // 执行墙钟（ms）
        uint32_t free_ratio_bp = 0;    // 决策时 /data 空闲率（万分比）
        // 块层前后差分（第 11~14 字段）
        uint64_t discard_ios_delta = 0;
        uint64_t discard_merges_delta = 0;
        uint64_t discard_sectors_delta = 0;
        uint64_t time_queue_delta_ms = 0;
        // 命中熔断（时间/段上限）时置 true——安全退出，结果仍上报
        bool circuit_breaker = false;
        std::string error;             // 空=成功；非空为失败原因（CXX-002 不吞错）
    };

    // statvfs 取挂载点容量。失败返回 false（errno 保留）。
    static bool StatMount(const char* mount_path, uint64_t* total_bytes,
                          uint64_t* free_bytes);

    // 读状态文件末次成功 trim 的 epoch 秒。
    //   文件不存在 → *never_trimmed=true 返回 true（首次清理）；
    //   读取成功但内容非法（非纯数字）→ 返回 false（CXX-003 输入防御）；
    //   out 空指针 → false。
    static bool ReadState(const char* state_path, uint64_t* last_epoch,
                          bool* never_trimmed);

    // 原子写状态文件（epoch 秒）：mkdir -p 目录 → 写临时文件 → rename。
    // 失败返回 false（errno 保留，CXX-002 不吞错）。
    static bool WriteState(const char* state_dir, const char* state_path,
                           uint64_t epoch_sec);

    // 分段 FITRIM：打开挂载点目录 fd，自盘尾向前按 seg_bytes 分段调用
    // FITRIM（每次 minlen=0）。段间 usleep sleep_us；熔断（预算/段上限）
    // 命中即安全退出（circuit_breaker=true，已执行段保留）。
    // max_segments==0 表示按设备大小自动取段数（上限 kTrimMaxSegments）。
    // 返回 true = 至少执行了一段且无致命错误；false = 打开失败/无段可执行/
    // 首个 ioctl 致命失败（error 带原因）。out 空指针返回 false。
    bool ExecuteSegmentedTrim(uint64_t dev_size, uint32_t max_segments,
                              uint64_t seg_bytes, uint32_t sleep_us,
                              uint64_t budget_ms, Result* out);

private:
    // 读 sysfs 一行到 out（std::ifstream）。
    static bool ReadFile(const char* path, std::string* out);
};

}  // namespace lciod
}  // namespace lechao
