// ============================================================
// SchemaParser.cpp — 事件 schema 解析器实现
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：实现 SchemaParser 类的所有方法，包括：
//   1) JSON 配置文件解析（loadFromFile / parseJson）
//   2) 热重载 + 失败回滚（reload）
//   3) 按 event_id 查找 schema（find）
//   4) 二进制日志记录合法性校验（validate）
//
// 校验策略：采用"先整体解析 schema，再逐字段比对"的方式，
// 而非逐个字段解析。这样出错时能给出具体的字段索引，
// 便于定位内核驱动中的 bug。
// ============================================================

#include "SchemaParser.h"
#include "../include/lcview_events.h"
#include <fstream>
#include <sstream>
#include <android-base/logging.h>
#include <json/json.h>
#include "lechao_log.h"

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

// 热重载：先备份当前 mSchemaMap，加载新版本失败时还原。
// 为什么需要热重载而非进程重启：
//   内核驱动持续输出日志，daemon 重启会导致短暂中断。
//   热重载可以在不中断日志采集的前提下更新配置。
// 使用 std::move 转移旧数据到临时变量，保证异常安全。
bool SchemaParser::reload(const std::string& path)
{
    std::unordered_map<uint16_t, EventSchema> oldMap = std::move(mSchemaMap);
    int oldVersion = mVersion;

    mSchemaMap.clear();
    mVersion = 0;

    if (loadFromFile(path))
        return true;

    // 加载失败，恢复旧版本 schema，保证服务不中断
    mSchemaMap = std::move(oldMap);
    mVersion = oldVersion;
    LOG(ERROR) << "SchemaParser: reload failed, keeping previous schema";
    return false;
}

// 核心解析逻辑：将 JSON 内容递归解析为 EventSchema 对象集合
bool SchemaParser::parseJson(const std::string& jsonContent)
{
    Json::Value root;
    Json::CharReaderBuilder builder;
    std::string errors;
    std::istringstream iss(jsonContent);

    if (!Json::parseFromStream(builder, iss, &root, &errors)) {
        LOG(ERROR) << "SchemaParser: JSON parse error: " << errors;
        return false;
    }

    mVersion = root.get("version", 0).asInt();
    const Json::Value& events = root["events"];
    if (!events.isArray()) {
        LOG(ERROR) << "SchemaParser: 'events' is not an array";
        return false;
    }

    for (const auto& ev : events) {
        EventSchema schema;
        schema.id = ev["id"].asUInt();
        schema.name = ev["name"].asString();
        schema.desc = ev.get("desc", "").asString();

        const Json::Value& fields = ev["fields"];
        if (!fields.isArray()) {
            LOG(ERROR) << "SchemaParser: event " << schema.id << " has no fields";
            return false;
        }

        for (const auto& f : fields) {
            FieldDef fd;
            fd.name = f["name"].asString();
            fd.type = parseFieldType(f["type"].asString());
            if (fd.type == FieldType::UNKNOWN) {
                LOG(ERROR) << "SchemaParser: unknown type '"
                           << f["type"].asString()
                           << "' in event " << schema.id;
                return false;
            }
            schema.fields.push_back(fd);
        }

        LC_LOGD("SchemaParser: parsed event id=" << schema.id << " name=" << schema.name << " fields=" << schema.fields.size());
        mSchemaMap[schema.id] = std::move(schema);
    }

    LOG(INFO) << "SchemaParser: loaded " << mSchemaMap.size() << " events";
    return true;
}

// 通过 event_id 在哈希表中查找对应的 schema 定义
const EventSchema* SchemaParser::find(uint16_t eventId) const
{
    auto it = mSchemaMap.find(eventId);
    return (it != mSchemaMap.end()) ? &it->second : nullptr;
}

// 校验二进制日志记录是否合法：
//   Step 1: 检查是否至少包含固定头（16 字节）
//   Step 2: 检查魔数是否为 0x4C56（'LV'）
//   Step 3: 检查 event_id 是否在 schema 中定义
//   Step 4: 检查 field_count 是否与 schema 定义一致
//   Step 5: 逐个字段检查类型标识是否匹配 schema
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
    if (hdr->field_count != (uint8_t)schema->fields.size()) {
        errMsg = "field count mismatch: expected " +
                 std::to_string(schema->fields.size()) +
                 ", got " + std::to_string(hdr->field_count);
        return false;
    }

    // 逐字段校验类型标识，同时确保数据长度足够
    const uint8_t* ptr = data + sizeof(struct lcview_record_hdr);
    const uint8_t* end = data + len;

    for (size_t i = 0; i < schema->fields.size(); i++) {
        if (ptr >= end) {
            errMsg = "unexpected EOF at field " + std::to_string(i);
            return false;
        }

        uint8_t wireType = *ptr;
        uint8_t expectedType = static_cast<uint8_t>(schema->fields[i].type);

        // 类型匹配性检查：字段头的 type 必须和 schema 中定义的类型一致
        if (wireType != expectedType) {
            errMsg = "type mismatch at field " + std::to_string(i) +
                     ": expected " + std::to_string(expectedType) +
                     ", got " + std::to_string(wireType);
            return false;
        }

        ptr++;

        // 根据类型跳过字段值区域：
        // INT32/FLOAT = 4 字节, INT64 = 8 字节,
        // STRING/BINARY = 2 字节长度前缀 + 变长数据
        switch (wireType) {
        case LCVIEW_TYPE_INT32:
        case LCVIEW_TYPE_FLOAT:
            ptr += 4;
            break;
        case LCVIEW_TYPE_INT64:
            ptr += 8;
            break;
        case LCVIEW_TYPE_STRING:
        case LCVIEW_TYPE_BINARY: {
            if (ptr + 2 > end) {
                errMsg = "unexpected EOF at field len " + std::to_string(i);
                return false;
            }
            uint16_t fieldLen;
            memcpy(&fieldLen, ptr, 2);
            ptr += 2 + fieldLen;
            break;
        }
        default:
            errMsg = "unknown field type " + std::to_string(wireType);
            return false;
        }
    }

    // 验证总长度匹配：确保没有多余或缺失的字节
    // 这是防止数据截断或粘包的最后一道防线
    size_t consumed = ptr - data;
    if (consumed != len) {
        errMsg = "record length mismatch: expected " +
                 std::to_string(consumed) + ", got " + std::to_string(len);
        return false;
    }

    return true;
}
