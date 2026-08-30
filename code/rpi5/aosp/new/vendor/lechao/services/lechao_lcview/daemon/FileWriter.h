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
#include <vector>
#include <chrono>
#include <cstdint>
#include <ctime>

// 文件写入配置结构体
// logDir — 日志根目录
// maxFileSizeMb — 单个文件大小上限（超过触发轮转）
// maxTotalSizeMb — 所有日志文件总大小上限（超过删除最旧文件）
// retentionScanEveryWrites — enforceRetention 降频阈值（方向 4）：
//   每累计 N 次写入才真正扫描一次日志目录（opendir+stat 有成本，
//   原每轮主循环全目录扫描，空批轮次也扫——改为按写入计数触发）。
//   0 表示关闭降频（每次调用都扫描，单测显式调用场景使用）
struct FileWriterConfig {
    std::string logDir = "/data/vendor/lechao_lcview/logs";
    size_t maxFileSizeMb = 50;
    size_t maxTotalSizeMb = 500;
    size_t retentionScanEveryWrites = 256;
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

    // DROP 分类计数（CXX-004 语义延续：丢记录不再静默，daemon 心跳可见，
    // conserve 判红后可据此定位丢在哪一条路径）
    struct DropCounters {
        uint64_t openFailed = 0;     // writeRecord 文件打开失败
        uint64_t formatEmpty = 0;    // formatJsonLine 返回空（字段不匹配等）
        uint64_t formatOob = 0;      // formatJsonLine 字段越界（数据不足）
        uint64_t reopenFailed = 0;   // 写失败恢复重开失败
        uint64_t retryFailed = 0;    // 恢复后重试二次写失败
        uint64_t invalidNotOpen = 0; // invalid 事件流未打开
    };
    // 返回当前累计的 DROP 计数（心跳输出用）
    const DropCounters& dropCounters() const { return mDrops; }

    // 写路径耗时统计（方向 3：drain 被攒包策略钉死，对写路径成本不敏感，
    // 心跳输出平均微秒/条作为微优化可判定指标）
    struct WriteTimings {
        uint64_t formatCount = 0;   // formatJsonLine 调用次数
        uint64_t formatTotalUs = 0; // formatJsonLine 累计耗时（微秒）
        uint64_t writeCount = 0;    // writeRecord 落盘次数（含恢复重试）
        uint64_t writeTotalUs = 0;  // writeRecord 累计耗时（微秒，不含 format）
    };
    const WriteTimings& writeTimings() const { return mTimings; }

private:
    // 根据 event schema、日期和轮转序号生成文件名
    std::string makeFilename(const EventSchema& schema, const std::string& date, int seq);
    // 获取当前日期的 YYYYMMDD 字符串
    std::string makeDateStr();
    // 扫描日志目录：该 event+date 已存在的最大轮转序号 +1（重启后 seq 续接）
    int nextSeqFor(const EventSchema& schema, const std::string& date);
    // 打开/创建某个 event_id 对应的日志文件
    void openFile(uint16_t eventId, const EventSchema& schema);
    // 将二进制记录格式化为一行 JSONL 字符串
    std::string formatJsonLine(const EventSchema& schema,
                               const struct lcview_record_hdr* hdr,
                               const uint8_t* fields,
                               size_t fieldsLen);
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
    // 写盘 + flush + 失败恢复（拆分自 writeRecord，行为不变）；
    // 返回是否成功写入（失败路径已累计 DROP 计数与写耗时）。
    // 失败恢复前先回退到写前偏移（截断首写可能部分落盘的残留半行），
    // 再重开重写——保证恢复后磁盘只有合法整行（坏行归零）
    bool writeLineFlush(FileState& fs, const std::string& line);
    // 写路径耗时累计（微秒；供心跳输出平均微秒/条）
    void recordWriteTiming(std::chrono::steady_clock::time_point start);

    // 日志目录扫描结果：路径 + mtime + size（enforceRetention 淘汰用）
    struct LogFile {
        std::string path;
        time_t mtime;
        std::int64_t size;
    };
    // 扫描日志目录，收集全部 .jsonl/.log 文件（拆分自
    // enforceRetention 的扫描段，行为不变）
    std::vector<LogFile> scanLogFiles();
    // 按 mtime 升序淘汰最旧文件直至总大小 <= maxTotalSizeMb（拆分自
    // enforceRetention 的淘汰段，行为不变）
    void evictOldFiles(std::vector<LogFile>& files);

    FileWriterConfig mCfg;

    // 按 event_id 索引所有已打开的文件状态
    std::unordered_map<uint16_t, FileState> mFiles;
    // 非法记录日志文件流
    std::ofstream mInvalidStream;
    // 非法记录日志文件路径
    std::string mInvalidFilename;
    // DROP 分类累计计数（六条 DROP 路径，进 daemon 心跳）
    DropCounters mDrops;
    // 写路径耗时统计（方向 3，见 WriteTimings）
    WriteTimings mTimings;
    // 距上次 enforceRetention 实际扫描的写入次数（方向 4 降频）
    size_t mWritesSinceRetention = 0;
};
