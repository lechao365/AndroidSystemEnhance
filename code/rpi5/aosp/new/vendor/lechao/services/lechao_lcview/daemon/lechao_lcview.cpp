// ============================================================
// lechao_lcview.cpp — LcView 守护进程主入口
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：作为事件日志系统的直读端，负责：
//   1) 经 DeviceReader（EpollDeviceReader）直读内核字符设备
//      /dev/vendor_lechao_lcview（open/epoll/read/ioctl）
//   2) 调用 SchemaParser 对二进制日志记录进行校验和解析
//   3) 调用 FileWriter 将解析后的日志写入 JSONL 文件
//   4) 处理日志文件轮转和过期删除（磁盘空间管理）
//
// 架构演进：daemon 直读内核，HAL 已退役（二进制/rc/VINTF/ILcView 全删）——
// 原 HAL 读取职责（DeviceReader）迁入本进程，getBatch/waitForHal/
// rebindAfterError/DEAD_OBJECT 分支取消。本批：写路径耗时指标进心跳
// （avg_format_us/avg_write_us）、enforceRetention 按写入计数降频、
// 主循环体抽 runMainLoop（main 拆分，行为不变）。
// ============================================================

#define LOG_TAG "lechao_lcview"

#include "SchemaParser.h"
#include "FileWriter.h"
#include "batch_parser.h"
#include "DeviceReader.h"
#include "../include/lcview_events.h"
#include <log/log.h>
#include <csignal>
#include <atomic>
#include <chrono>
#include <cerrno>
#include <cstring>
#include <thread>
#include "lechao_log.h"

using namespace vendor::lechao::lcview;

// 全局运行标志，被信号处理器置 false 以触发优雅退出
static std::atomic<bool> gRunning(true);

// 构建标识：每次上板验证批次唯一，启动/心跳日志携带，
// 供板端 grep 精确确认"新二进制已在运行"（防假验证）
#define LCVIEW_BUILD_TAG "LCVIEW-VERIFY-20260829-05"

// 信号处理函数：收到 SIGINT/SIGTERM 时设置退出标志，
// 使主循环自然结束，确保当前批次日志不丢失
static void signalHandler(int) {
    gRunning = false;
}

// ============================================================
// 主循环三段拆分（本批）：原 runMainLoop 105 行仍超长，按职责拆为
// 读取（readOnce）/ 心跳（emitHeartbeat）/ 落盘（flushSegment），
// 行为完全不变（拆分自原 main() 的 runMainLoop）
// ============================================================

// 读取段：单次 epoll 读 + 读计数 + 致命读错误处理
// 返回原始读字节数；n<0 表示致命读错误（已打日志并累计 readErr，
// 调用方须退出主循环交 init 重启）
static ssize_t readOnce(EpollDeviceReader& reader, uint8_t* buf, size_t bufSize,
                        int timeoutMs, size_t& offset, int& readOk,
                        int& readEmpty, int& readErr,
                        std::chrono::steady_clock::time_point& dataArrivedAt)
{
    ssize_t n = reader.waitAndRead(buf, offset, bufSize, timeoutMs);
    if (n < 0) {
        // CXX-004: 致命读错误 4 步退出（日志 → 调用方 exit 交 init 重启），
        // 禁止静默 return 僵尸态（采集链路中断须可见）
        readErr++;
        ALOGE("lechao_lcview: read error, errno=%d (%s), buffered=%zu, "
              "exiting for init restart", errno, strerror(errno), offset);
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
// 写路径指标（方向 3）：formatJsonLine 与 writeRecord 平均微秒/条，
// 作微优化的可判定指标（drain 被攒包策略钉死，对写路径不敏感）
static void emitHeartbeat(int loopCount, EpollDeviceReader& reader,
                          FileWriter& writer, int64_t& overrunAccum, int readErr,
                          long long jsonlRecords)
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
    ALOGI("lechao_lcview: heartbeat, loop=%d, overrun=%lld, dropped=%llu, "
          "readErr=%d, total_records=%u, jsonl_records=%lld, "
          "drop_open=%llu drop_format=%llu drop_oob=%llu "
          "drop_reopen=%llu drop_retry=%llu drop_invalid=%llu "
          "drop_invalidwrite=%llu, "
          "avg_format_us=%llu avg_write_us=%llu",
          loopCount, static_cast<long long>(overrunAccum), dropped,
          readErr, reader.getTotalRecords(), jsonlRecords,
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
static void flushSegment(EpollDeviceReader& reader, SchemaParser& schema,
                         FileWriter& writer, const uint8_t* buf, size_t& offset,
                         std::chrono::steady_clock::time_point& dataArrivedAt,
                         int& flushCount, long long& jsonlRecords, ssize_t n,
                         size_t bufSize)
{
    static constexpr auto kMaxBufferAge = std::chrono::milliseconds(500);
    bool timedOut = (n == 0);
    bool ageExpired = (offset > 0 &&
        std::chrono::steady_clock::now() - dataArrivedAt > kMaxBufferAge);
    if (shouldFlushBatch(offset, timedOut, ageExpired, bufSize)) {
        flushCount++;
        std::vector<uint8_t> batch(buf, buf + offset);
        BatchParseResult parsed = parseBatch(schema, writer, batch);
        jsonlRecords += parsed.validCnt;
        ALOGI("lechao_lcview: batch parsed: %u valid, %u invalid, %zuB "
              "(build=%s)", parsed.validCnt, parsed.invalidCnt, batch.size(),
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
// 拆分自原 main()（157 行）：主循环体独立成函数后，main 仅保留
// 初始化/退出骨架，行为完全不变
static int runMainLoop(EpollDeviceReader& reader, SchemaParser& schema,
                       FileWriter& writer)
{
    int loopCount = 0;
    int readOk = 0, readEmpty = 0, readErr = 0, flushCount = 0;
    int64_t overrunAccum = 0;
    // JSONL 落盘累计条数（守恒校验基准：内核 total_records ≈ overrun + 落盘条数）
    long long jsonlRecords = 0;
    static constexpr size_t kBufSize = 64 * 1024;
    static constexpr int kEpollTimeoutMs = 1000;
    uint8_t buf[kBufSize];
    size_t offset = 0;
    auto dataArrivedAt = std::chrono::steady_clock::time_point::max();

    while (gRunning) {
        ssize_t n = readOnce(reader, buf, kBufSize, kEpollTimeoutMs, offset,
                             readOk, readEmpty, readErr, dataArrivedAt);
        loopCount++;

        if (n < 0)
            return 1;  // 致命读错误：readOnce 已打日志，退出交 init 重启

        if (loopCount % 30 == 0)
            emitHeartbeat(loopCount, reader, writer, overrunAccum, readErr,
                          jsonlRecords);

        if (::lechao::debugVerbose()) {
            ALOGI("lechao_lcview: tick loop=%d buffered=%zu readOk=%d "
                  "readEmpty=%d readErr=%d flush=%d",
                  loopCount, offset, readOk, readEmpty, readErr, flushCount);
        }

        flushSegment(reader, schema, writer, buf, offset, dataArrivedAt,
                     flushCount, jsonlRecords, n, kBufSize);
    }

    ALOGI("lechao_lcview: exiting, readOk=%d readEmpty=%d readErr=%d flush=%d",
          readOk, readEmpty, readErr, flushCount);
    return 0;
}

int main(int argc, char* argv[])
{
    // 注册信号处理器，支持 init 发送 SIGTERM 停止服务
    signal(SIGINT, signalHandler);
    signal(SIGTERM, signalHandler);

    ALOGI("lechao_lcview: starting, build=%s", LCVIEW_BUILD_TAG);

    SchemaParser schema;
    // 默认 schema 路径：vendor 分区的配置文件
    std::string schemaPath = "/vendor/etc/lcview_events.json";
    // 支持命令行参数覆盖 schema 路径（方便测试）
    if (argc > 1)
        schemaPath = argv[1];

    // v3.4 优化: schema 加载失败时重试 30 次（最多 15 秒），
    //   因为 schema 文件所在的 vendor 分区可能在启动早期尚未挂载完成。
    //   替代旧版本直接 FATAL 退出的策略，提高启动可靠性。
    //   （重试逻辑抽入 batch_parser 可测函数）
    const bool schemaOk = loadSchemaWithRetry(schema, schemaPath, 30);
    if (!schemaOk) {
        ALOGE("lechao_lcview: failed to load schema from %s", schemaPath.c_str());
        return 1;
    }
    ALOGI("lechao_lcview: loaded %zu event schemas", schema.eventCount());

    // 文件写入配置
    // logDir: 日志存储目录（需确保 /data/vendor/lechao_lcview 存在且有写权限）
    // maxFileSizeMb: 单个日志文件最大 50MB，超过则轮转
    // maxTotalSizeMb: 总日志量上限 500MB，超限则删除最旧文件
    FileWriterConfig fwCfg;
    fwCfg.logDir = "/data/vendor/lechao_lcview/logs";
    fwCfg.maxFileSizeMb = 50;
    fwCfg.maxTotalSizeMb = 500;
    FileWriter writer(fwCfg);

    // 直读内核设备（原 HAL 职责并入 daemon；设备节点单打开限制，
    // 部署须先停 HAL，否则 open 返 EBUSY）
    EpollDeviceReader reader;
    int openRetry = 0;
    while (gRunning && !reader.open()) {
        if (++openRetry >= 1200) {
            /* CXX-004: 设备打开失败退出，rc 非 oneshot 交 init 重启重试；
             * 禁止静默 return 伪装正常（采集链路不可用须可见） */
            ALOGE("lechao_lcview: cannot open device after retries, exiting for init restart");
            return 1;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    if (!gRunning) {
        ALOGI("lechao_lcview: exiting (stopped during open)");
        return 0;
    }
    ALOGI("lechao_lcview: device opened, entering main loop");

    // 主循环：直读内核攒包 → 解析 → 写盘（原 HAL readerLoop 攒包语义
    // 迁入 daemon：64KB 缓冲、1s epoll 超时、500ms 滞留窗 flush）
    return runMainLoop(reader, schema, writer);
}
