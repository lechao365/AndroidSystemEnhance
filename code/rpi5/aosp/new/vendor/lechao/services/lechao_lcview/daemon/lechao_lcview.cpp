// ============================================================
// lechao_lcview.cpp — LcView 守护进程主入口
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：作为事件日志系统的中间层，负责：
//   1) 通过 AIDL 连接到 HAL 服务，以 Binder IPC 方式拉取数据
//   2) 调用 SchemaParser 对二进制日志记录进行校验和解析
//   3) 调用 FileWriter 将解析后的日志写入 JSONL 文件
//   4) 处理日志文件轮转和过期删除（磁盘空间管理）
//
// 为什么设计为独立 daemon 而非在 HAL 内直接写文件：
//   分离 HAL 和 daemon 使内核驱动读取（HAL）和日志持久化（daemon）
//   解耦。HAL 崩溃不影响已写入的日志，daemon 重启自动重连。
//   同时 daemon 运行在 system 域，比 vendor HAL 有更多文件系统权限。
// ============================================================

#define LOG_TAG "lechao_lcview"

#include "SchemaParser.h"
#include "FileWriter.h"
#include "batch_parser.h"
#include "../include/lcview_events.h"
#include <aidl/vendor/lechao/lcview/ILcView.h>
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <log/log.h>
#include <csignal>
#include <atomic>
#include <thread>
#include <chrono>
#include "lechao_log.h"

using aidl::vendor::lechao::lcview::ILcView;
using namespace vendor::lechao::lcview;

// 全局运行标志，被信号处理器置 false 以触发优雅退出
static std::atomic<bool> gRunning(true);

// 构建标识：每次上板验证批次唯一，启动/心跳日志携带，
// 供板端 grep 精确确认"新二进制已在运行"（防假验证）
#define LCVIEW_BUILD_TAG "LCVIEW-VERIFY-20260826-01"

// 信号处理函数：收到 SIGINT/SIGTERM 时设置退出标志，
// 使主循环自然结束，确保当前批次日志不丢失
static void signalHandler(int) {
    gRunning = false;
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
    //   因为 schema 文件所在的 vendor 分区可能在 HAL 之后才挂载完成。
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

    // 设置为 0 表示不创建额外的 Binder 线程，
    // daemon 的主循环本身就在主线程中，不需要额外的 Binder 处理线程
    ABinderProcess_setThreadPoolMaxThreadCount(0);

    // bind_hal: 通过 AServiceManager 查找并绑定 HAL 服务的 lambda
    // 返回 nullptr 表示 HAL 尚未就绪
    auto bind_hal = [](const std::string& service) -> std::shared_ptr<ILcView> {
        AIBinder* raw = AServiceManager_checkService(service.c_str());
        if (!raw) {
            ALOGW("lechao_lcview: HAL not ready, retrying...");
            return nullptr;
        }
        ::ndk::SpAIBinder binder(raw);
        auto hal = ILcView::fromBinder(binder);
        if (!hal)
            ALOGE("lechao_lcview: cannot cast to ILcView");
        return hal;
    };

    const std::string serviceName = "vendor.lechao.lcview.ILcView/default";
    // 等待 HAL 服务就绪，最多重试 1200 次（100ms 间隔，约 2 分钟）。
    // 为什么需要等待：HAL 与 daemon 的启动顺序无保证，daemon 必须优雅重试。
    // （等待循环抽入 batch_parser 可测函数）
    auto hal = waitForHal(bind_hal, serviceName, 1200);
    if (!hal) {
        ALOGE("lechao_lcview: cannot connect to HAL after retries");
        return 1;
    }

    ALOGI("lechao_lcview: connected to HAL, entering main loop");

    // 主循环：不断从 HAL 拉取批次数据、解析、写入文件
    int loopCount = 0;
    // JSONL 落盘累计条数（守恒校验基准：内核 total_records ≈ overrun + 落盘条数）
    long long jsonlRecords = 0;
    while (gRunning) {
        loopCount++;
        if (loopCount % 30 == 0) {
            // CXX-004: 周期查询内核 ring buffer 溢出计数并打进心跳，
            // 日志丢失（内核写满覆盖）对上层可见，不再静默
            int32_t overrun = 0;
            int64_t totalRecords = 0;
            hal->getOverrunCount(&overrun);
            hal->getTotalRecords(&totalRecords);
            ALOGI("lechao_lcview: heartbeat, loop=%d, kernel overrun=%d, "
                  "total_records=%lld, jsonl_records=%lld",
                  loopCount, overrun, static_cast<long long>(totalRecords),
                  jsonlRecords);
        }

        std::vector<uint8_t> batch;
        LC_ALOGD("getBatch: calling...");
        ndk::ScopedAStatus status = hal->getBatch(&batch);

        if (::lechao::debugVerbose()) {
            ALOGI("lechao_lcview: tick loop=%d batch_sz=%zu", loopCount, batch.size());
        }

        // Binder 通信失败处理：
        // 可能是 HAL 重启或服务管理器暂时不可用，
        // 等待 1 秒后尝试重新绑定（重绑抽入 batch_parser 可测函数）
        if (!status.isOk()) {
            ALOGE("lechao_lcview: getBatch() binder error: %s",
                  status.getDescription().c_str());
            std::this_thread::sleep_for(std::chrono::seconds(1));
            hal = rebindAfterError(bind_hal, serviceName);
            if (!hal) {
                /* CXX-004: return 0 伪装正常完成是故障静默——
                 * rc 非 oneshot，exit(1) 让 init 自动重启拉起采集链路 */
                ALOGE("lechao_lcview: lost HAL connection, exiting for init restart");
                exit(1);
            }
            continue;
        }

        // v3.4 优化: getBatch() 已改为阻塞式（HAL 内部 condition_variable 等待），
        // 空批次对应 HAL 超时（1s 无数据），直接落入轮转检查后继续循环。
        if (!batch.empty()) {
            ALOGI("lechao_lcview: batch received: %zu bytes (build=%s)",
                  batch.size(), LCVIEW_BUILD_TAG);

            // 解析批次数据：批次 = 4 字节长度前缀 + 二进制记录
            // （解析逻辑抽入 batch_parser 可测函数）
            BatchParseResult parsed = parseBatch(schema, writer, batch);
            jsonlRecords += parsed.validCnt;
            ALOGI("lechao_lcview: batch parsed: %u valid, %u invalid (build=%s)",
                  parsed.validCnt, parsed.invalidCnt, LCVIEW_BUILD_TAG);
        } else {
            LC_ALOGD("getBatch: empty batch");
        }

        // 每轮统一执行轮转与容量检查（含空批次轮次）：
        // 跨天后的首个空批次也要触发按日轮转，避免新数据落进旧日期文件
        writer.checkRotation();
        writer.enforceRetention();
    }

    ALOGI("lechao_lcview: exiting");
    return 0;
}
