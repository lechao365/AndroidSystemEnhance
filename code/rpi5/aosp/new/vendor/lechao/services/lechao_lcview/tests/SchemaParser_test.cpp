// SchemaParser_test.cpp — 分支覆盖测试
// 拦截：S9（输入防御）+ S5-daemon（2B 长度前缀小端序）
//
// 分支覆盖目标：
//   SchemaParser::validate 全部 11 个出口分支
//   SchemaParser::parseJson 全部关键字段校验分支
//
// 测试技巧：#define private public 访问 parseJson 私有方法

#define private public
#define protected public
#include <gtest/gtest.h>
#include <endian.h>
#include <cstring>
#include <vector>
#include "SchemaParser.h"
#include "lcview_events.h"
#undef private
#undef protected

namespace {
// 合法 schema 最小样本（1 个 event，含 string + int64 字段）
// 注意：首字段为 string——StringFieldLenEof/DataExceeds 等长度分支测试
// 依赖首字段类型与 schema 匹配，否则先命中 TypeMismatch 不可达
constexpr const char* kValidJson = R"({
  "version": 1,
  "events": [
    {
      "id": 4, "name": "usb_transport_start", "desc": "test event",
      "fields": [
        {"name": "label", "type": "string"},
        {"name": "device_index", "type": "int64"}
      ]
    }
  ]
})";

// 含全部 5 种字段类型的 schema（用于 formatJsonLine 全 case 覆盖的契约）
constexpr const char* kAllFieldTypesJson = R"({
  "events": [{
    "id": 7, "name": "all_types", "desc": "x",
    "fields": [
      {"name": "i32", "type": "int32"},
      {"name": "i64", "type": "int64"},
      {"name": "f",   "type": "float"},
      {"name": "s",   "type": "string"},
      {"name": "b",   "type": "binary"}
    ]
  }]
})";

// 构造一条合法 record（与 schema id=4 匹配：STRING + INT64）
std::vector<uint8_t> buildValidRecord() {
    std::vector<uint8_t> buf(33, 0);
    auto* hdr = reinterpret_cast<lcview_record_hdr*>(buf.data());
    hdr->magic = LCVIEW_MAGIC;
    hdr->event_id = 4;
    hdr->level = LCVIEW_LEVEL_INFO;
    hdr->field_count = 2;
    hdr->reserved = 0;
    hdr->timestamp_ns = 0x1234567890ABCDEF;
    uint8_t* p = buf.data() + sizeof(lcview_record_hdr);
    p[0] = LCVIEW_TYPE_STRING;
    uint16_t len_le = htole16(5);
    memcpy(p + 1, &len_le, 2);
    memcpy(p + 3, "hello", 5);
    p += 8;
    p[0] = LCVIEW_TYPE_INT64;
    int64_t v = 42;
    memcpy(p + 1, &v, 8);
    return buf;
}
}  // namespace

// ============================================================
// SchemaParser::parseJson 分支覆盖（S9 输入防御）
// ============================================================

TEST(SchemaParserParseJsonTest, ValidJson_ParsesAllEvents) {
    SchemaParser sp;
    ASSERT_TRUE(sp.parseJson(kValidJson));
    EXPECT_EQ(sp.eventCount(), 1u);
    const EventSchema* s = sp.find(4);
    ASSERT_NE(s, nullptr);
    EXPECT_EQ(s->name, "usb_transport_start");
    EXPECT_EQ(s->desc, "test event");
    EXPECT_EQ(s->fields.size(), 2u);
}

TEST(SchemaParserParseJsonTest, AllFieldTypes_ParsedCorrectly) {
    SchemaParser sp;
    ASSERT_TRUE(sp.parseJson(kAllFieldTypesJson));
    const EventSchema* s = sp.find(7);
    ASSERT_NE(s, nullptr);
    EXPECT_EQ(s->fields.size(), 5u);
    EXPECT_EQ(s->fields[0].type, FieldType::INT32);
    EXPECT_EQ(s->fields[1].type, FieldType::INT64);
    EXPECT_EQ(s->fields[2].type, FieldType::FLOAT);
    EXPECT_EQ(s->fields[3].type, FieldType::STRING);
    EXPECT_EQ(s->fields[4].type, FieldType::BINARY);
}

TEST(SchemaParserParseJsonTest, MissingVersion_DefaultsToZero) {
    constexpr const char* json = R"({"events": [
        {"id": 1, "name": "x", "fields": []}
    ]})";
    SchemaParser sp;
    EXPECT_TRUE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, WrongVersionType_DefaultsToZero) {
    constexpr const char* json = R"({"version": "not_int", "events": [
        {"id": 1, "name": "x", "fields": []}
    ]})";
    SchemaParser sp;
    EXPECT_TRUE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, EmptyEventsArray_ParsesZero) {
    constexpr const char* json = R"({ "events": [] })";
    SchemaParser sp;
    EXPECT_TRUE(sp.parseJson(json));
    EXPECT_EQ(sp.eventCount(), 0u);
}

TEST(SchemaParserParseJsonTest, EventsNotArray_ReturnsFalse) {
    constexpr const char* json = R"({"events": "not_array"})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, EventsMissing_ReturnsFalse) {
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(R"({"version": 1})"));
}

TEST(SchemaParserParseJsonTest, EventMissingId_ReturnsFalse) {
    constexpr const char* json = R"({"events": [{"name": "no_id", "fields": []}]})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, EventIdWrongType_ReturnsFalse) {
    constexpr const char* json = R"({"events": [{"id": "str", "name": "x", "fields": []}]})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, EventMissingName_ReturnsFalse) {
    constexpr const char* json = R"({"events": [{"id": 1, "fields": []}]})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, EventNameWrongType_ReturnsFalse) {
    constexpr const char* json = R"({"events": [{"id": 1, "name": 123, "fields": []}]})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, EventMissingFields_ReturnsFalse) {
    constexpr const char* json = R"({"events": [{"id": 1, "name": "x"}]})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, FieldsNotArray_ReturnsFalse) {
    constexpr const char* json = R"({"events": [{"id": 1, "name": "x", "fields": "no"}]})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, FieldMissingName_ReturnsFalse) {
    constexpr const char* json = R"({"events": [{
        "id": 1, "name": "x", "fields": [{"type": "int32"}]
    }]})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, FieldNameWrongType_ReturnsFalse) {
    constexpr const char* json = R"({"events": [{
        "id": 1, "name": "x", "fields": [{"name": 123, "type": "int32"}]
    }]})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, FieldMissingType_ReturnsFalse) {
    constexpr const char* json = R"({"events": [{
        "id": 1, "name": "x", "fields": [{"name": "f"}]
    }]})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, FieldTypeWrongType_ReturnsFalse) {
    constexpr const char* json = R"({"events": [{
        "id": 1, "name": "x", "fields": [{"name": "f", "type": 123}]
    }]})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, FieldUnknownTypeName_ReturnsFalse) {
    constexpr const char* json = R"({"events": [{
        "id": 1, "name": "x", "fields": [{"name": "f", "type": "blob"}]
    }]})";
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson(json));
}

TEST(SchemaParserParseJsonTest, DuplicateId_OverwritesWithWarning) {
    constexpr const char* json = R"({"events": [
        {"id": 1, "name": "first", "fields": []},
        {"id": 1, "name": "second", "fields": []}
    ]})";
    SchemaParser sp;
    EXPECT_TRUE(sp.parseJson(json));
    const EventSchema* s = sp.find(1);
    ASSERT_NE(s, nullptr);
    EXPECT_EQ(s->name, "second");
}

TEST(SchemaParserParseJsonTest, MalformedJson_ReturnsFalse) {
    SchemaParser sp;
    EXPECT_FALSE(sp.parseJson("{ invalid json"));
}

TEST(SchemaParserParseJsonTest, FindUnknownId_ReturnsNull) {
    SchemaParser sp;
    ASSERT_TRUE(sp.parseJson(kValidJson));
    EXPECT_EQ(sp.find(999), nullptr);
}

// 方向 3：生产配置（/vendor/etc/lcview_events.json）加载校验——
// 此前 UT 全用内联 kValidJson，真配置不被任何测试加载；
// 上板跑时该路径即生产配置文件（host 环境跳过）
TEST(SchemaParserParseJsonTest, ProductionConfig_LoadsAll10Events) {
    if (access("/vendor/etc/lcview_events.json", R_OK) != 0) {
        GTEST_SKIP() << "生产配置不存在（host 环境）";
        return;
    }
    SchemaParser sp;
    ASSERT_TRUE(sp.loadFromFile("/vendor/etc/lcview_events.json"));
    EXPECT_EQ(sp.eventCount(), 10u);
    // id 4..13 全部定义（与内核 lcview_events.h 一致）
    for (uint16_t id = 4; id <= 13; id++) {
        const EventSchema* s = sp.find(id);
        ASSERT_NE(s, nullptr) << "事件 " << id << " 缺失";
        EXPECT_FALSE(s->name.empty());
        EXPECT_FALSE(s->fields.empty()) << "事件 " << id << " 无字段";
    }
}

// ============================================================
// SchemaParser::validate 分支覆盖（11 个出口分支）
// ============================================================

class SchemaParserValidateTest : public ::testing::Test {
protected:
    void SetUp() override {
        ASSERT_TRUE(sp_.parseJson(kValidJson));
        valid_ = buildValidRecord();
    }
    SchemaParser sp_;
    std::vector<uint8_t> valid_;
};

// 分支 11: 全部通过
TEST_F(SchemaParserValidateTest, ValidRecord_ReturnsTrue) {
    std::string err;
    EXPECT_TRUE(sp_.validate(valid_.data(), valid_.size(), err)) << err;
}

// 分支 1: len < sizeof(hdr)
TEST_F(SchemaParserValidateTest, TooShortForHeader_ReturnsFalse) {
    std::string err;
    EXPECT_FALSE(sp_.validate(valid_.data(), 15, err));
    EXPECT_NE(err.find("too short"), std::string::npos);
}

// 分支 2: magic 错误
TEST_F(SchemaParserValidateTest, BadMagic_ReturnsFalse) {
    auto rec = valid_;
    reinterpret_cast<lcview_record_hdr*>(rec.data())->magic = 0x1234;
    std::string err;
    EXPECT_FALSE(sp_.validate(rec.data(), rec.size(), err));
    EXPECT_NE(err.find("magic"), std::string::npos);
}

// 分支 3: 未知 event_id
TEST_F(SchemaParserValidateTest, UnknownEventId_ReturnsFalse) {
    auto rec = valid_;
    reinterpret_cast<lcview_record_hdr*>(rec.data())->event_id = 999;
    std::string err;
    EXPECT_FALSE(sp_.validate(rec.data(), rec.size(), err));
    EXPECT_NE(err.find("unknown event_id"), std::string::npos);
}

// 分支 4: field_count 不匹配
TEST_F(SchemaParserValidateTest, FieldCountMismatch_ReturnsFalse) {
    auto rec = valid_;
    reinterpret_cast<lcview_record_hdr*>(rec.data())->field_count = 99;
    std::string err;
    EXPECT_FALSE(sp_.validate(rec.data(), rec.size(), err));
    EXPECT_NE(err.find("field count mismatch"), std::string::npos);
}

// 分支 5: ptr >= end（字段区不足）
TEST_F(SchemaParserValidateTest, UnexpectedEofAtField_ReturnsFalse) {
    auto rec = valid_;
    // 截断 hdr 后只留 1 字节（不足以放一个完整字段）
    rec.resize(sizeof(lcview_record_hdr) + 1);
    reinterpret_cast<lcview_record_hdr*>(rec.data())->field_count = 2;
    std::string err;
    EXPECT_FALSE(sp_.validate(rec.data(), rec.size(), err));
    EXPECT_NE(err.find("EOF"), std::string::npos);
}

// 分支 6: 字段类型不匹配
TEST_F(SchemaParserValidateTest, TypeMismatch_ReturnsFalse) {
    auto rec = valid_;
    // 首字段 schema 期望 STRING(4)，改成 INT64(2) → type mismatch
    rec[sizeof(lcview_record_hdr)] = LCVIEW_TYPE_INT64;
    std::string err;
    EXPECT_FALSE(sp_.validate(rec.data(), rec.size(), err));
    EXPECT_NE(err.find("type mismatch"), std::string::npos);
}

// 分支 7: STRING/BINARY 长度前缀本身越界（ptr+2 > end）
TEST_F(SchemaParserValidateTest, StringFieldLenEof_ReturnsFalse) {
    auto rec = valid_;
    // 首字段类型与 schema 匹配（string），但 record 在 type 后立刻截断
    // （缺 2B 长度前缀）→ 读长度时 EOF；field_count 须保持 2 与 schema 一致
    // （此前设 1 会先命中 field count mismatch，此分支不可达）
    rec.resize(sizeof(lcview_record_hdr) + 1);  // 只有 type 字节
    std::string err;
    EXPECT_FALSE(sp_.validate(rec.data(), rec.size(), err));
    EXPECT_NE(err.find("EOF"), std::string::npos);
}

// 分支 8: STRING/BINARY 数据长度超过 record 剩余
TEST_F(SchemaParserValidateTest, StringFieldDataExceeds_ReturnsFalse) {
    // 构造一条 record，STRING 长度前缀声明 100 但实际无数据；
    // field_count 须为 2 与 schema 一致（设 1 会先命中 field count mismatch）
    // （首字段类型 string 与 schema 匹配，走到长度检查——此前首字段 int64
    //  会先命中 TypeMismatch，此分支不可达）
    std::vector<uint8_t> rec(sizeof(lcview_record_hdr) + 3, 0);
    auto* hdr = reinterpret_cast<lcview_record_hdr*>(rec.data());
    hdr->magic = LCVIEW_MAGIC;
    hdr->event_id = 4;
    hdr->field_count = 2;
    hdr->timestamp_ns = 0;
    uint8_t* p = rec.data() + sizeof(lcview_record_hdr);
    p[0] = LCVIEW_TYPE_STRING;
    uint16_t big_le = htole16(100);
    memcpy(p + 1, &big_le, 2);
    std::string err;
    EXPECT_FALSE(sp_.validate(rec.data(), rec.size(), err));
    EXPECT_NE(err.find("exceeds record"), std::string::npos);
}

// 分支 10: consumed != len（总长度不匹配）
TEST_F(SchemaParserValidateTest, LengthMismatch_ReturnsFalse) {
    auto rec = valid_;
    // 在末尾追加 1 字节垃圾数据（record 变长但字段没变）
    rec.push_back(0xFF);
    std::string err;
    EXPECT_FALSE(sp_.validate(rec.data(), rec.size(), err));
    EXPECT_NE(err.find("length mismatch"), std::string::npos);
}

// ============================================================
// S5 字节序分支覆盖
// ============================================================

TEST_F(SchemaParserValidateTest, StringFieldLen_LittleEndian_ValidatesOk) {
    std::string err;
    EXPECT_TRUE(sp_.validate(valid_.data(), valid_.size(), err)) << err;
}

TEST_F(SchemaParserValidateTest, StringFieldLen_BigEndian_Rejected) {
    auto rec = valid_;
    // string 首字段长度前缀位于 sizeof(hdr) + 1(type) = 17（相对 record 起始）
    size_t len_offset = sizeof(lcview_record_hdr) + 1;
    uint16_t be_len = htobe16(5);
    memcpy(rec.data() + len_offset, &be_len, 2);
    std::string err;
    // 大端序 5 = 0x0500，le 读取后变 1280，远超 record 末尾 → exceeds record
    EXPECT_FALSE(sp_.validate(rec.data(), rec.size(), err));
}
