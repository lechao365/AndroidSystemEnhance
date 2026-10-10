// ============================================================
// batch_parser.cpp — daemon 主循环可测函数实现
// 所属模块：LcView 事件日志系统 — Daemon 层
// 实现见 batch_parser.h；逻辑从 lechao_lcview.cpp main() 原样迁移，
// 不改变运行时行为（parseBatch 即 main 循环的批次解析段）。
// 架构演进：daemon 直读内核后取消 HAL 绑定（waitForHal/
// rebindAfterError/HalBinder/ILcView 移除）。
// ============================================================

#define LOG_TAG "lechao_lcview"

#include "batch_parser.h"
#include "record_codec.h"
#include "../include/lcview_events.h"
#include <log/log.h>
#include <cstring>
#include <thread>

using namespace vendor::lechao::lcview;

// R-09 方向 4：reason 分类映射（纯函数）。SchemaParser 动态 errMsg 与
// parseBatch 常量 reason 统一归类（见 batch_parser.h 头注释）：
//   返 0 = 其他（unknown event_id 边界、schema vanished 防御分支）
//   返 1 = 坏长度/坏前缀类（wire 损坏/截断/错位，与 schema 无关）
//   返 2 = schema 漂移类（字段数/类型与 schema 定义不符）
int vendor::lechao::lcview::classifyInvalidReason(const std::string &reason)
{
    // 坏长度/坏前缀：长度前缀、固定头、尾部残留、wire 损坏、越界截断、
    // 未知字段类型——数据在传输/缓冲环节损坏，schema 无关
    static const char *kBadLenMarks[] = {
        "bad length",          "record too small",
        "trailing bytes",      "data too short for header",
        "bad magic",           "unexpected EOF at field",
        "data exceeds record", "record length mismatch",
        "unknown field type",
    };
    for (const char *mark : kBadLenMarks)
    {
        if (reason.find(mark) != std::string::npos)
            return 1;
    }
    // schema 漂移：字段数与类型不匹配——schema 文件过期或与内核打点
    // 版本不同步（需重编 schema / 升级 schema 定义）
    if (reason.find("field count mismatch") != std::string::npos)
        return 2;
    if (reason.find("type mismatch at field") != std::string::npos)
        return 2;
    // unknown event_id / schema vanished 等边界与防御分支归其他
    return 0;
}

// R-19 P5 方向 1：纯解析层实现——批次框架切分（4B 长度前缀 + 记录边界 +
// 结构级校验），无任何写入副作用。回调逐条收记录判定；回调返回 false 或
// 遇到 kBadLen（坏长度终止整批）即停止遍历。结构级校验与 schema 无关：
//   - 坏长度（total_len<4 或越界）：本批剩余字节全部不可信，批次终止
//   - record too small（净长不足固定头）
//   - bad magic（魔数不符）
// 字段级/schema 语义校验留在 kValid 回调（生产走 schema.validate，测试走
// decodeRecordField），本层不耦合 SchemaParser。
// CXX-001：批次 4B 长度前缀与 record 头多字节字段统一走 record_codec.h
// 的显式小端助手（readLe32/recordMagic/recordEventId），线材契约小端
// （lcview_events.h 大端编译守卫），禁止裸 memcpy 假设主机序

void vendor::lechao::lcview::parseBatchRecords(
    const uint8_t* data, size_t len, const ParsedBatchCallback& cb)
{
    size_t offset = 0;
    while (offset + 4 <= len) {
        // 读取本条记录的总长度（含自身 4 字节）。LCV-02：小端线材契约，
        // readLe32 显式字节拼接（与内核写入端 lcview_builder 同机同序，
        // 保持现有小端结果不变；CXX-001 防跨大小端假设）
        const uint32_t total_len = readLe32(data + offset);

        // 长度校验：最小长度和边界检查
        if (total_len < 4 || offset + total_len > len) {
            ParsedBatchRecord rec;
            rec.kind = ParsedBatchRecord::Kind::kBadLen;
            rec.data = data + offset;   // 批尾剩余起点（offset 起全部不可信）
            rec.len = len - offset;
            rec.reason = "bad length at offset=" + std::to_string(offset) +
                         " total_len=" + std::to_string(total_len);
            cb(rec);
            return;  // kBadLen 语义上终止整批（后续记录不再解析）
        }

        const uint8_t* recordStart = data + offset + 4;
        size_t recordDataLen = total_len - 4;

        // 记录必须至少包含固定头的大小
        if (recordDataLen < sizeof(struct lcview_record_hdr)) {
            ParsedBatchRecord rec;
            rec.kind = ParsedBatchRecord::Kind::kTooSmall;
            rec.data = recordStart;
            rec.len = recordDataLen;
            rec.reason = "record too small";
            if (!cb(rec))
                return;
            offset += total_len;
            continue;
        }

        // 魔数校验：快速识别数据损坏（与 schema 无关的结构级检查）
        // CXX-001：多字节 magic 显式小端读取，禁止结构体强转假设主机序
        const struct lcview_record_hdr* hdr =
            reinterpret_cast<const struct lcview_record_hdr*>(recordStart);
        if (recordMagic(hdr) != LCVIEW_MAGIC) {
            ParsedBatchRecord rec;
            rec.kind = ParsedBatchRecord::Kind::kBadMagic;
            rec.data = recordStart;
            rec.len = recordDataLen;
            rec.reason = "bad magic";
            if (!cb(rec))
                return;
            offset += total_len;
            continue;
        }

        ParsedBatchRecord rec;
        rec.kind = ParsedBatchRecord::Kind::kValid;
        rec.data = recordStart;
        rec.len = recordDataLen;
        if (!cb(rec))
            return;
        offset += total_len;
    }

    // 批次尾部残留（<4B 读不出长度前缀）：不进入主循环，单独送出
    if (offset != len) {
        ParsedBatchRecord rec;
        rec.kind = ParsedBatchRecord::Kind::kTrailing;
        rec.data = data + offset;
        rec.len = len - offset;
        rec.reason = "trailing bytes";
        cb(rec);
    }
}

BatchParseResult vendor::lechao::lcview::parseBatch(
    SchemaParser& schema, FileWriter& writer,
    const uint8_t* data, size_t len)
{
    BatchParseResult result;

    // R-08 方向 2：批次级 flush 事务起点——本批所有 writeRecord/writeInvalid
    // 只写 ofstream 缓冲不 flush，批次尾 endBatch 统一 flush + 失败整批回滚
    // （消每记录一次 write syscall）
    writer.beginBatch();

    // R-19 P5 方向 1：解析与写入动作分离——批次框架层（4B 前缀切分/结构级
    // 校验）收敛到纯解析函数 parseBatchRecords，本函数只消费解析结果做
    // schema 语义校验与落盘，不再内联框架循环（逻辑等价，行为不变）
    parseBatchRecords(data, len, [&](const ParsedBatchRecord& rec) -> bool {
        switch (rec.kind) {
        case ParsedBatchRecord::Kind::kBadLen:
            // LCV-03：坏长度截断后续解析，本批剩余字节全部不可信——
            // invalidCnt 必须计数（心跳 invalid_records 可见），否则坏数据
            // 风暴下 parseBatch 静默丢数据。writeInvalid 已覆盖 offset 到
            // 批尾的全部字节；返回 false 终止遍历（后续记录不再处理）
            result.invalidCnt++;
            // R-09 方向 4：reason 分类计数（坏长度→badLenCnt，心跳可见）
            result.badLenCnt++;
            writer.writeInvalid(rec.data, rec.len, rec.reason);
            ALOGE("lechao_lcview: parse: %s, drop %zu bytes to batch tail",
                  rec.reason.c_str(), rec.len);
            return false;
        case ParsedBatchRecord::Kind::kTooSmall:
            ALOGE("lechao_lcview: parse: record too small (%zu < %zu)",
                  rec.len, sizeof(struct lcview_record_hdr));
            writer.writeInvalid(rec.data, rec.len, rec.reason);
            result.invalidCnt++;
            result.badLenCnt++; // R-09 方向 4：坏长度类（净长不足固定头）
            return true;
        case ParsedBatchRecord::Kind::kBadMagic:
            ALOGE("lechao_lcview: parse: bad magic");
            writer.writeInvalid(rec.data, rec.len, rec.reason);
            result.invalidCnt++;
            result.badLenCnt++; // R-09 方向 4：坏长度类（魔数不符归坏前缀）
            return true;
        case ParsedBatchRecord::Kind::kTrailing:
            // 批次尾部残留（<4B 读不出长度前缀）：直读路径拼包 bug 现场必须
            // 落盘 invalid，禁止静默丢弃（CXX-004 故障可见性）
            writer.writeInvalid(rec.data, rec.len, rec.reason);
            ALOGE("lechao_lcview: parse: %zu trailing bytes at batch tail",
                  rec.len);
            result.invalidCnt++;
            result.badLenCnt++; // R-09 方向 4：坏长度类（尾部残留读不出长度前缀）
            return true;
        case ParsedBatchRecord::Kind::kValid: {
            const struct lcview_record_hdr* hdr =
                reinterpret_cast<const struct lcview_record_hdr*>(rec.data);
            const uint8_t* fields = rec.data + sizeof(struct lcview_record_hdr);
            size_t fieldsLen = rec.len - sizeof(struct lcview_record_hdr);

            // 使用 SchemaParser 校验记录的魔法数字、event_id、字段数、
            // 字段类型和总长度是否完整合法
            std::string errMsg;
            if (schema.validate(rec.data, rec.len, errMsg)) {
                const EventSchema* es = schema.find(recordEventId(hdr));
                if (es) {
                    writer.writeRecord(*es, hdr, fields, fieldsLen);
                    result.validCnt++;
                } else {
                    // validate 通过但 find 失败（理论不可达）：防御分支，
                    // 禁止静默丢数据（CXX-004 故障可见性）
                    ALOGE("lechao_lcview: parse: schema for event %u vanished",
                          recordEventId(hdr));
                    writer.writeInvalid(rec.data, rec.len, "schema vanished");
                    result.invalidCnt++;
                    result.miscInvalidCnt++; // R-09 方向 4：防御分支归其他
                }
            } else {
                writer.writeInvalid(rec.data, rec.len, errMsg);
                ALOGE("lechao_lcview: parse: validate failed: %s", errMsg.c_str());
                result.invalidCnt++;
                // R-09 方向 4：动态 errMsg 按 classifyInvalidReason 归类
                const int cls = classifyInvalidReason(errMsg);
                if (cls == 1)
                    result.badLenCnt++;
                else if (cls == 2)
                    result.schemaDriftCnt++;
                else
                    result.miscInvalidCnt++;
            }
            return true;
        }
        }
        return true;
    });

    // R-08 方向 2：批次尾统一 flush（含全部 writeRecord/writeInvalid 缓冲），
    // 失败整批回滚在 endBatch 内部处理（dropBatchFlush 计数）
    writer.endBatch();
    return result;
}

bool vendor::lechao::lcview::loadSchemaWithRetry(SchemaParser &schema, const std::string &path,
                                                 const std::function<bool()> &shouldStop,
                                                 int maxRetries,
                                                 std::chrono::milliseconds interval)
{
    // 方向 4：可中断 + 总尝试上限语义。
    //   shouldStop：重试期间收到停止信号（SIGTERM/SIGINT 置位 → 谓词返
    //   true）立即退出，不再等满 maxRetries×interval——否则 init stop 被
    //   最长 15s 的重试窗口卡住。
    //   maxRetries 为"总尝试上限"（含首次）：do-while 保证至少尝试 1 次
    //   （maxRetries=0 时仅首次尝试，成功即返回、失败即退出）。
    //   R-19 P5 方向 4 修正 off-by-one——原实现首次尝试在 while 条件内不
    //   计入 attempt，循环体又执行 maxRetries 次，实际总尝试 maxRetries+1
    //   次（如 maxRetries=30 实际尝试 31 次），与"总尝试上限"语义不符。
    //   日志语义（attempt N/maxRetries）不变。
    int attempt = 0;
    do {
        attempt++;
        if (schema.loadFromFile(path))
            break;
        ALOGW("lechao_lcview: schema load attempt %d/%d failed", attempt, maxRetries);
        if (attempt >= maxRetries)
            break;  // 总尝试已达上限，不再重试
        if (shouldStop())
            break;  // 停止信号：立即退出，不等满重试窗口
        std::this_thread::sleep_for(interval);
    } while (true);
    return schema.eventCount() > 0;
}

int vendor::lechao::lcview::schemaLoadExitCode(bool schemaOk, bool running)
{
    // 方向 4：未运行（关停中断，running=false）返回 0——schema 重试窗口
    // 被 SIGTERM 打断时优雅退出，init 不再判崩溃重启；真失败返回 1 交
    // init 重启重试
    if (schemaOk)
        return 0;
    return running ? 1 : 0;
}

bool vendor::lechao::lcview::shouldFlushBatch(
    size_t buffered, bool timedOut, bool ageExpired, size_t bufferCapacity)
{
    if (buffered == 0) return false;  // 空批不 flush（避免空批次写放大）
    return buffered >= bufferCapacity || timedOut || ageExpired;
}

bool vendor::lechao::lcview::shouldPreventiveFlush(
    size_t buffered, size_t bufferCapacity, size_t minRead)
{
    // 满/越界（剩余 <=0）：必须 flush，防 (bufferCapacity - buffered) 下溢
    if (buffered >= bufferCapacity) return true;
    return (bufferCapacity - buffered) < minRead;
}

bool vendor::lechao::lcview::shouldFlushOnExit(size_t buffered)
{
    return buffered > 0;
}
