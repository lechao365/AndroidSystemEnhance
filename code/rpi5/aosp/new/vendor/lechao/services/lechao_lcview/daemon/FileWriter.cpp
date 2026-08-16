// ============================================================
// FileWriter.cpp — 日志文件写入器实现
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：实现 FileWriter 类的所有方法，将解析后的二进制
//   日志记录以 JSONL 格式写入磁盘。负责：
//   1) 创建日志目录结构（递归 mkdir）
//   2) 按 event_id 拆分文件（事件隔离，便于独立分析）
//   3) 每次写入后立即 flush 保证数据不丢
//   4) 跨天轮转和文件大小触发轮转
//   5) 总容量 LRU 淘汰策略
// ============================================================

#define LOG_TAG "lechao_lcview"

#include "FileWriter.h"
#include "../include/lcview_events.h"
#include <sys/stat.h>
#include <sys/types.h>
#include <dirent.h>
#include <unistd.h>
#include <algorithm>
#include <sstream>
#include <iomanip>
#include <cerrno>
#include <cstring>
#include <log/log.h>
#include <android-base/file.h>
#include "lechao_log.h"

// 递归创建目录：
// Android 上 mkdir 不自动创建父目录，所以需要逐级创建。
// 为什么不用 mkdir -p 或 shell 命令：因为这是 C++ 用户态程序，
// 不能依赖 shell 环境，且递归实现更可靠。
static bool mkdirRecursive(const std::string& path, mode_t mode)
{
    if (path.empty() || path == "/") return true;

    std::string parent = path;
    while (!parent.empty() && parent.back() == '/')
        parent.pop_back();

    size_t pos = parent.rfind('/');
    if (pos != std::string::npos && pos > 0) {
        std::string parentDir = parent.substr(0, pos);
        struct stat st;
        if (stat(parentDir.c_str(), &st) != 0) {
            if (!mkdirRecursive(parentDir, mode))
                return false;
        }
    }

    // EEXIST 表示目录已存在，不算错误
    if (mkdir(path.c_str(), mode) == 0 || errno == EEXIST)
        return true;

    ALOGE("FileWriter: mkdir %s failed: %s", path.c_str(), strerror(errno));
    return false;
}

// 构造函数：创建日志目录、uploaded 子目录、打开 invalid 日志文件
// uploaded 目录为将来"已上传标记"预留，当前未使用
FileWriter::FileWriter(const FileWriterConfig& cfg) : mCfg(cfg)
{
    // 确保日志根目录存在
    mkdirRecursive(mCfg.logDir, 0755);
    // 创建 uploaded 子目录（标记已上传到远程存储的文件）
    std::string uploadedDir = mCfg.logDir + "/uploaded";
    mkdirRecursive(uploadedDir, 0755);

    // 追加模式打开 invalid_records.log，不覆盖已有内容
    mInvalidFilename = mCfg.logDir + "/invalid_records.log";
    mInvalidStream.open(mInvalidFilename, std::ios::app);
    if (!mInvalidStream.is_open())
        ALOGE("FileWriter: cannot open %s", mInvalidFilename.c_str());
}

// 析构函数：关闭所有打开的文件流
FileWriter::~FileWriter()
{
    // 遍历关闭所有事件文件流
    for (auto& [id, fs] : mFiles)
        if (fs.stream.is_open())
            fs.stream.close();
    // 关闭 invalid 日志文件流
    if (mInvalidStream.is_open())
        mInvalidStream.close();
}

// 获取当前本地时间的 YYYYMMDD 格式字符串
// 用于文件名中的日期标签和轮转判断
std::string FileWriter::makeDateStr()
{
    time_t now = time(nullptr);
    struct tm tm_buf;
    localtime_r(&now, &tm_buf);
    char buf[16];
    strftime(buf, sizeof(buf), "%Y%m%d", &tm_buf);
    return std::string(buf);
}

// 生成规范化的日志文件路径
// 格式：{logDir}/{event_id}_{event_name}_{YYYYMMDD}_p{seq}.jsonl
// 示例：/data/vendor/lechao_lcview/logs/4_usb_transport_start_20260606_p0.jsonl
std::string FileWriter::makeFilename(const EventSchema& schema,
                                      const std::string& date)
{
    auto it = mFiles.find(schema.id);
    int seq = (it != mFiles.end()) ? it->second.seq : 0;

    std::ostringstream oss;
    oss << mCfg.logDir << "/" << schema.id << "_" << schema.name
        << "_" << date << "_p" << seq << ".jsonl";
    return oss.str();
}

// 打开或创建某个 event_id 对应的日志文件
// 如果文件已存在且有内容，恢复 currentSize 以便后续轮转判断
void FileWriter::openFile(uint16_t eventId, const EventSchema& schema)
{
    // 如果该 event 已有打开的文件，先关闭
    auto it = mFiles.find(eventId);
    if (it != mFiles.end() && it->second.stream.is_open())
        it->second.stream.close();

    std::string date = makeDateStr();
    FileState fs;
    fs.eventId = eventId;
    fs.eventName = schema.name;
    fs.currentFilename = makeFilename(schema, date);
    fs.currentDate = date;
    fs.seq = 0;
    fs.currentSize = 0;
    // 以追加模式打开，文件不存在时自动创建
    fs.stream.open(fs.currentFilename, std::ios::app);
    if (!fs.stream.is_open()) {
        ALOGE("FileWriter: cannot open %s", fs.currentFilename.c_str());
        return;
    }

    LC_ALOGD("FileWriter: opened file: %s", fs.currentFilename.c_str());

    mFiles[eventId] = std::move(fs);
}

// 关闭某个 event_id 对应的日志文件并从 mFiles 中移除
void FileWriter::closeFile(uint16_t eventId)
{
    auto it = mFiles.find(eventId);
    if (it != mFiles.end()) {
        if (it->second.stream.is_open())
            it->second.stream.close();
        mFiles.erase(it);
    }
}

// JSON 字符串转义：对 " 和 \ 字符进行转义处理
// 为什么手动实现而非用 JSON 库：formatJsonLine 需要最高性能，
// 减少 JSON 库的字符串处理开销
static void jsonEscapeString(std::ostringstream& oss, const std::string& s)
{
    oss << "\"";
    for (char c : s) {
        if (c == '"' || c == '\\')
            oss << '\\';
        oss << c;
    }
    oss << "\"";
}

// 将二进制记录格式化为 JSONL 一行
// 输出格式：{"ts":<timestamp>,"id":<event_id>,"level":<level>,"f":[<field_values>]}
// f 数组中的元素顺序与 schema 中的 fields 顺序一致，
// 但不包含字段名（仅值），以节省磁盘空间
// v3.4 优化: 使用 thread_local ostringstream 复用，避免每次调用
// 创建/销毁 ostringstream 的堆分配开销。
// std::str("") + clear() 重置流状态，不释放底层 buffer。
// NOTE: thread_local 在此场景下等价于 static，因为 writeRecord()
// 仅在 daemon 主线程中被调用（单线程模型）。若将来多线程写入，
// thread_local 可保证每个线程独立，无需额外同步。
std::string FileWriter::formatJsonLine(const EventSchema& schema,
                                        const struct lcview_record_hdr* hdr,
                                        const uint8_t* fields,
                                        size_t fieldsLen)
{
    thread_local std::ostringstream oss;
    oss.str("");   // 清空内容
    oss.clear();   // 重置错误状态

    oss << "{\"ts\":" << hdr->timestamp_ns
        << ",\"id\":" << hdr->event_id
        << ",\"level\":" << (int)hdr->level
        << ",\"f\":[";

    const uint8_t* ptr = fields;
    const uint8_t* const end = fields + fieldsLen;

#define LCVIEW_NEED(n) do { \
    if ((size_t)(end - ptr) < (size_t)(n)) { \
        ALOGE("FileWriter: formatJsonLine: out-of-bounds at field %zu (need %zu, remain %zd)", \
              i, (size_t)(n), (ssize_t)(end - ptr)); \
        return std::string(); \
    } \
} while (0)

    for (size_t i = 0; i < schema.fields.size(); i++) {
        if (i > 0) oss << ",";

        LCVIEW_NEED(1);
        uint8_t type = *ptr;
        ptr++;

        switch (type) {
        case LCVIEW_TYPE_INT32: {
            LCVIEW_NEED(4);
            int32_t val;
            memcpy(&val, ptr, 4);
            oss << val;
            ptr += 4;
            break;
        }
        case LCVIEW_TYPE_INT64: {
            LCVIEW_NEED(8);
            int64_t val;
            memcpy(&val, ptr, 8);
            oss << val;
            ptr += 8;
            break;
        }
        case LCVIEW_TYPE_FLOAT: {
            LCVIEW_NEED(4);
            float val;
            memcpy(&val, ptr, 4);
            oss << val;
            ptr += 4;
            break;
        }
        case LCVIEW_TYPE_STRING: {
            LCVIEW_NEED(2);
            uint16_t len;
            memcpy(&len, ptr, 2);
            ptr += 2;
            LCVIEW_NEED(len);
            std::string s(reinterpret_cast<const char*>(ptr), len);
            ptr += len;
            jsonEscapeString(oss, s);
            break;
        }
        case LCVIEW_TYPE_BINARY: {
            LCVIEW_NEED(2);
            uint16_t len;
            memcpy(&len, ptr, 2);
            ptr += 2;
            LCVIEW_NEED(len);
            oss << "\"";
            for (uint16_t j = 0; j < len; j++)
                oss << std::hex << std::setfill('0')
                    << std::setw(2) << (unsigned)ptr[j];
            ptr += len;
            oss << "\"" << std::dec;
            break;
        }
        default:
            oss << "null";
            break;
        }
    }
#undef LCVIEW_NEED
    oss << "]}\n";
    return oss.str();
}

// 写入一条合法记录到对应事件的文件中
// 如果文件尚未打开，自动创建；写入后立即 flush
void FileWriter::writeRecord(const EventSchema& schema,
                              const struct lcview_record_hdr* hdr,
                              const uint8_t* fields,
                              size_t fieldsLen)
{
    auto it = mFiles.find(schema.id);
    // 如果对应 event_id 的文件还未打开，自动 openFile
    if (it == mFiles.end() || !it->second.stream.is_open()) {
        openFile(schema.id, schema);
        it = mFiles.find(schema.id);
        if (it == mFiles.end() || !it->second.stream.is_open()) {
            ALOGE("FileWriter: writeRecord: cannot open file for event %u, DROPPING", schema.id);
            return;
        }
    }

    std::string line = formatJsonLine(schema, hdr, fields, fieldsLen);

    if (line.empty()) {
        ALOGE("FileWriter: writeRecord: formatJsonLine returned empty for event %u, DROPPING", schema.id);
        return;
    }

    LC_ALOGD("lechao_lcview: write %u %s", schema.id, line.c_str());

    it->second.stream << line;
    if (it->second.stream.fail()) {
        ALOGE("FileWriter: write failed for event %u", schema.id);
        return;
    }

    // 每次写入后立即 flush，防止进程崩溃导致数据丢失
    it->second.stream.flush();
    it->second.currentSize += line.size();
}

// 写入非法记录到 invalid_records.log
// 记录原因和大小，供事后调试分析
void FileWriter::writeInvalid(const uint8_t* data, size_t len,
                               const std::string& reason)
{
    if (!mInvalidStream.is_open()) {
        ALOGE("FileWriter: writeInvalid: stream not open, DROPPING reason=%s", reason.c_str());
        return;
    }
    mInvalidStream << "{\"reason\":\"" << reason
                   << "\",\"size\":" << len << "}\n";
    mInvalidStream.flush();
}

// 文件轮转检查：
// 对每个已打开的文件，如果日期已变更（跨天）或
// 当前文件大小超过 maxFileSizeMb，则关闭当前文件，
// 按规则生成新的文件名后打开新文件。
// 同一天内的轮转 seq 递增；跨天重置 seq 为 0。
void FileWriter::checkRotation()
{
    std::string today = makeDateStr();

    for (auto& [id, fs] : mFiles) {
        bool needRotate = false;

        // 日期变更 → 必须轮转到新文件
        if (fs.currentDate != today)
            needRotate = true;

        // 文件超过大小限制 → 轮转
        if (fs.currentSize >= mCfg.maxFileSizeMb * 1024 * 1024)
            needRotate = true;

        if (needRotate) {
            ALOGI("FileWriter: rotating: %s (size=%zu, date=%s)", fs.currentFilename.c_str(), fs.currentSize, fs.currentDate.c_str());
            // 关闭当前文件
            if (fs.stream.is_open())
                fs.stream.close();

            // 同一天内 seq 递增，跨天重置
            if (fs.currentDate == today)
                fs.seq++;
            else
                fs.seq = 0;

            // 用 stub schema 生成新文件名（只需 id 和 name）
            EventSchema stubSchema;
            stubSchema.id = fs.eventId;
            stubSchema.name = fs.eventName;

            fs.currentDate = today;
            fs.currentFilename = makeFilename(stubSchema, today);
            fs.currentSize = 0;
            fs.stream.open(fs.currentFilename, std::ios::app);
            if (!fs.stream.is_open())
                ALOGE("FileWriter: cannot open %s", fs.currentFilename.c_str());
        }
    }
}

// 容量限制清理：删除最旧的日志文件直到总大小 <= maxTotalSizeMb
// 策略：扫描日志目录下的所有 .jsonl 和 .log 文件，
// 按 mtime 从小到大（最旧优先）排序，逐个删除直到满足容量限制。
// 为什么选择 LRU（最旧）而非 LRF（最大）：
//   日志文件按日期命名，最旧的文件分析价值最低。
void FileWriter::enforceRetention()
{
    size_t maxBytes = mCfg.maxTotalSizeMb * 1024 * 1024;

    struct LogFile {
        std::string path;
        time_t mtime;
        off_t size;
    };
    std::vector<LogFile> files;

    DIR* dir = opendir(mCfg.logDir.c_str());
    if (!dir) {
        ALOGE("FileWriter: enforceRetention: opendir(%s) failed: %s", mCfg.logDir.c_str(), strerror(errno));
        return;
    }

    // 遍历日志目录，收集所有 .jsonl 和 .log 文件
    struct dirent* entry;
    while ((entry = readdir(dir)) != nullptr) {
        std::string name(entry->d_name);
        if (name.find(".jsonl") == std::string::npos &&
            name.find(".log") == std::string::npos)
            continue;

        std::string fullPath = mCfg.logDir + "/" + name;
        struct stat st;
        if (stat(fullPath.c_str(), &st) == 0)
            files.push_back({fullPath, st.st_mtime, st.st_size});
    }
    closedir(dir);

    // 按修改时间升序排列（最旧的在前）
    std::sort(files.begin(), files.end(),
              [](const LogFile& a, const LogFile& b) {
                  return a.mtime < b.mtime;
              });

    // 计算当前总大小
    size_t totalSize = 0;
    for (const auto& f : files)
        totalSize += f.size;

    // 从最旧文件开始删除，直到总大小 <= maxBytes
    // 跳过当前正在写入的文件，避免删除后 writeRecord 写入失败
    for (const auto& f : files) {
        if (totalSize <= maxBytes) break;
        bool isOpen = false;
        for (const auto& [id, fs] : mFiles) {
            if (fs.currentFilename == f.path) {
                isOpen = true;
                break;
            }
        }
        if (isOpen)
            continue;
        if (unlink(f.path.c_str()) == 0) {
            totalSize -= f.size;
            ALOGI("FileWriter: deleted old log %s", f.path.c_str());
        } else {
            ALOGE("FileWriter: enforceRetention: unlink(%s) failed: %s", f.path.c_str(), strerror(errno));
        }
    }
}
