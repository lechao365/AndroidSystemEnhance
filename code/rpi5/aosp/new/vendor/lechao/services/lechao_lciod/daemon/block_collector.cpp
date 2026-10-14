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
#include <cstdlib>

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

// 解析 zram bd_stat 一行（3 字段：bd_count/bd_reads/bd_writes，均 4KiB 页数）。
// 全空行/out 空指针判 false 并 ALOGE；字段数不足 3 时缺省补 0（兼容旧内核
// 或写回未配置时的裁剪节点）。
bool ParseZramBdStat(const std::string& line, ZramBdSample* out)
{
    if (!out)
        return false;
    std::istringstream iss(line);
    uint64_t vals[3] = {0};
    int n = 0;
    uint64_t v;
    while (n < 3 && (iss >> v))
        vals[n++] = v;
    if (n < 1) {
        // CXX-003：空行/无字段判 false 带上下文
        ALOGE("lechao_lciod: zram bd_stat: empty line");
        return false;
    }
    out->bd_count = vals[0];
    out->bd_reads = vals[1];
    out->bd_writes = vals[2];
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

    // 写回后备统计（bd_stat）：可选增强源——CONFIG_ZRAM_WRITEBACK 关闭或
    // backing_dev 未配置时节点可能缺失，缺失/解析失败仅缺省 0（*out 已置 0）
    // 并限频 ALOGE，不判失败（zram 观测主功能不依赖写回）。方向三批二：
    // daemon 采集 bd_stat 写回计数供换出页回写量观测。
    const std::string bdPath = base + "/bd_stat";
    std::string bdLine;
    if (readFileToString(bdPath, bdLine)) {
        ZramBdSample bd;
        if (ParseZramBdStat(bdLine, &bd)) {
            out->bdCount = bd.bd_count;
            out->bdReads = bd.bd_reads;
            out->bdWrites = bd.bd_writes;
        }
    } else {
        logReadErrorRateLimited("read zram bd_stat", bdPath);
    }
    return true;
}

// 解析 sysfs 属性值为非负 uint64：仅去除行尾空白（sysfs 属性值带尾随换行），
// 行首空白/负号/字母等一律判false（CXX-003 输入防御）。空/out 空指针返回
// false；逐字符十进制校验并防累加溢出回绕（CXX-002）。
bool SdCollector::ParseU64(const std::string& line, uint64_t* out)
{
    if (!out)
        return false;
    size_t end = line.size();
    while (end > 0) {
        const char c = line[end - 1];
        if (c == '\n' || c == '\r' || c == ' ' || c == '\t')
            --end;
        else
            break;
    }
    if (end == 0)
        return false;
    uint64_t v = 0;
    for (size_t i = 0; i < end; ++i) {
        const char ch = line[i];
        if (ch < '0' || ch > '9')
            return false;  // CXX-003：非十进制数字（含负号/内部空白）判非法
        const uint64_t digit = static_cast<uint64_t>(ch - '0');
        // CXX-002：累加前检溢出，超 uint64 上界判 false（不回绕）
        if (v > (UINT64_MAX - digit) / 10)
            return false;
        v = v * 10 + digit;
    }
    *out = v;
    return true;
}

// 采样 lciod_sd sysfs（enable/window_bytes/window_requests/stuck_count 与
// SSR 解码速度档 ssr_speed_class/ssr_uhs_grade/ssr_valid）。逐属性读取，
// 任一读/解析失败即限频 ALOGE 并返回 false（CXX-002 不吞错）；模块缺失时
// 由调用方降级跳过本轮。
bool SdCollector::SampleSd(Sample* out)
{
    if (!out)
        return false;
    *out = Sample{};
    const std::string base = "/sys/class/lciod_sd/lciod_sd/";
    struct Prop {
        const char* name;
        uint64_t* field;
    };
    const Prop props[] = {
        {"enable", &out->enable},
        {"window_bytes", &out->windowBytes},
        {"window_requests", &out->windowRequests},
        {"stuck_count", &out->stuckCount},
        {"ssr_speed_class", &out->ssrSpeedClass},
        {"ssr_uhs_grade", &out->ssrUhsGrade},
        {"ssr_valid", &out->ssrValid},
    };
    for (const auto& p : props) {
        std::string content;
        if (!readFileToString(base + p.name, content)) {
            logReadErrorRateLimited("read lciod_sd sysfs", base);
            return false;
        }
        if (!ParseU64(content, p.field)) {
            logReadErrorRateLimited("read lciod_sd sysfs", base);
            return false;
        }
    }
    out->ok = true;
    return true;
}

// ============================================================
// zram 换入压力观测（方向三批一）
// ============================================================

// 解析 PSI io "some ..." 行（avg10/avg60/avg300/total）。仅认 some 行，供
// 换入压力窗聚合的 PSI 维度；缺 total 或缺 avg 判 false（CXX-003 输入防御）。
bool ParsePsiIoSome(const std::string& line, PsiIoSample* out)
{
    if (!out)
        return false;
    std::istringstream iss(line);
    std::string first;
    if (!(iss >> first) || first != "some")
        return false;
    bool have_avg = false;
    bool have_total = false;
    std::string tok;
    while (iss >> tok) {
        const size_t eq = tok.find('=');
        if (eq == std::string::npos)
            continue;
        const std::string k = tok.substr(0, eq);
        const std::string v = tok.substr(eq + 1);
        if (k == "avg10") {
            out->avg10 = std::strtod(v.c_str(), nullptr);
            have_avg = true;
        } else if (k == "avg60") {
            out->avg60 = std::strtod(v.c_str(), nullptr);
        } else if (k == "avg300") {
            out->avg300 = std::strtod(v.c_str(), nullptr);
        } else if (k == "total") {
            // CXX-003：仅接受整段为十进制数字（防尾随垃圾被吞）
            char* endp = nullptr;
            const unsigned long long n = std::strtoull(v.c_str(), &endp, 10);
            if (endp != nullptr && *endp == '\0') {
                out->total_us = static_cast<uint64_t>(n);
                have_total = true;
            }
        }
    }
    return have_avg && have_total;
}

// 从 /proc/vmstat 文本提取键值（"<key> <value>" 逐对读取）→ 非负十进制。
// 缺键/越界/非数字判 false（CXX-003）。
bool ParseVmstatKey(const std::string& text, const std::string& key, uint64_t* out)
{
    if (!out || key.empty())
        return false;
    std::istringstream iss(text);
    std::string k;
    uint64_t v;
    while (iss >> k >> v) {
        if (k == key) {
            *out = v;
            return true;
        }
    }
    return false;
}

// 换入压力超阈判定：窗口换入页数 > 0 且 >= 阈值 → true。阈值 0 表示任何
// 换入即触发（演练降阈用），但仍在"确有换入"前提下，避免零活动假绿。
bool ShouldReportZramSwapPressure(uint64_t swapin_pages, uint64_t threshold_pages)
{
    return swapin_pages > 0 && swapin_pages >= threshold_pages;
}

// 采样一窗换入压力。任一必需源（vmstat/PSI io）读取或解析失败即限频 ALOGE
// 并返回 false（调用方降级跳过本轮，CXX-002 不吞错）；zram io_stat 属可选
// （zram 未启用时缺失，视为 0 不阻断）。counter reset 时差分取 curr（防下溢）。
bool ZramSwapPressureCollector::SampleWindow(Sample* out)
{
    if (!out)
        return false;
    std::string vm;
    if (!readFileToString(kVmstatPath, vm)) {
        logReadErrorRateLimited("read vmstat", kVmstatPath);
        return false;
    }
    uint64_t swapin = 0;
    uint64_t swapout = 0;
    if (!ParseVmstatKey(vm, "pswpin", &swapin) ||
        !ParseVmstatKey(vm, "pswpout", &swapout)) {
        logReadErrorRateLimited("parse vmstat swap counters", kVmstatPath);
        return false;
    }

    std::string psi;
    if (!readFileToString(kPsiIoPath, psi)) {
        logReadErrorRateLimited("read psi io", kPsiIoPath);
        return false;
    }
    PsiIoSample ps;
    bool psi_ok = false;
    {
        std::istringstream iss(psi);
        std::string l;
        while (std::getline(iss, l)) {
            if (ParsePsiIoSome(l, &ps)) {
                psi_ok = true;
                break;
            }
        }
    }
    if (!psi_ok) {
        logReadErrorRateLimited("parse psi io some", kPsiIoPath);
        return false;
    }

    // zram0 io_stat 为可选维度：未启用/缺节点时不阻断（换入压力主判据为 vmstat）
    uint64_t zread = 0;
    uint64_t zwrite = 0;
    std::string io_line;
    if (readFileToString("/sys/block/zram0/io_stat", io_line)) {
        ZramIoSample io;
        if (ParseZramIoStat(io_line, &io)) {
            zread = io.read_bytes;
            zwrite = io.write_bytes;
        }
    }

    *out = Sample{};
    if (!have_) {
        // 首窗只建 prev（无差分），下一窗起输出
        have_ = true;
        prevSwapin_ = swapin;
        prevSwapout_ = swapout;
        prevZramRead_ = zread;
        prevZramWrite_ = zwrite;
        prevPsiTotal_ = ps.total_us;
        return true;
    }
    auto diff = [](uint64_t cur, uint64_t prev) {
        return cur >= prev ? (cur - prev) : cur;  // reset 取 curr，永不下溢
    };
    out->primed = true;
    out->swapinPages = diff(swapin, prevSwapin_);
    out->swapoutPages = diff(swapout, prevSwapout_);
    out->zramReadBytes = diff(zread, prevZramRead_);
    out->zramWriteBytes = diff(zwrite, prevZramWrite_);
    out->psiIoTotalUs = diff(ps.total_us, prevPsiTotal_);
    prevSwapin_ = swapin;
    prevSwapout_ = swapout;
    prevZramRead_ = zread;
    prevZramWrite_ = zwrite;
    prevPsiTotal_ = ps.total_us;
    return true;
}

}  // namespace lciod
}  // namespace lechao
