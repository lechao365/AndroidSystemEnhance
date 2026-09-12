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
// 本批（runMainLoop 提到可测边界）：主循环三段（readOnce/emitHeartbeat/
// flushSegment）与 runMainLoop 整体抽到 main_loop.cpp 并注入 DeviceReader
// 抽象接口（可测缝），main 仅保留初始化/退出骨架与信号注册；行为不变。
// ============================================================

#include "main_loop.h"
#include "SchemaParser.h"
#include "FileWriter.h"
#include "batch_parser.h"
#include "DeviceReader.h"
#include "../include/lcview_events.h"
#include <log/log.h>
#include <thread>
#include "lechao_log.h"

using namespace vendor::lechao::lcview;

int main(int argc, char* argv[])
{
    // 注册信号处理器，支持 init 发送 SIGTERM 停止服务
    // （gRunning 与 signalHandler 收口在 main_loop，此处仅注册）
    installSignalHandlers();

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
