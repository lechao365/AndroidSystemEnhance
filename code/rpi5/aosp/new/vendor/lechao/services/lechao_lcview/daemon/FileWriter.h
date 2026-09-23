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
#include <sys/types.h>

// 文件写入配置结构体
// logDir — 日志根目录
// maxFileSizeMb — 单个文件大小上限（超过触发轮转）
// maxTotalSizeMb — 所有日志文件总大小上限（超过删除最旧文件）
// retentionScanEveryWrites — enforceRetention 降频阈值（方向 4）：
//   每累计 N 次写入才真正扫描一次日志目录（opendir+stat 有成本，
//   原每轮主循环全目录扫描，空批轮次也扫——改为按写入计数触发）。
//   0 表示关闭降频（每次调用都扫描，单测显式调用场景使用）
// maxInvalidFileSizeMb — invalid_records.log 单文件轮转阈值：
//   超过即轮转为 invalid_records_{date}_p{seq}.log（LCV-01），
//   轮转后的旧文件不再被 invalid 流持有，正常参与容量淘汰。
//   invalid 是诊断数据，默认阈值小于业务日志（10MB vs 50MB）
struct FileWriterConfig {
    std::string logDir = "/data/vendor/lechao_lcview/logs";
    size_t maxFileSizeMb = 50;
    size_t maxTotalSizeMb = 500;
    size_t retentionScanEveryWrites = 256;
    // 保留策略时间兜底（方向 1）：即使写入计数未达阈值，距上次实际扫描
    // 满 retentionScanMaxIntervalSec 秒也强制执行一次 enforceRetention——
    // 防止"长静默期 + 少量写入永不触发扫描"的陈旧超限数据滞留。
    // 0 表示关闭时间兜底（单测显式调用 enforceRetention 断言扫描行为的
    // 场景可与 retentionScanEveryWrites=0 同用）
    size_t retentionScanMaxIntervalSec = 300;
    size_t maxInvalidFileSizeMb = 10;
    // R-10 方向 1：写路径计时开关。热路径（writeRecord/writeLineFlush）
    // 每记录调用两次 steady_clock::now()（format 计时 + write 计时）固定
    // 开销；默认关闭（false）跳过计时统计，需要观测延迟/心跳 avg/max/
    // 直方图时置 true 打开（心跳各耗时字段仅在打开时才有意义，关闭恒 0）
    bool trackWriteTimings = false;
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
        uint64_t formatEmpty = 0;    // formatJsonLine 返回空（writeRecord 丢弃计数点）
        uint64_t formatOob = 0;      // 保留供心跳格式兼容（计数点已收敛到 formatEmpty）
        uint64_t reopenFailed = 0;   // 写失败恢复重开失败
        uint64_t retryFailed = 0;    // 恢复后重试二次写失败
        uint64_t invalidNotOpen = 0; // invalid 事件流未打开
        uint64_t invalidWriteFailed = 0; // invalid 写失败恢复后仍失败（reopen/retry）
        uint64_t dropRotate = 0;    // checkRotation 轮转后重开新文件失败（方向 3）
        uint64_t dropInvRotate = 0; // invalid 轮转失败（rename/reopen 任一失败，方向 3）
        uint64_t dropRollback = 0; // 写失败恢复回退截断失败（rollbackFileTo，方向 3）
        uint64_t dropBatchFlush = 0; // R-08 方向 2：批次尾 flush 失败整批回滚（文件级计数）
    };
    // 返回当前累计的 DROP 计数（心跳输出用）
    const DropCounters& dropCounters() const { return mDrops; }

    // 落盘计数（方向 5）：writeRecord/writeInvalid 真正成功落盘（含恢复
    // 重试成功）的记录数，供守恒右式替代解析成功数——解析成功计数在
    // writeRecord 内部 DROP（openFile 失败 / formatEmpty 等）时仍 +1，
    // 守恒右式用它会高估落盘，dev 恒向负偏（落盘超产生误报）。落盘计数
    // 只在 flush 成功后累计，与磁盘真实一致
    struct PersistCounters
    {
        uint64_t valid = 0;   // 合法记录成功落盘数
        uint64_t invalid = 0; // 非法记录成功落盘数（invalid_records.log）
    };
    const PersistCounters &persistCounters() const { return mPersist; }

    // 刷活跃文件落盘（方向 5）：心跳 30s 同锚调用，对全部已打开的事件
    // 文件与 invalid 流按路径 fdatasync（flush 只到内核页缓存，崩溃/断电
    // 丢数据；fdatasync 才真正落盘）。轮转前对旧文件单独 fsync 见
    // checkRotation 内部实现
    void fsyncActiveFiles();

    // 写路径耗时统计（方向 3：drain 被攒包策略钉死，对写路径成本不敏感，
    // 心跳输出平均微秒/条作为微优化可判定指标）
    struct WriteTimings {
        uint64_t formatCount = 0;   // formatJsonLine 调用次数
        uint64_t formatTotalUs = 0; // formatJsonLine 累计耗时（微秒）
        uint64_t writeCount = 0;    // writeRecord 落盘次数（含恢复重试）
        uint64_t writeTotalUs = 0;  // writeRecord 累计耗时（微秒，不含 format）
        // R-09 方向 2：累计 max（自 daemon 启动起，尾延迟上界可见）
        uint64_t formatMaxUs = 0; // formatJsonLine 单条最大耗时（微秒）
        uint64_t writeMaxUs = 0;  // writeRecord 单条最大耗时（微秒）
    };
    const WriteTimings& writeTimings() const { return mTimings; }

    // R-09 方向 2：延迟直方图分桶（尾延迟分布可见性）。耗时区间边界
    // 固定数组，超过末桶并入末桶（饱和计数）。桶边界按典型写路径量级
    // 设定：format 单条 ~几~几十微秒，write 单条 ~几十~几百微秒（含
    // 恢复重试可到毫秒级）；用对数递增覆盖两个量级。
    struct LatencyHistogram
    {
        static constexpr size_t kBuckets = 6;
        // 桶边界（微秒）：<1, <5, <20, <100, <500, >=500（饱和末桶）
        static constexpr uint64_t kBoundsUs[kBuckets - 1] = {
            1, 5, 20, 100, 500,
        };
        uint64_t formatBuckets[kBuckets] = {}; // formatJsonLine 耗时分布
        uint64_t writeBuckets[kBuckets] = {};  // writeRecord 耗时分布

        // 按耗时（微秒）累加到对应桶（越界钳制到末桶）
        static size_t bucketFor(uint64_t us)
        {
            for (size_t i = 0; i < kBuckets - 1; ++i)
                if (us < kBoundsUs[i])
                    return i;
            return kBuckets - 1;
        }
        void recordFormat(uint64_t us) { formatBuckets[bucketFor(us)]++; }
        void recordWrite(uint64_t us) { writeBuckets[bucketFor(us)]++; }
        void reset()
        {
            for (size_t i = 0; i < kBuckets; ++i)
                formatBuckets[i] = writeBuckets[i] = 0;
        }
    };

    // 写路径延迟窗口快照（R-09 方向 2）：取并重置窗口内 max 与直方图，
    // 供心跳按 30s 窗口输出。累计 max/计数无法从两次累计快照差还原
    // 窗口 max，须由本方法在窗口边界重置（对齐 ConserveBaseline 模式）。
    struct WindowLatency
    {
        uint64_t formatMaxUs = 0;   // 窗口内 formatJsonLine 单条最大耗时
        uint64_t writeMaxUs = 0;    // 窗口内 writeRecord 单条最大耗时
        LatencyHistogram histogram; // 窗口内延迟直方图
    };
    // 返回并重置窗口延迟统计（emitHeartbeat 每心跳调用一次）
    WindowLatency takeLatencyWindow();

    // R-09 方向 3：event_id 分布统计（容量规划有据）。writeRecord 成功
    // 落盘时按 schema.id 累计；心跳取并重置窗口分布。槽位上限 64（事件
    // id 合法范围上界，越界忽略——validate 已保证 event_id 合法）。
    struct EventDist
    {
        static constexpr size_t kSlots = 64;
        uint64_t counts[kSlots] = {};
        void record(uint32_t eventId)
        {
            if (eventId < kSlots)
                counts[eventId]++;
        }
        void reset()
        {
            for (size_t i = 0; i < kSlots; ++i)
                counts[i] = 0;
        }
        // 取窗口 top event（出现次数最多者；全 0 返 (0,0)）
        void top(uint32_t &id, uint64_t &cnt) const
        {
            id = 0;
            cnt = 0;
            for (size_t i = 0; i < kSlots; ++i)
                if (counts[i] > cnt)
                {
                    id = static_cast<uint32_t>(i);
                    cnt = counts[i];
                }
        }
    };
    // 返回并重置窗口 event 分布（emitHeartbeat 每心跳调用）
    EventDist takeEventDistWindow();

    // R-13 方向 2：事件序列间隙（seq gap）窗口统计。
    // 内核为每条事件分配全局递增 seq_no（hdr.seq_no），daemon formatJsonLine
    // 收到记录时记录其 seq。窗口内 gap = (last_seq - first_seq + 1) - count：
    // 正值表示窗口内存在序列跳跃（ring 驱逐 overrun / ENOSPC 丢弃 /
    // FileWriter DROP / 读端漏读），把"丢事件量"从守恒 dev 中独立成可观测
    // 指标（NTP 回拨时 ts 不可作排序基，seq 定序可靠）。
    // seq==0 视为旧内核记录（无 seq 语义）不计入，防新旧内核混跑误报。
    struct SeqGapStats
    {
        uint64_t count = 0;    // 窗口内收到（含 seq 语义）的记录条数
        uint32_t firstSeq = 0; // 窗口内最小 seq_no
        uint32_t lastSeq = 0;  // 窗口内最大 seq_no
        uint32_t seqWrap = 0;  // 窗口内检测到 u32 回绕次数（seq 减小）
        void reset()
        {
            count = 0;
            firstSeq = 0;
            lastSeq = 0;
            seqWrap = 0;
        }
        // 记录一条 seq（0 忽略——无 seq 语义的旧内核记录不参与 gap 统计）
        void record(uint32_t seq)
        {
            if (seq == 0)
                return;
            if (count == 0)
            {
                firstSeq = lastSeq = seq;
            }
            else if (seq >= lastSeq)
            {
                lastSeq = seq;
            }
            else
            {
                seqWrap++; // 回绕（内核重启或 u32 归零）
                if (firstSeq == 0 || seq < firstSeq)
                    firstSeq = seq;
                lastSeq = seq;
            }
            count++;
        }
        // 窗口内序列间隙（回绕时不精确，仅累计不判红；无记录返 0）
        uint64_t gap() const
        {
            if (count == 0)
                return 0;
            uint64_t span = static_cast<uint64_t>(lastSeq) - firstSeq + 1;
            return (span > count) ? (span - count) : 0;
        }
    };
    // 返回并重置窗口 seq 间隙统计（emitHeartbeat 每心跳调用）
    SeqGapStats takeSeqGapWindow();

    // R-08 方向 2：批次级 flush 事务。
    // 批次 = parseBatch 一次调用处理的记录集合（flushSegment 攒出的 64KB
    // 缓冲）。原每记录 flush（每次 write syscall），改批次尾统一 flush——
    // 消每记录一次 write syscall 的系统调用放大。
    // 语义：
    //   beginBatch()：清空批次起点记录（parseBatch 开头调用）；
    //   writeRecord/writeInvalid 首次触碰某文件时记录该文件"批次起点偏移"
    //     （写前 currentSize），写入只进 ofstream 缓冲（不 flush）；
    //   endBatch()：flush 本批触碰的全部事件文件 + invalid 流；任一 flush
    //     失败即"写失败整批 rollback"——对该文件 rollbackFileTo(批次起点)
    //     截断回批次起点，本批该文件写入全部撤销（半行/多行残留清零）。
    //     失败文件计 dropBatchFlush 并返回 false（调用方按批丢弃计数）。
    //   触碰文件集合经 mBatchStarts（eventId→起点）跟踪；invalid 流单独
    //   记录 mInvalidBatchStart。
    void beginBatch();
    bool endBatch();
    // 本批次触碰的事件文件数（endBatch flush 判空用，0=空批直接返回）
    bool hasBatchTouched() const { return !mBatchStarts.empty(); }

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
        // R-10 方向 3：size 是否失真（degraded）。openFile/checkRotation
        // 打开时 stat 失败即置 true——currentSize 未从持久层恢复（保持 0）
        // 属失真值。失真状态下写失败恢复禁止 rollbackFileTo 到该失真
        // 基准（0 会清空整个已有文件），改为跳过回退直接重开流再恢复
        // 真实大小。stat 成功后清 false
        bool sizeDegraded = false;
        // R-10 方向 4：打开文件的 inode 标识（st_dev + st_ino）。evictOldFiles
        // 淘汰扫描以 (dev,ino) 集合判定"是否正在打开"，替代原路径字符串相等
        // 比较（两侧拼法不同源，任一侧格式漂移即误删打开中文件）。打开成功
        // 且 stat 成功时记录；true 表示 inode 有效
        dev_t dev = 0;
        ino_t ino = 0;
        bool hasInode = false;
    };
    // 写盘 + flush + 失败恢复（拆分自 writeRecord，行为不变）；
    // 返回是否成功写入（失败路径已累计 DROP 计数与写耗时）。
    // 失败恢复前先回退到写前偏移（截断首写可能部分落盘的残留半行），
    // 再重开重写——保证恢复后磁盘只有合法整行（坏行归零）
    // R-08 方向 2：本函数不再 flush（批次尾统一 flush，见 endBatch），
    // 只负责把 line 写入 ofstream 缓冲并做单记录级失败恢复。
    bool writeLineFlush(FileState& fs, const std::string& line);
    // 写路径耗时累计（微秒；供心跳输出平均微秒/条）
    void recordWriteTiming(std::chrono::steady_clock::time_point start);

    // invalid 文件轮转（LCV-01）：close → rename 为带日期+seq 的
    // 轮转名 → 重开新文件。rename 失败时尽力重开原文件继续追加
    // （不丢数据），成败由调用方按流状态判定
    void rotateInvalid();
    // 打开 invalid 流（追加模式，不覆盖已有内容）：writeInvalid 流未开
    // 先重开（方向 2）、构造函数、rotateInvalid 重开共用；重开后恢复
    // mInvalidSize（fstat），失败仅 ALOGE，由调用方按流状态判定
    void openInvalidStream();
    // 扫描日志目录：invalid_records_{date}_p<seq>.log 已存在的最大
    // 轮转序号 +1（重启/多次轮转后 seq 续接，与 nextSeqFor 同模式）
    int nextInvalidSeqFor(const std::string& date);
    // 按路径 fdatasync（方向 5）：open + fdatasync + close，尽力而为，
    // 失败 ALOGE（心跳周期短，静默不刷即丢数据，须可见）
    static void fsyncFileByPath(const std::string &path);
    // 打开文件后修复残留半行（方向 4）：末字节非换行说明上次异常退出
    // 留下半行，截断至最后一个换行，避免半行与后续行粘连成非法 JSONL。
    // 返回修复后文件字节数（供恢复 currentSize/mInvalidSize）
    static size_t truncateToLastNewline(const std::string &path);
    // 回退文件到指定偏移（写失败恢复前截断残留半行）。返回回退后文件
    // 真实大小（fstat），供调用方校准 currentSize/mInvalidSize——内存计数
    // 与磁盘实际不一致会导致后续追加/轮转判定基于失真值而误截（方向 2）。
    // 失败返回 SIZE_MAX（调用方保持原计数）并累计 dropRollback（方向 3，
    // 回滚失败也须可见，进心跳 dropped 求和）
    size_t rollbackFileTo(const std::string &path, size_t offset);

    // 日志目录扫描结果：路径 + mtime + size（enforceRetention 淘汰用）。
    // R-10 方向 4：dev/ino 为文件 inode 标识——evictOldFiles 以 inode 集合
    // 判定"是否打开中"（替代路径字符串相等比较），stat 成功即填充
    struct LogFile {
        std::string path;
        time_t mtime;
        std::int64_t size;
        dev_t dev = 0;
        ino_t ino = 0;
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
    // invalid 当前文件已写入字节数（CXX-002：构造时 stat 从持久层恢复，
    // 写成功累计，轮转归零——供轮转阈值判定与失败恢复 rollback 基准）
    size_t mInvalidSize = 0;
    // R-10 方向 3：invalid 流 size 是否失真（degraded）。openInvalidStream
    // stat 失败即置 true——mInvalidSize 未恢复（保持 0）属失真值，写失败
    // 恢复禁止 rollbackFileTo 到失真 0 基准（会清空 invalid_records.log
    // 已有诊断），跳过回退直接重开。stat 成功后清 false
    bool mInvalidSizeDegraded = false;
    // R-10 方向 4：invalid 流的 inode 标识（st_dev + st_ino），evictOldFiles
    // 以 inode 集合判定跳过（替代 f.path == mInvalidFilename 路径比较）；
    // true 表示 inode 有效
    dev_t mInvalidDev = 0;
    ino_t mInvalidIno = 0;
    bool mInvalidHasInode = false;
    // DROP 分类累计计数（10 条 DROP 路径，进 daemon 心跳）。
    // 方向 4：DROP 计数点收敛到 writeRecord 的 formatEmpty；formatOob
    // 保留供心跳格式兼容，当前无自增路径（同一次丢弃只计 1 次不虚高）
    DropCounters mDrops;
    // 落盘计数（方向 5：守恒右式数据源，见 PersistCounters）
    PersistCounters mPersist;
    // 写路径耗时统计（方向 3，见 WriteTimings）
    WriteTimings mTimings;
    // R-09 方向 2：窗口延迟统计（takeLatencyWindow 取并重置）
    WindowLatency mLatencyWindow;
    // R-09 方向 3：窗口 event 分布（takeEventDistWindow 取并重置）
    EventDist mEventDist;
    // 距上次 enforceRetention 实际扫描的写入次数（方向 4 降频）；
    // 构造时初始化为满阈值（LCV-13：启动首扫清理历史超限数据）
    size_t mWritesSinceRetention = 0;
    // 距上次 enforceRetention 实际扫描的时刻（方向 1 时间兜底）：
    // 写入计数未达阈值但距上次扫描满 retentionScanMaxIntervalSec 秒时
    // 也强制执行，防长静默期陈旧超限数据滞留；构造时置 now 避免启动
    // 首扫（计数满触发）与时间兜底叠加干扰
    std::chrono::steady_clock::time_point mLastRetentionScanAt;
    // evictOldFiles 跳过打开文件告警的 ratelimit 计数（方向 2）：
    // 每累计 kEvictSkipWarnEvery 次跳过才打 1 条 ALOGW，防超限场景
    // 每轮扫描刷屏（成员变量而非 static，避免多实例/测试间串扰）
    unsigned mEvictSkipWarnCount = 0;
    // R-08 方向 4：缓存日期串（YYYYMMDD）跨天刷新——makeDateStr 原每次
    // 调用 time()+localtime_r()+strftime()+构造 string（checkRotation 每轮
    // 主循环调，含空批轮，固定开销）。缓存后仅在跨天时刷新（新日期串
    // 与缓存不同才重算），消每主循环 time 系统调用。mDateStr 为缓存值，
    // mDateChecked 标记本轮是否已核对（防同轮多次调用重复 time()）。
    std::string mDateStr;
    time_t mDateChecked = 0;
    // R-08 方向 2：批次级 flush 事务的触碰文件起点跟踪（见 beginBatch/
    // endBatch 注释）。mBatchStarts[eventId] = 本批次首次写该文件前的
    // currentSize（rollback 基准）；mInvalidBatchStart = invalid 流本批次
    // 首次写前的 mInvalidSize（无触碰时用 SIZE_MAX 标记）。
    std::unordered_map<uint16_t, size_t> mBatchStarts;
    size_t mInvalidBatchStart = SIZE_MAX;
    // R-08 方向 2：批次内累计计数（endBatch 成功才并入全局 mPersist/
    // mWritesSinceRetention；flush 失败整批回滚则丢弃，防守恒右式虚增）
    uint64_t mBatchPersistValid = 0;
    uint64_t mBatchPersistInvalid = 0;
    uint64_t mBatchWritesSinceRetention = 0;
    uint64_t mBatchInvalidWrites = 0;
    // R-13 方向 2：窗口 seq 间隙统计（formatJsonLine 收到记录时 record seq，
    // 心跳 takeSeqGapWindow 取并重置）
    SeqGapStats mSeqGap;
};
