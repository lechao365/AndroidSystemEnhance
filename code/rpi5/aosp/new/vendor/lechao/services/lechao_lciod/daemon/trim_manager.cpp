// ============================================================
// trim_manager.cpp — ext4 碎片清理链（FITRIM）管理器实现（UP-110 批一）
// 实现见 trim_manager.h。纯函数无 I/O；执行层经 statvfs + 状态文件 +
// 挂载点 FITRIM ioctl 工作，单线程调用（运行循环），无锁。
// 读取/写入失败均返回 false 并保留 errno（CXX-002 禁止错误吞噬）。
// ============================================================

#define LOG_TAG "lechao_lciod"

#include "trim_manager.h"

#include <log/log.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <fcntl.h>
#include <unistd.h>
#include <cerrno>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <ctime>
#include <string>

// FITRIM：ext4 在挂载点目录 fd 上发起，仅释放文件系统空闲块（无损）。
// 本仓按 hal/vendor_lechao_usbd-ioctl.h 先例手工声明内核 ABI（避免
// <linux/fs.h> 与 bionic 头冲突）：_IOWR('X',121,24B)=0xC0185879，
// 与内核 FITRIM 数值一致（fstrim_range 为 3×u64）。
struct lechao_fstrim_range {
    uint64_t start;
    uint64_t len;
    uint64_t minlen;
};
#ifndef FITRIM
#define FITRIM _IOWR('X', 121, struct lechao_fstrim_range)
#endif

namespace lechao {
namespace lciod {

using clock = std::chrono::steady_clock;

// ============================================================
// 纯函数层
// ============================================================

uint32_t ComputeFreeRatioBp(uint64_t free_bytes, uint64_t total_bytes)
{
    if (total_bytes == 0)
        return 0;  // CXX-002 除零防护
    if (free_bytes > total_bytes)
        free_bytes = total_bytes;  // 防御异常输入，钳制到 100%
    // 先乘 10000 再除：free <= total <= ~2^44 字节量级，乘法不溢出 64 位
    return static_cast<uint32_t>((free_bytes * 10000ull) / total_bytes);
}

bool ShouldTrim(bool enabled, bool drill, uint32_t free_ratio_bp,
                uint32_t free_threshold_bp, uint64_t days_since_last,
                uint32_t min_days, bool never_trimmed)
{
    if (!enabled)
        return false;
    if (drill)
        return true;  // 演练强制（仍须 enabled）
    if (never_trimmed)
        return true;  // 首次清理无历史门槛
    return free_ratio_bp >= free_threshold_bp &&
           days_since_last >= static_cast<uint64_t>(min_days);
}

uint32_t SegmentCount(uint64_t dev_size, uint64_t seg_bytes)
{
    if (dev_size == 0 || seg_bytes == 0)
        return 0;  // CXX-002 除零/边界防护
    const uint64_t n = (dev_size + seg_bytes - 1) / seg_bytes;  // ceil
    if (n > 0xffffffffull)
        return 0xffffffffu;  // 钳制到 uint32 上限
    return static_cast<uint32_t>(n);
}

bool SegmentAtFromTail(uint64_t dev_size, uint64_t seg_bytes, uint32_t idx,
                       uint64_t* start, uint64_t* len)
{
    if (dev_size == 0 || seg_bytes == 0 || start == nullptr || len == nullptr)
        return false;
    const uint32_t count = SegmentCount(dev_size, seg_bytes);
    if (idx >= count)
        return false;
    // 尾段 idx=0：start = dev_size - seg_bytes（首个完整段在尾部）
    const uint64_t tail = dev_size - static_cast<uint64_t>(idx) * seg_bytes;
    const uint64_t seg = tail >= seg_bytes ? seg_bytes : tail;
    *start = tail - seg;
    *len = seg;
    return true;
}

bool CircuitBreakerTripped(uint64_t elapsed_ms, uint64_t budget_ms,
                           uint32_t segments_done, uint32_t max_segments)
{
    return elapsed_ms >= budget_ms || segments_done >= max_segments;
}

// ============================================================
// 执行层
// ============================================================

bool TrimManager::ReadFile(const char* path, std::string* out)
{
    if (path == nullptr || out == nullptr)
        return false;
    const int fd = ::open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0)
        return false;
    char buf[256];
    const ssize_t n = ::read(fd, buf, sizeof(buf) - 1);
    const int saved = errno;
    ::close(fd);
    if (n < 0) {
        errno = saved;
        return false;
    }
    buf[n] = '\0';
    *out = std::string(buf);
    return true;
}

bool TrimManager::StatMount(const char* mount_path, uint64_t* total_bytes,
                            uint64_t* free_bytes)
{
    if (mount_path == nullptr || total_bytes == nullptr || free_bytes == nullptr)
        return false;
    struct statvfs st {};
    if (::statvfs(mount_path, &st) != 0)
        return false;
    // f_frsize 为基本块大小；总/空闲空间 = frsize * blocks/bfree（64 位量级）
    *total_bytes = static_cast<uint64_t>(st.f_frsize) *
                   static_cast<uint64_t>(st.f_blocks);
    *free_bytes = static_cast<uint64_t>(st.f_frsize) *
                  static_cast<uint64_t>(st.f_bfree);
    return true;
}

bool TrimManager::ReadState(const char* state_path, uint64_t* last_epoch,
                            bool* never_trimmed)
{
    if (state_path == nullptr || last_epoch == nullptr || never_trimmed == nullptr)
        return false;
    *never_trimmed = false;
    std::string text;
    if (!ReadFile(state_path, &text)) {
        if (errno == ENOENT) {
            *never_trimmed = true;  // 首次：无状态文件，允许清理
            return true;
        }
        return false;  // 其他读取失败不吞错
    }
    // CXX-003 输入防御：内容须为纯十进制，逐字符校验（禁 strtoull 宽松性）
    std::string v;
    for (char c : text) {
        if (c == '\n' || c == '\r' || c == ' ' || c == '\t')
            continue;
        if (c < '0' || c > '9')
            return false;  // 非数字即非法，拒用
        v.push_back(c);
    }
    if (v.empty())
        return false;
    uint64_t acc = 0;
    for (char c : v) {
        const uint64_t digit = static_cast<uint64_t>(c - '0');
        if (acc > (UINT64_MAX - digit) / 10ull)
            return false;  // CXX-002 溢出防护
        acc = acc * 10ull + digit;
    }
    *last_epoch = acc;
    return true;
}

bool TrimManager::WriteState(const char* state_dir, const char* state_path,
                             uint64_t epoch_sec)
{
    if (state_dir == nullptr || state_path == nullptr)
        return false;
    // mkdir -p：目录已存在（EEXIST）视为成功；其余错误不吞
    if (::mkdir(state_dir, 0755) != 0 && errno != EEXIST)
        return false;
    // 原子写：临时文件 + rename（崩溃不致半写状态）
    std::string tmp = std::string(state_path) + ".tmp";
    const int fd = ::open(tmp.c_str(), O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC,
                          0644);
    if (fd < 0)
        return false;
    char buf[32];
    const int n = ::snprintf(buf, sizeof(buf), "%llu\n",
                             static_cast<unsigned long long>(epoch_sec));
    ssize_t wr = (n > 0) ? ::write(fd, buf, static_cast<size_t>(n)) : -1;
    const int saved = errno;
    if (wr != n)
        ::unlink(tmp.c_str());
    ::fsync(fd);
    ::close(fd);
    if (wr != n) {
        errno = saved;
        return false;
    }
    if (::rename(tmp.c_str(), state_path) != 0) {
        ::unlink(tmp.c_str());
        return false;
    }
    return true;
}

bool TrimManager::ExecuteSegmentedTrim(uint64_t dev_size, uint32_t max_segments,
                                       uint64_t seg_bytes, uint32_t sleep_us,
                                       uint64_t budget_ms, Result* out)
{
    if (out == nullptr)
        return false;
    out->trimmed_bytes = 0;
    out->segments = 0;
    out->duration_ms = 0;
    out->circuit_breaker = false;

    const uint32_t hard_max = max_segments > 0 ? max_segments : kTrimMaxSegments;
    const uint32_t total = SegmentCount(dev_size, seg_bytes);
    const uint32_t todo = total < hard_max ? total : hard_max;
    if (todo == 0) {
        out->error = "no segment to trim (empty device or zero segment size)";
        return false;
    }

    const int fd = ::open(kTrimMountPath, O_RDONLY | O_DIRECTORY | O_CLOEXEC);
    if (fd < 0) {
        out->error = std::string("open ") + kTrimMountPath +
                     " failed: " + std::strerror(errno);
        return false;
    }

    const auto t0 = clock::now();
    bool fatal = false;
    uint32_t done = 0;
    for (uint32_t idx = 0; idx < todo; ++idx) {
        const uint64_t elapsed_ms = static_cast<uint64_t>(
            std::chrono::duration_cast<std::chrono::milliseconds>(
                clock::now() - t0).count());
        if (CircuitBreakerTripped(elapsed_ms, budget_ms, idx, hard_max)) {
            out->circuit_breaker = true;
            ALOGW("lechao_lciod: trim circuit breaker tripped at segment %u/%u "
                  "(elapsed=%llums)", idx, todo,
                  static_cast<unsigned long long>(elapsed_ms));
            break;
        }
        uint64_t start = 0, len = 0;
        if (!SegmentAtFromTail(dev_size, seg_bytes, idx, &start, &len))
            break;
        struct lechao_fstrim_range range {};
        range.start = start;
        range.len = len;
        range.minlen = 0;  // 0 = 由文件系统按块粒度裁量
        if (::ioctl(fd, FITRIM, &range) != 0) {
            // EOPNOTSUPP/ENOTTY/ENOSYS：底层不支持 discard → 非可自愈，致命退出
            if (errno == EOPNOTSUPP || errno == ENOTTY || errno == ENOSYS) {
                out->error = std::string("FITRIM unsupported: ") +
                             std::strerror(errno);
                fatal = true;
                break;
            }
            // 单段瞬时失败（EINTR/EIO 等）：记录并继续后续段（不吞错）
            ALOGW("lechao_lciod: FITRIM segment %u failed: %s (continue)",
                  idx, std::strerror(errno));
            ++done;
        } else {
            // 内核按实际清理量回填 range.len
            out->trimmed_bytes += range.len;
            ++done;
        }
        if (idx + 1 < todo && sleep_us > 0)
            ::usleep(sleep_us);
    }

    out->segments = done;
    out->duration_ms = static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            clock::now() - t0).count());
    ::close(fd);
    if (fatal)
        return false;
    if (done == 0) {
        if (out->error.empty())
            out->error = "no FITRIM segment executed";
        return false;
    }
    return true;
}

}  // namespace lciod
}  // namespace lechao
