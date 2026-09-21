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

// 可控计数器值 reader（R-03 方向 3）：直调 emitHeartbeat 断言字段透传——
// 各内核计数器可注入非零值，验证收集逻辑真实把这些值送入 HeartbeatFields
// （原单测只构造 HeartbeatFields 直写，不覆盖收集路径）
class ControlledDeviceReader : public DeviceReader {
public:
    ControlledDeviceReader() = default;
    bool open() override { return true; }
    ssize_t waitAndRead(uint8_t*, size_t, size_t, int) override { return -1; }
    void close() override {}
    uint32_t getOverrun() override { return overrun; }
    uint32_t getTotalRecords() override { return totalRecords; }
    uint32_t getDropped() override { return dropped; }
    uint32_t getRingSizeBytes() override { return ringSize; }
    uint64_t ioctlErr() const override { return ioctlErr_; }
    uint64_t eofCount() const override { return eof; }

    uint32_t overrun = 0;
    uint32_t totalRecords = 0;
    uint32_t dropped = 0;
    uint32_t ringSize = 256 * 1024;
    uint64_t ioctlErr_ = 0;
    uint64_t eof = 0;
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
    EXPECT_FALSE(shouldAlarmConservation(100, 0, 0, 0, 100, 0, kDefaultTol));
    // overrun/invalid 计入后守恒成立
    EXPECT_FALSE(shouldAlarmConservation(100, 10, 0, 0, 80, 10, kDefaultTol));
}

TEST(MainLoopConservationTest, DroppedIsAbsorbedByLeftSide) {
    // 方向 7：ENOSPC 丢弃计入守恒右式（droppedDelta）后被吸收——
    // 产生 100、丢弃 100（未落盘）dev=0，不误报负偏差
    EXPECT_FALSE(shouldAlarmConservation(100, 0, 100, 0, 0, 0, kDefaultTol));
    // 混合去向：驱逐 10 + 丢弃 5 + 合法落盘 80 + 非法落盘 5 = 100
    EXPECT_FALSE(shouldAlarmConservation(100, 10, 5, 0, 80, 5, kDefaultTol));
    // 丢弃超过产生（计数漂移/重复丢弃）→ 负偏差超容差告警
    EXPECT_TRUE(shouldAlarmConservation(100, 0, 100 + kDefaultTol + 1, 0, 0, 0,
                                        kDefaultTol));
}

TEST(MainLoopConservationTest, WriterDropAbsorbedByLeftSide) {
    // R-07 方向 3：FileWriter DROP 增量（writerDropDelta）计入守恒右式后
    // 被吸收——产生 100、写路径丢弃 100（openFailed/formatEmpty/...）
    // 且未落盘 dev=0，消除丢记录正向偏差（原右式无该去向，dev=+100 被
    // 容差静默吞掉或误报 CONSERVATION BROKEN）
    EXPECT_FALSE(shouldAlarmConservation(100, 0, 0, 100, 0, 0, kDefaultTol));
    // 混合：驱逐 10 + 内核丢弃 5 + 写路径丢弃 5 + 合法落盘 70 + 非法落盘 10
    EXPECT_FALSE(shouldAlarmConservation(100, 10, 5, 5, 70, 10, kDefaultTol));
    // 写路径丢弃超过产生（计数漂移/重复丢弃）→ 负偏差超容差告警
    EXPECT_TRUE(shouldAlarmConservation(100, 0, 0, 100 + kDefaultTol + 1, 0, 0,
                                        kDefaultTol));
}

TEST(MainLoopConservationTest, InFlightWithinTolerance_NoAlarm) {
    // 在途积压未超容差：不告警（容差边界 dev == tol 严格大于才告警）
    EXPECT_FALSE(shouldAlarmConservation(1000, 0, 0, 0, 900, 0, kDefaultTol));
    EXPECT_FALSE(shouldAlarmConservation(1000 + kDefaultTol, 0, 0, 0, 1000, 0,
                                         kDefaultTol));
    // 负向同理：落盘略超产生但在容差内
    EXPECT_FALSE(shouldAlarmConservation(1000, 0, 0, 0, 1000 + kDefaultTol, 0,
                                         kDefaultTol));
}

TEST(MainLoopConservationTest, PositiveDeviationBeyondTolerance_Alarms) {
    // 产生未落盘超容差：丢记录/在途积压异常告警
    EXPECT_TRUE(shouldAlarmConservation(1000 + kDefaultTol + 1, 0, 0, 0, 1000,
                                        0, kDefaultTol));
}

TEST(MainLoopConservationTest, NegativeDeviationBeyondTolerance_Alarms) {
    // 落盘超过产生超容差：重复落盘/计数漂移告警
    EXPECT_TRUE(shouldAlarmConservation(1000, 0, 0, 0, 1000 + kDefaultTol + 1,
                                        0, kDefaultTol));
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
                                    uint64_t ioctlErr = 0,
                                    uint64_t writerDrop = 0) {
    return ConserveBaseline::Sample{
        total, overrun, dropped, writerDrop, ioctlErr, valid, invalid, ring,
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

TEST(MainLoopBaselineTest, WriterDropAbsorbedWithBaselineAdvance) {
    // R-07 方向 3：FileWriter DROP 增量进守恒右式且随基线推进——首心跳
    // 建基线（writerDrop=5），次心跳 writerDrop=105（+100），产生 +100
    // 全被写路径丢弃（未落盘），dev=0 不告警
    ConserveBaseline bl;
    bl.updateAndCheck(makeSample(100, 0, 0, 100, 0, 256 * 1024, 0, 5));
    auto r = bl.updateAndCheck(makeSample(200, 0, 0, 100, 0, 256 * 1024, 0,
                                          105));
    EXPECT_FALSE(r.broken);
    EXPECT_EQ(r.writerDropDelta, 100u);
    EXPECT_EQ(r.dev, 0);
}

TEST(MainLoopBaselineTest, KernelCountRollback_RebuildsBaseline) {
    // R-07 方向 4：total 或 dropped 回退（当前采样 < 基线）即整体重建同锚，
    // 不告警——模拟内核模块重载/重启后计数器清零（旧基线是重载前大值，
    // 直接算增量会下溢成巨大值误报守恒破坏）
    ConserveBaseline bl;
    bl.updateAndCheck(makeSample(100000, 0, 5000, 90000, 0));
    // 内核重载清零：total 100000→1000、dropped 5000→0，均回退
    auto r = bl.updateAndCheck(makeSample(1000, 0, 0, 90000, 0));
    EXPECT_FALSE(r.broken);          // 不得误报
    EXPECT_EQ(r.totalDelta, 0u);     // 本窗口不判定增量
    EXPECT_EQ(bl.total, 1000u);      // 基线重建到重载后值（同锚）
    EXPECT_EQ(bl.dropped, 0u);
    // 重建后下一心跳恢复正常增量判定（total 1000→2000）
    auto r2 = bl.updateAndCheck(makeSample(2000, 0, 0, 90000, 0));
    EXPECT_FALSE(r2.broken);
    EXPECT_EQ(r2.totalDelta, 1000u);
}

TEST(MainLoopBaselineTest, DroppedRollback_RebuildsBaseline) {
    // R-07 方向 4：仅 dropped 回退（total 正常）同样触发整体重建——内核
    // 只清零 dropped 不清 total（异常现场），防 droppedΔ 下溢误报
    ConserveBaseline bl;
    bl.updateAndCheck(makeSample(1000, 0, 5000, 900, 0));
    auto r = bl.updateAndCheck(makeSample(2000, 0, 0, 1800, 0));
    EXPECT_FALSE(r.broken);
    EXPECT_EQ(bl.total, 2000u);      // 同锚重建（total 也重取当前值）
    EXPECT_EQ(bl.dropped, 0u);
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
    // R-03 方向 3：直调生产函数 emitHeartbeat（去 static 后可测边界），注入
    // ControlledDeviceReader + 真实 FileWriter + 记录型 writer，断言字段真实
    // 透传——覆盖收集逻辑（reader 计数器值 + 入参 → HeartbeatFields），
    // 原单测只构造 HeartbeatFields 直写，不覆盖收集路径，字段错位/漏取
    // 在收集层被静默吞掉无法暴露。
    std::string tmpl = "/data/local/tmp/lcview_hb_XXXXXX";
    char* tmp = mkdtemp(tmpl.data());
    ASSERT_NE(tmp, nullptr);
    FileWriterConfig cfg;
    cfg.logDir = tmp;
    cfg.maxFileSizeMb = 50;
    cfg.maxTotalSizeMb = 500;
    FileWriter writer(cfg);

    // 先真实写一条合法记录 → FileWriter 侧字段非零（jsonl 落盘 / avg 耗时 /
    // dropped 求和的分项），验证这些字段经 emitHeartbeat 透传到 writer
    SchemaParser sp = makeSchema();
    auto rec = makeValidRecord();
    const auto* hdr = reinterpret_cast<const lcview_record_hdr*>(rec.data());
    const uint8_t* fields = rec.data() + sizeof(lcview_record_hdr);
    const EventSchema* schema = sp.find(hdr->event_id);
    ASSERT_NE(schema, nullptr);
    writer.writeRecord(*schema, hdr, fields,
                       rec.size() - sizeof(lcview_record_hdr));

    // 可控 reader：内核计数器非零值注入
    ControlledDeviceReader reader;
    reader.overrun = 7;
    reader.totalRecords = 1000;
    reader.dropped = 3;
    reader.ringSize = 512 * 1024;
    reader.ioctlErr_ = 0;
    reader.eof = 2;

    int64_t overrunAccum = 0;
    ConserveBaseline conserve;
    RecordingHeartbeatWriter w;
    WindowStats window;  // R-09：窗口统计（峰值/速率/分类）透传
    // 首心跳：ioctl 正常 → 建基线（updateAndCheck 返回 false 不告警，字段
    // 仍须完整透传）
    emitHeartbeat(42, reader, writer, overrunAccum, 1, 900, 5, conserve, w,
                  window);

    EXPECT_EQ(w.last.loop, 42u);
    // overrunAccum 累计 reader.getOverrun()=7
    EXPECT_EQ(w.last.overrun, 7);
    // dropped 求和 = dropCounters 全分项（真实写入成功，全 0）
    EXPECT_EQ(w.last.dropped, 0u);
    EXPECT_EQ(w.last.readErr, 1u);
    EXPECT_EQ(w.last.totalRecords, 1000u);
    EXPECT_EQ(w.last.jsonlRecords, 900);
    EXPECT_EQ(w.last.invalidRecords, 5);
    EXPECT_EQ(w.last.ioctlErr, 0u);
    EXPECT_EQ(w.last.eofCount, 2u);
    // FileWriter 真实写入后：persistCounters.valid=1 → jsonl 落盘计数
    // （注：jsonlRecords 入参 900 是调用方累计，persistCounters 独立；
    //  守恒基线首心跳只建基线不告警）
    EXPECT_TRUE(conserve.initialized);
    EXPECT_EQ(conserve.total, 1000u);

    std::string cmd = "rm -rf " + std::string(tmp);
    system(cmd.c_str());
}
