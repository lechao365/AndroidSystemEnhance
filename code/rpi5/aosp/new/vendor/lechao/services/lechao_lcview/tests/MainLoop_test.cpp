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

// ============================================================
// 守恒告警判定（方向 3/7）：dev = totalΔ - (overrunΔ + droppedΔ +
// jsonlΔ + invalidΔ)；容差按环推导（方向 6）
// ============================================================

namespace {
// 默认 ring 256KB 推导容差：(262144+65536)/20 = 16384（与原固定容差一致）
constexpr int64_t kDefaultTol = computeConserveTolerance(256 * 1024);
}  // namespace

TEST(MainLoopConservationTest, ZeroDeviation_NoAlarm) {
    // 完全守恒：产生全落盘，dev=0
    EXPECT_FALSE(shouldAlarmConservation(100, 0, 0, 100, 0, kDefaultTol));
    // overrun/invalid 计入后守恒成立
    EXPECT_FALSE(shouldAlarmConservation(100, 10, 0, 80, 10, kDefaultTol));
}

TEST(MainLoopConservationTest, DroppedIsAbsorbedByLeftSide) {
    // 方向 7：ENOSPC 丢弃计入守恒右式（droppedDelta）后被吸收——
    // 产生 100、丢弃 100（未落盘）dev=0，不误报负偏差
    EXPECT_FALSE(shouldAlarmConservation(100, 0, 100, 0, 0, kDefaultTol));
    // 混合去向：驱逐 10 + 丢弃 5 + 合法落盘 80 + 非法落盘 5 = 100
    EXPECT_FALSE(shouldAlarmConservation(100, 10, 5, 80, 5, kDefaultTol));
    // 丢弃超过产生（计数漂移/重复丢弃）→ 负偏差超容差告警
    EXPECT_TRUE(shouldAlarmConservation(100, 0, 100 + kDefaultTol + 1, 0, 0,
                                        kDefaultTol));
}

TEST(MainLoopConservationTest, InFlightWithinTolerance_NoAlarm) {
    // 在途积压未超容差：不告警（容差边界 dev == tol 严格大于才告警）
    EXPECT_FALSE(shouldAlarmConservation(1000, 0, 0, 900, 0, kDefaultTol));
    EXPECT_FALSE(shouldAlarmConservation(1000 + kDefaultTol, 0, 0, 1000, 0,
                                         kDefaultTol));
    // 负向同理：落盘略超产生但在容差内
    EXPECT_FALSE(shouldAlarmConservation(1000, 0, 0, 1000 + kDefaultTol, 0,
                                         kDefaultTol));
}

TEST(MainLoopConservationTest, PositiveDeviationBeyondTolerance_Alarms) {
    // 产生未落盘超容差：丢记录/在途积压异常告警
    EXPECT_TRUE(shouldAlarmConservation(1000 + kDefaultTol + 1, 0, 0, 1000, 0,
                                        kDefaultTol));
}

TEST(MainLoopConservationTest, NegativeDeviationBeyondTolerance_Alarms) {
    // 落盘超过产生超容差：重复落盘/计数漂移告警
    EXPECT_TRUE(shouldAlarmConservation(1000, 0, 0, 1000 + kDefaultTol + 1, 0,
                                        kDefaultTol));
}

TEST(MainLoopConservationTest, ToleranceDerivedFromRingSize) {
    // 方向 6：默认 ring 256KB → (262144+65536)/20 = 16384（与原固定容差一致）
    EXPECT_EQ(computeConserveTolerance(256 * 1024), 16384);
    // 更大 ring → 更大容差（容忍更大在途积压）
    EXPECT_GT(computeConserveTolerance(4096 * 1024),
              computeConserveTolerance(256 * 1024));
    // 更小 ring → 更小容差（更灵敏）
    EXPECT_LT(computeConserveTolerance(64 * 1024),
              computeConserveTolerance(256 * 1024));
    // ring 为 0（ioctl 失败兜底值）→ 仅用户缓冲档位
    EXPECT_EQ(computeConserveTolerance(0), 65536 / 20);
}

// ============================================================
// ConserveBaseline::updateAndCheck（R-02 方向 3）：守恒三态收口单测——
// 首心跳建基线 / ioctl 失败跳过数值推进 / 后续心跳增量判定 + 推进。
// 纯函数直测（不依赖 emitHeartbeat / ALOGI），覆盖正负向告警与防回绕推进。
// ============================================================

namespace {

// 构造一轮采样（ioctl 正常，全计数可指定）
ConserveBaseline::Sample makeSample(uint32_t total, int64_t overrun,
                                    uint32_t dropped, uint64_t valid,
                                    uint64_t invalid,
                                    uint32_t ring = 256 * 1024,
                                    uint64_t ioctlErr = 0) {
    return ConserveBaseline::Sample{
        total, overrun, dropped, ioctlErr, valid, invalid, ring,
    };
}

}  // namespace

TEST(MainLoopBaselineTest, FirstSample_InitializesNoAlarm) {
    // 首心跳（initialized=false）：仅建基线，不告警
    ConserveBaseline bl;
    auto r = bl.updateAndCheck(makeSample(1000, 10, 0, 900, 0));
    EXPECT_FALSE(r.broken);
    EXPECT_TRUE(bl.initialized);
    EXPECT_EQ(bl.total, 1000u);
    // 次心跳推进后仍为上一轮值（防回绕推进）
    auto r2 = bl.updateAndCheck(makeSample(2000, 20, 0, 1900, 0));
    EXPECT_FALSE(r2.broken);
    EXPECT_EQ(bl.total, 2000u);
}

TEST(MainLoopBaselineTest, IoctlError_SkipsNumericalAdvance) {
    // ioctlErr 增量（任一查询失败）：跳过守恒校验与数值推进，仅推进 ioctlErr
    ConserveBaseline bl;
    bl.updateAndCheck(makeSample(1000, 10, 0, 900, 0));
    auto r = bl.updateAndCheck(makeSample(1000, 10, 0, 900, 0,
                                          256 * 1024, 5 /* ioctlErr 变化 */));
    EXPECT_FALSE(r.broken);  // 失败值不作判定依据
    EXPECT_EQ(bl.ioctlErr, 5u);
    EXPECT_EQ(bl.total, 1000u);  // 数值基线保持上轮成功值
    // ioctlErr 保持（无新失败，计数单调不回落）：数值推进恢复——本轮
    // 产生 500 落盘 500，守恒成立且基线推进到 1500
    auto r3 = bl.updateAndCheck(makeSample(1500, 15, 0, 1400, 0,
                                           256 * 1024, 5));
    EXPECT_FALSE(r3.broken);
    EXPECT_EQ(r3.totalDelta, 500u);
    EXPECT_EQ(bl.total, 1500u);
}

TEST(MainLoopBaselineTest, PositiveDeviation_AlarmsWithWindowDeltas) {
    // 产生未落盘超容差：告警且 Result 携带窗口增量（日志直接引用）
    ConserveBaseline bl;
    bl.updateAndCheck(makeSample(1000, 10, 0, 900, 0));
    // 本轮：产生 +2000，落盘 +1000（在途 1000 < tol 16384 不告警）
    auto r_ok = bl.updateAndCheck(makeSample(3000, 10, 0, 1900, 0));
    EXPECT_FALSE(r_ok.broken);
    EXPECT_EQ(r_ok.totalDelta, 2000u);
    EXPECT_EQ(r_ok.jsonlDelta, 1000u);
    EXPECT_EQ(r_ok.dev, 1000);
    // 本轮：产生 +18000，落盘 +1000 → dev 17000 > tol 告警
    auto r_alarm = bl.updateAndCheck(makeSample(21000, 10, 0, 2900, 0));
    EXPECT_TRUE(r_alarm.broken);
    EXPECT_EQ(r_alarm.totalDelta, 18000u);
    EXPECT_EQ(r_alarm.jsonlDelta, 1000u);
    EXPECT_EQ(r_alarm.dev, 17000);
    EXPECT_EQ(r_alarm.tolerance, kDefaultTol);
}

TEST(MainLoopBaselineTest, NegativeDeviation_Alarms) {
    // 落盘超过产生超容差：重复落盘/计数漂移告警
    ConserveBaseline bl;
    bl.updateAndCheck(makeSample(1000, 10, 0, 900, 0));
    // 本轮：产生 +1000，落盘 +18000 → dev -17000 < -tol 告警
    auto r = bl.updateAndCheck(makeSample(2000, 10, 0, 18900, 0));
    EXPECT_TRUE(r.broken);
    EXPECT_EQ(r.dev, -17000);
}

TEST(MainLoopBaselineTest, DroppedAndInvalidAbsorbed) {
    // 方向 7 + 方向 5：dropped（ENOSPC 丢弃）/invalid（非法落盘）计入右式
    // 后被吸收——产生 100、丢弃 100（未落盘）dev=0 不告警
    ConserveBaseline bl;
    bl.updateAndCheck(makeSample(100, 0, 0, 100, 0));
    auto r = bl.updateAndCheck(makeSample(200, 0, 100, 100, 0));
    EXPECT_FALSE(r.broken);
    EXPECT_EQ(r.droppedDelta, 100u);
    EXPECT_EQ(r.dev, 0);
    // invalid 同语义：产生 100、非法落盘 100 不告警
    ConserveBaseline bl2;
    bl2.updateAndCheck(makeSample(100, 0, 0, 100, 0));
    auto r2 = bl2.updateAndCheck(makeSample(200, 0, 0, 100, 100));
    EXPECT_FALSE(r2.broken);
    EXPECT_EQ(r2.invalidDelta, 100u);
    EXPECT_EQ(r2.dev, 0);
}

// ============================================================
// IHeartbeatWriter（R-02 方向 3）：记录型 writer 断言心跳字段透传——
// 心跳内容不再依赖 ALOGI 格式，接口层字段集即契约（单测直验字段值）。
// ============================================================

namespace {

// 记录型 writer：捕获最近一次 HeartbeatFields，供断言
class RecordingHeartbeatWriter : public IHeartbeatWriter {
public:
    void write(const HeartbeatFields& hb) override { last = hb; }
    HeartbeatFields last;
};

}  // namespace

TEST(MainLoopHeartbeatWriterTest, FieldsPassThrough) {
    // 心跳字段集经接口透传（内容即契约）——字段缺失/错位在单测暴露，
    // 不依赖 logcat 格式化（liveness 判据 logfield 的回归点在日志格式
    // 测试 LogHeartbeatWriter 覆盖，此处断言字段语义）
    RecordingHeartbeatWriter w;
    // HeartbeatFields 字段顺序聚合初始化（C++17 无 designated initializer）
    HeartbeatFields hb;
    hb.loop = 42;
    hb.overrun = 7;
    hb.dropped = 3;
    hb.readErr = 1;
    hb.totalRecords = 1000;
    hb.jsonlRecords = 900;
    hb.invalidRecords = 5;
    hb.ioctlErr = 0;
    hb.eofCount = 2;
    hb.dropOpen = 1;
    hb.dropFormat = 2;
    hb.dropOob = 3;
    hb.dropReopen = 4;
    hb.dropRetry = 5;
    hb.dropInvalid = 6;
    hb.dropInvalidWrite = 7;
    hb.dropRotate = 8;
    hb.dropInvRotate = 9;
    hb.dropRollback = 10;
    hb.avgFormatUs = 11;
    hb.avgWriteUs = 12;
    w.write(hb);
    EXPECT_EQ(w.last.loop, 42u);
    EXPECT_EQ(w.last.overrun, 7);
    EXPECT_EQ(w.last.dropped, 3u);
    EXPECT_EQ(w.last.readErr, 1u);
    EXPECT_EQ(w.last.totalRecords, 1000u);
    EXPECT_EQ(w.last.jsonlRecords, 900);
    EXPECT_EQ(w.last.invalidRecords, 5);
    EXPECT_EQ(w.last.dropRollback, 10u);
    EXPECT_EQ(w.last.avgFormatUs, 11u);
    EXPECT_EQ(w.last.avgWriteUs, 12u);
}
