// ============================================================
// block_collector.h — 块设备/zram 用户态观测采集器（R3 方向1/2/3）
// 所属模块：LcIod IO 健康监控 — Daemon 层（R5 方向1 自 lcview 平移）
// 设计目的：两层结构——
//   纯函数层（本头声明，单测直接调用）：解析 /sys/block/<dev>/stat、
//   zram mm_stat/io_stat，单调差分、速率/平均延迟计算、悬挂检测、
//   规则二（慢盘）判定。无 I/O、无状态，可脱离系统独立测试；
//   采集器层（BlockCollector/ZramCollector，仅主循环线程调用，无锁）：
//   枚举块设备、读 sysfs、内部维护 prev 差分状态，主循环每 10s 采样。
// ============================================================

#pragma once

#include <cstdint>
#include <string>
#include <vector>
#include <unordered_map>
#include <chrono>

namespace lechao {
namespace lciod {

// ---- 阈值常量（含取值理由注释，R3 契约要求） ----
// slow_disk 事件 id（与 config/lcview_events.json id=14 定义对应）
inline constexpr uint16_t kEventSlowDiskId = 14;
// 块设备/zram 采样周期（秒）：主循环每 10s 采样一次（时间驱动，
// 与负载解耦；10s 窗口既足够平滑瞬时抖动，又能较快响应状态变化）
inline constexpr uint32_t kBlockSampleIntervalSec = 10;
// 规则二（慢盘）写平均延迟阈值（ms）：持续超过该值判慢盘。
// 取值理由：正常 eMMC/UFS 写平均延迟在几十 ms 级（含队列拥塞），
// 200ms 已远超正常上界，可捕获慢盘/IO 风暴；配合 require_consecutive=3
// 的连续判定，滤除瞬时尖峰误报。
inline constexpr double kSlowDiskWriteLatThresholdMs = 200.0;
// 规则二连续超阈次数：连续 3 次采样（=30s）写延迟超阈才触发 slow_disk
// 事件——瞬时抖动（单次 10s 采样超阈）不触发，防误报刷屏。
inline constexpr uint32_t kSlowDiskRequireConsecutive = 3;
// 悬挂检测 inflight 阈值：inflight >= 32 且写延迟 >= 1s 且连续 3 次才判
// 悬挂。取值理由：eMMC/SD 块设备队列深度通常为个位数，32 个并发在飞
// 请求属异常（设备停滞/驱动挂起特征），配合高延迟共同判据降低误报。
inline constexpr uint64_t kInflightHangThreshold = 32;
// 悬挂检测写延迟阈值（ms）：>= 1000ms（1s）且 inflight 高才判悬挂——
// 正常写延迟远低于 1s，1s 级延迟是设备挂起/介质故障的强特征。
inline constexpr double kLatencyHangThresholdMs = 1000.0;

// 单次块设备统计采样（/sys/block/<dev>/stat 的 15 字段，全 uint64）。
// 字段序与内核 Documentation/block/stat.txt 一致：前 11 字段为经典格式，
// 后 4 字段为 discard（TRIM）统计（新内核）。
struct BlockStatSample {
    uint64_t read_ios = 0;
    uint64_t read_merges = 0;
    uint64_t read_sectors = 0;
    uint64_t read_ticks_ms = 0;
    uint64_t write_ios = 0;
    uint64_t write_merges = 0;
    uint64_t write_sectors = 0;
    uint64_t write_ticks_ms = 0;
    uint64_t in_flight = 0;
    uint64_t io_ticks_ms = 0;
    uint64_t time_in_queue_ms = 0;
    uint64_t discard_ios = 0;
    uint64_t discard_merges = 0;
    uint64_t discard_sectors = 0;
    uint64_t discard_ticks_ms = 0;
};

// 两次采样的单调差分（与 BlockStatSample 同字段布局）。counter reset
//（curr < prev，内核计数归零/设备重挂载）时该字段取 curr（从 0 重算）。
struct BlockStatDelta {
    uint64_t read_ios = 0;
    uint64_t read_merges = 0;
    uint64_t read_sectors = 0;
    uint64_t read_ticks_ms = 0;
    uint64_t write_ios = 0;
    uint64_t write_merges = 0;
    uint64_t write_sectors = 0;
    uint64_t write_ticks_ms = 0;
    uint64_t in_flight = 0;
    uint64_t io_ticks_ms = 0;
    uint64_t time_in_queue_ms = 0;
    uint64_t discard_ios = 0;
    uint64_t discard_merges = 0;
    uint64_t discard_sectors = 0;
    uint64_t discard_ticks_ms = 0;
};

// 解析 /sys/block/<dev>/stat 一行（空格/制表符分隔的 15 个无符号数）。
// 兼容 15 字段（完整，含 discard 段）或 11 字段（旧内核，discard 补 0）。
// CXX-003 输入防御：字段数 < 11 判 false 并 ALOGE；out 空指针判 false。
bool ParseBlockStat(const std::string& line, BlockStatSample* out);

// 单调差分：curr >= prev 取 curr - prev；curr < prev（counter reset）取 curr
//（CXX-002 回绕防护：差分永不下溢）。15 字段逐项处理。
BlockStatDelta DiffBlockSamples(const BlockStatSample& prev,
                                const BlockStatSample& curr);

// 速率/平均延迟计算结果：
//   read_iops / write_iops           — 窗口 IOPS（次/秒）
//   read_bytes_per_s / write_bytes_per_s — 窗口吞吐（字节/秒，sector=512B）
//   read_avg_lat_ms / write_avg_lat_ms   — 每 IO 平均延迟（ms）= ticks / ios
//   busy_ratio                         — 设备繁忙占比（io_ticks_ms/窗口毫秒，0..1）
// CXX-002 除零防护：period_ns==0 → 全部速率类与 busy 为 0（平均延迟只依赖
// ios，不依赖 period，仍正常计算）；ios==0 → 对应 iops/平均延迟为 0。
struct BlockRates {
    double read_iops = 0.0;
    double read_bytes_per_s = 0.0;
    double write_iops = 0.0;
    double write_bytes_per_s = 0.0;
    double read_avg_lat_ms = 0.0;
    double write_avg_lat_ms = 0.0;
    double busy_ratio = 0.0;
};
BlockRates ComputeBlockRates(const BlockStatDelta& delta, uint64_t period_ns);

// zram mm_stat 采样（8 字段；mm_stat 原生 7 字段，huge_pages 缺省补 0）。
// 字节均以字节为单位（orig/compr 为未压缩/压缩后数据大小）。
struct ZramMmSample {
    uint64_t orig_data_size = 0;
    uint64_t compr_data_size = 0;
    uint64_t mem_used_total = 0;
    uint64_t mem_limit = 0;
    uint64_t mem_used_max = 0;
    uint64_t same_pages = 0;
    uint64_t pages_compacted = 0;
    uint64_t huge_pages = 0;
};
// 解析 zram mm_stat 一行。字段缺省补 0（CXX-002 不吞错：全空行/out 空指针
// 判 false 并 ALOGE）。
bool ParseZramMmStat(const std::string& line, ZramMmSample* out);

// zram io_stat 采样（6 字段；旧内核仅 4 字段，缺省补 0）。
struct ZramIoSample {
    uint64_t failed_reads = 0;
    uint64_t failed_writes = 0;
    uint64_t invalid_io = 0;
    uint64_t notify_free = 0;
    uint64_t read_bytes = 0;
    uint64_t write_bytes = 0;
};
// 解析 zram io_stat 一行。4~6 字段兼容，缺省补 0；全空行/out 空指针判 false。
bool ParseZramIoStat(const std::string& line, ZramIoSample* out);

// 悬挂检测状态：consecutive_high 连续满足条件次数。
struct HangState {
    uint32_t consecutive_high = 0;
};
// 悬挂检测（纯函数）：inflight >= inflight_threshold 且 write_lat_ms >=
// lat_threshold_ms 时 consecutive_high++，否则清零；>=3 返回 true。
// CXX-002：consecutive_high 到 UINT32_MAX 后不再自增（防溢出回绕）。
bool UpdateHangState(HangState* state, uint64_t inflight,
                     uint64_t inflight_threshold, double write_lat_ms,
                     double lat_threshold_ms);

// 规则二（慢盘）状态：consecutive 连续超阈次数。
struct SlowDiskState {
    uint32_t consecutive = 0;
};
// 规则二（纯函数）：write_avg_lat_ms > threshold_ms（严格大于）时
// consecutive++，否则清零；>= require_consecutive 返回 true。
// 触发后不自行重置（由主循环调用方决定是否收敛事件频率）。
// CXX-002：consecutive 到 UINT32_MAX 后不再自增（防溢出回绕）。
bool EvaluateSlowDiskRule(SlowDiskState* state, double write_avg_lat_ms,
                          double threshold_ms, uint32_t require_consecutive);

// BlockCollector：枚举 /sys/block/* 块设备并做 10s 周期差分采样。
// 仅主循环线程调用（无锁，内部 mPrev 状态不被并发访问）。
// dev 首次采样只存 prev 返回"空"（无速率），第二次起计算 BlockRates。
class BlockCollector {
public:
    // 枚举块设备名（排除 loop*/ram*/zram* 虚拟设备）。opendir 失败返回
    // false 并限频 ALOGE（CXX-002 不吞错）。
    bool EnumerateBlockDevices(std::vector<std::string>& out);
    // 读 /sys/block/<dev>/stat 解析为采样。读/解析失败返回 false（限频
    // ALOGE，CXX-002 不吞错）。
    bool ReadBlockStat(const std::string& dev, BlockStatSample* out);
    // 单设备差分采样：读当前 stat → 与上次 prev 单调差分 → 按实际采样
    // 间隔（prev 到本次的时刻差）计算 BlockRates。首次采样只存 prev
    // 返回 false（无速率）。outInflight 返回当前 in_flight 瞬时值（block
    // 日志行与悬挂检测用）。读失败返回 false。
    bool SampleDevice(const std::string& dev, BlockRates* out,
                      uint64_t* outInflight = nullptr);

private:
    struct PrevSample {
        BlockStatSample sample;
        std::chrono::steady_clock::time_point at;
    };
    // dev 名 → 上次采样样本 + 时刻（首次采样建立，之后每次采样推进）
    std::unordered_map<std::string, PrevSample> mPrev;
};

// ZramCollector：读 /sys/block/zram0/{mm_stat,io_stat}，输出水位/压缩比/
// io 统计。仅主循环线程调用（无锁）。
class ZramCollector {
public:
    struct Sample {
        uint64_t memUsedTotal = 0; // mm_stat.mem_used_total（当前已用内存）
        uint64_t memUsedMax = 0;   // mm_stat.mem_used_max（峰值水位）
        double comprRatio = 0.0;   // 压缩比 = orig/compr（CXX-002 除零防护 0）
        uint64_t failedReads = 0;  // io_stat.failed_reads
        uint64_t failedWrites = 0; // io_stat.failed_writes
        uint64_t readBytes = 0;    // io_stat.read_bytes
        uint64_t writeBytes = 0;   // io_stat.write_bytes
    };
    // 采样 zram0。任一关键文件读取/解析失败返回 false（限频 ALOGE，
    // CXX-002 不吞错）。
    bool SampleZram(Sample* out);
};

}  // namespace lciod
}  // namespace lechao
