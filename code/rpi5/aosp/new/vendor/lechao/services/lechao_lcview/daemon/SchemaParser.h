// ============================================================
// SchemaParser.h — 事件 schema 解析器头文件
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：声明 SchemaParser 类，负责从 JSON 配置文件加载
//   事件 schema 定义，并提供按 event_id 查找和二进制记录校验功能。
//   同时定义了 FieldType/FieldDef/EventSchema 等核心数据类型。
// ============================================================

#pragma once

#include <string>
#include <vector>
#include <unordered_map>
#include <cstdint>

// 字段类型枚举，值与 lcview_events.h 中的 LCVIEW_TYPE_* 宏一致。
// UNKNOWN 用于解析时遇到未知类型字符串的兜底值。
enum class FieldType {
    INT32 = 1,
    INT64 = 2,
    FLOAT = 3,
    STRING = 4,
    BINARY = 5,
    UNKNOWN = 0xFF
};

// 字段定义：包含字段名和类型
struct FieldDef {
    std::string name;
    FieldType type;
};

// 事件 schema：描述一种事件的所有元信息
// id — 事件唯一标识（与 lcview_record_hdr.event_id 对应）
// name — 事件名称（用于文件命名和日志输出）
// desc — 事件描述文本
// fields — 字段定义列表（顺序必须与二进制记录中字段出现的顺序完全一致）
struct EventSchema {
    uint16_t id;
    std::string name;
    std::string desc;
    std::vector<FieldDef> fields;
};

// SchemaParser 类：
// 职责三合一：加载、查找、校验。
// 为什么不是分离为 Loader/Validator 两个类：
//   当前校验逻辑依赖 schema 的字段类型和数量信息，在同一个类中
//   保持数据内聚，减少不必要的公开接口。
class SchemaParser {
public:
    // 从文件加载 schema，失败返回 false
    bool loadFromFile(const std::string& path);
    // 热重载 schema：先备份当前 schema，加载新版本失败时自动回滚。
    // 设计目的是支持运行时更新配置而不重启 daemon。
    bool reload(const std::string& path);

    // 按 event_id 查找 schema，未找到返回 nullptr
    const EventSchema* find(uint16_t eventId) const;

    // 校验二进制记录是否匹配 schema 定义：
    //   检查 magic、event_id、field_count、每个字段的类型、
    //   以及总长度一致性。
    bool validate(const uint8_t* data, size_t len, std::string& errMsg) const;

    // 返回已加载的 schema 数量
    size_t eventCount() const { return mSchemaMap.size(); }

private:
    // 解析 JSON 字符串内容到 mSchemaMap
    bool parseJson(const std::string& jsonContent);

    // 使用哈希表以 O(1) 复杂度按 event_id 查找 schema
    std::unordered_map<uint16_t, EventSchema> mSchemaMap;
    // schema 版本号（从 JSON 配置读取），为将来迁移预留
    int mVersion = 0;
};
