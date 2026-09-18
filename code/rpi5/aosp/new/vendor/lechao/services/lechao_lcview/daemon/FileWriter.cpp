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
#include <cmath>
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
    // 方向 4：容量阈值下限校验——maxTotalSizeMb=0 属非法配置（总容量上限
    // 为 0 时每次淘汰扫描都会尝试删除全部文件，容量管理失效且会误删有效
    // 日志），告警并钳制到安全默认（结构体缺省 500MB），防配置错误静默
    // 生效；仅在构造期钳制到成员副本，不改写调用方传入的 cfg 本体
    if (mCfg.maxTotalSizeMb == 0) {
        ALOGE("FileWriter: maxTotalSizeMb=0 invalid, clamping to safe default "
              "500MB");
        mCfg.maxTotalSizeMb = 500;
    }
    // 方向 4（补齐批次 7 未覆盖项）：maxFileSizeMb=0 属非法配置（单文件
    // 上限为 0 时 checkRotation 每轮都把 currentSize>=0 判真而无限轮转，
    // 每写一条就新建文件），告警并钳制到安全默认（结构体缺省 50MB）
    if (mCfg.maxFileSizeMb == 0) {
        ALOGE("FileWriter: maxFileSizeMb=0 invalid, clamping to safe default "
              "50MB");
        mCfg.maxFileSizeMb = 50;
    }
    // 方向 4（补齐批次 7 未覆盖项）：maxInvalidFileSizeMb=0 属非法配置
    // （invalid 轮转阈值为 0 时每条 invalid 写入都触发 rotateInvalid，
    // 坏数据风暴下无界轮转文件刷盘），告警并钳制到安全默认（结构体
    // 缺省 10MB）
    if (mCfg.maxInvalidFileSizeMb == 0) {
        ALOGE("FileWriter: maxInvalidFileSizeMb=0 invalid, clamping to safe "
              "default 10MB");
        mCfg.maxInvalidFileSizeMb = 10;
    }
    // LCV-13：计数器置满——启动后首次 enforceRetention 即全量扫描，
    // 清理上次运行遗留的超限数据（否则静默期内永不清理）
    mWritesSinceRetention = cfg.retentionScanEveryWrites;
    // 方向 1：时间兜底起点置 now——启动首扫由计数满触发（LCV-13），
    // 时间兜底从首次扫描时刻重新起算（构造点置 now 防双重触发）
    mLastRetentionScanAt = std::chrono::steady_clock::now();
    // LCV-15：目录创建失败必须可见（仅靠后续 openFailed 间接可见时，
    // 首条心跳前无直接信号）；权限 0750 与 rc 文件策略一致
    if (!mkdirRecursive(mCfg.logDir, 0750))
        ALOGE("FileWriter: cannot create log dir %s", mCfg.logDir.c_str());
    // 创建 uploaded 子目录（标记已上传到远程存储的文件）
    std::string uploadedDir = mCfg.logDir + "/uploaded";
    if (!mkdirRecursive(uploadedDir, 0750))
        ALOGE("FileWriter: cannot create uploaded dir %s", uploadedDir.c_str());

    // 追加模式打开 invalid_records.log，不覆盖已有内容；统一走
    // openInvalidStream（方向 4 半行修复 + CXX-002 大小恢复一体）
    mInvalidFilename = mCfg.logDir + "/invalid_records.log";
    openInvalidStream();
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
    // 方向 6：localtime_r 返回 nullptr 即失败（无效 time_t / 时区数据缺失），
    // 静默使用未初始化 tm_buf 是 UB（CXX-001 输入防御）——失败告警并回退
    // 固定纪元日期，防文件命名与跨天轮转判定基于脏数据
    if (localtime_r(&now, &tm_buf) == nullptr) {
        ALOGE("FileWriter: makeDateStr: localtime_r failed: %s, fallback to "
              "epoch", strerror(errno));
        return "19700101";
    }
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
    if (!dir) {
        // 方向 5：opendir 失败须可见——静默 return 0 会让 seq 归 0 续接，
        // daemon 重启/跨天后重复写 _p0 追加旧文件（轮转约束失效）且无信号
        ALOGE("FileWriter: nextSeqFor: opendir(%s) failed: %s (seq not continued, "
              "may rewrite _p0)", mCfg.logDir.c_str(), strerror(errno));
        return 0;
    }
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
        // 方向 1：重启（mFiles 空）打开最高 seq 现有文件并修复残留半行，
        // 而不是新建更高 seq 文件——上次异常退出留在最高 seq 文件的半行
        // 须截断（下方 truncateToLastNewline），否则与后续追加行粘连成
        // 非法 JSONL；同时避免生成空 _p{max+1}。nextSeqFor 返回 max+1：
        // >0 即有现有文件（最高 seq = next-1，继续追加）；==0 无文件
        // （新建 _p0，追加模式打开同名已有文件同样不丢数据）
        const int next = nextSeqFor(schema, date);
        seq = (next > 0) ? (next - 1) : 0;
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

    // 方向 4：打开后先修复上次异常退出的残留半行（末字节非换行截断至
    // 最后换行），再恢复大小——否则半行与后续行粘连成非法 JSONL。
    // 半行修复优先于 stat 恢复：修复后字节数才反映真实完整行边界
    truncateToLastNewline(fs.currentFilename);

    // 追加模式下文件可能已有内容：stat 恢复 currentSize，兑现轮转约束。
    // LCV-11：stat 失败必须可见（静默保持 0 会让该文件轮转约束失效，
    // 可超限近一倍且无任何信号）。方向 4：半行已截断，stat 结果反映
    // 截断后字节数
    struct stat st;
    if (stat(fs.currentFilename.c_str(), &st) == 0)
        fs.currentSize = static_cast<size_t>(st.st_size);
    else
        ALOGE("FileWriter: openFile: stat %s failed: %s (rotation constraint degraded)",
              fs.currentFilename.c_str(), strerror(errno));

    LC_ALOGD("FileWriter: opened file: %s (restored size=%zu)",
             fs.currentFilename.c_str(), fs.currentSize);

    mFiles[eventId] = std::move(fs);
}

// 校验从 s[i] 开始的 UTF-8 多字节序列长度（LCV-05）：
// 返回合法序列字节数（2-4），非法/截断返回 0。
// 首字节 0x80-0xC1（连续字节/overlong 编码）与 0xF5-0xFF（超码位）非法
static size_t utf8SeqLen(const std::string& s, size_t i)
{
    auto isCont = [&](size_t k) {
        return k < s.size() &&
               (static_cast<unsigned char>(s[k]) & 0xC0) == 0x80;
    };
    const unsigned char c = static_cast<unsigned char>(s[i]);
    if (c >= 0xC2 && c <= 0xDF)                       // 2 字节
        return isCont(i + 1) ? 2 : 0;
    if (c == 0xE0) {                                  // 3 字节（排除 overlong）
        return (i + 2 < s.size() &&
                static_cast<unsigned char>(s[i + 1]) >= 0xA0 &&
                static_cast<unsigned char>(s[i + 1]) <= 0xBF &&
                isCont(i + 2)) ? 3 : 0;
    }
    if ((c >= 0xE1 && c <= 0xEC) || (c >= 0xEE && c <= 0xEF))
        return (isCont(i + 1) && isCont(i + 2)) ? 3 : 0;
    if (c == 0xED) {                                  // 3 字节（排除 surrogate）
        return (i + 2 < s.size() &&
                static_cast<unsigned char>(s[i + 1]) >= 0x80 &&
                static_cast<unsigned char>(s[i + 1]) <= 0x9F &&
                isCont(i + 2)) ? 3 : 0;
    }
    if (c == 0xF0) {                                  // 4 字节（排除 overlong）
        return (i + 3 < s.size() &&
                static_cast<unsigned char>(s[i + 1]) >= 0x90 &&
                static_cast<unsigned char>(s[i + 1]) <= 0xBF &&
                isCont(i + 2) && isCont(i + 3)) ? 4 : 0;
    }
    if (c >= 0xF1 && c <= 0xF3)
        return (isCont(i + 1) && isCont(i + 2) && isCont(i + 3)) ? 4 : 0;
    if (c == 0xF4) {                                  // 4 字节（上限 U+10FFFF）
        return (i + 3 < s.size() &&
                static_cast<unsigned char>(s[i + 1]) >= 0x80 &&
                static_cast<unsigned char>(s[i + 1]) <= 0x8F &&
                isCont(i + 2) && isCont(i + 3)) ? 4 : 0;
    }
    return 0;  // 0x80-0xC1 / 0xF5-0xFF 非法首字节
}

// JSON 字符串转义（formatJsonLine 与 writeInvalid 共用同一函数）：
// 对 " \ 及控制字符做 JSON 合法转义，防止输出行裂行/非法 JSONL。
//   - "  \  \b \f \n \r \t 具名转义；
//   - 其余 < 0x20 控制字符按 \u00XX 转义（按 unsigned char 判读，
//     避免有符号 char 下非 ASCII 高位字节误判为负值进入 \u 分支）；
//   - >= 0x80 字节做 UTF-8 合法性校验（LCV-05）：合法多字节序列原样
//     直传（JSON 字符串为 Unicode，UTF-8 合法），非法序列逐字节按
//     \u00XX 转义——USB 描述符字符串来自设备固件可含任意字节，
//     严格 JSON 解析器（json.loads 等）对无效 UTF-8 行直接抛异常，
//     一行坏数据会污染整文件分析；
//   - 原实现仅转义具名控制字符与引号反斜杠，USB 描述符含换行时
//     输出行即裂行（P0）。
// 为什么手动实现而非用 JSON 库：formatJsonLine 需要最高性能，
// 减少 JSON 库的字符串处理开销
static void jsonEscapeString(std::ostringstream& oss, const std::string& s)
{
    oss << "\"";
    for (size_t i = 0; i < s.size(); i++) {
        unsigned char c = static_cast<unsigned char>(s[i]);
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
            } else if (c >= 0x80) {
                // 合法 UTF-8 序列整体直传，非法字节降级 \u00XX
                size_t seq = utf8SeqLen(s, i);
                if (seq > 0) {
                    oss.write(s.data() + i, static_cast<std::streamsize>(seq));
                    i += seq - 1;
                } else {
                    oss << "\\u00" << std::hex << std::setw(2)
                        << std::setfill('0') << static_cast<unsigned>(c)
                        << std::dec;
                }
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
        // LCV-04：NaN/Inf（除零等场景）的默认输出 "nan"/"inf" 非合法
        // JSON 数值——严格解析器对整行抛异常。降级 null 保整行可解析，
        // 数值丢失由消费端 null 判读
        if (std::isnan(val) || std::isinf(val))
            oss << "null";
        else
            // LCV-20：默认 6 位有效数字截断 float 精度（约 7.2 位），
            // 提升至 9 位保真输出（对整型/字符串输出无影响）
            oss << std::setprecision(9) << val;
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
            // 方向 4：DROP 计数点收敛到 writeRecord（formatEmpty），本函数
            // 只返回空串，不自行计数——避免同一丢弃在 format 与 write 双计
            return std::string();
        }

        DecodedField df;
        FieldDecodeResult r = decodeRecordField(&ptr, end, &df);
        if (r == FieldDecodeResult::kTruncated) {
            // 越界：与 LCVIEW_NEED 失败同语义，返回空串交 writeRecord 计数
            ALOGE("FileWriter: formatJsonLine: truncated at field %zu",
                  i);
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
// 返回回退后文件真实大小（fstat）供调用方校准内存计数（方向 2）；失败
// 返回 SIZE_MAX 并计 dropRollback（方向 3：回滚失败也进心跳 dropped 求和，
// 不能只 ALOGE 静默——回滚失败 = 半行残留风险，须可见）
size_t FileWriter::rollbackFileTo(const std::string& path, size_t offset)
{
    int fd = open(path.c_str(), O_WRONLY | O_CLOEXEC);
    if (fd < 0) {
        mDrops.dropRollback++;
        ALOGE("FileWriter: rollback open %s failed: %s",
              path.c_str(), strerror(errno));
        return SIZE_MAX;
    }
    if (ftruncate(fd, static_cast<off_t>(offset)) != 0) {
        mDrops.dropRollback++;
        ALOGE("FileWriter: rollback truncate %s to %zu failed: %s",
              path.c_str(), offset, strerror(errno));
    }
    close(fd);
    struct stat st;
    if (stat(path.c_str(), &st) != 0) {
        mDrops.dropRollback++;
        ALOGE("FileWriter: rollback stat %s failed: %s",
              path.c_str(), strerror(errno));
        return SIZE_MAX;
    }
    return static_cast<size_t>(st.st_size);
}

// 按路径 fdatasync（方向 5）：open + fdatasync + close。
// ofstream flush 只把用户态缓冲刷到内核页缓存，断电/崩溃时页缓存丢失；
// fdatasync 才把文件数据真正落盘。心跳 30s 同锚调用 + 轮转前刷旧文件，
// 缩小断电数据丢失窗口。尽力而为，失败 ALOGE 可见（不阻断写路径）。
void FileWriter::fsyncFileByPath(const std::string& path)
{
    int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC);
    if (fd < 0) {
        ALOGE("FileWriter: fsync open %s failed: %s",
              path.c_str(), strerror(errno));
        return;
    }
    if (fdatasync(fd) != 0)
        ALOGE("FileWriter: fdatasync %s failed: %s",
              path.c_str(), strerror(errno));
    close(fd);
}

// 打开文件后修复残留半行（方向 4）：异常退出（kill -9/断电）可能留下
// 末尾无换行的半行 JSON，与后续追加的行粘连成非法 JSONL（一次粘连污染
// 一条记录）。打开/轮转/重启恢复时调用：末字节非换行则截断至最后一个
// 换行，只保留完整行。返回修复后文件字节数。
// 实现：读文件内容找最后一个 '\n'，存在则截断到其后 1 字节（保留换行
// 符本身），不存在（纯半行/空文件）则截断为 0。文件不可开/非普通文件
// 返回 0（无法修复，保持现状由调用方按 size=0 处理）。
size_t FileWriter::truncateToLastNewline(const std::string& path)
{
    int fd = open(path.c_str(), O_RDWR | O_CLOEXEC);
    if (fd < 0)
        return 0;
    struct stat st;
    if (fstat(fd, &st) != 0 || !S_ISREG(st.st_mode)) {
        close(fd);
        return 0;
    }
    const off_t size = st.st_size;
    off_t lastNl = -1;
    // 分块从文件尾向前找最后一个换行：日志单文件 <=50MB，心跳外低频调用
    // （open/轮转/重启），全文件读一次可接受；不追求 mmap 的复杂度。
    // 从尾向前的第一块内从块尾往前扫，找到的第一个换行即文件最后一个换行
    // （若文件以换行结尾则 lastNl=size-1，keep==size 不截断）；块内无换行
    // 则继续读更靠前的块直至文件头。
    // 旧实现从文件头分块向前找：>64KB 多行文件在前 64KB 块内即命中换行并
    // 被误当"最后一个换行"，其后所有完整行被整段截断（方向 1 截断回归，
    // 对应 FileWriter_test 的 >64KB 用例）
    constexpr size_t kChunk = 64 * 1024;
    std::vector<char> buf(kChunk);
    off_t pos = size;
    while (pos > 0) {
        off_t start = pos - std::min<off_t>(kChunk, pos);
        size_t want = static_cast<size_t>(pos - start);
        ssize_t n = pread(fd, buf.data(), want, start);
        if (n <= 0)
            break;
        for (ssize_t i = n - 1; i >= 0; i--) {
            if (buf[i] == '\n') {
                lastNl = start + i;
                break;
            }
        }
        if (lastNl != -1)
            break;
        pos = start;
    }
    off_t keep = (lastNl >= 0) ? lastNl + 1 : 0;
    if (keep != size) {
        if (ftruncate(fd, keep) != 0)
            ALOGE("FileWriter: truncateToLastNewline %s to %lld failed: %s",
                  path.c_str(), static_cast<long long>(keep), strerror(errno));
        // 截断后定位到末尾，保证后续追加写正确（ofstream 以 append 打开，
        // 对同一文件新 open 时 O_APPEND 已定位；此处 fd 仅用于截断+stat）
    }
    close(fd);
    return static_cast<size_t>(keep);
}

// 打开 invalid 流（追加模式，不覆盖已有内容）：writeInvalid 流未开先重开
// （方向 2）、构造函数、rotateInvalid 重开共用。打开后先修复残留半行
// （方向 4，末字节非换行截断至最后换行），再 fstat 恢复 mInvalidSize
// （CXX-002 从持久层恢复）。失败仅 ALOGE，由调用方按流状态判定（写路径
// 走 invalidNotOpen 计数可见）。
void FileWriter::openInvalidStream()
{
    mInvalidStream.open(mInvalidFilename, std::ios::app);
    if (!mInvalidStream.is_open()) {
        ALOGE("FileWriter: openInvalidStream: cannot open %s",
              mInvalidFilename.c_str());
        return;
    }
    truncateToLastNewline(mInvalidFilename);
    struct stat st;
    if (stat(mInvalidFilename.c_str(), &st) == 0)
        mInvalidSize = static_cast<size_t>(st.st_size);
    else
        ALOGE("FileWriter: openInvalidStream: stat %s failed: %s",
              mInvalidFilename.c_str(), strerror(errno));
}

// 刷活跃文件落盘（方向 5）：心跳 30s 同锚调用。遍历全部已打开的事件
// 文件 + invalid 流，按路径 fdatasync。flush 已由写路径保证，此处补
// 内核页缓存→磁盘的持久化，缩小断电丢失窗口。文件打开失败的跳过
// （is_open 判定），避免对已关闭流 fsync 报错刷屏
void FileWriter::fsyncActiveFiles()
{
    for (auto& [id, fs] : mFiles) {
        if (fs.stream.is_open())
            fsyncFileByPath(fs.currentFilename);
    }
    if (mInvalidStream.is_open())
        fsyncFileByPath(mInvalidFilename);
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
        // 方向 2：以回退后真实文件大小校准 currentSize（截断后磁盘实际
        // 字节数 = writeBase，重开后续写/轮转判定不基于失真的内存计数；
        // 回退失败返回 SIZE_MAX 时保持原计数）
        const size_t rolled = rollbackFileTo(fs.currentFilename, writeBase);
        if (rolled != SIZE_MAX)
            fs.currentSize = rolled;
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
            // CXX-004 坏行归零延续：重试同样可能部分落盘，须回退到写前
            // 偏移截断残留半行——否则残留与下一条记录粘成非法 JSON
            // （下一条从 currentSize=writeBase 续写，不清残留即粘连）。
            // 方向 2：同时校准 currentSize 为回退后真实大小
            const size_t rolledRetry = rollbackFileTo(fs.currentFilename, writeBase);
            if (rolledRetry != SIZE_MAX)
                fs.currentSize = rolledRetry;
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
        // 方向 4：DROP 计数点收敛到 writeRecord——空串是唯一丢弃分类，
        // 此处计 formatEmpty 后再 return（formatJsonLine 不再自行计数，
        // 同一次丢弃只计 1 次，心跳 dropped 不虚高）
        mDrops.formatEmpty++;
        ALOGE("FileWriter: writeRecord: formatJsonLine returned empty for event %u, DROPPING", schema.id);
        return;
    }

    LC_ALOGD("lechao_lcview: write %u %s", schema.id, line.c_str());

    // 写盘 + flush + 失败恢复（CXX-002，含写耗时累计）
    if (!writeLineFlush(it->second, line))
        return;

    // 方向 5：真正落盘成功才累计（守恒右式数据源）——writeLineFlush
    // 返回 true 即 flush 成功、磁盘已有完整行
    mPersist.valid++;

    // 方向 4：写入计数累计，供 enforceRetention 按写入阈值降频扫描
    mWritesSinceRetention++;

    it->second.currentSize += line.size();
}

// invalid 文件轮转（LCV-01）：close → rename 为 invalid_records_{date}_p{seq}.log
// → 重开新文件并清零累计大小。轮转后的旧文件不再被 mInvalidStream 持有，
// enforceRetention 可正常淘汰（evictOldFiles 仅跳过当前 mInvalidFilename，
// 轮转文件名不同天然参与淘汰）。rename 失败（目录只读等）时重开原文件
// 继续追加保底不丢数据，累计大小保留待下轮重试（方向 3：仅 rename 成功
// 才归零，失败从持久层恢复——归零会致无界增长且回滚抹诊断）。
// 方向 2：rename 成功 reopen 失败时也归零——旧内容已轮转走，重开失败仅
// 影响后续写入（走 invalidNotOpen 计数），与"未轮转"语义不同，归零正确
// 反映"当前 invalid_records.log 为空"。
// 方向 3：rename/reopen 任一失败累计 dropInvRotate（轮转失败也进心跳
// dropped 求和，只 ALOGE 会静默丢诊断）
void FileWriter::rotateInvalid()
{
    mInvalidStream.flush();
    mInvalidStream.close();
    // 方向 5：轮转前刷旧文件落盘（fdatasync），防断电丢轮转边界数据
    fsyncFileByPath(mInvalidFilename);
    const std::string date = makeDateStr();
    // 方向 3：seq 不可用（nextInvalidSeqFor 返回 -1，opendir 失败）时跳过
    // rename——无法确定目标序号，贸然 rename 成 _p{-1} 或覆盖已有轮转文件
    // 会造成诊断数据错位/覆盖；仅重开原文件继续追加，保底不丢数据
    const int nextSeq = nextInvalidSeqFor(date);
    bool renamed = false;
    std::string rotated;
    if (nextSeq >= 0) {
        rotated = mCfg.logDir + "/invalid_records_" + date
                  + "_p" + std::to_string(nextSeq) + ".log";
        renamed = (rename(mInvalidFilename.c_str(), rotated.c_str()) == 0);
        if (!renamed) {
            mDrops.dropInvRotate++;
            ALOGE("FileWriter: rotateInvalid: rename to %s failed: %s",
                  rotated.c_str(), strerror(errno));
        }
    } else {
        mDrops.dropInvRotate++;
        ALOGE("FileWriter: rotateInvalid: nextInvalidSeqFor failed, skipping rename");
    }
    mInvalidStream.open(mInvalidFilename, std::ios::app);
    if (!mInvalidStream.is_open()) {
        mDrops.dropInvRotate++;
        // 重开失败：后续 writeInvalid 走 invalidNotOpen 分支计数可见
        ALOGE("FileWriter: rotateInvalid: reopen %s failed",
              mInvalidFilename.c_str());
        if (renamed)
            mInvalidSize = 0;  // 方向 2：rename 成功（旧内容已走），reopen 失败也归零
        return;
    }
    if (renamed) {
        mInvalidSize = 0;
        ALOGI("FileWriter: rotated invalid log to %s", rotated.c_str());
    } else {
        // 方向 3：rename 失败时原文件内容仍在，从持久层恢复累计大小，
        // 保持轮转阈值判定有效——归零会使 invalid 日志无界增长，且
        // 后续失败恢复的 rollbackFileTo(ftruncate) 以 0 为基准抹掉已有
        // 诊断（CXX-002）；stat 失败保留原值同样不清零
        struct stat st;
        if (stat(mInvalidFilename.c_str(), &st) == 0)
            mInvalidSize = static_cast<size_t>(st.st_size);
    }
}

// 扫描日志目录中 invalid_records_{date}_p<seq>.log 的最大轮转序号 +1。
// 与 nextSeqFor 同模式：readdir 纯文件名前缀匹配 + 尾部 seq 解析；
// 无匹配返 0（首轮轮转）
int FileWriter::nextInvalidSeqFor(const std::string& date)
{
    const std::string prefix = "invalid_records_" + date + "_p";
    const std::string suffix = ".log";
    int maxSeq = -1;
    DIR* dir = opendir(mCfg.logDir.c_str());
    if (!dir) {
        // 方向 3：opendir 失败返回 -1（区别于无匹配的 0）——调用方
        // rotateInvalid 据此跳过 rename，防止以未知序号覆盖已有轮转文件
        ALOGE("FileWriter: nextInvalidSeqFor: opendir(%s) failed: %s",
              mCfg.logDir.c_str(), strerror(errno));
        return -1;
    }
    struct dirent* entry;
    while ((entry = readdir(dir)) != nullptr) {
        std::string name(entry->d_name);
        if (name.compare(0, prefix.size(), prefix) != 0)
            continue;
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

// 写入非法记录到 invalid_records.log
// 记录原因、大小和原始字节（hex 截断），供事后离线重解析定位协议缺陷
// CXX-003: reason 含 " / \ 及控制字符时必须转义（转义并入 jsonEscapeString，
// 与 formatJsonLine 同规则），否则输出行非合法 JSONL / 裂行
// LCV-01: 超阈值先轮转，invalid 文件不再无界增长（坏数据风暴写爆 /data）
void FileWriter::writeInvalid(const uint8_t* data, size_t len,
                               const std::string& reason)
{
    // 方向 2：流未开先重开（追加模式 + fstat 恢复 mInvalidSize），重开成功
    // 继续写入——仅重开失败才计 invalidNotOpen。原实现未开即弃（坏数据风暴
    // 后流异常关闭、恢复路径已 reopen 时，未开直接丢弃会静默丢诊断）
    if (!mInvalidStream.is_open()) {
        openInvalidStream();
        if (!mInvalidStream.is_open()) {
            ALOGE("FileWriter: writeInvalid: stream not open, DROPPING reason=%s",
                  reason.c_str());
            mDrops.invalidNotOpen++;
            return;
        }
        ALOGI("FileWriter: writeInvalid: reopened invalid stream");
    }
    // 超过单文件轮转阈值：先轮转再写（写前检查，单文件最多超一个 payload）
    if (mInvalidSize >= mCfg.maxInvalidFileSizeMb * 1024 * 1024)
        rotateInvalid();
    if (!mInvalidStream.is_open()) {
        // rotateInvalid 重开失败：按未打开语义计数（防轮转失败后静默丢弃）
        ALOGE("FileWriter: writeInvalid: rotate left stream closed, DROPPING reason=%s",
              reason.c_str());
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
         * 恢复路径：clear 清粘滞 → 回退首写残留（LCV-07，与 writeLineFlush
         * 同语义：首写可能部分落盘，重开前须截断回写前偏移 mInvalidSize，
         * 否则残留半行与下一条追加粘连成非法 JSONL）→ 重开流 → 重试一次，
         * 仍失败计 invalidWriteFailed（进心跳 dropped 求和与分项） */
        ALOGE("FileWriter: writeInvalid: write failed, attempting recovery");
        mInvalidStream.clear();
        mInvalidStream.close();
        // 方向 2：以回退后真实大小校准 mInvalidSize（防内存计数失真导致
        // 后续轮转阈值判定错误 / 再 rollback 时误截已有诊断）
        const size_t rolledInv = rollbackFileTo(mInvalidFilename, mInvalidSize);
        if (rolledInv != SIZE_MAX)
            mInvalidSize = rolledInv;
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
            // 重试同样可能部分落盘：回退到写前偏移截断残留半行（LCV-07）。
            // 方向 2：同时校准 mInvalidSize 为回退后真实大小
            const size_t rolledInvRetry =
                rollbackFileTo(mInvalidFilename, mInvalidSize);
            if (rolledInvRetry != SIZE_MAX)
                mInvalidSize = rolledInvRetry;
            return;
        }
        ALOGI("FileWriter: writeInvalid: recovered invalid stream");
    }
    // 写成功（含恢复重试成功）才累计，失败路径保持写前偏移供 rollback
    mInvalidSize += payload.size();
    // 方向 5：invalid 真正落盘成功才累计（守恒右式 invalid 项数据源）
    mPersist.invalid++;
    // 方向 1：writeInvalid 成功也推进写入计数——invalid 坏数据风暴也须
    // 触发容量扫描（原只有 writeRecord 推进，纯 invalid 写入时保留策略
    // 永不扫描，超限数据滞留）；enforceRetention 另有 300s 时间兜底
    mWritesSinceRetention++;
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
            // 方向 5：轮转前刷旧文件落盘（fdatasync），防断电丢轮转边界数据
            if (fs.stream.is_open()) {
                fsyncFileByPath(fs.currentFilename);
                fs.stream.close();
            }

            // 用 stub schema 生成新文件名（只需 id 和 name）
            EventSchema stubSchema;
            stubSchema.id = fs.eventId;
            stubSchema.name = fs.eventName;

            // 同一天内 seq 递增；跨天走 nextSeqFor 续接（扫描目标日期目录
            // 已有最大 seq+1）——时钟回拨/跨天后重复写 _p0 追加旧文件的 P0
            // 修复：旧逻辑跨天硬置 seq=0，系统时间回拨（currentDate 超前）
            // 触发轮转时会重复写回拨目标日期已有的 _p0 文件（CXX-002 重启
            // 后 seq 续接语义同源，只是触发点从 openFile 挪到 checkRotation）
            if (fs.currentDate == today)
                fs.seq++;
            else
                fs.seq = nextSeqFor(stubSchema, today);

            fs.currentDate = today;
            fs.currentFilename = makeFilename(stubSchema, today, fs.seq);
            fs.currentSize = 0;
            fs.stream.open(fs.currentFilename, std::ios::app);
            if (!fs.stream.is_open()) {
                mDrops.dropRotate++;
                ALOGE("FileWriter: cannot open %s", fs.currentFilename.c_str());
            } else {
                // 方向 4：轮转新开文件同样修复残留半行（目标 seq 文件若
                // 已存在且上次异常退出留有半行，须截断再统计）
                truncateToLastNewline(fs.currentFilename);
                // 追加模式打开旧轮转文件时同样恢复大小（与 openFile 一致）
                struct stat st;
                if (stat(fs.currentFilename.c_str(), &st) == 0)
                    fs.currentSize = static_cast<size_t>(st.st_size);
                else
                    // 方向 5：轮转 stat 失败须可见——静默保持 0 会让该文件
                    // 轮转约束失效（可超限近一倍且无信号，与 openFile 同语义）
                    ALOGE("FileWriter: checkRotation: stat %s failed: %s "
                          "(rotation constraint degraded)",
                          fs.currentFilename.c_str(), strerror(errno));
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
    closedir(dir);

    // 方向 1：扫描范围扩展为「日志根目录 + uploaded 子目录」——uploaded
    // 下是"已上传远程存储"的标记文件（目录当前仅预留，上传器二期实现），
    // 也须纳入容量统计并参与 LRU 淘汰，否则上传过的历史文件永不回收。
    // 二期约束：上传器上线后须保证 uploaded 文件名的可重入（已上传即
    // 淘汰、不重复传输），且本扫描的 mtime 淘汰语义对上传标记文件同样
    // 适用；uploaded 内文件默认全部参与淘汰，与业务日志同容量池。
    // uploaded 子目录不存在/不可读时静默跳过（构造函数会创建，外部删除
    // 属预期场景，不刷 ALOGE）
    auto collectDir = [&files](const std::string& dirPath) {
        DIR* sub = opendir(dirPath.c_str());
        if (!sub)
            return;
        // 遍历目录，收集所有 .jsonl 和 .log 文件。
        // LCV-10：精确后缀匹配（原子串包含会把 foo.jsonl.bak / x.log.old
        // 等文件误入淘汰候选——目录虽由 daemon 自管，人工排障放置的
        // 中间文件不应被静默删除）
        struct dirent* entry;
        while ((entry = readdir(sub)) != nullptr) {
            std::string name(entry->d_name);
            const std::string kJsonl = ".jsonl", kLog = ".log";
            bool isJsonl = name.size() >= kJsonl.size() &&
                name.compare(name.size() - kJsonl.size(), kJsonl.size(), kJsonl) == 0;
            bool isLog = name.size() >= kLog.size() &&
                name.compare(name.size() - kLog.size(), kLog.size(), kLog) == 0;
            if (!isJsonl && !isLog)
                continue;

            std::string fullPath = dirPath + "/" + name;
            struct stat st;
            if (stat(fullPath.c_str(), &st) == 0)
                files.push_back({fullPath, st.st_mtime,
                                 static_cast<std::int64_t>(st.st_size)});
        }
        closedir(sub);
    };

    collectDir(mCfg.logDir);
    collectDir(mCfg.logDir + "/uploaded");
    return files;
}

// 淘汰段：按 mtime 升序（最旧优先）删除文件直至总大小 <= maxTotalSizeMb；
// 跳过当前正在写入/被 invalid 流持有的文件（拆分自 enforceRetention，
// 行为不变）
// 跳过打开文件告警的 ratelimit 周期（方向 2）：每累计 64 次跳过才打 1 条
static constexpr unsigned kEvictSkipWarnEvery = 64;
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
    // NOTE（方向 2 脆弱点）："是否打开"的判定基于字符串路径相等比较
    // （fs.currentFilename == f.path / f.path == mInvalidFilename）。
    // 路径两侧拼法不同源：openFile 拼 mCfg.logDir + "/" + name，而
    // scanLogFiles 拼 dirPath + "/" + name（uploaded 子目录为
    // mCfg.logDir + "/uploaded" + "/" + name）。任一侧格式漂移（尾斜杠/
    // 符号链接/相对路径/大小写）即比较失败，打开中的文件可能被误删——
    // 当前一致性靠两侧同用 mCfg.logDir 前缀拼装保证，改动任一侧须同步。
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
        if (isOpen) {
            // 方向 2：跳过打开文件加 ratelimited 告警——持续超限时每轮
            // 扫描都在跳过同一批打开文件，静默会让"淘汰未达上限"无信号；
            // ratelimit 防逐文件/逐轮刷屏（每 kEvictSkipWarnEvery 次跳过
            // 才打一条，成员计数保证多实例/多轮测试互不串扰）
            if (++mEvictSkipWarnCount % kEvictSkipWarnEvery == 1)
                ALOGW("FileWriter: evictOldFiles: skipping open file %s "
                      "(capacity limit may not be reached)", f.path.c_str());
            continue;
        }
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
    // 单测显式调用 enforceRetention 断言扫描行为的场景使用）。
    // LCV-13：构造时把计数器初始化为满阈值——启动后首次调用即执行
    // 全量扫描，清理上次运行遗留的超限数据（静默期内永不清理的空洞）。
    // 方向 1 时间兜底：写入计数未达阈值但距上次实际扫描满
    // retentionScanMaxIntervalSec 秒（缺省 300s）也强制执行——纯 invalid
    // 写入 / 长静默期少量写入时计数阈值永不达，陈旧超限数据滞留；
    // 时间兜底与计数是"或"关系，任一满足即扫描
    const bool timeDue = mCfg.retentionScanMaxIntervalSec > 0 &&
        (std::chrono::steady_clock::now() - mLastRetentionScanAt) >=
            std::chrono::seconds(mCfg.retentionScanMaxIntervalSec);
    if (mCfg.retentionScanEveryWrites > 0 &&
        mWritesSinceRetention < mCfg.retentionScanEveryWrites && !timeDue) {
        return;
    }
    mWritesSinceRetention = 0;
    mLastRetentionScanAt = std::chrono::steady_clock::now();

    // 扫描 → 淘汰 两段（行为不变）
    std::vector<LogFile> files = scanLogFiles();
    evictOldFiles(files);
}
