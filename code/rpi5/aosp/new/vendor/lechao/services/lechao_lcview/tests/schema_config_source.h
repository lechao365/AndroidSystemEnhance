// ============================================================
// schema_config_source.h — UT 期望事件集合的单源辅助
// 所属模块：LcView 事件日志系统 — 测试
// 设计目的：生产 schema 配置（/vendor/etc/lcview_events.json）是事件集合
//   的唯一事实源。UT 若硬编码事件数/ID 区间，每次新增事件都要人工同步，
//   漏同步即首跑 fail（批一/批四同根因）。本辅助在测试启动时直接解析同一
//   份配置，期望值由配置生成——新增事件无需改 UT。
// 约束：配置不存在（host 环境）时返回 false，调用方 GTEST_SKIP；
//   解析失败同样返回 false（不得把坏配置当空集合假绿）。
// CXX-003：JSON 字段一律 isMember + isXxx 前置校验，禁 try/catch。
// ============================================================
#ifndef LECHAO_LCVIEW_TESTS_SCHEMA_CONFIG_SOURCE_H_
#define LECHAO_LCVIEW_TESTS_SCHEMA_CONFIG_SOURCE_H_

#include <cstddef>
#include <fstream>
#include <sstream>
#include <string>
#include <vector>

#include <json/json.h>

namespace lechao_test {

// 上板生产配置路径（与 daemon 加载路径一致）
constexpr const char* kSchemaConfigPath = "/vendor/etc/lcview_events.json";

// 配置中单个事件的期望值（id/name/字段数）
struct ExpectedEvent {
    int id = 0;
    std::string name;
    std::size_t fieldCount = 0;
};

// 读取配置事件集合 → out。
// 返回 false 表示配置不可用（不存在/解析失败/结构非法），out 保持为空。
inline bool loadExpectedEvents(std::vector<ExpectedEvent>* out) {
    if (out == nullptr) {
        return false;
    }
    out->clear();

    std::ifstream file(kSchemaConfigPath);
    if (!file.is_open()) {
        return false;
    }
    std::stringstream buf;
    buf << file.rdbuf();

    Json::Value root;
    Json::CharReaderBuilder builder;
    builder.settings_["allowComments"] = false;
    builder.settings_["strictRoot"] = false;
    std::string errors;
    std::istringstream iss(buf.str());
    if (!Json::parseFromStream(builder, iss, &root, &errors)) {
        return false;
    }
    if (!root.isObject() || !root.isMember("events")
            || !root["events"].isArray()) {
        return false;
    }

    std::vector<ExpectedEvent> parsed;
    for (const auto& ev : root["events"]) {
        if (!ev.isObject() || !ev.isMember("id") || !ev["id"].isInt()
                || !ev.isMember("name") || !ev["name"].isString()) {
            return false;
        }
        ExpectedEvent expected;
        expected.id = ev["id"].asInt();
        expected.name = ev["name"].asString();
        if (ev.isMember("fields")) {
            if (!ev["fields"].isArray()) {
                return false;
            }
            expected.fieldCount = ev["fields"].size();
        }
        parsed.push_back(expected);
    }
    out->swap(parsed);
    return true;
}

}  // namespace lechao_test

#endif  // LECHAO_LCVIEW_TESTS_SCHEMA_CONFIG_SOURCE_H_
