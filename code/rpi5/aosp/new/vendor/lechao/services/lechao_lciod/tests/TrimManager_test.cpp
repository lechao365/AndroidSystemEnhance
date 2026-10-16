// ============================================================
// TrimManager_test.cpp — ext4 碎片清理链（TrimManager）单元测试（UP-110 批一）
// 所属模块：LcIod IO 健康监控 — 测试
// 覆盖：
//   纯函数层（无 I/O，确定性）：ComputeFreeRatioBp / ShouldTrim /
//   SegmentCount / SegmentAtFromTail / CircuitBreakerTripped——含除零、越界、
//   阈值边界、溢出钳制等防御路径（CXX-002/CXX-003）；
//   执行层 I/O 副作用项：StatMount 取值、ReadState/WriteState 状态文件
//   往返与非法内容拒绝（临时目录 /data/local/tmp，测试内建即清）。
// 注：ExecuteSegmentedTrim 依赖真实挂载点 FITRIM（破坏/系统态相关），
//     交由板端 lciod_check --mode trim 闭环验证，此处不构造 real ioctl。
// ============================================================

#include "trim_manager.h"

#include <gtest/gtest.h>

#include <cstdio>
#include <fstream>
#include <string>
#include <unistd.h>

namespace lechao {
namespace lciod {
namespace {

// ---------------- ComputeFreeRatioBp ----------------

TEST(TrimPure, FreeRatioBasic) {
    EXPECT_EQ(ComputeFreeRatioBp(0, 100), 0u);
    EXPECT_EQ(ComputeFreeRatioBp(50, 100), 5000u);
    EXPECT_EQ(ComputeFreeRatioBp(100, 100), 10000u);
    EXPECT_EQ(ComputeFreeRatioBp(1, 4), 2500u);
}

TEST(TrimPure, FreeRatioDivideByZeroReturnsZero) {
    // CXX-002：total==0 除零防护
    EXPECT_EQ(ComputeFreeRatioBp(123, 0), 0u);
}

TEST(TrimPure, FreeRatioClampsOverTotal) {
    // 异常输入 free>total：钳制到 100%（万分数 10000）
    EXPECT_EQ(ComputeFreeRatioBp(200, 100), 10000u);
}

// ---------------- ShouldTrim ----------------

TEST(TrimDecision, DisabledAlwaysFalse) {
    EXPECT_FALSE(ShouldTrim(false, true, 10000, 5000, 999, 7, true));
    EXPECT_FALSE(ShouldTrim(false, false, 10000, 5000, 999, 7, false));
}

TEST(TrimDecision, DrillForcesWhenEnabled) {
    // 演练强制：即便空闲率/天数不达标，开启即决策通过
    EXPECT_TRUE(ShouldTrim(true, true, 0, 5000, 0, 7, false));
}

TEST(TrimDecision, NeverTrimmedAllowsFirst) {
    EXPECT_TRUE(ShouldTrim(true, false, 0, 5000, 0, 7, true));
}

TEST(TrimDecision, ThresholdAndDaysBothRequired) {
    // 空闲率达标但天数不足 → 拒
    EXPECT_FALSE(ShouldTrim(true, false, 5000, 5000, 6, 7, false));
    // 天数达标但空闲率不足 → 拒
    EXPECT_FALSE(ShouldTrim(true, false, 4999, 5000, 7, 7, false));
    // 两者同时达标 → 通过
    EXPECT_TRUE(ShouldTrim(true, false, 5000, 5000, 7, 7, false));
    EXPECT_TRUE(ShouldTrim(true, false, 8000, 5000, 30, 7, false));
}

// ---------------- SegmentCount ----------------

TEST(TrimSegment, CountCeil) {
    EXPECT_EQ(SegmentCount(100, 32), 4u);     // ceil(3.125)
    EXPECT_EQ(SegmentCount(64, 32), 2u);
    EXPECT_EQ(SegmentCount(33, 32), 2u);
    EXPECT_EQ(SegmentCount(1, 32), 1u);
}

TEST(TrimSegment, CountEdges) {
    EXPECT_EQ(SegmentCount(0, 32), 0u);  // CXX-002
    EXPECT_EQ(SegmentCount(100, 0), 0u); // CXX-002
}

// ---------------- SegmentAtFromTail ----------------

TEST(TrimSegment, FromTailFirstIsFullTailSegment) {
    uint64_t start = 0, len = 0;
    // dev=100, seg=32, count=4；idx0 → start=68, len=32
    ASSERT_TRUE(SegmentAtFromTail(100, 32, 0, &start, &len));
    EXPECT_EQ(start, 68u);
    EXPECT_EQ(len, 32u);
}

TEST(TrimSegment, FromTailLastIsPartial) {
    uint64_t start = 0, len = 0;
    // idx3 → tail=100-96=4 → start=0, len=4（首段不满）
    ASSERT_TRUE(SegmentAtFromTail(100, 32, 3, &start, &len));
    EXPECT_EQ(start, 0u);
    EXPECT_EQ(len, 4u);
}

TEST(TrimSegment, FromTailOutOfRangeAndEdges) {
    uint64_t start = 0, len = 0;
    EXPECT_FALSE(SegmentAtFromTail(100, 32, 4, &start, &len));  // idx 越界
    EXPECT_FALSE(SegmentAtFromTail(0, 32, 0, &start, &len));    // 空设备
    EXPECT_FALSE(SegmentAtFromTail(100, 0, 0, &start, &len));   // 零段
    EXPECT_FALSE(SegmentAtFromTail(100, 32, 0, nullptr, &len)); // 空指针
    EXPECT_FALSE(SegmentAtFromTail(100, 32, 0, &start, nullptr));
}

// ---------------- CircuitBreakerTripped ----------------

TEST(TrimBreaker, TimeBudget) {
    EXPECT_FALSE(CircuitBreakerTripped(299999, 300000, 0, 8192));
    EXPECT_TRUE(CircuitBreakerTripped(300000, 300000, 0, 8192));
    EXPECT_TRUE(CircuitBreakerTripped(300001, 300000, 0, 8192));
}

TEST(TrimBreaker, SegmentCap) {
    EXPECT_FALSE(CircuitBreakerTripped(0, 300000, 8191, 8192));
    EXPECT_TRUE(CircuitBreakerTripped(0, 300000, 8192, 8192));
}

// ---------------- StatMount（执行层 I/O） ----------------

TEST(TrimExec, StatMountRoot) {
    uint64_t total = 0, freeb = 0;
    ASSERT_TRUE(TrimManager::StatMount("/", &total, &freeb));
    EXPECT_GT(total, 0u);
    EXPECT_LE(freeb, total);
}

TEST(TrimExec, StatMountNullArgs) {
    uint64_t total = 0, freeb = 0;
    EXPECT_FALSE(TrimManager::StatMount(nullptr, &total, &freeb));
    EXPECT_FALSE(TrimManager::StatMount("/", nullptr, &freeb));
    EXPECT_FALSE(TrimManager::StatMount("/", &total, nullptr));
}

// ---------------- ReadState / WriteState ----------------

TEST(TrimState, MissingFileMeansNeverTrimmed) {
    const std::string path = std::string("/data/local/tmp/lechao_trim_absent_") +
                             std::to_string(getpid());
    ::unlink(path.c_str());
    uint64_t epoch = 42;
    bool never = false;
    ASSERT_TRUE(TrimManager::ReadState(path.c_str(), &epoch, &never));
    EXPECT_TRUE(never);
    EXPECT_EQ(epoch, 42u);  // 未写入
}

TEST(TrimState, WriteReadRoundTrip) {
    const std::string dir = std::string("/data/local/tmp/lechao_trim_dir_") +
                            std::to_string(getpid());
    const std::string path = dir + "/trim-state";
    ::unlink(path.c_str());
    ::rmdir(dir.c_str());

    ASSERT_TRUE(TrimManager::WriteState(dir.c_str(), path.c_str(), 1700000000ull));

    uint64_t epoch = 0;
    bool never = true;
    ASSERT_TRUE(TrimManager::ReadState(path.c_str(), &epoch, &never));
    EXPECT_FALSE(never);
    EXPECT_EQ(epoch, 1700000000ull);

    ::unlink(path.c_str());
    ::rmdir(dir.c_str());
}

TEST(TrimState, InvalidContentRejected) {
    // CXX-003：非数字内容须拒绝（不得宽松解析）
    const std::string path = std::string("/data/local/tmp/lechao_trim_bad_") +
                             std::to_string(getpid());
    {
        std::ofstream ofs(path);
        ofs << "12a3\n";
    }
    uint64_t epoch = 0;
    bool never = false;
    EXPECT_FALSE(TrimManager::ReadState(path.c_str(), &epoch, &never));
    ::unlink(path.c_str());
}

TEST(TrimState, EmptyContentRejected) {
    const std::string path = std::string("/data/local/tmp/lechao_trim_empty_") +
                             std::to_string(getpid());
    {
        std::ofstream ofs(path);
        ofs << "\n";
    }
    uint64_t epoch = 0;
    bool never = false;
    EXPECT_FALSE(TrimManager::ReadState(path.c_str(), &epoch, &never));
    ::unlink(path.c_str());
}

TEST(TrimState, NullArgsRejected) {
    uint64_t epoch = 0;
    bool never = false;
    EXPECT_FALSE(TrimManager::ReadState(nullptr, &epoch, &never));
    EXPECT_FALSE(TrimManager::ReadState("/x", nullptr, &never));
    EXPECT_FALSE(TrimManager::ReadState("/x", &epoch, nullptr));
}

}  // namespace
}  // namespace lciod
}  // namespace lechao
