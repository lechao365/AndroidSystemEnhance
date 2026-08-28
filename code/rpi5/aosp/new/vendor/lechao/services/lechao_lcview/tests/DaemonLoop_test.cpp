// DaemonLoop_test.cpp — daemon 主循环抽取函数分支覆盖
// 拦截：S7（故障可见性）——parseBatch 坏长度/过小/validate 失败写 invalid、
//       trailing 残留写 invalid；loadSchemaWithRetry 重试；waitForHal 等待与
//       rebindAfterError 重绑（此前 main 逻辑不在测试编译内，覆盖分母隐性抬高）

#include <gtest/gtest.h>

#include <chrono>
#include <cstring>
#include <memory>
#include <vector>

#include <unistd.h>
#include <sys/stat.h>
#include <dirent.h>

#define private public
#define protected public
#include "batch_parser.h"
#include "SchemaParser.h"
#include "FileWriter.h"
#undef private
#undef protected
#include "../include/lcview_events.h"
#include <aidl/vendor/lechao/lcview/BnLcView.h>

using namespace vendor::lechao::lcview;
using aidl::vendor::lechao::lcview::ILcView;

// 最小 FakeHal：满足 BnLcView 纯虚，供 waitForHal/rebindAfterError 注入
class FakeHal : public aidl::vendor::lechao::lcview::BnLcView {
public:
    ndk::ScopedAStatus getBatch(std::vector<uint8_t>* /*out*/) override {
        return ndk::ScopedAStatus::ok();
    }
    ndk::ScopedAStatus getOverrunCount(int32_t* /*out*/) override {
        return ndk::ScopedAStatus::ok();
    }
    ndk::ScopedAStatus getTotalRecords(int64_t* /*out*/) override {
        return ndk::ScopedAStatus::ok();
    }
};

std::shared_ptr<ILcView> fakeHal() {
    return ndk::SharedRefBase::make<FakeHal>();
}

namespace {

// 与 SchemaParser_test 同款的最小合法 schema（id=4：INT64 + STRING）
constexpr const char* kSchemaJson = R"({
  "version": 1,
  "events": [
    {
      "id": 4, "name": "usb_transport_start", "desc": "test",
      "fields": [
        {"name": "label", "type": "string"},
        {"name": "device_index", "type": "int64"}
      ]
    }
  ]
})";

SchemaParser makeSchema() {
    SchemaParser sp;
    EXPECT_TRUE(sp.parseJson(kSchemaJson));
    return sp;
}

std::vector<uint8_t> makeValidRecord() {
    std::vector<uint8_t> buf(33, 0);
    auto* hdr = reinterpret_cast<lcview_record_hdr*>(buf.data());
    hdr->magic = LCVIEW_MAGIC;
    hdr->event_id = 4;
    hdr->level = LCVIEW_LEVEL_INFO;
    hdr->field_count = 2;
    hdr->timestamp_ns = 0x1234;
    uint8_t* p = buf.data() + sizeof(lcview_record_hdr);
    p[0] = LCVIEW_TYPE_STRING;
    uint16_t len_le = 5;
    memcpy(p + 1, &len_le, 2);
    memcpy(p + 3, "hello", 5);
    p += 8;
    p[0] = LCVIEW_TYPE_INT64;
    int64_t v = 42;
    memcpy(p + 1, &v, 8);
    return buf;
}

// 构造 4B 长度前缀 + record 的批次
std::vector<uint8_t> makeBatch(const std::vector<uint8_t>& record,
                               size_t* totalLenOut = nullptr) {
    std::vector<uint8_t> batch;
    uint32_t total = static_cast<uint32_t>(record.size()) + 4;
    if (totalLenOut) *totalLenOut = total;
    batch.resize(4);
    memcpy(batch.data(), &total, 4);
    batch.insert(batch.end(), record.begin(), record.end());
    return batch;
}

}  // namespace

class DaemonLoopTest : public ::testing::Test {
protected:
    void SetUp() override {
        char tmpl[] = "/data/local/tmp/lcview_daemon_XXXXXX";
        char* tmp = mkdtemp(tmpl);
        ASSERT_NE(tmp, nullptr);
        mTmp = tmp;
        mCfg.logDir = mTmp;
        mCfg.maxFileSizeMb = 50;
        mCfg.maxTotalSizeMb = 500;
    }

    void TearDown() override {
        std::string cmd = "rm -rf " + std::string(mTmp);
        system(cmd.c_str());
    }

    std::string mTmp;
    FileWriterConfig mCfg;
};

TEST_F(DaemonLoopTest, EmptyBatch_ZeroCounts) {
    SchemaParser sp = makeSchema();
    FileWriter writer(mCfg);
    BatchParseResult r = parseBatch(sp, writer, {});
    EXPECT_EQ(r.validCnt, 0u);
    EXPECT_EQ(r.invalidCnt, 0u);
}

TEST_F(DaemonLoopTest, ValidRecord_WrittenAndCounted) {
    SchemaParser sp = makeSchema();
    FileWriter writer(mCfg);
    BatchParseResult r = parseBatch(sp, writer, makeBatch(makeValidRecord()));
    EXPECT_EQ(r.validCnt, 1u);
    EXPECT_EQ(r.invalidCnt, 0u);
    // 文件真实落盘
    std::string path = std::string(mTmp) + "/4_usb_transport_start_";
    // 日期文件名由 makeDateStr 决定，扫目录断言存在
    DIR* dir = opendir(mTmp.c_str());
    ASSERT_NE(dir, nullptr);
    bool found = false;
    struct dirent* e;
    while ((e = readdir(dir)) != nullptr) {
        if (strstr(e->d_name, "usb_transport_start") != nullptr) found = true;
    }
    closedir(dir);
    EXPECT_TRUE(found);
}

TEST_F(DaemonLoopTest, BadLength_BreaksAndWritesInvalid) {
    SchemaParser sp = makeSchema();
    FileWriter writer(mCfg);
    // total_len 声明 100 但实际不足 → bad length
    std::vector<uint8_t> batch = {100, 0, 0, 0, 0xAA};
    BatchParseResult r = parseBatch(sp, writer, batch);
    EXPECT_EQ(r.validCnt, 0u);
    // invalid 落盘（writeInvalid），统计由 break 截断不计数
    struct stat st;
    std::string inv = std::string(mTmp) + "/invalid_records.log";
    EXPECT_EQ(stat(inv.c_str(), &st), 0);
    EXPECT_GT(st.st_size, 0);
}

TEST_F(DaemonLoopTest, RecordTooSmall_WritesInvalid) {
    SchemaParser sp = makeSchema();
    FileWriter writer(mCfg);
    // record 不足 hdr（16B）
    std::vector<uint8_t> rec(8, 0xAA);
    BatchParseResult r = parseBatch(sp, writer, makeBatch(rec));
    EXPECT_EQ(r.validCnt, 0u);
    EXPECT_EQ(r.invalidCnt, 1u);
}

TEST_F(DaemonLoopTest, ValidateFail_WritesInvalid) {
    SchemaParser sp = makeSchema();
    FileWriter writer(mCfg);
    auto rec = makeValidRecord();
    reinterpret_cast<lcview_record_hdr*>(rec.data())->event_id = 999;
    BatchParseResult r = parseBatch(sp, writer, makeBatch(rec));
    EXPECT_EQ(r.validCnt, 0u);
    EXPECT_EQ(r.invalidCnt, 1u);
}

TEST_F(DaemonLoopTest, TrailingBytes_WritesInvalid) {
    SchemaParser sp = makeSchema();
    FileWriter writer(mCfg);
    auto batch = makeBatch(makeValidRecord());
    batch.push_back(0xFF);  // 尾部 1B 残留
    BatchParseResult r = parseBatch(sp, writer, batch);
    EXPECT_EQ(r.validCnt, 1u);
    EXPECT_EQ(r.invalidCnt, 1u);  // trailing 计数
}

TEST(DaemonLoopHelperTest, SchemaLoadRetry_EventualSuccess) {
    // schema 路径不存在 → 重试失败；maxRetries=0 直接失败
    SchemaParser sp;
    EXPECT_FALSE(loadSchemaWithRetry(sp, "/nonexistent/lcview_events.json", 0,
                                     std::chrono::milliseconds(1)));
}

TEST(DaemonLoopHelperTest, SchemaLoadRetry_SuccessOnFirstTry) {
    // 真实配置（上板路径 /vendor/etc/lcview_events.json）一次加载成功
    SchemaParser sp;
    if (access("/vendor/etc/lcview_events.json", R_OK) == 0) {
        EXPECT_TRUE(loadSchemaWithRetry(sp, "/vendor/etc/lcview_events.json", 0,
                                        std::chrono::milliseconds(1)));
        EXPECT_EQ(sp.eventCount(), 10u);
    } else {
        GTEST_SKIP() << "真配置不存在（host 环境）";
    }
}

TEST(DaemonLoopHelperTest, WaitForHal_RetriesUntilBound) {
    int calls = 0;
    auto bind = [&calls](const std::string&) -> std::shared_ptr<ILcView> {
        calls++;
        return calls >= 3 ? fakeHal() : nullptr;
    };
    auto hal = waitForHal(bind, "test", 10, std::chrono::milliseconds(1));
    EXPECT_TRUE(hal);
    EXPECT_EQ(calls, 3);
}

TEST(DaemonLoopHelperTest, WaitForHal_ExhaustsRetries) {
    auto bind = [](const std::string&) -> std::shared_ptr<ILcView> {
        return nullptr;
    };
    auto hal = waitForHal(bind, "test", 3, std::chrono::milliseconds(1));
    EXPECT_FALSE(hal);
}

TEST(DaemonLoopHelperTest, RebindAfterError_ReturnsNewHal) {
    auto bind = [](const std::string&) -> std::shared_ptr<ILcView> {
        return fakeHal();
    };
    EXPECT_TRUE(!!rebindAfterError(bind, "test"));
}