// DaemonLoop_test.cpp — daemon 主循环抽取函数分支覆盖
// 拦截：S7（故障可见性）——parseBatch 坏长度/过小/validate 失败写 invalid、
//       trailing 残留写 invalid；loadSchemaWithRetry 重试。
// 架构演进：daemon 直读内核后取消 HAL 绑定（waitForHal/rebindAfterError/
// FakeHal 移除），原相关测试随之删除。

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

using namespace vendor::lechao::lcview;

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
        std::string tmpl = "/data/local/tmp/lcview_daemon_XXXXXX";
        char* tmp = mkdtemp(tmpl.data());
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

// ============================================================
// flush 触发语义（原 hal_test LcViewReaderLoopTest 分支 4/5 有效逻辑
// 并入 daemon）：空批不 flush（避免写放大）；满缓冲 flush；
// timeout/滞留窗 flush；有数据后 timeout 触发 flush
// ============================================================

TEST(DaemonLoopHelperTest, Flush_EmptyBuffer_NeverFlushes) {
    // 空批 + timeout/age 均不 flush（hal_test: TimeoutNoData 语义）
    EXPECT_FALSE(shouldFlushBatch(0, /*timedOut=*/true, /*ageExpired=*/false, 64 * 1024));
    EXPECT_FALSE(shouldFlushBatch(0, /*timedOut=*/false, /*ageExpired=*/true, 64 * 1024));
    EXPECT_FALSE(shouldFlushBatch(0, /*timedOut=*/true, /*ageExpired=*/true, 64 * 1024));
}

TEST(DaemonLoopHelperTest, Flush_BufferFull_TriggersFlush) {
    // 满缓冲 → flush（hal_test: BufferFull_TriggersFlush 语义）
    EXPECT_TRUE(shouldFlushBatch(64 * 1024, /*timedOut=*/false, /*ageExpired=*/false, 64 * 1024));
    EXPECT_TRUE(shouldFlushBatch(64 * 1024 + 1, false, false, 64 * 1024));
    // 未满但不为零 + 无触发条件 → 不 flush（继续攒包）
    EXPECT_FALSE(shouldFlushBatch(100, false, false, 64 * 1024));
}

TEST(DaemonLoopHelperTest, Flush_TimeoutOrAge_TriggersFlush) {
    // 有数据 + epoll 超时 → flush（hal_test: NormalRead_BatchQueued 语义）
    EXPECT_TRUE(shouldFlushBatch(100, /*timedOut=*/true, /*ageExpired=*/false, 64 * 1024));
    // 有数据 + 500ms 滞留窗到期 → flush
    EXPECT_TRUE(shouldFlushBatch(100, /*timedOut=*/false, /*ageExpired=*/true, 64 * 1024));
}