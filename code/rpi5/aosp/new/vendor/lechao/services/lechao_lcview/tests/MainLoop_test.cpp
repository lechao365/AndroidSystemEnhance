// MainLoop_test.cpp — runMainLoop 可测边界覆盖（方向 1）
// 拦截：runMainLoop 从含 main() 的 lechao_lcview.cpp 抽到 main_loop.cpp
//   并注入 DeviceReader 抽象接口后，主循环接线首次可被单测编译覆盖——
//   此前 main() 文件与 gtest 主函数冲突无法编入 cc_test，主循环长期
//   零检出。本测试注入 FakeDeviceReader（先返 N 字节批次再返 -1），
//   断言 writer 收到该批次（致命读错误退出前 flush 残留路径，丢数据
//   收口 方向 2 的端到端接线验证）。

#include <gtest/gtest.h>

#include <chrono>
#include <cstring>
#include <memory>
#include <vector>

#include <dirent.h>
#include <sys/stat.h>
#include <unistd.h>

// 测试需调 SchemaParser::parseJson（私有），与 DaemonLoop_test 同款
// #define private public 技巧（Android gtest 编译期可见，链接无碍）
#define private public
#define protected public
#include "main_loop.h"
#include "batch_parser.h"
#include "SchemaParser.h"
#include "FileWriter.h"
#undef private
#undef protected
#include "../include/lcview_events.h"

using namespace vendor::lechao::lcview;

namespace {

// 与 DaemonLoop_test 同款的最小合法 schema（id=4：INT64 + STRING）
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
std::vector<uint8_t> makeBatch(const std::vector<uint8_t>& record) {
    std::vector<uint8_t> batch;
    uint32_t total = static_cast<uint32_t>(record.size()) + 4;
    batch.resize(4);
    memcpy(batch.data(), &total, 4);
    batch.insert(batch.end(), record.begin(), record.end());
    return batch;
}

// 注入用 FakeDeviceReader：waitAndRead 按预设序列返回（先数据再致命错误）。
// 方向 1：主循环接线经抽象 DeviceReader 注入即可测，不依赖真实设备/pipe
class FakeDeviceReader : public DeviceReader {
public:
    explicit FakeDeviceReader(std::vector<uint8_t> batch) : mBatch(std::move(batch)) {}

    bool open() override { return true; }
    ssize_t waitAndRead(uint8_t* buf, size_t offset, size_t cap,
                        int /*timeoutMs*/) override {
        if (mServed >= 1) {
            return -1;  // 致命读错误（errno 由调用方场景决定，此处默认 EIO）
        }
        if (offset + mBatch.size() > cap) {
            return -1;
        }
        memcpy(buf + offset, mBatch.data(), mBatch.size());
        mServed++;
        return static_cast<ssize_t>(mBatch.size());
    }
    uint32_t getOverrun() override { return 0; }
    uint32_t getTotalRecords() override { return 0; }
    void close() override {}
    uint64_t ioctlErr() const override { return 0; }
    uint64_t eofCount() const override { return 0; }

    size_t served() const { return mServed; }

private:
    std::vector<uint8_t> mBatch;
    size_t mServed = 0;
};

}  // namespace

class MainLoopTest : public ::testing::Test {
protected:
    void SetUp() override {
        std::string tmpl = "/data/local/tmp/lcview_mainloop_XXXXXX";
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

    // 断言 logDir 下存在含指定事件名的记录文件（真实落盘证据）
    bool logFileExists(const char* eventName) {
        DIR* dir = opendir(mTmp.c_str());
        if (!dir) return false;
        bool found = false;
        struct dirent* e;
        while ((e = readdir(dir)) != nullptr) {
            if (strstr(e->d_name, eventName) != nullptr) found = true;
        }
        closedir(dir);
        return found;
    }

    std::string mTmp;
    FileWriterConfig mCfg;
};

TEST_F(MainLoopTest, ReaderBatchThenFatal_WriterGetsBatch) {
    // 方向 1：注入 reader 先返 N 字节批次、再返 -1（致命读错误）——
    // 主循环应在致命错误退出前强制 flush 缓冲残留（丢数据收口 方向 2），
    // writer 必须收到该批次。堵 runMainLoop 长期接线零检出。
    SchemaParser sp = makeSchema();
    FileWriter writer(mCfg);
    FakeDeviceReader reader(makeBatch(makeValidRecord()));

    int rc = runMainLoop(reader, sp, writer);

    // 致命读错误路径 return 1（交 init 重启）
    EXPECT_EQ(rc, 1);
    // 已消耗 2 次 waitAndRead（一次数据 + 一次致命错误）
    EXPECT_EQ(reader.served(), 1u);
    // writer 收到该批次：落盘文件真实存在
    EXPECT_TRUE(logFileExists("usb_transport_start"));
}
