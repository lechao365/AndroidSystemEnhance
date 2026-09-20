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
#include "../include/lcview_events.h"
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

// 守恒告警判定（方向 3/7）：dev = totalΔ - (overrunΔ + droppedΔ + jsonlΔ +
// invalidΔ)，|dev| 超容差即判告警（正值：产生未落盘在途积压/丢记录；负值：
// 落盘超过产生，重复落盘/计数漂移）。纯函数，供 emitHeartbeat 与单测共用。
bool shouldAlarmConservation(uint64_t totalDelta, uint64_t overrunDelta,
                             uint64_t droppedDelta, uint64_t jsonlDelta,
                             uint64_t invalidDelta, int64_t tolerance)
{
    const int64_t dev = static_cast<int64_t>(totalDelta)
        - static_cast<int64_t>(overrunDelta + droppedDelta
                               + jsonlDelta + invalidDelta);
    return dev > tolerance || dev < -tolerance;
}

// 守恒基线单心跳推进与告警判定（R-02 方向 3，抽自 emitHeartbeat 内联逻辑）。
// 语义见 main_loop.h ConserveBaseline::updateAndCheck 注释；实现把"ioctl
// 失败跳过数值推进 / 首心跳建基线 / 后续心跳增量判定 + 推进"三态收口，
// emitHeartbeat 不再内联守恒逻辑（可测边界），只消费 Result 打告警日志。
ConserveBaseline::Result ConserveBaseline::updateAndCheck(const Sample& s)
{
    Result r;
    if (s.ioctlErr != ioctlErr) {
        // 方向 6：本轮 ioctl 失败，跳过守恒校验并仅推进 ioctlErr 基线；
        // 数值基线保持上次成功值（防失败返 0 失真）
        ioctlErr = s.ioctlErr;
        return r;
    }
    if (!initialized) {
        // 首心跳（ioctl 成功）：建立基线，次心跳起按增量比较
        initialized = true;
        total = s.total;
        overrun = s.overrun;
        dropped = s.dropped;
        persistedValid = s.persistedValid;
        persistedInvalid = s.persistedInvalid;
        return r;
    }
    r.totalDelta = static_cast<uint64_t>(s.total - total);
    r.overrunDelta = static_cast<uint64_t>(s.overrun - overrun);
    r.droppedDelta = static_cast<uint64_t>(s.dropped - dropped);
    r.jsonlDelta = static_cast<uint64_t>(s.persistedValid - persistedValid);
    r.invalidDelta = static_cast<uint64_t>(s.persistedInvalid
                                           - persistedInvalid);
    r.tolerance = computeConserveTolerance(s.ringSizeBytes);
    r.dev = static_cast<int64_t>(r.totalDelta)
        - static_cast<int64_t>(r.overrunDelta + r.droppedDelta
                               + r.jsonlDelta + r.invalidDelta);
    r.broken = shouldAlarmConservation(
        r.totalDelta, r.overrunDelta, r.droppedDelta, r.jsonlDelta,
        r.invalidDelta, r.tolerance);
    // 方向 6：每心跳推进数值基线（防 uint32 total_records 回绕）
    total = s.total;
    overrun = s.overrun;
    dropped = s.dropped;
    persistedValid = s.persistedValid;
    persistedInvalid = s.persistedInvalid;
    return r;
}

// 生产心跳 writer：格式化 HeartbeatFields 为 ALOGI 心跳行（原 emitHeartbeat
// 内联 ALOGI 移此），供 runMainLoop 生产注入（liveness 判据 logfield 字段
// 顺序与内容保持兼容，不得变更字段名）。
void LogHeartbeatWriter::write(const HeartbeatFields& hb)
{
    ALOGI("lechao_lcview: heartbeat, loop=%llu, overrun=%lld, dropped=%llu, "
          "readErr=%llu, total_records=%u, jsonl_records=%lld, "
          "invalid_records=%lld, ioctl_err=%llu, eof=%llu, "
          "drop_open=%llu drop_format=%llu drop_oob=%llu "
          "drop_reopen=%llu drop_retry=%llu drop_invalid=%llu "
          "drop_invalidwrite=%llu "
          "drop_rotate=%llu drop_invrotate=%llu drop_rollback=%llu, "
          "avg_format_us=%llu avg_write_us=%llu",
          static_cast<unsigned long long>(hb.loop),
          static_cast<long long>(hb.overrun),
          static_cast<unsigned long long>(hb.dropped),
          static_cast<unsigned long long>(hb.readErr),
          hb.totalRecords, hb.jsonlRecords, hb.invalidRecords,
          static_cast<unsigned long long>(hb.ioctlErr),
          static_cast<unsigned long long>(hb.eofCount),
          static_cast<unsigned long long>(hb.dropOpen),
          static_cast<unsigned long long>(hb.dropFormat),
          static_cast<unsigned long long>(hb.dropOob),
          static_cast<unsigned long long>(hb.dropReopen),
          static_cast<unsigned long long>(hb.dropRetry),
          static_cast<unsigned long long>(hb.dropInvalid),
          static_cast<unsigned long long>(hb.dropInvalidWrite),
          static_cast<unsigned long long>(hb.dropRotate),
          static_cast<unsigned long long>(hb.dropInvRotate),
          static_cast<unsigned long long>(hb.dropRollback),
          static_cast<unsigned long long>(hb.avgFormatUs),
          static_cast<unsigned long long>(hb.avgWriteUs));
}

// 心跳段（每 30 loop）：直读内核 overrun/total_records，
// dropped 取 FileWriter DROP 合计（10 条丢记录路径汇总，
// 含 invalid 写失败恢复不成 invalidWriteFailed），
// readErr 为读错误计数——HAL 停用后三字段由 daemon 补齐，
// 供 liveness 判据（logfield overrun/dropped/readErr=0）继续成立；
// invalidRecords 为 parseBatch 丢弃累计（wire 漂移/坏记录判红可见性：
// 采集链路死了 jsonl 归零、三个零值字段仍全 0，须 invalid 累计兜底）；
// 写路径指标（方向 3）：formatJsonLine 与 writeRecord 平均微秒/条，
// 作微优化的可判定指标（drain 被攒包策略钉死，对写路径不敏感）。
// R-02 方向 3：输出端改 IHeartbeatWriter 接口注入（生产 LogHeartbeatWriter，
// 单测记录型 writer），守恒判定收口到 conserve.updateAndCheck()。
// R-03 方向 3：去 static 并入头声明（main_loop.h）——emitHeartbeat 成为
// 可测边界，单测直调生产函数注入 FakeDeviceReader + FileWriter，断言字段
// 真实透传到 writer（原单测只构造 HeartbeatFields 直写，不覆盖收集逻辑）。
void emitHeartbeat(uint64_t loopCount, DeviceReader& reader,
                   FileWriter& writer, int64_t& overrunAccum,
                   uint64_t readErr,
                   long long jsonlRecords, long long invalidRecords,
                   ConserveBaseline& conserve, IHeartbeatWriter& out)
{
    uint32_t ov = reader.getOverrun();
    overrunAccum += ov;
    const FileWriter::DropCounters& dc = writer.dropCounters();
    // 方向 3：dropped 求和纳入 dropRotate/dropInvRotate/dropRollback——
    // 轮转/回滚失败也属丢记录（或半行残留风险），须进心跳求和与分项
    uint64_t dropped = static_cast<uint64_t>(dc.openFailed)
        + dc.formatEmpty + dc.formatOob + dc.reopenFailed
        + dc.retryFailed + dc.invalidNotOpen + dc.invalidWriteFailed
        + dc.dropRotate + dc.dropInvRotate + dc.dropRollback;
    // 守恒校验（方向 3/5/6/7）：内核 total_records 累计产生应等于
    // overrun（驱逐）+ dropped（ENOSPC 丢弃）+ jsonl（合法落盘）+
    // invalid（非法落盘）之和，偏差即"在途积压"（内核 ring 未读 + 用户态
    // 64KB 攒包缓冲未落盘），超容差即守恒破坏（丢记录/重复落盘/计数漂移）。
    // 右式去向改用真实计数：jsonl/invalid 取 FileWriter 落盘计数（方向 5，
    // 解析成功数在 DROP 时仍 +1 会高估落盘致负向误报），dropped 取内核
    // dropped_cnt（方向 7，ENOSPC 丢弃与 total_records 同步递增）。
    // 重启适配 + 防回绕 + ioctl 失败跳过（方向 6）：基线移 runMainLoop
    // 局部（ConserveBaseline）并按心跳推进——daemon 重启后内核累计不归零
    // 而进程内计数归零，须增量比较；相邻心跳窗口比较使 uint32 total 永不
    // 回绕；任一 ioctl 失败（查询返 0 伪装真实 0）时跳过守恒且不推进数值
    // 基线，防失败值失真。
    const uint32_t total = reader.getTotalRecords();
    const uint32_t kernDropped = reader.getDropped();
    const uint32_t ringSize = reader.getRingSizeBytes();
    const FileWriter::PersistCounters& pc = writer.persistCounters();
    // R-02 方向 3：守恒逻辑收口到 ConserveBaseline::updateAndCheck（纯函数，
    // 单测可注入 Sample 覆盖三态 + 正负向告警）；Result.broken 即守恒破坏，
    // 告警详情直接引用 Result 各项增量（不做外部反推，防推进后基线差失真）。
    ConserveBaseline::Sample sample = {
        total, overrunAccum, kernDropped, reader.ioctlErr(),
        pc.valid, pc.invalid, ringSize,
    };
    const ConserveBaseline::Result cr = conserve.updateAndCheck(sample);
    if (cr.broken) {
        ALOGE("lechao_lcview: CONSERVATION BROKEN: dev=%lld (tol=%lld), "
              "total_delta=%llu overrun_delta=%llu dropped_delta=%llu "
              "jsonl_delta=%llu invalid_delta=%llu",
              static_cast<long long>(cr.dev),
              static_cast<long long>(cr.tolerance),
              static_cast<unsigned long long>(cr.totalDelta),
              static_cast<unsigned long long>(cr.overrunDelta),
              static_cast<unsigned long long>(cr.droppedDelta),
              static_cast<unsigned long long>(cr.jsonlDelta),
              static_cast<unsigned long long>(cr.invalidDelta));
    }
    // 方向 5：心跳 30s 同锚刷活跃文件落盘（fdatasync），缩小断电丢失窗口
    writer.fsyncActiveFiles();
    const FileWriter::WriteTimings& wt = writer.writeTimings();
    uint64_t avgFormatUs = wt.formatCount ? wt.formatTotalUs / wt.formatCount : 0;
    uint64_t avgWriteUs = wt.writeCount ? wt.writeTotalUs / wt.writeCount : 0;
    HeartbeatFields hb = {
        loopCount, overrunAccum, dropped, readErr,
        total, jsonlRecords, invalidRecords,
        reader.ioctlErr(), reader.eofCount(),
        static_cast<uint64_t>(dc.openFailed),
        static_cast<uint64_t>(dc.formatEmpty),
        static_cast<uint64_t>(dc.formatOob),
        static_cast<uint64_t>(dc.reopenFailed),
        static_cast<uint64_t>(dc.retryFailed),
        static_cast<uint64_t>(dc.invalidNotOpen),
        static_cast<uint64_t>(dc.invalidWriteFailed),
        static_cast<uint64_t>(dc.dropRotate),
        static_cast<uint64_t>(dc.dropInvRotate),
        static_cast<uint64_t>(dc.dropRollback),
        avgFormatUs, avgWriteUs,
    };
    out.write(hb);
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
    // 守恒基线（方向 6）：runMainLoop 局部，经引用传入 emitHeartbeat——
    // 替代 emitHeartbeat 内 static 局部（多实例/多测试串扰 + 无法按心跳
    // 推进防 uint32 回绕）
    ConserveBaseline conserve;
    // R-02 方向 3：心跳输出端注入生产 writer（ALOGI 落盘）——emitHeartbeat
    // 只依赖 IHeartbeatWriter 接口，单测注入记录型 writer 可断言心跳内容
    LogHeartbeatWriter heartbeatWriter;
    static constexpr size_t kBufSize = 64 * 1024;
    static constexpr int kEpollTimeoutMs = 1000;
    // 预防性 flush 阈值 = 单条记录上限（LCVIEW_MAX_RECORD_SIZE，真相源内核
    // LCVIEW_BUILDER_MAX_SIZE）：内核 read 无 4096 读下限（lcview_ring_read
    // 仅按记录长度上限校验，不要求 cap-offset 固定值）；EMSGSIZE（首条记录
    // 放不下剩余缓冲，KRN-001）由"剩余空间恒 >= 单条记录上限"的预防性 flush
    // 提前闭合，读路径不再因缓冲不足返 EMSGSIZE
    static constexpr size_t kMinReadSize = LCVIEW_MAX_RECORD_SIZE;
    // 契约收敛硬约束（方向 4）：缓冲总大小必须能容纳至少一条最大记录——
    // 否则预防性 flush 后剩余空间不可能容纳单条记录上限，EMSGSIZE 必漏网
    // （原断言 kMinReadSize >= LCVIEW_MAX_RECORD_SIZE 中 kMinReadSize 定义
    // 即为 LCVIEW_MAX_RECORD_SIZE，恒真无防护价值，改为对 kBufSize 的真实
    // 门禁）
    static_assert(kBufSize >= LCVIEW_MAX_RECORD_SIZE,
                  "kBufSize 须不小于单条记录上限 LCVIEW_MAX_RECORD_SIZE");
    // 契约门禁（方向 4）：用户态单条上限必须与内核真相源 LCVIEW_BUILDER_MAX_SIZE
    // 相等——两侧仅一处修改而缓冲预算未同步即漂移，缓冲"够大"的断言会被绕过
    static_assert(LCVIEW_BUILDER_MAX_SIZE == LCVIEW_MAX_RECORD_SIZE,
                  "LCVIEW_BUILDER_MAX_SIZE（内核真相源）须与 "
                  "LCVIEW_MAX_RECORD_SIZE 相等");
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
                          jsonlRecords, invalidRecords, conserve,
                          heartbeatWriter);
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
