// ============================================================
// batch_parser.h — daemon 主循环可测函数抽取
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：把 lechao_lcview.cpp main() 内循环的批次解析/schema 重试
//   逻辑抽为独立函数并编入 filegroup，使 UT 可直接覆盖
//   （此前 main 逻辑不在任何测试编译内，等于覆盖率分母隐性抬高）。
//   架构演进：daemon 直读内核后取消 HAL 绑定（waitForHal/
//   rebindAfterError/HalBinder 移除），仅保留解析与 schema 重试。
// ============================================================

#pragma once

#include <chrono>
#include <cstdint>
#include <string>
#include <vector>

#include "SchemaParser.h"
#include "FileWriter.h"

namespace vendor {
namespace lechao {
namespace lcview {

// 批次解析结果统计
struct BatchParseResult {
    unsigned validCnt = 0;
    unsigned invalidCnt = 0;
};

// 解析一个批次（4B 长度前缀 + 二进制记录序列），写盘并返回统计。
// LCV-06：接口收为 data/len 指针对——调用方（主循环 flushSegment）
// 不再构造 std::vector 中转（最大 64KB 拷贝/批），零拷贝透传读缓冲。
// 覆盖：坏长度/过小记录/validate 失败写 invalid、合法记录写盘、
//       尾部残留写 invalid（CXX-004 故障可见性）
BatchParseResult parseBatch(SchemaParser& schema, FileWriter& writer,
                            const uint8_t* data, size_t len);

// schema 加载重试（vendor 分区可能晚于 daemon 就绪）：
// 最多 maxRetries 次、每次间隔 interval，eventCount>0 即成功
bool loadSchemaWithRetry(SchemaParser& schema, const std::string& path,
                         int maxRetries,
                         std::chrono::milliseconds interval =
                             std::chrono::milliseconds(500));

// flush 触发判定（hal_test readerLoop 的满/超时/滞留窗语义并入 daemon）：
//   缓冲非空 且（缓冲满 || epoll 超时 || 500ms 滞留窗到期）即应 flush 攒包。
// 参数：buffered 当前缓冲字节数，timedOut 本轮 epoll 超时（无新数据），
//   ageExpired 滞留窗到期，bufferCapacity 缓冲上限。
// 覆盖（原 LcViewReaderLoopTest 分支 5 语义）：
//   TimeoutNoData 空缓冲不 flush；BufferFull 满缓冲 flush；NormalRead 数据后
//   timeout/age flush；timeout 空批不 flush（空批不该产生写放大）
bool shouldFlushBatch(size_t buffered, bool timedOut, bool ageExpired,
                      size_t bufferCapacity);

// 预防性 flush 判定（丢数据收口 方向 1）：缓冲剩余空间不足以容纳内核
// 单次读最小单位（minRead）时，须先强制 flush 清空缓冲——否则
// waitAndRead 以 cap-offset 调内核 read，内核因剩余容量过小返回
// -EINVAL，主循环退出交 init 重启形成退出环（数据持续丢失）。
// 参数：buffered 当前缓冲字节数，bufferCapacity 缓冲上限，
//   minRead 内核单次读最小容量。buffered>=bufferCapacity（满/越界）同样
//   须 flush（剩余为 0）。
bool shouldPreventiveFlush(size_t buffered, size_t bufferCapacity,
                           size_t minRead);

// 退出前强制 flush 判定（丢数据收口 方向 2）：缓冲尚有未落盘数据即须
// 强制 flush（n=0 触发 timedOut 落盘）——致命读错误退出与优雅退出两条
// 出口都不得丢弃已收数据。
bool shouldFlushOnExit(size_t buffered);

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor
