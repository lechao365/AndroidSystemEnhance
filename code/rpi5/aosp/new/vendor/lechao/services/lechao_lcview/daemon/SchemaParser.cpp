// ============================================================
// SchemaParser.cpp — 事件 schema 解析器实现
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：实现 SchemaParser 类的所有方法，包括：
//   1) JSON 配置文件解析（loadFromFile / parseJson）
//   2) 按 event_id 查找 schema（find）
//   3) 二进制日志记录合法性校验（validate）
//
// 校验策略：采用"先整体解析 schema，再逐字段比对"的方式，
// 而非逐个字段解析。这样出错时能给出具体的字段索引，
// 便于定位内核驱动中的 bug。
// ============================================================

#include "SchemaParser.h"
#include "record_codec.h"
#include "../include/lcview_events.h"
#include <fstream>
#include <sstream>
#include <android-base/logging.h>
#include <json/json.h>
#include "lechao_log.h"

// record_codec 解码器符号（定义于 vendor::lechao::lcview 命名空间，
// 本文件类定义不在该命名空间内，逐符号引入避免全量 using 的歧义风险）
using vendor::lechao::lcview::DecodedField;
using vendor::lechao::lcview::FieldDecodeResult;
using vendor::lechao::lcview::decodeRecordField;

// 将 JSON 中的字符串类型名映射为 FieldType 枚举值
// 设计决策：写死字符串到枚举的映射而非通过反射，
// 因为类型集合是固定的（int32/int64/float/string/binary）
static FieldType parseFieldType(const std::string& s) {
    if (s == "int32")  return FieldType::INT32;
    if (s == "int64")  return FieldType::INT64;
    if (s == "float")  return FieldType::FLOAT;
    if (s == "string") return FieldType::STRING;
    if (s == "binary") return FieldType::BINARY;
    return FieldType::UNKNOWN;
}

bool SchemaParser::loadFromFile(const std::string& path)
{
    std::ifstream file(path);
    if (!file.is_open()) {
        LOG(ERROR) << "SchemaParser: cannot open " << path;
        return false;
    }
    std::stringstream buf;
    buf << file.rdbuf();
    return parseJson(buf.str());
}

// 解析单个 event 定义（含 fields 数组）到 EventSchema
// 拆分自 parseJson：原 parseJson 超长（126 行），事件级解析独立成函数
// 后主流程仅保留 JSON 解析与原子提交骨架，行为完全不变
bool SchemaParser::parseEventDef(const Json::Value& ev, EventSchema* out)
{
    if (!ev.isMember("id") || !ev["id"].isUInt()) {
        LOG(ERROR) << "SchemaParser: event 'id' missing or not a uint";
        return false;
    }
    uint32_t rawId = ev["id"].asUInt();
    /* EventSchema.id 为 uint16_t：超范围赋值会静默截断，
     * 可能与其他 id 冲突产生脏 schema（CXX-003 边界校验） */
    if (rawId > 0xFFFF) {
        LOG(ERROR) << "SchemaParser: event id " << rawId
                   << " exceeds uint16_t range";
        return false;
    }
    out->id = static_cast<uint16_t>(rawId);

    if (!ev.isMember("name") || !ev["name"].isString()) {
        LOG(ERROR) << "SchemaParser: event " << out->id
                   << " 'name' missing or not a string";
        return false;
    }
    out->name = ev["name"].asString();

    if (ev.isMember("desc")) {
        if (!ev["desc"].isString()) {
            LOG(ERROR) << "SchemaParser: event " << out->id
                       << " 'desc' is not a string";
            return false;
        }
        out->desc = ev["desc"].asString();
    }

    if (!ev.isMember("fields") || !ev["fields"].isArray()) {
        LOG(ERROR) << "SchemaParser: event " << out->id
                   << " has no fields array";
        return false;
    }
    const Json::Value& fields = ev["fields"];

    for (const auto& f : fields) {
        FieldDef fd;
        if (!f.isMember("name") || !f["name"].isString()) {
            LOG(ERROR) << "SchemaParser: event " << out->id
                       << " field 'name' missing or not a string";
            return false;
        }
        fd.name = f["name"].asString();

        if (!f.isMember("type") || !f["type"].isString()) {
            LOG(ERROR) << "SchemaParser: event " << out->id
                       << " field '" << fd.name
                       << "' type missing or not a string";
            return false;
        }
        fd.type = parseFieldType(f["type"].asString());
        if (fd.type == FieldType::UNKNOWN) {
            LOG(ERROR) << "SchemaParser: unknown type '"
                       << f["type"].asString()
                       << "' in event " << out->id;
            return false;
        }
        out->fields.push_back(fd);
    }
    return true;
}

// 核心解析逻辑：将 JSON 内容递归解析为 EventSchema 对象集合
bool SchemaParser::parseJson(const std::string& jsonContent)
{
    Json::Value root;
    Json::CharReaderBuilder builder;
    builder.settings_["allowComments"] = false;
    builder.settings_["strictRoot"] = false;
    std::string errors;
    std::istringstream iss(jsonContent);

    if (!Json::parseFromStream(builder, iss, &root, &errors)) {
        LOG(ERROR) << "SchemaParser: JSON parse error: " << errors;
        return false;
    }

    /* CXX-003: root 必须为 object（strictRoot 已放开，此处显式校验），
     * 根为数组时 root["events"] 行为未定义 */
    if (!root.isObject()) {
        LOG(ERROR) << "SchemaParser: root is not an object";
        return false;
    }

    /* CXX-003: 所有字段 isMember + isXxx 双重前置校验。
     * jsoncpp 在 -fno-exceptions 下字段缺失/类型不匹配直接 abort，
     * schema 配置属不可信外部输入，禁止假设字段存在且类型正确。
     * version 例外：仅为元数据，缺失/类型错时缺省 0 不阻断解析
     * （与 SchemaParser_test.MissingVersion_DefaultsToZero 契约一致） */
    int newVersion = 0;
    if (root.isMember("version") && root["version"].isInt())
        newVersion = root["version"].asInt();

    if (!root.isMember("events") || !root["events"].isArray()) {
        LOG(ERROR) << "SchemaParser: 'events' missing or not an array";
        return false;
    }
    const Json::Value& events = root["events"];

    /* 原子提交：先全部解析到局部 map，全部成功后再 swap 到成员变量。
     * 任意一步失败都直接返回，mSchemaMap 保持完整旧状态。 */
    std::unordered_map<uint16_t, EventSchema> newMap;

    for (const auto& ev : events) {
        EventSchema schema;
        if (!parseEventDef(ev, &schema))
            return false;

        /* 重复 id 不阻断解析（后者覆盖前者，与
         * SchemaParser_test.DuplicateId_OverwritesWithWarning 契约一致），
         * 但必须 WARNING 让配置错误可见（CXX-004 故障可见性精神） */
        auto dup = newMap.find(schema.id);
        if (dup != newMap.end()) {
            LOG(WARNING) << "SchemaParser: duplicate event id " << schema.id
                         << " (" << schema.name << ") overwrites previous '"
                         << dup->second.name << "'";
        }

        LC_LOGD("SchemaParser: parsed event id=" << schema.id << " name=" << schema.name << " fields=" << schema.fields.size());
        newMap[schema.id] = std::move(schema);
    }

    mSchemaMap = std::move(newMap);
    mVersion = newVersion;
    LOG(INFO) << "SchemaParser: loaded " << mSchemaMap.size() << " events";
    return true;
}

// 通过 event_id 在哈希表中查找对应的 schema 定义
const EventSchema* SchemaParser::find(uint16_t eventId) const
{
    auto it = mSchemaMap.find(eventId);
    return (it != mSchemaMap.end()) ? &it->second : nullptr;
}

// 逐字段校验：类型标识匹配 + 数据长度足够（拆分自 validate，行为不变）
// 字段推进统一走 record_codec::decodeRecordField（与
// FileWriter::formatJsonLine 共用同一 TLV 解码器，消除协议
// 遍历逻辑的重复实现——本文件曾手写同款 switch）
bool SchemaParser::validateFields(const EventSchema& schema,
                                  const uint8_t* data, size_t len,
                                  size_t& consumed, std::string& errMsg) const
{
    const uint8_t* ptr = data + sizeof(struct lcview_record_hdr);
    const uint8_t* end = data + len;

    for (size_t i = 0; i < schema.fields.size(); i++) {
        if (ptr >= end) {
            errMsg = "unexpected EOF at field " + std::to_string(i);
            return false;
        }

        DecodedField df;
        FieldDecodeResult r = decodeRecordField(&ptr, end, &df);
        if (r == FieldDecodeResult::kTruncated) {
            // STRING/BINARY 变长字段需区分两种截断（解码器经
            // df.valueLen 区分：0 = 长度前缀缺失，>0 = 已读长度但值越界），
            // 保持与历史错误消息一致（单测断言文本）
            if ((df.type == LCVIEW_TYPE_STRING || df.type == LCVIEW_TYPE_BINARY)
                && df.valueLen > 0) {
                errMsg = "field " + std::to_string(i) + " data exceeds record (len="
                         + std::to_string(df.valueLen) + ", remaining="
                         + std::to_string(end - ptr) + ")";
            } else if (df.type == LCVIEW_TYPE_STRING || df.type == LCVIEW_TYPE_BINARY) {
                errMsg = "unexpected EOF at field len " + std::to_string(i);
            } else {
                errMsg = "unexpected EOF at field " + std::to_string(i);
            }
            return false;
        }
        if (r == FieldDecodeResult::kUnknown) {
            errMsg = "unknown field type " + std::to_string(df.type);
            return false;
        }

        uint8_t expectedType = static_cast<uint8_t>(schema.fields[i].type);

        // 类型匹配性检查：字段头的 type 必须和 schema 中定义的类型一致
        if (df.type != expectedType) {
            errMsg = "type mismatch at field " + std::to_string(i) +
                     ": expected " + std::to_string(expectedType) +
                     ", got " + std::to_string(df.type);
            return false;
        }

        // STRING/BINARY 变长字段：长度前缀越界或数据越界已在
        // decodeRecordField 内统一拦截（kTruncated），此处无需再查
    }

    // 返回已消费字节数（供调用方做总长度匹配）
    consumed = static_cast<size_t>(ptr - data);
    return true;
}

// 校验二进制日志记录是否合法：
//   Step 1: 检查是否至少包含固定头（16 字节）
//   Step 2: 检查魔数是否为 0x4C56（'LV'）
//   Step 3: 检查 event_id 是否在 schema 中定义
//   Step 4: 检查 field_count 是否与 schema 定义一致
//   Step 5: 逐个字段检查类型标识是否匹配 schema（拆入 validateFields）
//   Step 6: 跳过字段值后，验证总消耗长度与传入长度一致
// 为什么这么严谨：
//   内核驱动的 ring buffer 可能出现数据错位、部分写入等异常，
//   严谨校验可以防止损坏数据被写入 JSONL 文件，避免后续分析出错。
bool SchemaParser::validate(const uint8_t* data, size_t len,
                             std::string& errMsg) const
{
    // 最小长度必须能容纳记录头
    if (len < sizeof(struct lcview_record_hdr)) {
        errMsg = "data too short for header";
        return false;
    }

    const struct lcview_record_hdr* hdr =
        reinterpret_cast<const struct lcview_record_hdr*>(data);

    // 魔数校验：快速识别数据损坏
    if (hdr->magic != LCVIEW_MAGIC) {
        errMsg = "bad magic";
        return false;
    }

    const EventSchema* schema = find(hdr->event_id);
    if (!schema) {
        errMsg = "unknown event_id " + std::to_string(hdr->event_id);
        return false;
    }

    // field_count 必须与 schema 定义一致
    /* hdr->field_count 是 uint8_t，自然提升为 int 比较；
     * size() 直接比较防止 schema 字段数 > 255 时强转截断匹配 */
    if ((size_t)hdr->field_count != schema->fields.size()) {
        errMsg = "field count mismatch: expected " +
                 std::to_string(schema->fields.size()) +
                 ", got " + std::to_string(hdr->field_count);
        return false;
    }

    // 逐字段校验（TLV 解码 + 类型匹配 + 越界拦截），返回已消费字节数
    size_t consumed = 0;
    if (!validateFields(*schema, data, len, consumed, errMsg))
        return false;

    // 验证总长度匹配：确保没有多余或缺失的字节
    // 这是防止数据截断或粘包的最后一道防线
    if (consumed != len) {
        errMsg = "record length mismatch: expected " +
                 std::to_string(consumed) + ", got " + std::to_string(len);
        return false;
    }

    return true;
}
