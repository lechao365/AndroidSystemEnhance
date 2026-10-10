// ============================================================
// block_collector.cpp — 块设备/zram 用户态观测采集器实现（R3 方向1/2/3）
// 所属模块：LcIod IO 健康监控 — Daemon 层（R5 方向1 自 lcview 平移）
// 实现见 block_collector.h；纯函数无 I/O，采集器经 sysfs 读设备统计。
// sysfs 读取失败降级：返回 false 并限频 ALOGE（CXX-002 禁止错误吞噬）。
// 全部函数单线程调用（主循环 10s 采样），无锁、无独立线程。
// ============================================================

#define LOG_TAG "lechao_lciod"

#include "block_collector.h"

#include <log/log.h>
#include <dirent.h>
#include <sys/types.h>
#include <sstream>
#include <fstream>
#include <cstddef>
#include <cstdint>

namespace lechao {
namespace lciod {

namespace {

// CXX-002 不允许吞错：sysfs 读失败须 ALOGE，但限频防高频刷屏——设备
// 长时间缺失/权限异常时主循环每 10s 尝试一次，无限 ALOGE 污染日志。
// 单线程调用（主循环），文件静态无需加锁。
constexpr auto kRateLimitInterval = std::chrono::seconds(60);
std::chrono::steady_clock::time_point gLastErrLogAt;

void logReadErrorRateLimited(const char* what, const std::string& path)
{
    const auto now = std::chrono::steady_clock::now();
    if (now - gLastErrLogAt >= kRateLimitInterval) {
        ALOGE("lechao_lciod: %s failed: %s", what, path.c_str());
        gLastErrLogAt = now;
    }
}

// 读取整个文件到 string（sysfs 统计文件均小体积）。失败返回 false
//（调用方负责限频 ALOGE）。
bool readFileToString(const std::string& path, std::string& out)
{
    std::ifstream ifs(path);
    if (!ifs.is_open())
        return false;
    std::ostringstream oss;
    oss << ifs.rdbuf();
    if (ifs.bad())
        return false;
    out = oss.str();
    return true;
}

}  // namespace

// 解析 /sys/block/<dev>/stat 一行。兼容 15 或 11 字段：字段数 < 11 判
// false（CXX-003 输入防御，带字段名上下文日志）；11~14 字段（旧内核无
// discard 段）discard 字段补 0。
bool ParseBlockStat(const std::string& line, BlockStatSample* out)
{
    if (!out)
        return false;
    std::istringstream iss(line);
    uint64_t vals[15] = {0};
    int n = 0;
    uint64_t v;
    while (n < 15 && (iss >> v))
        vals[n++] = v;
    if (n < 11) {
        // CXX-003：解析失败返回明确错误码并带上下文（行首 80 字节防刷屏
        // 长行截断）
        ALOGE("lechao_lciod: block stat: insufficient fields (%d < 11): %s",
              n, line.substr(0, 80).c_str());
        return false;
    }
    out->read_ios = vals[0];
    out->read_merges = vals[1];
    out->read_sectors = vals[2];
    out->read_ticks_ms = vals[3];
    out->write_ios = vals[4];
    out->write_merges = vals[5];
    out->write_sectors = vals[6];
    out->write_ticks_ms = vals[7];
    out->in_flight = vals[8];
    out->io_ticks_ms = vals[9];
    out->time_in_queue_ms = vals[10];
    // 11~14 字段：discard 段缺省补 0（vals 已初始化为 0，仅对已解析到的
    // 字段取真实值，未解析到的自然为 0）
    out->discard_ios = (n >= 12) ? vals[11] : 0;
    out->discard_merges = (n >= 13) ? vals[12] : 0;
    out->discard_sectors = (n >= 14) ? vals[13] : 0;
    out->discard_ticks_ms = (n >= 15) ? vals[14] : 0;
    return true;
}

// 单调差分：curr >= prev 取 curr - prev；curr < prev（counter reset，
// 内核计数归零/设备重挂载）取 curr（从 0 重算）——CXX-002 回绕防护，
// 差分永不下溢。15 字段逐项处理。
BlockStatDelta DiffBlockSamples(const BlockStatSample& prev,
                                const BlockStatSample& curr)
{
    BlockStatDelta d;
#define LCVIEW_DIFF_FIELD(f) \
    d.f = (curr.f >= prev.f) ? (curr.f - prev.f) : curr.f;
    LCVIEW_DIFF_FIELD(read_ios)
    LCVIEW_DIFF_FIELD(read_merges)
    LCVIEW_DIFF_FIELD(read_sectors)
    LCVIEW_DIFF_FIELD(read_ticks_ms)
    LCVIEW_DIFF_FIELD(write_ios)
    LCVIEW_DIFF_FIELD(write_merges)
    LCVIEW_DIFF_FIELD(write_sectors)
    LCVIEW_DIFF_FIELD(write_ticks_ms)
    LCVIEW_DIFF_FIELD(in_flight)
    LCVIEW_DIFF_FIELD(io_ticks_ms)
    LCVIEW_DIFF_FIELD(time_in_queue_ms)
    LCVIEW_DIFF_FIELD(discard_ios)
    LCVIEW_DIFF_FIELD(discard_merges)
    LCVIEW_DIFF_FIELD(discard_sectors)
    LCVIEW_DIFF_FIELD(discard_ticks_ms)
#undef LCVIEW_DIFF_FIELD
    return d;
}

// 速率/平均延迟计算。sector=512B；CXX-002 除零防护：
//   period_ns==0 → 速率类/busy 为 0（平均延迟不依赖 period，仍计算）；
//   ios==0 → 对应 iops/平均延迟为 0。
// 吞吐与忙占比用 double 计算，规避 sector*512 整数溢出（CXX-002）。
BlockRates ComputeBlockRates(const BlockStatDelta& delta, uint64_t period_ns)
{
    BlockRates r;
    constexpr double kSectorBytes = 512.0;
    const double periodSec = static_cast<double>(period_ns) / 1e9;
    const double periodMs = static_cast<double>(period_ns) / 1e6;
    if (periodSec > 0.0) {
        r.read_iops = static_cast<double>(delta.read_ios) / periodSec;
        r.read_bytes_per_s =
            static_cast<double>(delta.read_sectors) * kSectorBytes / periodSec;
        r.write_iops = static_cast<double>(delta.write_ios) / periodSec;
        r.write_bytes_per_s =
            static_cast<double>(delta.write_sectors) * kSectorBytes / periodSec;
        if (periodMs > 0.0) {
            const double busy =
                static_cast<double>(delta.io_ticks_ms) / periodMs;
            // io_ticks 理论上限为窗口时长，采样抖动可能微超，钳制到 [0,1]
            r.busy_ratio = busy > 1.0 ? 1.0 : (busy < 0.0 ? 0.0 : busy);
        }
    }
    if (delta.read_ios > 0)
        r.read_avg_lat_ms =
            static_cast<double>(delta.read_ticks_ms) /
            static_cast<double>(delta.read_ios);
    if (delta.write_ios > 0)
        r.write_avg_lat_ms =
            static_cast<double>(delta.write_ticks_ms) /
            static_cast<double>(delta.write_ios);
    return r;
}

// 解析 zram mm_stat 一行（8 字段，mm_stat 原生 7 字段时 huge_pages 补 0）。
bool ParseZramMmStat(const std::string& line, ZramMmSample* out)
{
    if (!out)
        return false;
    std::istringstream iss(line);
    uint64_t vals[8] = {0};
    int n = 0;
    uint64_t v;
    while (n < 8 && (iss >> v))
        vals[n++] = v;
    if (n < 1) {
        // CXX-003：空行/无字段判 false 带上下文
        ALOGE("lechao_lciod: zram mm_stat: empty line");
        return false;
    }
    out->orig_data_size = vals[0];
    out->compr_data_size = vals[1];
    out->mem_used_total = vals[2];
    out->mem_limit = vals[3];
    out->mem_used_max = vals[4];
    out->same_pages = vals[5];
    out->pages_compacted = vals[6];
    // huge_pages：旧格式/独立节点时缺省补 0（vals[7] 已初始化为 0）
    out->huge_pages = vals[7];
    return true;
}

// 解析 zram io_stat 一行（6 字段，旧内核 4 字段时 read/write_bytes 补 0）。
bool ParseZramIoStat(const std::string& line, ZramIoSample* out)
{
    if (!out)
        return false;
    std::istringstream iss(line);
    uint64_t vals[6] = {0};
    int n = 0;
    uint64_t v;
    while (n < 6 && (iss >> v))
        vals[n++] = v;
    if (n < 1) {
        ALOGE("lechao_lciod: zram io_stat: empty line");
        return false;
    }
    out->failed_reads = vals[0];
    out->failed_writes = vals[1];
    out->invalid_io = vals[2];
    out->notify_free = vals[3];
    // 旧内核仅 4 字段：read/write_bytes 缺省补 0
    out->read_bytes = vals[4];
    out->write_bytes = vals[5];
    return true;
}

// 悬挂检测：inflight 高且写延迟高持续 3 次判悬挂。
bool UpdateHangState(HangState* state, uint64_t inflight,
                     uint64_t inflight_threshold, double write_lat_ms,
                     double lat_threshold_ms)
{
    if (!state)
        return false;
    if (inflight >= inflight_threshold && write_lat_ms >= lat_threshold_ms) {
        // CXX-002：UINT32_MAX 饱和（10s 一采，32 位计数实际不可能溢出，
        // 防御性钳制防回绕成 0 破坏"持续状态"语义）
        if (state->consecutive_high != UINT32_MAX)
            state->consecutive_high++;
    } else {
        state->consecutive_high = 0;
    }
    return state->consecutive_high >= 3;
}

// 规则二（慢盘）：写平均延迟严格大于阈值时连续计数，>= require_consecutive
// 判慢盘。触发后不自行重置（由主循环调用方决定事件收敛策略）。
bool EvaluateSlowDiskRule(SlowDiskState* state, double write_avg_lat_ms,
                          double threshold_ms, uint32_t require_consecutive)
{
    if (!state)
        return false;
    if (write_avg_lat_ms > threshold_ms) {
        if (state->consecutive != UINT32_MAX)
            state->consecutive++;
    } else {
        state->consecutive = 0;
    }
    return state->consecutive >= require_consecutive;
}

// 枚举 /sys/block/* 块设备，排除 loop*/ram*/zram* 虚拟设备。
bool BlockCollector::EnumerateBlockDevices(std::vector<std::string>& out)
{
    out.clear();
    DIR* dir = opendir("/sys/block");
    if (!dir) {
        // CXX-002 不吞错：枚举失败可见（限频）
        logReadErrorRateLimited("enumerate /sys/block",
                                "/sys/block (opendir)");
        return false;
    }
    struct dirent* e;
    while ((e = readdir(dir)) != nullptr) {
        const std::string name(e->d_name);
        if (name == "." || name == "..")
            continue;
        // 排除虚拟设备：loop 回环、ram 内存盘、zram 压缩盘（zram 单独采集）
        if (name.rfind("loop", 0) == 0)
            continue;
        if (name.rfind("ram", 0) == 0)
            continue;
        if (name.rfind("zram", 0) == 0)
            continue;
        out.push_back(name);
    }
    closedir(dir);
    return true;
}

// 读 /sys/block/<dev>/stat 并解析为采样。
bool BlockCollector::ReadBlockStat(const std::string& dev, BlockStatSample* out)
{
    if (!out)
        return false;
    const std::string path = "/sys/block/" + dev + "/stat";
    std::string content;
    if (!readFileToString(path, content)) {
        // CXX-002 不吞错：读失败返回 false 并限频 ALOGE
        logReadErrorRateLimited("read block stat", path);
        return false;
    }
    if (!ParseBlockStat(content, out)) {
        // ParseBlockStat 已带上下文 ALOGE
        logReadErrorRateLimited("parse block stat", path);
        return false;
    }
    return true;
}

// 单设备差分采样：首次只存 prev 返回 false（无速率），之后按实际采样间隔
// 计算 BlockRates 并返回 true。
bool BlockCollector::SampleDevice(const std::string& dev, BlockRates* out,
                                  uint64_t* outInflight)
{
    if (!out)
        return false;
    BlockStatSample curr;
    if (!ReadBlockStat(dev, &curr))
        return false;
    const auto now = std::chrono::steady_clock::now();
    const auto it = mPrev.find(dev);
    if (it == mPrev.end()) {
        // 首次采样：只存 prev，返回空（无速率可算）
        mPrev[dev] = PrevSample{curr, now};
        return false;
    }
    const uint64_t periodNs = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::nanoseconds>(
            now - it->second.at)
            .count());
    const BlockStatDelta delta = DiffBlockSamples(it->second.sample, curr);
    *out = ComputeBlockRates(delta, periodNs);
    if (outInflight)
        *outInflight = curr.in_flight;
    // 推进 prev（含时刻），下次采样以本次为基准
    it->second.sample = curr;
    it->second.at = now;
    return true;
}

// 采样 zram0 水位/io 统计。关键文件（mm_stat/io_stat）读取或解析失败
// 返回 false（限频 ALOGE，CXX-002 不吞错）。
bool ZramCollector::SampleZram(Sample* out)
{
    if (!out)
        return false;
    *out = Sample{};
    const std::string base = "/sys/block/zram0";
    const std::string mmPath = base + "/mm_stat";
    const std::string ioPath = base + "/io_stat";
    std::string mmLine, ioLine;
    if (!readFileToString(mmPath, mmLine) || !readFileToString(ioPath, ioLine)) {
        // 不区分哪个文件失败，统一限频告警（zram 节点缺失属常见部署差异）
        logReadErrorRateLimited("read zram stat", base);
        return false;
    }
    ZramMmSample mm;
    if (!ParseZramMmStat(mmLine, &mm))
        return false;  // ParseZramMmStat 已 ALOGE
    ZramIoSample io;
    if (!ParseZramIoStat(ioLine, &io))
        return false;  // ParseZramIoStat 已 ALOGE

    out->memUsedTotal = mm.mem_used_total;
    out->memUsedMax = mm.mem_used_max;
    // 压缩比 = 原始数据大小 / 压缩后大小（CXX-002 除零防护：compr==0 取 0）
    out->comprRatio = (mm.compr_data_size > 0)
                          ? static_cast<double>(mm.orig_data_size) /
                                static_cast<double>(mm.compr_data_size)
                          : 0.0;
    out->failedReads = io.failed_reads;
    out->failedWrites = io.failed_writes;
    out->readBytes = io.read_bytes;
    out->writeBytes = io.write_bytes;
    return true;
}

}  // namespace lciod
}  // namespace lechao
