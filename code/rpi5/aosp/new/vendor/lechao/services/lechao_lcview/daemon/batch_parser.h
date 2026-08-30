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
// 覆盖：坏长度/过小记录/validate 失败写 invalid、合法记录写盘、
//       尾部残留写 invalid（CXX-004 故障可见性）
BatchParseResult parseBatch(SchemaParser& schema, FileWriter& writer,
                            const std::vector<uint8_t>& batch);

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

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor
