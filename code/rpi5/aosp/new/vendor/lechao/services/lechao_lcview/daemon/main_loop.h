// ============================================================
// main_loop.h — daemon 直读主循环可测边界
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：把主循环体（readOnce/emitHeartbeat/flushSegment/
//   runMainLoop）从 lechao_lcview.cpp 抽到独立文件并编入
//   daemon_sources filegroup，使单测可链接覆盖主循环接线——
//   此前 runMainLoop 在含 main() 的 lechao_lcview.cpp 内，单测
//   无法编译该文件（与 gtest 主函数冲突），主循环长期零检出。
//   可测边界同时收口信号：installSignalHandlers 注册 SIGINT/
//   SIGTERM 置 gRunning=false 触发优雅退出，main 不再自行 signal。
// ============================================================

#pragma once

#include <atomic>
#include <cstdint>

#include "DeviceReader.h"
#include "SchemaParser.h"
#include "FileWriter.h"

namespace vendor {
namespace lechao {
namespace lcview {

// 构建标识：每次上板验证批次唯一，启动/心跳日志携带，
// 供板端 grep 精确确认"新二进制已在运行"（防假验证）
#define LCVIEW_BUILD_TAG "LCVIEW-VERIFY-20260912-01"

// 全局运行标志，被信号处理器置 false 以触发优雅退出
extern std::atomic<bool> gRunning;

// 注册 SIGINT/SIGTERM 信号处理器（置 gRunning=false）
void installSignalHandlers();

// 直读主循环：读内核 → 心跳 → 攒包 flush → 轮转/容量管理
// reader 为 DeviceReader 抽象接口——生产注入 EpollDeviceReader，
// 单测注入 FakeDeviceReader（先返数据再返 -1），主循环接线
// 可由单测覆盖（堵长期零检出）。
int runMainLoop(DeviceReader& reader, SchemaParser& schema, FileWriter& writer);

// 守恒告警容差（方向 6）：改按 ring 大小推导，不再用固定常量。容差语义
// = "在途积压上限"（内核 ring 未读 + 用户态 64KB 攒包缓冲未落盘的记录数
// 上界），与 ring 配置成正比；固定 16384 恰是默认 256KB ring 的推导值，
// ring 配置变化时固定值失配（过大漏报持续漂移 / 过小误报瞬时积压）。
// 推导式：(ring_size_bytes + 用户缓冲 64KB) / 最小记录 ~20B（前缀 4 + 头 16），
// 默认 ring 256KB → (262144+65536)/20 = 16384，与原固定容差一致。
constexpr uint32_t kConserveUserBufBytes = 64 * 1024;
constexpr uint32_t kConserveMinRecordEstimate = 20;

// 按 ring 大小推导守恒容差（纯函数，方向 6）：ring 越小容差越小，越灵敏；
// ring 越大容差越大，容忍更大在途积压。数据源 reader.getRingSizeBytes()
// ioctl 失败时返回 0（容差退化为最小档），由 ioctl 失败跳过守恒兜底。
inline constexpr int64_t computeConserveTolerance(uint32_t ringSizeBytes)
{
    return (static_cast<int64_t>(ringSizeBytes) + kConserveUserBufBytes) /
           kConserveMinRecordEstimate;
}

// 守恒基线（方向 6）：从 emitHeartbeat 的 static 局部移到 runMainLoop 局部
// 并经引用传入——static 局部在多实例/多测试连续运行间串扰，且无法按心跳
// 推进；改为调用方持有后每心跳推进基线（相邻心跳窗口比较，uint32
// total_records 永不回绕误报）。ioctl 失败（ioctlErr 增量，任一查询返 0
// 伪装真实 0）时跳过守恒校验且不推进数值基线，防失败值失真。
// R-13 方向 3：total/dropped 升 u64 消 uptime 回绕（内核计数已升
// atomic64_t，基线同宽；内核重载检测 `s.total < total` 仍保留）。
struct ConserveBaseline
{
    bool initialized = false;
    uint64_t total = 0;            // 内核 total_records 基线（R-13 升 u64）
    int64_t overrun = 0;           // 用户态 overrunAccum 基线
    uint64_t dropped = 0;          // 内核 dropped_cnt 基线（方向 7，R-13 升 u64）
    uint64_t writerDrop = 0;       // FileWriter DROP 累计基线（R-07 方向 3）
    uint64_t persistedValid = 0;   // FileWriter 合法落盘基线（方向 5）
    uint64_t persistedInvalid = 0; // FileWriter 非法落盘基线（方向 5）
    uint64_t ioctlErr = 0;         // DeviceReader ioctl 失败计数基线

    // 单心跳守恒采样输入（R-02 方向 3）：把 emitHeartbeat 内联的守恒判定
    // 所需数据打包，经 updateAndCheck 统一消费——纯数据结构、无 ALOGI
    // 依赖，使守恒推进/告警逻辑可脱离日志系统单测。
    struct Sample
    {
        uint64_t total;            // 本轮 getTotalRecords()（R-13 升 u64）
        int64_t overrun;           // 本轮 overrunAccum（累计）
        uint64_t dropped;          // 本轮 getDropped()（R-13 升 u64）
        uint64_t writerDrop;       // 本轮 FileWriter DROP 合计（R-07 方向 3）
        uint64_t ioctlErr;         // 本轮 reader.ioctlErr()
        uint64_t persistedValid;   // 本轮 writer.persistCounters().valid
        uint64_t persistedInvalid; // 本轮 writer.persistCounters().invalid
        uint32_t ringSizeBytes;    // 本轮 getRingSizeBytes()（容差推导）
    };

    // 单心跳守恒判定结果（R-02 方向 3）：updateAndCheck 返回本轮是否告警 +
    // 判定窗口增量/偏差，供告警日志直接引用（不依赖外部反推，防推进后
    // 基线差为 0 的日志失真）。
    struct Result
    {
        bool broken = false;          // 本轮守恒是否破坏（|dev| 超容差）
        uint64_t totalDelta = 0;      // 窗口产生增量（本轮 - 上轮）
        uint64_t overrunDelta = 0;    // 窗口驱逐增量
        uint64_t droppedDelta = 0;    // 窗口内核 ENOSPC 丢弃增量
        uint64_t writerDropDelta = 0; // 窗口 FileWriter DROP 增量（R-07 方向 3）
        uint64_t jsonlDelta = 0;      // 窗口合法落盘增量
        uint64_t invalidDelta = 0;    // 窗口非法落盘增量
        int64_t dev = 0;       // totalΔ - (overrunΔ+droppedΔ+writerDropΔ+jsonlΔ+invalidΔ)
        int64_t tolerance = 0; // 本轮容差（ring 推导）
    };

    // 单心跳守恒推进与告警判定（R-02 方向 3，纯函数，无 I/O）：
    //   - ioctlErr 较基线增量 → 跳过数值推进（防失败返 0 失真），仅推进
    //     ioctlErr 基线，返回 Result{broken=false}（失败值不作判定依据）；
    //   - 首心跳（未初始化）→ 建立全量基线，返回 Result{broken=false}；
    //   - 后续心跳 → 按相邻窗口增量计算 dev，|dev| 超容差置 broken=true，
    //     且无论是否告警都推进数值基线（防 uint32 回绕）。
    // Result 各项增量即本轮判定窗口真实增量（告警日志引用，不做外部反推）。
    Result updateAndCheck(const Sample &s);
};

// R-09 方向 1/3/4：心跳窗口统计载体。runMainLoop 按 30s 心跳窗口维护，
// 每次心跳把窗口累计刷新到 HeartbeatFields 后清零（峰值/速率/分布/分类
// 均按窗口口径输出，防累计均值掩盖短时波动）。
struct WindowStats
{
    // 方向 1：窗口峰值字节（背压/读取快慢直观反映）
    uint64_t peakReadBytes = 0;   // 窗口内单次 read 字节峰值
    uint64_t windowReadBytes = 0; // 窗口内累计读字节（速率计算分子）
    // 方向 3：窗口速率（容量规划有据）
    uint64_t windowValidRecords = 0; // 窗口内合法落盘条数
    uint64_t windowWrittenBytes = 0; // 窗口内落盘字节（event 文件 + invalid）
    // 方向 4：invalid 按 reason 分类窗口累计（坏长度 vs schema 漂移）
    long long invalidBadLen = 0;      // 窗口坏长度/坏前缀 invalid 累计
    long long invalidSchemaDrift = 0; // 窗口 schema 漂移 invalid 累计

    void reset()
    {
        peakReadBytes = 0;
        windowReadBytes = 0;
        windowValidRecords = 0;
        windowWrittenBytes = 0;
        invalidBadLen = 0;
        invalidSchemaDrift = 0;
    }
};

// 心跳字段集（R-02 方向 3）：emitHeartbeat 收集的全部指标，经 IHeartbeatWriter
// 透传；LogHeartbeatWriter 负责格式化 ALOGI，单测 writer 直接读字段断言。
// 置于 IHeartbeatWriter 之前——接口签名引用本类型，定义须先于接口声明。
struct HeartbeatFields
{
    uint64_t loop = 0;            // 主循环计数
    int64_t overrun = 0;          // 累计 overrun（用户态）
    uint64_t dropped = 0;         // dropped 求和（DropCounters 全分项）
    uint64_t readErr = 0;         // 读错误计数
    uint64_t totalRecords = 0;    // 内核 total_records（R-13 方向 3 升 u64）
    long long jsonlRecords = 0;   // 合法落盘累计
    long long invalidRecords = 0; // 非法落盘累计
    uint64_t ioctlErr = 0;        // DeviceReader ioctl 失败计数
    uint64_t eofCount = 0;        // EOF 计数
    // DropCounters 分项（心跳可见性，分项判红用）
    uint64_t dropOpen = 0, dropFormat = 0, dropOob = 0;
    uint64_t dropReopen = 0, dropRetry = 0;
    uint64_t dropInvalid = 0, dropInvalidWrite = 0;
    uint64_t dropRotate = 0, dropInvRotate = 0, dropRollback = 0;
    // 写路径平均耗时（微秒，方向 3 微优化可判定指标）
    uint64_t avgFormatUs = 0, avgWriteUs = 0;
    // R-09 方向 1：环水位与窗口峰值字节（背压直接可见性）
    uint32_t ringUsageBytes = 0; // 内核 ring 当前已用字节（getRingUsageBytes）
    uint32_t ringSizeBytes = 0;  // 内核 ring 总大小（getRingSizeBytes）
    uint64_t windowPeakBytes = 0; // 心跳窗口内单次读取字节峰值（读快慢直观反映）
    // R-09 方向 2：写路径每窗口 max 耗时（微秒，尾延迟可见性）
    uint64_t maxFormatUs = 0; // 窗口内 formatJsonLine 单条最大耗时（微秒）
    uint64_t maxWriteUs = 0;  // 窗口内 writeRecord 单条最大耗时（微秒）
    // R-09 方向 3：心跳窗口速率与 event_id 分布（容量规划有据）
    uint64_t recordsPerSec = 0; // 窗口合法落盘速率（条/秒）
    uint64_t bytesPerSec = 0;   // 窗口落盘字节速率（字节/秒）
    uint32_t topEventId = 0;    // 窗口内出现次数最多的 event_id
    uint64_t topEventCnt = 0;   // 窗口内 topEventId 出现次数
    // R-09 方向 4：parseBatch invalid 按 reason 分类（区分坏长度/schema 漂移）
    long long invalidBadLen = 0;      // 坏长度/坏前缀类 invalid 累计
    long long invalidSchemaDrift = 0; // schema 漂移类 invalid 累计
    // R-13 方向 2：事件序列间隙（seq gap）窗口指标
    uint64_t seqGap = 0;      // 窗口内序列间隙（last-first+1 - count）
    uint64_t seqReceived = 0; // 窗口内收到（含 seq 语义）记录条数
    uint32_t seqLast = 0;     // 窗口内最大 seq_no（定位回绕点）
};

// 心跳输出抽象接口（R-02 方向 3）：emitHeartbeat 的落盘端从 ALOGI 解耦为
// 接口，生产注入 LogHeartbeatWriter（ALOGI 落盘），单测注入记录型 writer
// 断言心跳内容。使"守恒校验逻辑"与"心跳如何输出"分离，后者不再污染
// 可测边界（C++ 单测编译期 ALOG 宏无依赖，但断言内容需记录）。
class IHeartbeatWriter
{
  public:
    virtual ~IHeartbeatWriter() = default;

    // 输出一条心跳（完整指标行）；实现方自行决定格式/去向
    virtual void write(const HeartbeatFields &hb) = 0;
};

// 生产心跳 writer：格式化字段为 ALOGI 心跳行（原 emitHeartbeat 内联实现）
class LogHeartbeatWriter : public IHeartbeatWriter
{
  public:
    void write(const HeartbeatFields &hb) override;
};

// 心跳段（R-03 方向 3，可测边界）：收集直读内核/FileWriter/守恒判定的全部
// 指标，经 IHeartbeatWriter 输出。生产 runMainLoop 注入 LogHeartbeatWriter，
// 单测直调本函数注入 FakeDeviceReader + FileWriter + 记录型 writer，断言字段
// 真实透传（原单测只构造 HeartbeatFields 直写，不覆盖收集逻辑）。
// reader 为 DeviceReader 抽象（getOverrun/getTotalRecords/getDropped/
// getRingSizeBytes/ioctlErr/eofCount），writer 为 FileWriter（dropCounters/
// persistCounters/writeTimings/fsyncActiveFiles），out 为心跳输出端。
void emitHeartbeat(uint64_t loopCount, DeviceReader &reader, FileWriter &writer,
                   int64_t &overrunAccum, uint64_t readErr, long long jsonlRecords,
                   long long invalidRecords, ConserveBaseline &conserve, IHeartbeatWriter &out,
                   const WindowStats &window);

// 守恒告警判定（纯函数，方向 3/7）：内核 total_records 增量应等于
// overrun + dropped + writerDrop + jsonl + invalid 增量之和（每条记录要么
// 被驱逐 overrun、要么 ENOSPC 丢弃 dropped、要么 FileWriter 落盘 DROP
// writerDrop、要么合法落盘 jsonl、要么非法落盘 invalid）。
// dev = totalΔ - (overrunΔ + droppedΔ + writerDropΔ + jsonlΔ + invalidΔ)：
// 正值表示产生未落盘（在途积压/丢记录），负值表示落盘超过产生（重复
// 落盘/计数漂移）；|dev| 超容差即判告警。抽成纯函数便于单测覆盖阈值与
// 正负两向。R-07 方向 3：右式并入 FileWriter DROP 增量——内核已计数
// （total_records）但 FileWriter 写路径丢弃（openFailed/formatEmpty/...
// /dropRollback）的记录，右式原四元组无法吸收，dev 恒向正偏（丢记录
// 正向偏差被容差静默吞掉或误报 CONSERVATION BROKEN）；并入后右式闭合。
bool shouldAlarmConservation(uint64_t totalDelta, uint64_t overrunDelta, uint64_t droppedDelta,
                             uint64_t writerDropDelta, uint64_t jsonlDelta, uint64_t invalidDelta,
                             int64_t tolerance);

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor
