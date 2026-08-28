// ============================================================
// batch_parser.h — daemon 主循环可测函数抽取
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：把 lechao_lcview.cpp main() 内循环的批次解析/schema 重试/
//   HAL 等待逻辑抽为独立函数并编入 filegroup，使 UT 可直接覆盖
//   （此前 main 逻辑不在任何测试编译内，等于覆盖率分母隐性抬高）。
// ============================================================

#pragma once

#include <chrono>
#include <cstdint>
#include <functional>
#include <memory>
#include <string>
#include <vector>

#include "SchemaParser.h"
#include "FileWriter.h"
#include <aidl/vendor/lechao/lcview/ILcView.h>

using aidl::vendor::lechao::lcview::ILcView;

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

// schema 加载重试（vendor 分区可能晚于 HAL 就绪）：
// 最多 maxRetries 次、每次间隔 interval，eventCount>0 即成功
bool loadSchemaWithRetry(SchemaParser& schema, const std::string& path,
                         int maxRetries,
                         std::chrono::milliseconds interval =
                             std::chrono::milliseconds(500));

// HAL 绑定器类型（可注入 lambda 供 UT 模拟）
using HalBinder =
    std::function<std::shared_ptr<ILcView>(const std::string&)>;

// 等待 HAL 就绪（最多 maxRetries 次、间隔 interval），成功返回非空
std::shared_ptr<ILcView> waitForHal(const HalBinder& bind,
                                    const std::string& serviceName,
                                    int maxRetries,
                                    std::chrono::milliseconds interval =
                                        std::chrono::milliseconds(100));

// getBatch 失败后的重绑处理：重绑成功返回新 HAL，失败返回 nullptr
// （调用方对 nullptr 须 exit(1) 交 init 重启，禁止静默降级）
std::shared_ptr<ILcView> rebindAfterError(const HalBinder& bind,
                                          const std::string& serviceName);

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor