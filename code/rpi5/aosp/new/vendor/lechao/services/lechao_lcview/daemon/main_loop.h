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

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor
