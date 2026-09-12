// ============================================================
// main_loop.cpp — daemon 直读主循环实现（自 lechao_lcview.cpp 抽离）
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：runMainLoop 提到可测边界——签名注入 DeviceReader 抽象
//   接口（生产 EpollDeviceReader / 单测 FakeDeviceReader），主循环
//   接线可被单测编译覆盖；行为与原 lechao_lcview.cpp 内实现一致：
//     1) 预防性 flush（缓冲剩余不足内核最小读单位）
//     2) 读内核（readOnce）→ 心跳（emitHeartbeat，时间驱动 30s）
//     3) 攒包 flush（flushSegment：满/超时/500ms 滞留窗）
//     4) 两条出口（致命读错误 / 优雅退出）前强制 flush 残留
// 信号：gRunning + signalHandler 随主循环迁入，installSignalHandlers
//   注册 SIGINT/SIGTERM（main 不再自行 signal）。
// 命名空间：全部实现置于 vendor::lechao::lcview（与 main_loop.h 声明
//   同域），避免链接时符号找不到（LTO 下全局函数与命名空间声明错位）。
// ============================================================

#define LOG_TAG "lechao_lcview"

#include "main_loop.h"

#include "batch_parser.h"
#include "lechao_log.h"
#include <log/log.h>
#include <csignal>
#include <cerrno>
#include <chrono>
#include <cstring>

namespace vendor {
namespace lechao {
namespace lcview {

// 全局运行标志，被信号处理器置 false 以触发优雅退出
std::atomic<bool> gRunning(true);

// 信号处理函数：收到 SIGINT/SIGTERM 时设置退出标志，
// 使主循环自然结束，确保当前批次日志不丢失
static void signalHandler(int) {
    gRunning = false;
}

void installSignalHandlers() {
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);
}

// ============================================================
// 主循环三段拆分（本批）：原 runMainLoop 105 行仍超长，按职责拆为
// 读取（readOnce）/ 心跳（emitHeartbeat）/ 落盘（flushSegment）。
// 丢数据收口：读取前若缓冲剩余空间不足内核最小读单位，先预防性
// flush（方向 1）；两条出口（致命读错误 / 优雅退出）前强制 flush
// 残留（方向 2）——除这两处外主体行为与拆分时一致。
// ============================================================

// 读取段：单次 epoll 读 + 读计数 + 致命读错误处理
// 返回原始读字节数；n<0 表示致命读错误（已打日志并累计 readErr，
// 调用方须退出主循环交 init 重启）
static ssize_t readOnce(DeviceReader& reader, uint8_t* buf, size_t bufSize,
                        int timeoutMs, size_t& offset, uint64_t& readOk,
                        uint64_t& readEmpty, uint64_t& readErr,
                        std::chrono::steady_clock::time_point& dataArrivedAt)
{
    ssize_t n = reader.waitAndRead(buf, offset, bufSize, timeoutMs);
    if (n < 0) {
        // CXX-004: 致命读错误 4 步退出（日志 → 调用方 exit 交 init 重启），
        // 禁止静默 return 僵尸态（采集链路中断须可见）。
        // LCV-12：先保存 errno 再用（strerror 允许修改 errno，实参
        // 求值顺序未指定，两次读取点可能不一致）
        int saved = errno;
        readErr++;
        ALOGE("lechao_lcview: read error, errno=%d (%s), buffered=%zu, "
              "exiting for init restart", saved, strerror(saved), offset);
        return n;
    }
    if (n > 0) {
        readOk++;
        if (offset == 0)
            dataArrivedAt = std::chrono::steady_clock::now();
        offset += static_cast<size_t>(n);
    } else {
        readEmpty++;
    }
    return n;
}

// 心跳段（每 30 loop）：直读内核 overrun/total_records，
// dropped 取 FileWriter DROP 合计（七条丢记录路径汇总，
// 含 invalid 写失败恢复不成 invalidWriteFailed），
// readErr 为读错误计数——HAL 停用后三字段由 daemon 补齐，
// 供 liveness 判据（logfield overrun/dropped/readErr=0）继续成立；
// invalidRecords 为 parseBatch 丢弃累计（wire 漂移/坏记录判红可见性：
// 采集链路死了 jsonl 归零、三个零值字段仍全 0，须 invalid 累计兜底）；
// 写路径指标（方向 3）：formatJsonLine 与 writeRecord 平均微秒/条，
// 作微优化的可判定指标（drain 被攒包策略钉死，对写路径不敏感）
static void emitHeartbeat(uint64_t loopCount, DeviceReader& reader,
                          FileWriter& writer, int64_t& overrunAccum,
                          uint64_t readErr,
                          long long jsonlRecords, long long invalidRecords)
{
    uint32_t ov = reader.getOverrun();
    overrunAccum += ov;
    const FileWriter::DropCounters& dc = writer.dropCounters();
    uint64_t dropped = static_cast<uint64_t>(dc.openFailed)
        + dc.formatEmpty + dc.formatOob + dc.reopenFailed
        + dc.retryFailed + dc.invalidNotOpen + dc.invalidWriteFailed;
    const FileWriter::WriteTimings& wt = writer.writeTimings();
    uint64_t avgFormatUs = wt.formatCount ? wt.formatTotalUs / wt.formatCount : 0;
    uint64_t avgWriteUs = wt.writeCount ? wt.writeTotalUs / wt.writeCount : 0;
    // LCV-16/17：ioctl 失败与 EOF 计数（失败返 0 与真实 0 在心跳可区分）
    ALOGI("lechao_lcview: heartbeat, loop=%llu, overrun=%lld, dropped=%llu, "
          "readErr=%llu, total_records=%u, jsonl_records=%lld, "
          "invalid_records=%lld, ioctl_err=%llu, eof=%llu, "
          "drop_open=%llu drop_format=%llu drop_oob=%llu "
          "drop_reopen=%llu drop_retry=%llu drop_invalid=%llu "
          "drop_invalidwrite=%llu, "
          "avg_format_us=%llu avg_write_us=%llu",
          static_cast<unsigned long long>(loopCount),
          static_cast<long long>(overrunAccum),
          static_cast<unsigned long long>(dropped),
          static_cast<unsigned long long>(readErr),
          reader.getTotalRecords(), jsonlRecords, invalidRecords,
          static_cast<unsigned long long>(reader.ioctlErr()),
          static_cast<unsigned long long>(reader.eofCount()),
          static_cast<unsigned long long>(dc.openFailed),
          static_cast<unsigned long long>(dc.formatEmpty),
          static_cast<unsigned long long>(dc.formatOob),
          static_cast<unsigned long long>(dc.reopenFailed),
          static_cast<unsigned long long>(dc.retryFailed),
          static_cast<unsigned long long>(dc.invalidNotOpen),
          static_cast<unsigned long long>(dc.invalidWriteFailed),
          static_cast<unsigned long long>(avgFormatUs),
          static_cast<unsigned long long>(avgWriteUs));
}

// 落盘段：flush 条件判定 → 攒包解析写盘 → 轮转/容量管理
// flush 条件（与 HAL readerLoop 同语义）：缓冲满 / epoll 超时 /
// 500ms 滞留窗到期——攒出的批次 = 4B 长度前缀 + 二进制记录序列
// （判定抽入 batch_parser::shouldFlushBatch，原 hal_test readerLoop
// flush 语义并入 daemon 单测覆盖）
static void flushSegment(DeviceReader& reader, SchemaParser& schema,
                         FileWriter& writer, const uint8_t* buf, size_t& offset,
                         std::chrono::steady_clock::time_point& dataArrivedAt,
                         uint64_t& flushCount, long long& jsonlRecords,
                         long long& invalidRecords, ssize_t n,
                         size_t bufSize)
{
    static constexpr auto kMaxBufferAge = std::chrono::milliseconds(500);
    bool timedOut = (n == 0);
    bool ageExpired = (offset > 0 &&
        std::chrono::steady_clock::now() - dataArrivedAt > kMaxBufferAge);
    if (shouldFlushBatch(offset, timedOut, ageExpired, bufSize)) {
        flushCount++;
        // LCV-06：指针+长度直传 parseBatch，不再构造 vector 中转
        // （最大 64KB 拷贝/批）
        BatchParseResult parsed = parseBatch(schema, writer, buf, offset);
        jsonlRecords += parsed.validCnt;
        // invalid 累计透传（方向 1）：wire 漂移等丢弃可见于心跳，
        // 防"采集链路死了 jsonl 归零而零值字段仍全 0"假绿
        invalidRecords += parsed.invalidCnt;
        ALOGI("lechao_lcview: batch parsed: %u valid, %u invalid, %zuB "
              "(build=%s)", parsed.validCnt, parsed.invalidCnt, offset,
              LCVIEW_BUILD_TAG);
        offset = 0;
        dataArrivedAt = std::chrono::steady_clock::time_point::max();
    }

    // 轮转与容量检查：checkRotation 每轮执行（跨天轮转需及时）；
    // enforceRetention 已按写入计数降频（方向 4，空批轮次不再全目录扫描）
    writer.checkRotation();
    writer.enforceRetention();
}

// 直读主循环：读内核 → 心跳 → 攒包 flush → 轮转/容量管理
// reader 为 DeviceReader 抽象接口：生产 EpollDeviceReader，单测
// 注入 FakeDeviceReader（先返数据再返 -1，验证 writer 收到该批次）
int runMainLoop(DeviceReader& reader, SchemaParser& schema, FileWriter& writer)
{
    // LCV-14：高频计数器改 uint64_t（int 有符号溢出是 UB，重载下
    // epoll 立返 loop 计数快速膨胀，周级即溢出）
    uint64_t loopCount = 0;
    uint64_t readOk = 0, readEmpty = 0, readErr = 0, flushCount = 0;
    int64_t overrunAccum = 0;
    // JSONL 落盘累计条数（守恒校验基准：内核 total_records ≈ overrun + 落盘条数）
    long long jsonlRecords = 0;
    // parseBatch 丢弃累计（invalid 计数，方向 1：心跳可见性）
    long long invalidRecords = 0;
    static constexpr size_t kBufSize = 64 * 1024;
    static constexpr int kEpollTimeoutMs = 1000;
    // 内核单次读最小容量：内核 read 要求 cap-offset >= 4096，否则 -EINVAL
    static constexpr size_t kMinReadSize = 4096;
    uint8_t buf[kBufSize];
    size_t offset = 0;
    auto dataArrivedAt = std::chrono::steady_clock::time_point::max();
    // 上次心跳时刻（时间驱动锚点，见主循环心跳判定）
    auto lastBeatAt = std::chrono::steady_clock::now();

    while (gRunning) {
        // 方向 1 预防性 flush：缓冲剩余空间不足以容纳内核最小读单位时，
        // 先强制落盘清空缓冲，令 read 的 cap-offset 恒 >= kMinReadSize，
        // 根治内核侧 -EINVAL 触发的主循环退出重启环
        if (shouldPreventiveFlush(offset, kBufSize, kMinReadSize)) {
            flushSegment(reader, schema, writer, buf, offset, dataArrivedAt,
                         flushCount, jsonlRecords, invalidRecords, 0, kBufSize);
        }

        ssize_t n = readOnce(reader, buf, kBufSize, kEpollTimeoutMs, offset,
                             readOk, readEmpty, readErr, dataArrivedAt);
        loopCount++;

        if (n < 0) {
            // 方向 2：致命读错误退出前强制落盘缓冲残留，不丢已收数据
            if (shouldFlushOnExit(offset)) {
                flushSegment(reader, schema, writer, buf, offset, dataArrivedAt,
                             flushCount, jsonlRecords, invalidRecords, 0, kBufSize);
            }
            return 1;  // 致命读错误：readOnce 已打日志，退出交 init 重启
        }

        // 心跳时间驱动：距上次满 30s 才发（原 loopCount % 30 在重载下
        // epoll 立返、loop 计数快速膨胀，心跳空转刷屏；时间驱动与
        // 负载解耦，静默期/高负载期都恒 30s 一发）
        auto now = std::chrono::steady_clock::now();
        if (now - lastBeatAt >= std::chrono::seconds(30)) {
            emitHeartbeat(loopCount, reader, writer, overrunAccum, readErr,
                          jsonlRecords, invalidRecords);
            lastBeatAt = now;
        }

        if (::lechao::debugVerbose()) {
            ALOGI("lechao_lcview: tick loop=%llu buffered=%zu readOk=%llu "
                  "readEmpty=%llu readErr=%llu flush=%llu",
                  static_cast<unsigned long long>(loopCount), offset,
                  static_cast<unsigned long long>(readOk),
                  static_cast<unsigned long long>(readEmpty),
                  static_cast<unsigned long long>(readErr),
                  static_cast<unsigned long long>(flushCount));
        }

        flushSegment(reader, schema, writer, buf, offset, dataArrivedAt,
                     flushCount, jsonlRecords, invalidRecords, n, kBufSize);
    }

    ALOGI("lechao_lcview: exiting, readOk=%llu readEmpty=%llu readErr=%llu "
          "flush=%llu",
          static_cast<unsigned long long>(readOk),
          static_cast<unsigned long long>(readEmpty),
          static_cast<unsigned long long>(readErr),
          static_cast<unsigned long long>(flushCount));
    // 方向 2：优雅退出前强制落盘缓冲残留，不丢已收数据
    if (shouldFlushOnExit(offset)) {
        flushSegment(reader, schema, writer, buf, offset, dataArrivedAt,
                     flushCount, jsonlRecords, invalidRecords, 0, kBufSize);
    }
    return 0;
}

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor
