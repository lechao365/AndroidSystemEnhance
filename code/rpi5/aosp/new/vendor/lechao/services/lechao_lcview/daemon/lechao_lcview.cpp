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

// 全局运行标志，被信号处理器置 false 以触发优雅退出
static std::atomic<bool> gRunning(true);

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

    ALOGI("lechao_lcview: starting");

    SchemaParser schema;
    // 默认 schema 路径：vendor 分区的配置文件
    std::string schemaPath = "/vendor/etc/lcview_events.json";
    // 支持命令行参数覆盖 schema 路径（方便测试）
    if (argc > 1)
        schemaPath = argv[1];

    // v3.4 优化: schema 加载失败时重试 30 次（最多 15 秒），
    //   因为 schema 文件所在的 vendor 分区可能在 HAL 之后才挂载完成。
    //   替代旧版本直接 FATAL 退出的策略，提高启动可靠性。
    int schemaRetry = 0;
    while (!schema.loadFromFile(schemaPath) && schemaRetry < 30) {
        ALOGW("lechao_lcview: schema not ready, retrying... (%d/30)", ++schemaRetry);
        std::this_thread::sleep_for(std::chrono::milliseconds(500));
    }
    if (!schema.eventCount()) {
        ALOGE("lechao_lcview: failed to load schema from %s after %d retries",
              schemaPath.c_str(), schemaRetry);
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
    int retryCount = 0;
    std::shared_ptr<ILcView> hal;
    // 等待 HAL 服务就绪，最多重试 100 次（约 10 秒）。
    // 为什么需要等待：HAL 和 daemon 的 init .rc 都有 oneshot 属性，
    // 无法保证 HAL 先于 daemon 启动，所以 daemon 必须优雅重试。
    while (!(hal = bind_hal(serviceName)) && retryCount < 1200) {
        std::this_thread::sleep_for(std::chrono::milliseconds(100));
        retryCount++;
    }
    if (!hal) {
        ALOGE("lechao_lcview: cannot connect to HAL after %d retries",
              retryCount);
        return 1;
    }

    ALOGI("lechao_lcview: connected to HAL, entering main loop");

    // 主循环：不断从 HAL 拉取批次数据、解析、写入文件
    int loopCount = 0;
    while (gRunning) {
        loopCount++;
        if (loopCount % 30 == 0)
            ALOGI("lechao_lcview: heartbeat, loop=%d", loopCount);

        std::vector<uint8_t> batch;
        LC_ALOGD("getBatch: calling...");
        ndk::ScopedAStatus status = hal->getBatch(&batch);

        if (::lechao::debugVerbose()) {
            ALOGI("lechao_lcview: tick loop=%d batch_sz=%zu", loopCount, batch.size());
        }

        // Binder 通信失败处理：
        // 可能是 HAL 重启或服务管理器暂时不可用，
        // 等待 1 秒后尝试重新绑定
        if (!status.isOk()) {
            ALOGE("lechao_lcview: getBatch() binder error: %s",
                  status.getDescription().c_str());
            std::this_thread::sleep_for(std::chrono::seconds(1));
            hal = bind_hal(serviceName);
            if (!hal) {
                ALOGE("lechao_lcview: lost HAL connection");
                break;
            }
            continue;
        }

        // v3.4 优化: getBatch() 已改为阻塞式（HAL 内部 condition_variable 等待），
        // 空批次对应 HAL 超时（1s 无数据），直接继续循环无需额外 sleep。
        if (batch.empty()) {
            LC_ALOGD("getBatch: empty, continue");
            continue;
        }

        ALOGI("lechao_lcview: batch received: %zu bytes", batch.size());

        // 解析批次数据：批次 = 4 字节长度前缀 + 二进制记录
        // 批量数据格式：[len(4B) | record_data(len-4B)] 的序列
        size_t offset = 0;
        while (offset + 4 <= batch.size()) {
            // 读取本条记录的总长度（含自身 4 字节）
            uint32_t total_len;
            memcpy(&total_len, batch.data() + offset, 4);

            // 长度校验：最小长度和边界检查
            if (total_len < 4 || offset + total_len > batch.size()) {
                writer.writeInvalid(batch.data() + offset,
                                      batch.size() - offset, "bad length");
                ALOGE("lechao_lcview: parse: bad length at offset=%zu, total_len=%u", offset, total_len);
                break;
            }

            const uint8_t* recordStart = batch.data() + offset + 4;
            size_t recordDataLen = total_len - 4;

            // 记录必须至少包含固定头的大小
            if (recordDataLen < sizeof(struct lcview_record_hdr)) {
                ALOGE("lechao_lcview: parse: record too small (%zu < %zu)", recordDataLen, sizeof(struct lcview_record_hdr));
                writer.writeInvalid(recordStart, recordDataLen,
                                      "record too small");
                offset += total_len;
                continue;
            }

            // 使用 SchemaParser 校验记录的魔法数字、event_id、字段数、
            // 字段类型和总长度是否完整合法
            std::string errMsg;
            if (schema.validate(recordStart, recordDataLen, errMsg)) {
                const struct lcview_record_hdr* hdr =
                    reinterpret_cast<const struct lcview_record_hdr*>(recordStart);
                const uint8_t* fields = recordStart + sizeof(struct lcview_record_hdr);
                const EventSchema* es = schema.find(hdr->event_id);
                if (es) {
                    LC_ALOGD("parse: event_id=%u valid, writing", hdr->event_id);
                    size_t fieldsLen = recordDataLen - sizeof(struct lcview_record_hdr);
                    writer.writeRecord(*es, hdr, fields, fieldsLen);
                }
            } else {
                writer.writeInvalid(recordStart, recordDataLen, errMsg);
                ALOGE("lechao_lcview: parse: validate failed: %s", errMsg.c_str());
            }

            offset += total_len;
        }

        // 检查是否需要文件轮转（按日期或文件大小）
        writer.checkRotation();
        // 清理超出总容量限制的最旧日志文件
        writer.enforceRetention();
    }

    ALOGI("lechao_lcview: exiting");
    return 0;
}
