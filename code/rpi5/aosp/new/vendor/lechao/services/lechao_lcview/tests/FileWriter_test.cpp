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
#include <utime.h>
#include <unistd.h>
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

TEST_F(FormatJsonLineTest, StringField_WithNewline_ProducesValidJson) {
    // P0 修复：USB 描述符等字符串含换行时原实现输出裸 \n 裂行（一行被拆成
    // 两行，json.loads 失败）；修复后 \n 具名转义，输出为合法 JSONL
    auto schema = makeSchema(4, "e", {FieldType::STRING});
    auto hdr = makeHdr(4, 1);
    auto fields = buildFields({FieldType::STRING}, {"line1\nline2"});
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    // 具名转义存在（反斜杠 + n 两个字符，非裸换行）
    EXPECT_NE(line.find("\\n"), std::string::npos);
    // 引号内无裸换行：整行除行尾 \n 外不得再有换行
    size_t nl = line.find('\n');
    EXPECT_NE(nl, std::string::npos);
    EXPECT_EQ(line.find('\n', nl + 1), std::string::npos);
}

TEST_F(FormatJsonLineTest, StringField_ControlChars_EscapeUnicode) {
    // 其余 < 0x20 控制字符（如 0x01）按 \u00XX 转义，输出仍为合法 JSON
    auto schema = makeSchema(4, "e", {FieldType::STRING});
    auto hdr = makeHdr(4, 1);
    std::string s;
    s.push_back('\x01');
    auto fields = buildFields({FieldType::STRING}, {s});
    auto line = writer_->formatJsonLine(schema, &hdr, fields.data(), fields.size());
    EXPECT_NE(line.find("\\u0001"), std::string::npos);
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
    // nextSeqFor 语义（重启续接）：mFiles 空时 openFile 打开更高 seq 的新文件
    // （不追加旧 _p0，避免轮转文件名混乱）；currentSize 恢复只适用于同进程
    // reopen（mFiles 已有该 event，seq 保持）——模拟流异常关闭后的重开场景
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "test_event", {FieldType::INT64});

    std::string date = writer.makeDateStr();
    // 首开创建 _p0 并写入 500KB
    writer.openFile(4, schema);
    std::string p0 = dir.path() + "/4_test_event_" + date + "_p0.jsonl";
    prewriteFile(p0, 500 * 1024);
    writer.mFiles[4].stream.close();  // 模拟流异常关闭

    writer.openFile(4, schema);  // mFiles 已有 → 同 seq 打开 _p0 → stat 恢复 size
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

TEST(FileWriterWriteInvalidTest, ReasonWithNewline_EscapesJson) {
    // reason 转义并入 jsonEscapeString（与 formatJsonLine 同规则）：
    // 含换行的 reason 不得裂行，输出仍为合法 JSONL
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);

    uint8_t data[] = {0xDE, 0xAD};
    writer.writeInvalid(data, 2, "bad\nmagic");

    std::string content = readFile(dir.path() + "/invalid_records.log");
    // 具名转义存在（反斜杠 + n）
    EXPECT_NE(content.find("bad\\nmagic"), std::string::npos);
    // 无裂行：整行除行尾 \n 外不得再有换行
    size_t nl = content.find('\n');
    EXPECT_NE(nl, std::string::npos);
    EXPECT_EQ(content.find('\n', nl + 1), std::string::npos);
}

TEST(FileWriterWriteInvalidTest, WriteFail_RecoversByReopen) {
    // P0 修复：failbit 粘滞使首写失败后 invalid 流余生空转（mode_invalid
    // 反判绿）；恢复路径 clear+reopen+retry 自愈，坏记录仍落盘。
    // 首写流指向 /dev/full（is_open 但写必 ENOSPC 设 failbit），
    // mInvalidFilename 保持正常路径 → 恢复 reopen 打开正常文件 retry 成功
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);

    writer.mInvalidStream.close();
    writer.mInvalidStream.open("/dev/full", std::ios::app);
    ASSERT_TRUE(writer.mInvalidStream.is_open());

    uint8_t data[] = {0xDE, 0xAD};
    writer.writeInvalid(data, 2, "broken");

    // 恢复路径成功：流重开回 invalid_records.log 且数据落盘，无 DROP 计数
    EXPECT_TRUE(writer.mInvalidStream.is_open());
    EXPECT_FALSE(writer.mInvalidStream.fail());
    EXPECT_EQ(writer.dropCounters().invalidWriteFailed, 0);
    std::string content = readFile(dir.path() + "/invalid_records.log");
    EXPECT_NE(content.find("broken"), std::string::npos);
}

TEST(FileWriterWriteInvalidTest, WriteFail_RetryFail_CountsAndClearsSticky) {
    // retry 仍失败（reopen 目标 /dev/full 恒 ENOSPC）→ invalidWriteFailed +1
    // （进心跳 dropped 求和与 drop_invalidwrite 分项）；failbit 被 clear，
    // 粘滞清除不空转（mode_invalid 不反判绿）
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);

    writer.mInvalidFilename = "/dev/full";
    writer.mInvalidStream.close();
    writer.mInvalidStream.open("/dev/full", std::ios::app);
    ASSERT_TRUE(writer.mInvalidStream.is_open());

    uint8_t data[] = {0x01, 0x02};
    writer.writeInvalid(data, 2, "broken");

    EXPECT_EQ(writer.dropCounters().invalidWriteFailed, 1);
    EXPECT_EQ(writer.dropCounters().invalidNotOpen, 0);
    EXPECT_FALSE(writer.mInvalidStream.fail());
    SUCCEED();
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

    // 首开（目录空 → seq=0 创建 _p0），再预写轮转目标 _p1（seq 0→1）
    writer.openFile(4, schema);
    std::string rotated = dir.path() + "/4_e_"
                          + writer.makeDateStr() + "_p1.jsonl";
    {
        std::ofstream f(rotated, std::ios::app);
        f << "x";
    }
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

TEST(FileWriterWriteRecordTest, OpenFileFails_Drops) {
    // 方向 2：首次 openFile 打开失败（只读目录）→ writeRecord DROPPING，
    // 不崩、不落盘、mFiles 无该 event（open 失败不静默通过）
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxFileSizeMb = 50;
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    auto hdr = makeHdr(4, 1);
    auto fields = buildFields({FieldType::INT64});

    chmod(dir.path().c_str(), 0500);  // 只读：openFile 的 stream.open 失败
    writer.writeRecord(schema, &hdr, fields.data(), fields.size());
    chmod(dir.path().c_str(), 0755);

    EXPECT_EQ(writer.mFiles.find(4), writer.mFiles.end());
    // 方向 2：DROP 不再只有 ALOGE——openFailed 计数 +1
    EXPECT_EQ(writer.dropCounters().openFailed, 1);
    SUCCEED();
}

TEST(FileWriterWriteRecordTest, RetryWriteFails_Drops) {
    // 方向 2：恢复路径 reopen 成功但 retry 二次写失败（/dev/full 恒 ENOSPC）
    // → DROPPING + clear（failbit 粘滞清除，后续不永久 DROP）
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxFileSizeMb = 50;
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});

    writer.openFile(4, schema);
    // 制造 failbit：关闭流后直接 open /dev/full（is_open true，写必 ENOSPC）
    writer.mFiles[4].stream.close();
    writer.mFiles[4].currentFilename = "/dev/full";
    writer.mFiles[4].stream.open("/dev/full", std::ios::app);
    ASSERT_TRUE(writer.mFiles[4].stream.is_open());

    struct lcview_record_hdr hdr = {};
    hdr.magic = LCVIEW_MAGIC;
    hdr.event_id = 4;
    hdr.field_count = 1;
    hdr.timestamp_ns = 7;
    int64_t v = 1;
    uint8_t fields[9] = {LCVIEW_TYPE_INT64, 0, 0, 0, 0, 0, 0, 0, 1};
    (void)v;

    writer.writeRecord(schema, &hdr, fields, sizeof(fields));

    // 不崩；二次失败后 failbit 被 clear（恢复路径清除粘滞）
    EXPECT_FALSE(writer.mFiles[4].stream.fail());
    // 方向 2：retry 二次写失败 → retryFailed 计数 +1（reopen 成功不算 drop）
    EXPECT_EQ(writer.dropCounters().retryFailed, 1);
    EXPECT_EQ(writer.dropCounters().reopenFailed, 0);
    SUCCEED();
}

// ============================================================
// DROP 分类计数（方向 2）：六条 DROP 路径各有计数，进 daemon 心跳，
// conserve 判红后可定位丢在哪一条
// ============================================================

TEST(FileWriterDropCountTest, FormatOob_Counts) {
    // formatJsonLine 字段越界（数据不足）→ formatOob +1，返回空串
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64, FieldType::INT64});
    auto hdr = makeHdr(4, 1);
    // 字段数据只有 1 字节（schema 需 8+8），触发 LCVIEW_NEED 越界
    std::vector<uint8_t> fields = {LCVIEW_TYPE_INT64};

    auto line = writer.formatJsonLine(schema, &hdr, fields.data(), fields.size());
    EXPECT_TRUE(line.empty());
    EXPECT_EQ(writer.dropCounters().formatOob, 1);
    SUCCEED();
}

TEST(FileWriterDropCountTest, WriteRecord_BadData_CountsFormat) {
    // writeRecord 传坏数据：formatOob + formatEmpty 各 +1（越界返回空 → 空丢弃）
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64, FieldType::INT64});
    auto hdr = makeHdr(4, 1);
    std::vector<uint8_t> fields = {LCVIEW_TYPE_INT64};  // 越界

    writer.writeRecord(schema, &hdr, fields.data(), fields.size());
    EXPECT_EQ(writer.dropCounters().formatOob, 1);
    EXPECT_EQ(writer.dropCounters().formatEmpty, 1);
    SUCCEED();
}

TEST(FileWriterDropCountTest, InvalidNotOpen_Counts) {
    // writeInvalid 时 invalid 流未打开 → invalidNotOpen +1
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    writer.mInvalidStream.close();  // 模拟 invalid 流打开失败/已关闭

    uint8_t data[4] = {0x01, 0x02, 0x03, 0x04};
    writer.writeInvalid(data, sizeof(data), "broken");
    EXPECT_EQ(writer.dropCounters().invalidNotOpen, 1);
    SUCCEED();
}

// ============================================================
// enforceRetention 全分支（方向 2）：LRU 容量淘汰
// ============================================================

TEST(FileWriterRetentionTest, DeletesOldestUntilUnderLimit) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxTotalSizeMb = 1;  // 1MB 上限
    cfg.retentionScanEveryWrites = 0;  // 关闭降频：显式调用即扫描
    FileWriter writer(cfg);
    std::string date = writer.makeDateStr();
    // 三个旧文件 600K/300K/200K 共 1.1MB > 1MB，删最旧 600K 后达标
    std::string f1 = dir.path() + "/1_a_" + date + "_p0.jsonl";
    std::string f2 = dir.path() + "/2_b_" + date + "_p0.jsonl";
    std::string f3 = dir.path() + "/3_c_" + date + "_p0.jsonl";
    prewriteFile(f1, 600 * 1024);
    prewriteFile(f2, 300 * 1024);
    prewriteFile(f3, 200 * 1024);
    struct utimbuf tb;
    tb.actime = 1000; tb.modtime = 1000; utime(f1.c_str(), &tb);
    tb.actime = 2000; tb.modtime = 2000; utime(f2.c_str(), &tb);
    tb.actime = 3000; tb.modtime = 3000; utime(f3.c_str(), &tb);

    writer.enforceRetention();

    EXPECT_NE(access(f1.c_str(), F_OK), 0);  // 最旧被删
    EXPECT_EQ(access(f2.c_str(), F_OK), 0);
    EXPECT_EQ(access(f3.c_str(), F_OK), 0);
}

TEST(FileWriterRetentionTest, SkipsCurrentlyOpenFile) {
    // 当前正在写入的文件（mFiles 持有）不得被删——删除后 writeRecord 写
    // 已删 inode，空间泄漏直至进程退出（CXX-002）
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxTotalSizeMb = 1;
    cfg.retentionScanEveryWrites = 0;  // 关闭降频：显式调用即扫描
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    std::string date = writer.makeDateStr();
    std::string cur = dir.path() + "/4_e_" + date + "_p0.jsonl";
    prewriteFile(cur, 700 * 1024);
    writer.openFile(4, schema);  // 打开（fstat 恢复 700K），在 mFiles 中
    std::string old = dir.path() + "/9_z_" + date + "_p0.jsonl";
    prewriteFile(old, 500 * 1024);
    struct utimbuf tb;
    tb.actime = 100; tb.modtime = 100; utime(old.c_str(), &tb);

    writer.enforceRetention();

    // 总 1.2MB > 1MB：跳过打开的 cur → 删最旧 old（500K）后 700K 达标
    EXPECT_EQ(access(cur.c_str(), F_OK), 0);
    EXPECT_NE(access(old.c_str(), F_OK), 0);
}

TEST(FileWriterRetentionTest, SkipsInvalidRecordsLog) {
    // invalid_records.log 被 mInvalidStream 持有：unlink 后 fd 继续写已删
    // inode 泄漏空间（CXX-002），必须跳过
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxTotalSizeMb = 1;
    cfg.retentionScanEveryWrites = 0;  // 关闭降频：显式调用即扫描
    FileWriter writer(cfg);
    std::string date = writer.makeDateStr();
    std::string invalid = dir.path() + "/invalid_records.log";
    {
        std::ofstream f(invalid, std::ios::app);
        f << std::string(700 * 1024, 'x');
    }
    struct utimbuf tb;
    tb.actime = 100; tb.modtime = 100; utime(invalid.c_str(), &tb);
    std::string other = dir.path() + "/9_z_" + date + "_p0.jsonl";
    prewriteFile(other, 500 * 1024);

    writer.enforceRetention();

    // invalid 跳过 → 删 other（500K），剩余 700K 达标
    EXPECT_EQ(access(invalid.c_str(), F_OK), 0);
    EXPECT_NE(access(other.c_str(), F_OK), 0);
}

TEST(FileWriterRetentionTest, UnlinkFailed_NoCrash) {
    // unlink 失败（只读目录）：打印错误不崩，文件保留
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxTotalSizeMb = 1;
    cfg.retentionScanEveryWrites = 0;  // 关闭降频：显式调用即扫描
    FileWriter writer(cfg);
    std::string date = writer.makeDateStr();
    std::string f1 = dir.path() + "/1_a_" + date + "_p0.jsonl";
    prewriteFile(f1, 700 * 1024);
    chmod(dir.path().c_str(), 0500);  // 只读：unlink 失败

    writer.enforceRetention();

    chmod(dir.path().c_str(), 0755);
    EXPECT_EQ(access(f1.c_str(), F_OK), 0);
}

TEST(FileWriterRetentionTest, OpensDirFailed_NoCrash) {
    // opendir 失败（目录不存在）：打印错误不崩
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path() + "/sub";
    cfg.maxTotalSizeMb = 1;
    cfg.retentionScanEveryWrites = 0;  // 关闭降频：显式调用即扫描
    FileWriter writer(cfg);  // 构造创建 sub/
    rmdir((dir.path() + "/sub").c_str());  // 删除目录 → opendir 失败

    writer.enforceRetention();
    SUCCEED();
}

TEST(FileWriterRetentionTest, UnderLimit_NoDelete) {
    // 总大小已满足：不删任何文件
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxTotalSizeMb = 100;
    cfg.retentionScanEveryWrites = 0;  // 关闭降频：显式调用即扫描
    FileWriter writer(cfg);
    std::string date = writer.makeDateStr();
    std::string f1 = dir.path() + "/1_a_" + date + "_p0.jsonl";
    prewriteFile(f1, 100 * 1024);

    writer.enforceRetention();

    EXPECT_EQ(access(f1.c_str(), F_OK), 0);
}

// ============================================================
// 重启 seq 续接（方向 3）：daemon 重启后从目录扫描续接轮转序号
// ============================================================

TEST(FileWriterSeqTest, Restart_ContinuesSeqFromExistingFiles) {
    // 重启后 mFiles 空：seq 归 0 会重复写 _p0 追加旧文件、轮转文件名混乱；
    // 修复后扫描当日已有 _p0/_p1 → 续接 seq=2（新文件 _p2）
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    std::string date = writer.makeDateStr();
    prewriteFile(dir.path() + "/4_e_" + date + "_p0.jsonl", 10);
    prewriteFile(dir.path() + "/4_e_" + date + "_p1.jsonl", 20);

    writer.openFile(4, schema);

    auto it = writer.mFiles.find(4);
    ASSERT_NE(it, writer.mFiles.end());
    EXPECT_EQ(it->second.seq, 2);
    EXPECT_NE(it->second.currentFilename.find("_p2.jsonl"),
              std::string::npos);
}

TEST(FileWriterSeqTest, Restart_NoFiles_SeqZero) {
    // 无当日文件：seq 从 0 开始（首次启动语义不变）
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});

    writer.openFile(4, schema);

    auto it = writer.mFiles.find(4);
    ASSERT_NE(it, writer.mFiles.end());
    EXPECT_EQ(it->second.seq, 0);
}

TEST(FileWriterSeqTest, Restart_IgnoresOtherEventsDates) {
    // 只续接同 event+date 的 seq：其他 event / 其他日期的文件不干扰
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    std::string date = writer.makeDateStr();
    prewriteFile(dir.path() + "/8_other_" + date + "_p5.jsonl", 10);
    prewriteFile(dir.path() + "/4_e_19990101_p9.jsonl", 10);
    prewriteFile(dir.path() + "/4_e_" + date + "_p0.jsonl", 10);

    writer.openFile(4, schema);

    auto it = writer.mFiles.find(4);
    ASSERT_NE(it, writer.mFiles.end());
    EXPECT_EQ(it->second.seq, 1);
}

// ============================================================
// evictOldFiles 直测（方向 3）：拆分后的淘汰段直接验证 mtime 排序
// ============================================================

TEST(FileWriterEvictTest, EvictsOldestByMtime) {
    // 三个文件 mtime 乱序（f2 最旧=1000），直测 evictOldFiles：
    // 按 mtime 升序（最旧优先）淘汰，总大小 1.1MB > 1MB → 删最旧后达标
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxTotalSizeMb = 1;
    FileWriter writer(cfg);
    std::string date = writer.makeDateStr();
    std::string f1 = dir.path() + "/1_a_" + date + "_p0.jsonl";
    std::string f2 = dir.path() + "/2_b_" + date + "_p0.jsonl";
    std::string f3 = dir.path() + "/3_c_" + date + "_p0.jsonl";
    prewriteFile(f1, 600 * 1024);
    prewriteFile(f2, 300 * 1024);
    prewriteFile(f3, 200 * 1024);
    struct utimbuf tb;
    tb.actime = 3000; tb.modtime = 3000; utime(f1.c_str(), &tb);
    tb.actime = 2000; tb.modtime = 2000; utime(f2.c_str(), &tb);
    tb.actime = 1000; tb.modtime = 1000; utime(f3.c_str(), &tb);

    std::vector<FileWriter::LogFile> files = writer.scanLogFiles();
    // scanLogFiles 含 invalid_records.log（构造时创建，0B），不硬编码数量
    ASSERT_GE(files.size(), 3u);
    writer.evictOldFiles(files);

    EXPECT_NE(access(f3.c_str(), F_OK), 0);  // mtime 最旧先删
    EXPECT_EQ(access(f2.c_str(), F_OK), 0);
    EXPECT_EQ(access(f1.c_str(), F_OK), 0);
}

TEST(FileWriterEvictTest, EvictsMultipleUntilUnderLimit) {
    // 总大小 1.5MB > 1MB：需删两个最旧（500K+400K）才达标（剩 600K）
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxTotalSizeMb = 1;
    FileWriter writer(cfg);
    std::string date = writer.makeDateStr();
    std::string f1 = dir.path() + "/1_a_" + date + "_p0.jsonl";
    std::string f2 = dir.path() + "/2_b_" + date + "_p0.jsonl";
    std::string f3 = dir.path() + "/3_c_" + date + "_p0.jsonl";
    prewriteFile(f1, 600 * 1024);
    prewriteFile(f2, 500 * 1024);
    prewriteFile(f3, 400 * 1024);
    struct utimbuf tb;
    tb.actime = 3000; tb.modtime = 3000; utime(f1.c_str(), &tb);
    tb.actime = 2000; tb.modtime = 2000; utime(f2.c_str(), &tb);
    tb.actime = 1000; tb.modtime = 1000; utime(f3.c_str(), &tb);

    std::vector<FileWriter::LogFile> files = writer.scanLogFiles();
    writer.evictOldFiles(files);

    EXPECT_NE(access(f3.c_str(), F_OK), 0);  // 最旧
    EXPECT_NE(access(f2.c_str(), F_OK), 0);  // 次旧
    EXPECT_EQ(access(f1.c_str(), F_OK), 0);  // 最新保留
}

TEST(FileWriterEvictTest, UnderLimit_EvictsNothing) {
    // 总大小未超限：一个都不删
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxTotalSizeMb = 100;
    FileWriter writer(cfg);
    std::string date = writer.makeDateStr();
    std::string f1 = dir.path() + "/1_a_" + date + "_p0.jsonl";
    prewriteFile(f1, 100 * 1024);

    std::vector<FileWriter::LogFile> files = writer.scanLogFiles();
    writer.evictOldFiles(files);

    EXPECT_EQ(access(f1.c_str(), F_OK), 0);
}

TEST(FileWriterEvictTest, SkipsOpenFileAndInvalidLog) {
    // 打开的当前文件 + invalid_records.log 必须跳过：删除最旧可删文件
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    cfg.maxTotalSizeMb = 1;
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    std::string date = writer.makeDateStr();
    // 先 openFile 再取实际文件名：openFile 会续接 seq（可能 _p1），
    // 当前文件以 mFiles[4].currentFilename 为准
    writer.openFile(4, schema);
    std::string cur = writer.mFiles[4].currentFilename;
    prewriteFile(cur, 700 * 1024);
    std::string old = dir.path() + "/9_z_" + date + "_p0.jsonl";
    prewriteFile(old, 500 * 1024);
    struct utimbuf tb;
    tb.actime = 100; tb.modtime = 100; utime(old.c_str(), &tb);
    std::string invalid = dir.path() + "/invalid_records.log";
    prewriteFile(invalid, 400 * 1024);
    tb.actime = 50; tb.modtime = 50; utime(invalid.c_str(), &tb);

    std::vector<FileWriter::LogFile> files = writer.scanLogFiles();
    writer.evictOldFiles(files);

    // invalid 最旧但被跳过 → 删 old（500K），cur 打开保留
    EXPECT_NE(access(old.c_str(), F_OK), 0);
    EXPECT_EQ(access(cur.c_str(), F_OK), 0);
    EXPECT_EQ(access(invalid.c_str(), F_OK), 0);
}

// ============================================================
// writeLineFlush 直测（方向 3）：拆分后的写盘+恢复路径直接验证
// ============================================================

TEST(FileWriterWriteLineFlushTest, NormalWrite_ReturnsTrue) {
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    writer.openFile(4, schema);
    std::string fname = writer.mFiles[4].currentFilename;

    bool ok = writer.writeLineFlush(writer.mFiles[4], "{\"x\":1}\n");
    EXPECT_TRUE(ok);
    EXPECT_EQ(writer.dropCounters().reopenFailed, 0);
    EXPECT_EQ(writer.dropCounters().retryFailed, 0);
    // 坏行归零：磁盘只有一条合法整行，无半行/重复
    EXPECT_EQ(readFile(fname), "{\"x\":1}\n");
    // writeLineFlush 只写盘，currentSize 由 writeRecord 负责累计
    EXPECT_EQ(writer.mFiles[4].currentSize, 0u);
}

TEST(FileWriterWriteLineFlushTest, FlushFail_ReopenFail_Drops) {
    // flush 失败（流失效）+ reopen 失败（只读目录）→ reopenFailed +1 返回 false
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    writer.openFile(4, schema);

    std::string fname = writer.mFiles[4].currentFilename;
    writer.mFiles[4].stream.close();  // close 后 << 设 failbit
    unlink(fname.c_str());
    chmod(dir.path().c_str(), 0500);  // reopen 失败

    bool ok = writer.writeLineFlush(writer.mFiles[4], "{\"x\":1}\n");
    chmod(dir.path().c_str(), 0755);

    EXPECT_FALSE(ok);
    EXPECT_EQ(writer.dropCounters().reopenFailed, 1);
    EXPECT_EQ(writer.dropCounters().retryFailed, 0);
    // 坏行归零：文件未产生任何残留（无半行、无整行）
    EXPECT_NE(access(fname.c_str(), F_OK), 0);
}

TEST(FileWriterWriteLineFlushTest, FlushFail_ReopenOk_RetryFail_Drops) {
    // flush 失败 + reopen 成功但 retry 写失败（/dev/full ENOSPC）→ retryFailed +1
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    writer.openFile(4, schema);

    writer.mFiles[4].stream.close();
    writer.mFiles[4].currentFilename = "/dev/full";
    writer.mFiles[4].stream.open("/dev/full", std::ios::app);
    ASSERT_TRUE(writer.mFiles[4].stream.is_open());

    bool ok = writer.writeLineFlush(writer.mFiles[4], "{\"x\":1}\n");

    EXPECT_FALSE(ok);
    EXPECT_EQ(writer.dropCounters().retryFailed, 1);
    EXPECT_EQ(writer.dropCounters().reopenFailed, 0);
    // failbit 被恢复路径清除（CXX-002 粘滞清除）
    EXPECT_FALSE(writer.mFiles[4].stream.fail());
    // 坏行归零：/dev/full 无持久存储可留残留，currentSize 不虚增
    EXPECT_EQ(writer.mFiles[4].currentSize, 0u);
}

TEST(FileWriterWriteLineFlushTest, FlushFail_RecoverySucceeds) {
    // flush 失败 + reopen 成功 + retry 成功 → 恢复写盘，无 DROP 计数；
    // 且首写部分落盘的残留半行被回退清除（写前记录偏移→失败回退→重试），
    // 磁盘只留合法整行（坏行归零，方向 3）
    TempDir dir;
    FileWriterConfig cfg;
    cfg.logDir = dir.path();
    FileWriter writer(cfg);
    auto schema = makeSchema(4, "e", {FieldType::INT64});
    writer.openFile(4, schema);
    std::string fname = writer.mFiles[4].currentFilename;

    // 先落一条合法记录并同步 currentSize（重试回退的写入前偏移基准 = 10）
    writer.mFiles[4].stream << "{\"pre\":1}\n";
    writer.mFiles[4].stream.flush();
    writer.mFiles[4].currentSize = 10;

    // 制造 failbit + 首写部分落盘残留：close 后 << 设 failbit，随后向磁盘
    // 直接追加半行（模拟 flush 失败前部分字节已落盘的现场）
    writer.mFiles[4].stream.close();
    std::string partial = "{\"x\":1}";  // 无换行的半行残留（7 字节）
    FILE* f = fopen(fname.c_str(), "a");
    ASSERT_NE(f, nullptr);
    ASSERT_EQ(fwrite(partial.data(), 1, partial.size(), f), partial.size());
    fclose(f);

    bool ok = writer.writeLineFlush(writer.mFiles[4], "{\"x\":1}\n");

    EXPECT_TRUE(ok);
    EXPECT_EQ(writer.dropCounters().reopenFailed, 0);
    EXPECT_EQ(writer.dropCounters().retryFailed, 0);
    EXPECT_TRUE(writer.mFiles[4].stream.is_open());
    // 坏行归零：残留半行被回退截断，重试整行只写一次，内容与偏移精确对齐
    EXPECT_EQ(readFile(fname), "{\"pre\":1}\n{\"x\":1}\n");
    EXPECT_EQ(writer.mFiles[4].currentSize, 10u);
}
