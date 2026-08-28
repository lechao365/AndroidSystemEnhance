// ============================================================
// FileWriter.h — 日志文件写入器头文件
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：声明 FileWriter 类，负责将校验通过的事件日志
//   以 JSONL 格式写入持久化存储。支持：
//   1) 按事件类型拆分到不同文件
//   2) 按日期和文件大小自动轮转
//   3) 总容量上限的自动过期删除
//   4) 异常记录写入独立的 invalid_records.log
// ============================================================

#pragma once

#include "SchemaParser.h"
#include <string>
#include <fstream>
#include <unordered_map>
#include <cstdint>
#include <ctime>

// 文件写入配置结构体
// logDir — 日志根目录
// maxFileSizeMb — 单个文件大小上限（超过触发轮转）
// maxTotalSizeMb — 所有日志文件总大小上限（超过删除最旧文件）
struct FileWriterConfig {
    std::string logDir = "/data/vendor/lechao_lcview/logs";
    size_t maxFileSizeMb = 50;
    size_t maxTotalSizeMb = 500;
};

// FileWriter 类：将事件日志写入结构化 JSONL 文件
// 文件命名规则：{event_id}_{event_name}_{YYYYMMDD}_p{seq}.jsonl
// 例如：4_usb_transport_start_20260606_p0.jsonl
// seq 是当日文件的轮转序号，从 0 开始递增
// 为什么用 JSONL 而非纯文本或 protobuf：
//   JSONL 每行一条独立 JSON 对象，兼容通用日志分析工具，
//   也便于按行传输和 grep 搜索。
class FileWriter {
public:
    explicit FileWriter(const FileWriterConfig& cfg);
    ~FileWriter();

    // 写入一条合法的事件记录到对应的 event 文件
    void writeRecord(const EventSchema& schema,
                     const struct lcview_record_hdr* hdr,
                     const uint8_t* fields,
                     size_t fieldsLen);

    // 写入一条非法记录到 invalid_records.log（用于诊断）
    void writeInvalid(const uint8_t* data, size_t len, const std::string& reason);

    // 检查所有已打开文件是否需要轮转（日期变更或大小超限）
    void checkRotation();
    // 清理超出总容量上限的最旧日志文件（LRU 策略）
    void enforceRetention();

private:
    // 根据 event schema、日期和轮转序号生成文件名
    std::string makeFilename(const EventSchema& schema, const std::string& date, int seq);
    // 获取当前日期的 YYYYMMDD 字符串
    std::string makeDateStr();
    // 打开/创建某个 event_id 对应的日志文件
    void openFile(uint16_t eventId, const EventSchema& schema);
    // 关闭某个 event_id 对应的日志文件
    void closeFile(uint16_t eventId);
    // 将二进制记录格式化为一行 JSONL 字符串
    std::string formatJsonLine(const EventSchema& schema,
                               const struct lcview_record_hdr* hdr,
                               const uint8_t* fields,
                               size_t fieldsLen);

    FileWriterConfig mCfg;

    // 每个打开的文件对应一个 FileState，管理文件流和轮转状态
    struct FileState {
        uint16_t eventId;
        std::string eventName;
        std::string currentFilename;  // 当前正在写入的文件路径
        std::string currentDate;      // 当前文件的日期（YYYYMMDD）
        int seq = 0;                  // 当日文件序列号
        size_t currentSize = 0;       // 当前文件已写入字节数
        std::ofstream stream;         // 文件输出流
    };

    // 按 event_id 索引所有已打开的文件状态
    std::unordered_map<uint16_t, FileState> mFiles;
    // 非法记录日志文件流
    std::ofstream mInvalidStream;
    // 非法记录日志文件路径
    std::string mInvalidFilename;
};
