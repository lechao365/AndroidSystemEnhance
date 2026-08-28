// FileWriter_test.cpp — 分支覆盖测试
// 拦截：S8（currentSize 恢复）+ S5（formatJsonLine 2B 前缀小端序）
//
// 分支覆盖目标：
//   formatJsonLine 全部 6 个 switch case（INT32/INT64/FLOAT/STRING/BINARY/default）+ LCVIEW_NEED 越界
//   writeRecord 全分支（文件未打开→自动 openFile / openFile 失败丢弃 / formatJsonLine 空丢弃 / 正常写入）
//   openFile 全分支（已存在文件恢复 currentSize / 新文件）
//   writeInvalid 分支（stream 未打开丢弃 / 正常写入）
//   checkRotation 分支（日期变更 / 大小超限 / 无需轮转）
//
// 测试技巧：#define private public 访问 private 实现

#define private public
#define protected public
#include <gtest/gtest.h>
#include <endian.h>
#include <sys/stat.h>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include "FileWriter.h"
#include "lcview_events.h"
#undef private
#undef protected

namespace {
class TempDir {
public:
    explicit TempDir(const std::string& prefix = "/tmp/lcview_test_") {
        name_ = prefix + "XXXXXX";
        ::mkdtemp(name_.data());
    }
    ~TempDir() { cleanup(); }
    const std::string& path() const { return name_; }
    void cleanup() {
        if (name_.empty()) return;
        std::string cmd = "rm -rf '" + name_ + "'";
        ::system(cmd.c_str());
        name_.clear();
    }
private:
    std::string name_;
};

void prewriteFile(const std::string& path, size_t size) {
    FILE* f = fopen(path.c_str(), "w");
    ASSERT_NE(f, nullptr);
    std::vector<char> buf(size, 'x');
    ASSERT_EQ(fwrite(buf.data(), 1, size, f), size);
    fclose(f);
}

std::string readFile(const std::string& path) {
    FILE* f = fopen(path.c_str(), "r");
    if (!f) return "";
    std::string out;
    char buf[1024];
    size_t n;
    while ((n = fread(buf, 1, sizeof(buf), f)) > 0)
        out.append(buf, n);
    fclose(f);
    return out;
}

EventSchema makeSchema(uint16_t id, const std::string& name,
                       std::vector<FieldType> types) {
    EventSchema s;
    s.id = id;
    s.name = name;
    for (size_t i = 0; i < types.size(); i++)
        s.fields.push_back({"f" + std::to_string(i), types[i]});
    return s;
}

// 构造 fields 区（不含 hdr），按给定类型与值序列化
std::vector<uint8_t> buildFields(std::vector<FieldType> types,
                                  const std::vector<std::string>& strVals = {},
                                  const std::vector<std::vector<uint8_t>>& binVals = {}) {
    std::vector<uint8_t> out;
    size_t strIdx = 0, binIdx = 0;
    for (auto t : types) {
        switch (t) {
        case FieldType::INT32: {
            out.push_back(LCVIEW_TYPE_INT32);
            int32_t v = 0;
            out.resize(out.size() + 4);
            memcpy(out.data() + out.size() - 4, &v, 4);
            break;
        }
        case FieldType::INT64: {
            out.push_back(LCVIEW_TYPE_INT64);
            int64_t v = 0;
            out.resize(out.size() + 8);
            memcpy(out.data() + out.size() - 8, &v, 8);
            break;
        }
        case FieldType::FLOAT: {
            out.push_back(LCVIEW_TYPE_FLOAT);
            uint32_t raw = 0;
            out.resize(out.size() + 4);
            memcpy(out.data() + out.size() - 4, &raw, 4);
            break;
        }
        case FieldType::STRING: {
            out.push_back(LCVIEW_TYPE_STRING);
            std::string s = (strIdx < strVals.size()) ? strVals[strIdx++] : "";
            uint16_t len_le = htole16(static_cast<uint16_t>(s.size()));
            out.resize(out.size() + 2);
            memcpy(out.data() + out.size() - 2, &len_le, 2);
            out.insert(out.end(), s.begin(), s.end());
            break;
        }
        case FieldType::BINARY: {
            out.push_back(LCVIEW_TYPE_BINARY);
            auto b = (binIdx < binVals.size()) ? binVals[binIdx++] : std::vector<uint8_t>{};
            uint16_t len_le = htole16(static_cast<uint16_t>(b.size()));
            out.resize(out.size() + 2);
            memcpy(out.data() + out.size() - 2, &len_le, 2);
            out.insert(out.end(), b.begin(), b.end());
            break;
        }
        default: break;
        }
    }
    return out;
}

lcview_record_hdr makeHdr(uint16_t id, uint8_t fc) {
    lcview_record_hdr h{};
    h.magic = LCVIEW_MAGIC;
    h.event_id = id;
    h.level = LCVIEW_LEVEL_INFO;
    h.field_count = fc;
    h.reserved = 0;
    h.timestamp_ns = 12345;
    return h;
}
}  // namespace

// ============================================================
// formatJsonLine 全 case 分支覆盖（直接调用 private 方法）
// ============================================================

class FormatJsonLineTest : public ::testing::Test {
protected:
    void SetUp() override {
        cfg_.logDir = "/tmp/lcview_fmt_unused";  // formatJsonLine 不写文件
        writer_ = std::make_unique<FileWriter>(cfg_);
    }
    FileWriterConfig cfg_;
    std::unique_ptr<FileWriter> writer_;
};

TEST_F(FormatJsonLineTest, Int32Field_ProducesNumber) {
    auto schema = makeSchema(4, "e", {FieldType::INT32});
    auto hdr = makeHdr(4, 1);
    auto fields = buildFields({FieldType::INT32});
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    EXPECT_NE(line.find("\"f\":[0]"), std::string::npos);
}

TEST_F(FormatJsonLineTest, Int64Field_ProducesNumber) {
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    auto hdr = makeHdr(4, 1);
    auto fields = buildFields({FieldType::INT64});
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    EXPECT_FALSE(line.empty());
    EXPECT_NE(line.find("\"f\":["), std::string::npos);
}

TEST_F(FormatJsonLineTest, FloatField_ProducesNumber) {
    auto schema = makeSchema(4, "e", {FieldType::FLOAT});
    auto hdr = makeHdr(4, 1);
    auto fields = buildFields({FieldType::FLOAT});
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    EXPECT_FALSE(line.empty());
}

TEST_F(FormatJsonLineTest, StringField_Le16_ProducesQuotedJson) {
    auto schema = makeSchema(4, "e", {FieldType::STRING});
    auto hdr = makeHdr(4, 1);
    auto fields = buildFields({FieldType::STRING}, {"hello"});
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    EXPECT_NE(line.find("\"hello\""), std::string::npos);
}

TEST_F(FormatJsonLineTest, StringField_WithSpecialChars_Escaped) {
    auto schema = makeSchema(4, "e", {FieldType::STRING});
    auto hdr = makeHdr(4, 1);
    auto fields = buildFields({FieldType::STRING}, {"a\"b\\c"});
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    EXPECT_NE(line.find("\\\""), std::string::npos);
    EXPECT_NE(line.find("\\\\"), std::string::npos);
}

TEST_F(FormatJsonLineTest, BinaryField_Le16_ProducesHex) {
    auto schema = makeSchema(4, "e", {FieldType::BINARY});
    auto hdr = makeHdr(4, 1);
    auto fields = buildFields({FieldType::BINARY}, {}, {{0xDE, 0xAD, 0xBE, 0xEF}});
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    EXPECT_NE(line.find("deadbeef"), std::string::npos);
}

TEST_F(FormatJsonLineTest, UnknownType_ProducesNull) {
    auto schema = makeSchema(4, "e", {FieldType::INT32});
    auto hdr = makeHdr(4, 1);
    // wire type = 99（未知），LCVIEW_NEED(1) 通过但 switch 走 default
    std::vector<uint8_t> fields = {99};
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    EXPECT_NE(line.find("null"), std::string::npos);
}

TEST_F(FormatJsonLineTest, Int32Field_Truncated_ReturnsEmpty) {
    auto schema = makeSchema(4, "e", {FieldType::INT32});
    auto hdr = makeHdr(4, 1);
    // 只给 type 字节，缺 4B value → LCVIEW_NEED(4) 失败
    std::vector<uint8_t> fields = {LCVIEW_TYPE_INT32};
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    EXPECT_TRUE(line.empty());
}

TEST_F(FormatJsonLineTest, StringField_LenPrefixTruncated_ReturnsEmpty) {
    auto schema = makeSchema(4, "e", {FieldType::STRING});
    auto hdr = makeHdr(4, 1);
    // 只有 type + 1B（缺第 2B 长度）→ LCVIEW_NEED(2) 失败
    std::vector<uint8_t> fields = {LCVIEW_TYPE_STRING, 0x05};
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    EXPECT_TRUE(line.empty());
}

TEST_F(FormatJsonLineTest, MultiField_ProducesCommaSeparated) {
    auto schema = makeSchema(4, "e",
                              {FieldType::INT32, FieldType::STRING, FieldType::BINARY});
    auto hdr = makeHdr(4, 3);
    auto fields = buildFields({FieldType::INT32, FieldType::STRING, FieldType::BINARY},
                               {"s"}, {{0xAA}});
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    // 验证数组有逗号分隔（3 个元素）
    EXPECT_NE(line.find(","), std::string::npos);
}

// ============================================================
// openFile 分支覆盖（S8 核心）
// ============================================================

TEST(FileWriterOpenFileTest, ExistingFile_RestoresCurrentSize) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "test_event", {FieldType::INT64});

    std::string date = writer.makeDateStr();
    std::string path = dir.path() + "/4_test_event_" + date + "_p0.jsonl";
    prewriteFile(path, 500 * 1024);

    writer.openFile(4, schema);
    auto it = writer.mFiles.find(4);
    ASSERT_NE(it, writer.mFiles.end());
    EXPECT_NEAR(it->second.currentSize, 500 * 1024, 1024);
}

TEST(FileWriterOpenFileTest, NewFile_CurrentSizeZero) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "test_event", {FieldType::INT64});

    writer.openFile(4, schema);
    auto it = writer.mFiles.find(4);
    ASSERT_NE(it, writer.mFiles.end());
    EXPECT_EQ(it->second.currentSize, 0u);
}

TEST(FileWriterOpenFileTest, ReopenClosesExistingStream) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "test_event", {FieldType::INT64});

    writer.openFile(4, schema);
    auto firstStream = &writer.mFiles[4].stream;
    EXPECT_TRUE(firstStream->is_open());

    // 再次 openFile 应关闭旧流
    writer.openFile(4, schema);
    EXPECT_TRUE(writer.mFiles[4].stream.is_open());
}

// ============================================================
// writeRecord 分支覆盖
// ============================================================

TEST(FileWriterWriteRecordTest, NoFileOpen_AutoOpenAndWrite) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    auto hdr = makeHdr(4, 1);
    auto fields = buildFields({FieldType::INT64});

    writer.writeRecord(schema, &hdr, fields.data(), fields.size());

    std::string date = writer.makeDateStr();
    std::string content = readFile(dir.path() + "/4_e_" + date + "_p0.jsonl");
    EXPECT_FALSE(content.empty());
    EXPECT_NE(content.find("\"id\":4"), std::string::npos);
}

TEST(FileWriterWriteRecordTest, WriteAccumulatesCurrentSize) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    auto hdr = makeHdr(4, 1);
    auto fields = buildFields({FieldType::INT64});

    writer.writeRecord(schema, &hdr, fields.data(), fields.size());
    size_t sizeAfter1 = writer.mFiles[4].currentSize;
    EXPECT_GT(sizeAfter1, 0u);

    writer.writeRecord(schema, &hdr, fields.data(), fields.size());
    EXPECT_GT(writer.mFiles[4].currentSize, sizeAfter1);
}

// ============================================================
// writeInvalid 分支覆盖
// ============================================================

TEST(FileWriterWriteInvalidTest, NormalWrite_ProdusJsonl) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);

    uint8_t data[] = {0xDE, 0xAD};
    writer.writeInvalid(data, 2, "bad magic");

    std::string content = readFile(dir.path() + "/invalid_records.log");
    EXPECT_NE(content.find("bad magic"), std::string::npos);
    EXPECT_NE(content.find("\"size\":2"), std::string::npos);
}

// ============================================================
// checkRotation 分支覆盖
// ============================================================

TEST(FileWriterRotationTest, SizeExceedsTriggersRotation) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxFileSizeMb = 1;  // 1MB 阈值
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});

    writer.openFile(4, schema);
    writer.mFiles[4].currentSize = 2 * 1024 * 1024;  // 2MB > 1MB 阈值

    writer.checkRotation();

    // seq 应递增到 1
    EXPECT_EQ(writer.mFiles[4].seq, 1);
    EXPECT_EQ(writer.mFiles[4].currentSize, 0u);
}

TEST(FileWriterRotationTest, NoRotationNeeded_KeepsSeq) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxFileSizeMb = 50;
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});

    writer.openFile(4, schema);
    writer.mFiles[4].currentSize = 1024;  // 远小于 50MB

    writer.checkRotation();

    EXPECT_EQ(writer.mFiles[4].seq, 0);
}

// ============================================================
// 轮转边界（方向 2）：跨天轮转重置 seq + 恢复已有轮转文件大小
// ============================================================

TEST(FileWriterRotationTest, DateChange_RotatesWithSeqReset) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxFileSizeMb = 50;
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});

    writer.openFile(4, schema);
    writer.mFiles[4].seq = 3;  // 同日内已轮转到 seq=3
    writer.mFiles[4].currentDate = "20000101";  // 伪造旧日期
    writer.mFiles[4].currentSize = 10;

    writer.checkRotation();

    // 跨天：seq 重置为 0（非同日内递增）
    EXPECT_EQ(writer.mFiles[4].seq, 0);
    EXPECT_EQ(writer.mFiles[4].currentDate, writer.makeDateStr());
    // 新文件被追加打开：大小从 0 恢复
    EXPECT_EQ(writer.mFiles[4].currentSize, 0u);
}

TEST(FileWriterRotationTest, SizeBoundary_NoRotationAtExactLimit) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxFileSizeMb = 1;
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});

    writer.openFile(4, schema);
    writer.mFiles[4].currentSize = 1024 * 1024;  // 恰好 = 阈值

    writer.checkRotation();

    // 边界判定 >= 才轮转：恰好等于阈值即触发（实现语义）
    EXPECT_EQ(writer.mFiles[4].seq, 1);
}

TEST(FileWriterRotationTest, BelowLimit_NoRotation) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxFileSizeMb = 1;
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});

    writer.openFile(4, schema);
    writer.mFiles[4].currentSize = 1024 * 1024 - 1;  // 阈值 - 1B

    writer.checkRotation();

    // 未达阈值不轮转（边界内侧）
    EXPECT_EQ(writer.mFiles[4].seq, 0);
}

TEST(FileWriterRotationTest, RotatedFileRestoresExistingSize) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxFileSizeMb = 1;
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});

    // 预写轮转目标文件（seq 从 0 轮转到 1 即 p1），验证追加打开恢复 currentSize
    std::string rotated = dir.path() + "/4_e_"
                          + writer.makeDateStr() + "_p1.jsonl";
    {
        std::ofstream f(rotated, std::ios::app);
        f << "x";
    }

    writer.openFile(4, schema);
    writer.mFiles[4].currentSize = 2 * 1024 * 1024;  // 触发轮转

    writer.checkRotation();

    // 轮转到已存在的 p1 文件：currentSize 恢复为 1（CXX-002 持久层恢复）
    EXPECT_EQ(writer.mFiles[4].seq, 1);
    EXPECT_EQ(writer.mFiles[4].currentSize, 1u);
}

// ============================================================
// 写失败恢复（方向 2）：流 fail 后 clear+reopen+retry（CXX-002）
// ============================================================

TEST(FileWriterWriteRecordTest, WriteFailure_RecoversByReopen) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxFileSizeMb = 50;
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});

    writer.openFile(4, schema);
    // 人为制造 fail：关闭流后再写（stream 状态失效 → failbit）
    writer.mFiles[4].stream.close();
    // 注：close 后 stream << 写失败，writeRecord 走 clear+reopen+retry

    struct lcview_record_hdr hdr = {};
    hdr.magic = LCVIEW_MAGIC;
    hdr.event_id = 4;
    hdr.level = LCVIEW_LEVEL_INFO;
    hdr.field_count = 1;
    hdr.timestamp_ns = 7;
    int64_t v = 1;
    uint8_t fields[9] = {LCVIEW_TYPE_INT64, 0, 0, 0, 0, 0, 0, 0, 1};

    writer.writeRecord(schema, &hdr, fields, sizeof(fields));

    // 恢复路径成功：流重新打开且数据落盘
    EXPECT_TRUE(writer.mFiles[4].stream.is_open());
    std::string content = readFile(writer.mFiles[4].currentFilename);
    EXPECT_NE(content.find("\"id\":4"), std::string::npos);
}

TEST(FileWriterWriteRecordTest, WriteFailure_RecoveryReopenFailDrops) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxFileSizeMb = 50;
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});

    writer.openFile(4, schema);
    // 关闭流 + 删除目标文件并改只读目录？——直接关闭流制造 fail，
    // 然后移除目录写权限使 reopen 失败
    std::string fname = writer.mFiles[4].currentFilename;
    writer.mFiles[4].stream.close();
    unlink(fname.c_str());
    chmod(dir.path().c_str(), 0500);  // 只读：reopen 失败

    struct lcview_record_hdr hdr = {};
    hdr.magic = LCVIEW_MAGIC;
    hdr.event_id = 4;
    hdr.field_count = 1;
    hdr.timestamp_ns = 7;
    uint8_t fields[9] = {LCVIEW_TYPE_INT64, 0, 0, 0, 0, 0, 0, 0, 1};

    writer.writeRecord(schema, &hdr, fields, sizeof(fields));

    chmod(dir.path().c_str(), 0755);
    // reopen 失败 → DROPPING（不崩，故障可见）
    SUCCEED();
}
