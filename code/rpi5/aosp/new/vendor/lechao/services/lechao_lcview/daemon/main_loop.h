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
    return (static_cast<int64_t>(ringSizeBytes) + kConserveUserBufBytes)
           / kConserveMinRecordEstimate;
}

// 守恒基线（方向 6）：从 emitHeartbeat 的 static 局部移到 runMainLoop 局部
// 并经引用传入——static 局部在多实例/多测试连续运行间串扰，且无法按心跳
// 推进；改为调用方持有后每心跳推进基线（相邻心跳窗口比较，uint32
// total_records 永不回绕误报）。ioctl 失败（ioctlErr 增量，任一查询返 0
// 伪装真实 0）时跳过守恒校验且不推进数值基线，防失败值失真。
struct ConserveBaseline {
    bool initialized = false;
    uint32_t total = 0;             // 内核 total_records 基线
    int64_t overrun = 0;            // 用户态 overrunAccum 基线
    uint64_t dropped = 0;           // 内核 dropped_cnt 基线（方向 7）
    uint64_t persistedValid = 0;    // FileWriter 合法落盘基线（方向 5）
    uint64_t persistedInvalid = 0;  // FileWriter 非法落盘基线（方向 5）
    uint64_t ioctlErr = 0;          // DeviceReader ioctl 失败计数基线
};

// 守恒告警判定（纯函数，方向 3/7）：内核 total_records 增量应等于
// overrun + dropped + jsonl + invalid 增量之和（每条记录要么被驱逐
// overrun、要么 ENOSPC 丢弃 dropped、要么合法落盘 jsonl、要么非法落盘
// invalid）。dev = totalΔ - (overrunΔ + droppedΔ + jsonlΔ + invalidΔ)：
// 正值表示产生未落盘（在途积压/丢记录），负值表示落盘超过产生（重复
// 落盘/计数漂移）；|dev| 超容差即判告警。抽成纯函数便于单测覆盖阈值与
// 正负两向。
bool shouldAlarmConservation(uint64_t totalDelta, uint64_t overrunDelta,
                             uint64_t droppedDelta, uint64_t jsonlDelta,
                             uint64_t invalidDelta, int64_t tolerance);

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor
