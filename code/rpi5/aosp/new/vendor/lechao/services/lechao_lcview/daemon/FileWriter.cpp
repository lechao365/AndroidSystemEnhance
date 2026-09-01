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
#include "record_codec.h"
#include "../include/lcview_events.h"
#include <sys/stat.h>
#include <dirent.h>
#include <unistd.h>
#include <fcntl.h>
#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <sstream>
#include <iomanip>
#include <cerrno>
#include <cstring>
#include <log/log.h>
#include <android-base/file.h>
#include "lechao_log.h"

// record_codec 解码器符号（定义于 vendor::lechao::lcview 命名空间，
// 本文件类定义不在该命名空间内，逐符号引入避免全量 using 的歧义风险）
using vendor::lechao::lcview::DecodedField;
using vendor::lechao::lcview::FieldDecodeResult;
using vendor::lechao::lcview::decodeRecordField;

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
// seq 由调用方显式传入（openFile/checkRotation 各自维护），
// 避免"文件名用旧 seq、FileState 却存 0"的不一致（CXX-002）
std::string FileWriter::makeFilename(const EventSchema& schema,
                                      const std::string& date, int seq)
{
    std::ostringstream oss;
    oss << mCfg.logDir << "/" << schema.id << "_" << schema.name
        << "_" << date << "_p" << seq << ".jsonl";
    return oss.str();
}

// 扫描日志目录：该 event+date 已存在的最大轮转序号 +1（重启后 seq 续接）。
// CXX-002 语义延续：daemon 重启后 mFiles 为空，seq 归 0 会重复写 _p0 追加
// 旧文件、轮转文件名混乱（恢复用例断言"轮转 seq 递增"的基础）。
// 匹配 {id}_{name}_{date}_p<seq>.jsonl，取 max(seq)+1；无匹配返 0。
// NOTE: readdir 返回的 d_name 为纯文件名（不含目录路径），故 prefix 只做
// 文件名前缀匹配（曾误拼 mCfg.logDir + "/" 前缀，compare 恒不匹配、
// 恒返 0——真机 daemon 重启后重复写 _p0 的根因，C++ 单测未在设备跑没暴露）
int FileWriter::nextSeqFor(const EventSchema& schema, const std::string& date)
{
    std::string prefix = std::to_string(schema.id) + "_" + schema.name
                         + "_" + date + "_p";
    int maxSeq = -1;
    DIR* dir = opendir(mCfg.logDir.c_str());
    if (!dir)
        return 0;
    struct dirent* entry;
    while ((entry = readdir(dir)) != nullptr) {
        std::string name(entry->d_name);
        if (name.compare(0, prefix.size(), prefix) != 0)
            continue;
        // 尾部须为 "<seq>.jsonl"
        const std::string suffix = ".jsonl";
        if (name.size() <= prefix.size() + suffix.size())
            continue;
        if (name.compare(name.size() - suffix.size(), suffix.size(), suffix) != 0)
            continue;
        std::string num = name.substr(prefix.size(),
                                      name.size() - prefix.size() - suffix.size());
        char* end = nullptr;
        long v = strtol(num.c_str(), &end, 10);
        if (end && *end == '\0' && v >= 0)
            maxSeq = std::max(maxSeq, static_cast<int>(v));
    }
    closedir(dir);
    return maxSeq + 1;
}

// 打开或创建某个 event_id 对应的日志文件
// CXX-002: 文件已存在时必须 fstat 恢复 currentSize（从持久层恢复状态），
// 否则 daemon 重启后追加模式打开旧文件，已有内容不计入大小，
// 单文件可超限近一倍，轮转约束失效
void FileWriter::openFile(uint16_t eventId, const EventSchema& schema)
{
    std::string date = makeDateStr();
    // 如果该 event 已有打开的文件，先关闭；seq 延续旧值保持文件名连续；
    // 重启（mFiles 空）则从目录扫描续接 seq（nextSeqFor）
    int seq = 0;
    auto it = mFiles.find(eventId);
    if (it != mFiles.end()) {
        if (it->second.stream.is_open())
            it->second.stream.close();
        seq = it->second.seq;
    } else {
        seq = nextSeqFor(schema, date);
    }

    FileState fs;
    fs.eventId = eventId;
    fs.eventName = schema.name;
    fs.currentFilename = makeFilename(schema, date, seq);
    fs.currentDate = date;
    fs.seq = seq;
    fs.currentSize = 0;
    // 以追加模式打开，文件不存在时自动创建
    fs.stream.open(fs.currentFilename, std::ios::app);
    if (!fs.stream.is_open()) {
        ALOGE("FileWriter: cannot open %s", fs.currentFilename.c_str());
        return;
    }

    // 追加模式下文件可能已有内容：stat 恢复 currentSize，兑现轮转约束
    struct stat st;
    if (stat(fs.currentFilename.c_str(), &st) == 0)
        fs.currentSize = static_cast<size_t>(st.st_size);

    LC_ALOGD("FileWriter: opened file: %s (restored size=%zu)",
             fs.currentFilename.c_str(), fs.currentSize);

    mFiles[eventId] = std::move(fs);
}

// JSON 字符串转义（formatJsonLine 与 writeInvalid 共用同一函数）：
// 对 " \ 及控制字符做 JSON 合法转义，防止输出行裂行/非法 JSONL。
//   - "  \  \b \f \n \r \t 具名转义；
//   - 其余 < 0x20 控制字符按 \u00XX 转义（按 unsigned char 判读，
//     避免有符号 char 下非 ASCII 高位字节误判为负值进入 \u 分支）；
//   - 原实现仅转引号与反斜杠，USB 描述符含换行时输出行即裂行（P0）。
// 为什么手动实现而非用 JSON 库：formatJsonLine 需要最高性能，
// 减少 JSON 库的字符串处理开销
static void jsonEscapeString(std::ostringstream& oss, const std::string& s)
{
    oss << "\"";
    for (unsigned char c : s) {
        switch (c) {
        case '"':  oss << "\\\""; break;
        case '\\': oss << "\\\\"; break;
        case '\b': oss << "\\b"; break;
        case '\f': oss << "\\f"; break;
        case '\n': oss << "\\n"; break;
        case '\r': oss << "\\r"; break;
        case '\t': oss << "\\t"; break;
        default:
            if (c < 0x20) {
                oss << "\\u00" << std::hex << std::setw(2)
                    << std::setfill('0') << static_cast<unsigned>(c)
                    << std::dec;
            } else {
                oss << c;
            }
            break;
        }
    }
    oss << "\"";
}

// 将单个解码字段值追加到 JSON 输出流（拆分自 formatJsonLine，行为不变）
// INT32/INT64/FLOAT 数值直出、STRING 转义、BINARY hex 输出、未知类型 null
static void appendFieldValue(std::ostringstream& oss, const DecodedField& df)
{
    switch (df.type) {
    case LCVIEW_TYPE_INT32: {
        int32_t val;
        memcpy(&val, df.value, 4);
        oss << val;
        break;
    }
    case LCVIEW_TYPE_INT64: {
        int64_t val;
        memcpy(&val, df.value, 8);
        oss << val;
        break;
    }
    case LCVIEW_TYPE_FLOAT: {
        float val;
        memcpy(&val, df.value, 4);
        oss << val;
        break;
    }
    case LCVIEW_TYPE_STRING: {
        std::string s(reinterpret_cast<const char*>(df.value), df.valueLen);
        jsonEscapeString(oss, s);
        break;
    }
    case LCVIEW_TYPE_BINARY: {
        oss << "\"";
        for (size_t j = 0; j < df.valueLen; j++)
            oss << std::hex << std::setfill('0')
                << std::setw(2) << (unsigned)df.value[j];
        oss << "\"" << std::dec;
        break;
    }
    default:
        oss << "null";
        break;
    }
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

    // 字段推进统一走 record_codec::decodeRecordField（与
    // SchemaParser::validate 共用同一 TLV 解码器；原此处手写
    // LCVIEW_NEED 宏 + switch 的越界/推进逻辑已收敛到解码器）
    for (size_t i = 0; i < schema.fields.size(); i++) {
        if (i > 0) oss << ",";

        if (ptr >= end) {
            ALOGE("FileWriter: formatJsonLine: out-of-bounds at field %zu (need 1, remain %zd)",
                  i, (ssize_t)(end - ptr));
            mDrops.formatOob++;
            return std::string();
        }

        DecodedField df;
        FieldDecodeResult r = decodeRecordField(&ptr, end, &df);
        if (r == FieldDecodeResult::kTruncated) {
            // 越界：与 LCVIEW_NEED 失败同语义，记 formatOob 丢弃
            ALOGE("FileWriter: formatJsonLine: truncated at field %zu",
                  i);
            mDrops.formatOob++;
            return std::string();
        }
        // kUnknown：未知类型输出 null 继续（与历史 default 语义一致，
        // 解码器已推进 1 字节 type）；kOk 正常解码，两者 df.type 均已填充
        appendFieldValue(oss, df);
    }
    oss << "]}\n";
    return oss.str();
}

// 写路径耗时累计（微秒；供心跳输出平均微秒/条）
void FileWriter::recordWriteTiming(std::chrono::steady_clock::time_point start)
{
    mTimings.writeCount++;
    mTimings.writeTotalUs += static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - start).count());
}

// 回退文件到指定偏移：flush 失败后首写可能部分落盘，重试前须截断掉残留的
// 半行，否则磁盘留"半行+整行"坏行（app 重开并重写整行只追加不清残留）。
// 仅尽力而为——文件不可打开/非普通文件（如 /dev/full）时静默忽略，成败由
// 后续重写决定（CXX-004 故障可恢复：不留坏行）
static void rollbackFileTo(const std::string& path, size_t offset)
{
    int fd = open(path.c_str(), O_WRONLY | O_CLOEXEC);
    if (fd < 0)
        return;
    if (ftruncate(fd, static_cast<off_t>(offset)) != 0)
        ALOGE("FileWriter: rollback truncate %s to %zu failed: %s",
              path.c_str(), offset, strerror(errno));
    close(fd);
}

// 写盘 + flush + 失败恢复（拆分自 writeRecord，行为不变）。
// 返回是否成功：失败路径已累计 DROP 计数（reopenFailed/retryFailed）
// 与写耗时，调用方须直接返回
bool FileWriter::writeLineFlush(FileState& fs, const std::string& line)
{
    auto tWriteStart = std::chrono::steady_clock::now();
    // 写前记录偏移（fs.currentSize 为上次成功后落盘字节数，即本行写入起点）：
    // 首次 flush 部分落盘后失败时，重试前须先回退到该偏移再重写，否则磁盘
    // 留半行加整行的坏行（app 重开并重写整行只追加不清残留）
    const size_t writeBase = fs.currentSize;
    // 写 + 立即 flush：flush 失败才算真失败——ofstream 缓冲未满时 << 只在
    // 内存缓冲不落盘、不设 failbit，只查 << 会漏掉磁盘写失败（RetryWriteFails
    // 设备真跑暴露：/dev/full 写入 60B 缓冲未满 fail()==0，flush 才置位）
    fs.stream << line;
    fs.stream.flush();
    if (fs.stream.fail()) {
        ALOGE("FileWriter: write failed for event %u, attempting recovery",
              fs.eventId);
        /* CXX-002: failbit 粘滞不清除会让该事件流从此永久失败，
         * 后续每条都 DROP（磁盘满恢复后也无法自愈的错误吞噬）。
         * 恢复路径：清错误状态 → 回退首写残留 → 重开流 → 重试一次 */
        fs.stream.clear();
        fs.stream.close();
        rollbackFileTo(fs.currentFilename, writeBase);
        fs.stream.open(fs.currentFilename, std::ios::app);
        if (!fs.stream.is_open()) {
            ALOGE("FileWriter: recovery reopen failed for event %u, DROPPING",
                  fs.eventId);
            mDrops.reopenFailed++;
            recordWriteTiming(tWriteStart);
            return false;
        }
        fs.stream << line;
        fs.stream.flush();
        if (fs.stream.fail()) {
            ALOGE("FileWriter: retry write failed for event %u, DROPPING",
                  fs.eventId);
            mDrops.retryFailed++;
            fs.stream.clear();
            recordWriteTiming(tWriteStart);
            return false;
        }
        ALOGI("FileWriter: recovered stream for event %u", fs.eventId);
    }
    recordWriteTiming(tWriteStart);
    return true;
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
            mDrops.openFailed++;
            return;
        }
    }

    // 写路径耗时统计（方向 3）：formatJsonLine 与写盘分开累计，
    // 心跳输出平均微秒/条，作为微优化可判定指标
    auto tFormatStart = std::chrono::steady_clock::now();
    std::string line = formatJsonLine(schema, hdr, fields, fieldsLen);
    mTimings.formatCount++;
    mTimings.formatTotalUs += static_cast<uint64_t>(
        std::chrono::duration_cast<std::chrono::microseconds>(
            std::chrono::steady_clock::now() - tFormatStart).count());

    if (line.empty()) {
        ALOGE("FileWriter: writeRecord: formatJsonLine returned empty for event %u, DROPPING", schema.id);
        mDrops.formatEmpty++;
        return;
    }

    LC_ALOGD("lechao_lcview: write %u %s", schema.id, line.c_str());

    // 写盘 + flush + 失败恢复（CXX-002，含写耗时累计）
    if (!writeLineFlush(it->second, line))
        return;

    // 方向 4：写入计数累计，供 enforceRetention 按写入阈值降频扫描
    mWritesSinceRetention++;

    it->second.currentSize += line.size();
}

// 写入非法记录到 invalid_records.log
// 记录原因、大小和原始字节（hex 截断），供事后离线重解析定位协议缺陷
// CXX-003: reason 含 " / \ 及控制字符时必须转义（转义并入 jsonEscapeString，
// 与 formatJsonLine 同规则），否则输出行非合法 JSONL / 裂行
void FileWriter::writeInvalid(const uint8_t* data, size_t len,
                               const std::string& reason)
{
    if (!mInvalidStream.is_open()) {
        ALOGE("FileWriter: writeInvalid: stream not open, DROPPING reason=%s", reason.c_str());
        mDrops.invalidNotOpen++;
        return;
    }
    // 原始数据 hex 落盘上限：足够定位协议问题，又不至于在损坏风暴下写爆磁盘
    static constexpr size_t kMaxDumpBytes = 256;

    // 整行先拼入局部流（reason 转义复用 jsonEscapeString），再一次性写盘
    std::ostringstream line;
    line << "{\"reason\":";
    jsonEscapeString(line, reason);
    line << ",\"size\":" << len << ",\"data\":\"";
    size_t dump = len < kMaxDumpBytes ? len : kMaxDumpBytes;
    for (size_t i = 0; i < dump; i++)
        line << std::hex << std::setfill('0') << std::setw(2)
             << (unsigned)data[i];
    line << std::dec << "\"}\n";
    const std::string payload = line.str();

    // 写 + flush：fail 判定必须看 flush——ofstream 缓冲未满时 << 只在内存
    // 缓冲不落盘、不设 failbit（与 writeLineFlush 同语义，CXX-002）
    mInvalidStream << payload;
    mInvalidStream.flush();
    if (mInvalidStream.fail()) {
        /* CXX-002: failbit 粘滞不清除会让 invalid 流从此永久失败——
         * 首写失败后余生空转，mode_invalid 反判绿（坏记录静默丢失）。
         * 恢复路径：clear 清粘滞 → 重开流 → 重试一次，仍失败计
         * invalidWriteFailed（进心跳 dropped 求和与 drop_invalidwrite 分项） */
        ALOGE("FileWriter: writeInvalid: write failed, attempting recovery");
        mInvalidStream.clear();
        mInvalidStream.close();
        mInvalidStream.open(mInvalidFilename, std::ios::app);
        if (!mInvalidStream.is_open()) {
            ALOGE("FileWriter: writeInvalid: recovery reopen failed, DROPPING reason=%s",
                  reason.c_str());
            mDrops.invalidWriteFailed++;
            return;
        }
        mInvalidStream << payload;
        mInvalidStream.flush();
        if (mInvalidStream.fail()) {
            ALOGE("FileWriter: writeInvalid: retry write failed, DROPPING reason=%s",
                  reason.c_str());
            mDrops.invalidWriteFailed++;
            mInvalidStream.clear();
            return;
        }
        ALOGI("FileWriter: writeInvalid: recovered invalid stream");
    }
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
            fs.currentFilename = makeFilename(stubSchema, today, fs.seq);
            fs.currentSize = 0;
            fs.stream.open(fs.currentFilename, std::ios::app);
            if (!fs.stream.is_open())
                ALOGE("FileWriter: cannot open %s", fs.currentFilename.c_str());
            else {
                // 追加模式打开旧轮转文件时同样恢复大小（与 openFile 一致）
                struct stat st;
                if (stat(fs.currentFilename.c_str(), &st) == 0)
                    fs.currentSize = static_cast<size_t>(st.st_size);
            }
        }
    }
}

// 扫描段：遍历日志目录，收集全部 .jsonl/.log 文件的 (路径, mtime, size)
// （拆分自 enforceRetention，行为不变）
std::vector<FileWriter::LogFile> FileWriter::scanLogFiles()
{
    std::vector<LogFile> files;

    DIR* dir = opendir(mCfg.logDir.c_str());
    if (!dir) {
        ALOGE("FileWriter: enforceRetention: opendir(%s) failed: %s", mCfg.logDir.c_str(), strerror(errno));
        return files;
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
            files.push_back({fullPath, st.st_mtime,
                             static_cast<std::int64_t>(st.st_size)});
    }
    closedir(dir);
    return files;
}

// 淘汰段：按 mtime 升序（最旧优先）删除文件直至总大小 <= maxTotalSizeMb；
// 跳过当前正在写入/被 invalid 流持有的文件（拆分自 enforceRetention，
// 行为不变）
void FileWriter::evictOldFiles(std::vector<LogFile>& files)
{
    size_t maxBytes = mCfg.maxTotalSizeMb * 1024 * 1024;

    // 按修改时间升序排列（最旧的在前）
    std::sort(files.begin(), files.end(),
              [](const LogFile& a, const LogFile& b) {
                  return a.mtime < b.mtime;
              });

    // 计算当前总大小
    size_t totalSize = 0;
    for (const auto& f : files)
        totalSize += static_cast<size_t>(f.size);

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
        // invalid_records.log 正被 mInvalidStream 持有：unlink 后 fd 会继续写
        // 已删除 inode，空间泄漏直至进程退出（CXX-002 资源生命周期）
        if (f.path == mInvalidFilename)
            isOpen = true;
        if (isOpen)
            continue;
        if (unlink(f.path.c_str()) == 0) {
            totalSize -= static_cast<size_t>(f.size);
            ALOGI("FileWriter: deleted old log %s", f.path.c_str());
        } else {
            ALOGE("FileWriter: enforceRetention: unlink(%s) failed: %s", f.path.c_str(), strerror(errno));
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
    // 方向 4 降频：每轮主循环全目录 opendir+stat 成本高（空批也扫），
    // 改为按写入计数触发——未达阈值直接跳过，避免无数据时反复扫描。
    // retentionScanEveryWrites == 0 表示关闭降频（每次调用都扫描，
    // 单测显式调用 enforceRetention 断言扫描行为的场景使用）
    if (mCfg.retentionScanEveryWrites > 0 &&
        mWritesSinceRetention < mCfg.retentionScanEveryWrites) {
        return;
    }
    mWritesSinceRetention = 0;

    // 扫描 → 淘汰 两段（行为不变）
    std::vector<LogFile> files = scanLogFiles();
    evictOldFiles(files);
}
