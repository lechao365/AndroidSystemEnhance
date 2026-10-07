// block_collector_test.cpp — 块设备/zram 观测纯函数单测（R3 方向1/2/3）
// 覆盖目标：
//   ParseBlockStat：15 字段 / 11 字段（discard 补 0）/ 字段不足判 false
//   DiffBlockSamples：单调差分 / counter reset（curr<prev 取 curr）
//   ComputeBlockRates：正常速率与平均延迟数值 / 除零（ios=0、period=0）
//   ParseZramMmStat / ParseZramIoStat：字段缺省补 0
//   UpdateHangState：三连判 true / 中断清零 / 阈值边界（>= 语义）
//   EvaluateSlowDiskRule：严格大于边界 / 连续计数 / require_consecutive

#include <gtest/gtest.h>

#include <string>

#include "block_collector.h"

using namespace vendor::lechao::lcview;

namespace {

// 构造部分字段的采样（其余字段默认 0），供差分/速率测试使用
BlockStatSample makeSample(uint64_t readIos, uint64_t readSectors,
                           uint64_t readTicks, uint64_t writeIos,
                           uint64_t writeSectors, uint64_t writeTicks,
                           uint64_t ioTicks)
{
    BlockStatSample s;
    s.read_ios = readIos;
    s.read_sectors = readSectors;
    s.read_ticks_ms = readTicks;
    s.write_ios = writeIos;
    s.write_sectors = writeSectors;
    s.write_ticks_ms = writeTicks;
    s.io_ticks_ms = ioTicks;
    return s;
}

}  // namespace

// ============================================================
// ParseBlockStat
// ============================================================

TEST(BlockCollectorParseTest, ParseBlockStat_Full15Fields)
{
    const std::string line = "100 2 800 150 50 1 400 60 0 30 90 5 0 10 3";
    BlockStatSample s;
    ASSERT_TRUE(ParseBlockStat(line, &s));
    EXPECT_EQ(s.read_ios, 100u);
    EXPECT_EQ(s.read_merges, 2u);
    EXPECT_EQ(s.read_sectors, 800u);
    EXPECT_EQ(s.read_ticks_ms, 150u);
    EXPECT_EQ(s.write_ios, 50u);
    EXPECT_EQ(s.write_merges, 1u);
    EXPECT_EQ(s.write_sectors, 400u);
    EXPECT_EQ(s.write_ticks_ms, 60u);
    EXPECT_EQ(s.in_flight, 0u);
    EXPECT_EQ(s.io_ticks_ms, 30u);
    EXPECT_EQ(s.time_in_queue_ms, 90u);
    EXPECT_EQ(s.discard_ios, 5u);
    EXPECT_EQ(s.discard_merges, 0u);
    EXPECT_EQ(s.discard_sectors, 10u);
    EXPECT_EQ(s.discard_ticks_ms, 3u);
}

TEST(BlockCollectorParseTest, ParseBlockStat_11Fields_DiscardZero)
{
    // 旧内核格式：仅前 11 字段，discard 段补 0
    const std::string line = "100 2 800 150 50 1 400 60 0 30 90";
    BlockStatSample s;
    ASSERT_TRUE(ParseBlockStat(line, &s));
    EXPECT_EQ(s.read_ios, 100u);
    EXPECT_EQ(s.read_merges, 2u);
    EXPECT_EQ(s.read_sectors, 800u);
    EXPECT_EQ(s.read_ticks_ms, 150u);
    EXPECT_EQ(s.write_ios, 50u);
    EXPECT_EQ(s.write_ticks_ms, 60u);
    EXPECT_EQ(s.in_flight, 0u);
    EXPECT_EQ(s.io_ticks_ms, 30u);
    EXPECT_EQ(s.time_in_queue_ms, 90u);
    EXPECT_EQ(s.discard_ios, 0u);
    EXPECT_EQ(s.discard_merges, 0u);
    EXPECT_EQ(s.discard_sectors, 0u);
    EXPECT_EQ(s.discard_ticks_ms, 0u);
}

TEST(BlockCollectorParseTest, ParseBlockStat_TooFewFields_False)
{
    // 10 字段：不足 11，判 false
    const std::string line = "100 2 800 150 50 1 400 60 0 30";
    BlockStatSample s;
    EXPECT_FALSE(ParseBlockStat(line, &s));
    // 空行同样判 false
    EXPECT_FALSE(ParseBlockStat("", &s));
    // 空指针判 false
    EXPECT_FALSE(ParseBlockStat(line, nullptr));
}

// ============================================================
// DiffBlockSamples
// ============================================================

TEST(BlockCollectorDiffTest, DiffBlockSamples_Monotonic)
{
    const BlockStatSample prev = makeSample(100, 800, 150, 50, 400, 60, 30);
    const BlockStatSample curr = makeSample(130, 1600, 300, 80, 800, 120, 60);
    const BlockStatDelta d = DiffBlockSamples(prev, curr);
    EXPECT_EQ(d.read_ios, 30u);
    EXPECT_EQ(d.read_sectors, 800u);
    EXPECT_EQ(d.read_ticks_ms, 150u);
    EXPECT_EQ(d.write_ios, 30u);
    EXPECT_EQ(d.write_sectors, 400u);
    EXPECT_EQ(d.write_ticks_ms, 60u);
    EXPECT_EQ(d.io_ticks_ms, 30u);
}

TEST(BlockCollectorDiffTest, DiffBlockSamples_CounterReset)
{
    // curr < prev（内核计数归零/设备重挂载）：取 curr（从 0 重算），
    // 差分不下溢
    const BlockStatSample prev = makeSample(1000, 8000, 1500, 500, 4000, 600, 300);
    const BlockStatSample curr = makeSample(10, 80, 15, 5, 40, 6, 3);
    const BlockStatDelta d = DiffBlockSamples(prev, curr);
    EXPECT_EQ(d.read_ios, 10u);
    EXPECT_EQ(d.read_sectors, 80u);
    EXPECT_EQ(d.read_ticks_ms, 15u);
    EXPECT_EQ(d.write_ios, 5u);
    EXPECT_EQ(d.write_sectors, 40u);
    EXPECT_EQ(d.write_ticks_ms, 6u);
    EXPECT_EQ(d.io_ticks_ms, 3u);
}

TEST(BlockCollectorDiffTest, DiffBlockSamples_Equal_Zero)
{
    // curr == prev：差分为 0
    const BlockStatSample a = makeSample(100, 800, 150, 50, 400, 60, 30);
    const BlockStatDelta d = DiffBlockSamples(a, a);
    EXPECT_EQ(d.read_ios, 0u);
    EXPECT_EQ(d.write_ticks_ms, 0u);
    EXPECT_EQ(d.io_ticks_ms, 0u);
}

// ============================================================
// ComputeBlockRates
// ============================================================

TEST(BlockCollectorRateTest, ComputeBlockRates_Normal)
{
    BlockStatDelta d;
    d.read_ios = 100;
    d.read_sectors = 800;    // 800 * 512 = 409600 B
    d.read_ticks_ms = 150;   // 150 / 100 = 1.5 ms
    d.write_ios = 50;
    d.write_sectors = 400;   // 400 * 512 = 204800 B
    d.write_ticks_ms = 200;  // 200 / 50 = 4 ms
    d.io_ticks_ms = 5000;    // 5000 / 10000 = 0.5 busy
    const uint64_t kPeriodNs = 10ull * 1000 * 1000 * 1000;  // 10s
    const BlockRates r = ComputeBlockRates(d, kPeriodNs);
    EXPECT_DOUBLE_EQ(r.read_iops, 10.0);       // 100 / 10s
    EXPECT_DOUBLE_EQ(r.read_bytes_per_s, 40960.0);   // 409600 / 10s
    EXPECT_DOUBLE_EQ(r.read_avg_lat_ms, 1.5);
    EXPECT_DOUBLE_EQ(r.write_iops, 5.0);       // 50 / 10s
    EXPECT_DOUBLE_EQ(r.write_bytes_per_s, 20480.0);  // 204800 / 10s
    EXPECT_DOUBLE_EQ(r.write_avg_lat_ms, 4.0);
    EXPECT_DOUBLE_EQ(r.busy_ratio, 0.5);
}

TEST(BlockCollectorRateTest, ComputeBlockRates_ZeroIos_ZeroLatAndIops)
{
    // ios=0 → iops 与平均延迟为 0；sectors 非 0 时吞吐仍按扇区计算
    BlockStatDelta d;
    d.read_sectors = 100;
    d.write_sectors = 100;
    d.io_ticks_ms = 1000;
    const uint64_t kPeriodNs = 10ull * 1000 * 1000 * 1000;  // 10s
    const BlockRates r = ComputeBlockRates(d, kPeriodNs);
    EXPECT_DOUBLE_EQ(r.read_iops, 0.0);
    EXPECT_DOUBLE_EQ(r.read_avg_lat_ms, 0.0);
    EXPECT_DOUBLE_EQ(r.read_bytes_per_s, 5120.0);  // 100*512/10
    EXPECT_DOUBLE_EQ(r.write_iops, 0.0);
    EXPECT_DOUBLE_EQ(r.write_avg_lat_ms, 0.0);
    EXPECT_DOUBLE_EQ(r.write_bytes_per_s, 5120.0);
    EXPECT_DOUBLE_EQ(r.busy_ratio, 0.1);
}

TEST(BlockCollectorRateTest, ComputeBlockRates_ZeroPeriod_ZeroRates)
{
    // period=0 → 速率类/busy 为 0；平均延迟只依赖 ios，仍计算
    BlockStatDelta d;
    d.read_ios = 100;
    d.read_sectors = 800;
    d.read_ticks_ms = 150;
    d.write_ios = 50;
    d.write_sectors = 400;
    d.io_ticks_ms = 500;
    const BlockRates r = ComputeBlockRates(d, 0);
    EXPECT_DOUBLE_EQ(r.read_iops, 0.0);
    EXPECT_DOUBLE_EQ(r.read_bytes_per_s, 0.0);
    EXPECT_DOUBLE_EQ(r.write_iops, 0.0);
    EXPECT_DOUBLE_EQ(r.write_bytes_per_s, 0.0);
    EXPECT_DOUBLE_EQ(r.busy_ratio, 0.0);
    EXPECT_DOUBLE_EQ(r.read_avg_lat_ms, 1.5);  // 150/100，不依赖 period
}

TEST(BlockCollectorRateTest, ComputeBlockRates_BusyClamped)
{
    // io_ticks 超窗口（采样抖动）→ busy 钳制到 1.0
    BlockStatDelta d;
    d.io_ticks_ms = 15000;  // 窗口 10s
    const uint64_t kPeriodNs = 10ull * 1000 * 1000 * 1000;
    const BlockRates r = ComputeBlockRates(d, kPeriodNs);
    EXPECT_DOUBLE_EQ(r.busy_ratio, 1.0);
}

// ============================================================
// ParseZramMmStat / ParseZramIoStat
// ============================================================

TEST(BlockCollectorZramTest, ParseZramMmStat_Full8Fields)
{
    const std::string line = "1048576 524288 262144 0 2097152 1024 512 8";
    ZramMmSample s;
    ASSERT_TRUE(ParseZramMmStat(line, &s));
    EXPECT_EQ(s.orig_data_size, 1048576u);
    EXPECT_EQ(s.compr_data_size, 524288u);
    EXPECT_EQ(s.mem_used_total, 262144u);
    EXPECT_EQ(s.mem_limit, 0u);
    EXPECT_EQ(s.mem_used_max, 2097152u);
    EXPECT_EQ(s.same_pages, 1024u);
    EXPECT_EQ(s.pages_compacted, 512u);
    EXPECT_EQ(s.huge_pages, 8u);
}

TEST(BlockCollectorZramTest, ParseZramMmStat_MissingHugePages_DefaultZero)
{
    // mm_stat 原生 7 字段（无 huge_pages）：缺省补 0
    const std::string line = "1048576 524288 262144 0 2097152 1024 512";
    ZramMmSample s;
    ASSERT_TRUE(ParseZramMmStat(line, &s));
    EXPECT_EQ(s.orig_data_size, 1048576u);
    EXPECT_EQ(s.compr_data_size, 524288u);
    EXPECT_EQ(s.mem_used_total, 262144u);
    EXPECT_EQ(s.pages_compacted, 512u);
    EXPECT_EQ(s.huge_pages, 0u);
}

TEST(BlockCollectorZramTest, ParseZramMmStat_Empty_False)
{
    ZramMmSample s;
    EXPECT_FALSE(ParseZramMmStat("", &s));
    EXPECT_FALSE(ParseZramMmStat("", nullptr));
}

TEST(BlockCollectorZramTest, ParseZramIoStat_SixFields)
{
    const std::string line = "1 2 3 4 500 600";
    ZramIoSample s;
    ASSERT_TRUE(ParseZramIoStat(line, &s));
    EXPECT_EQ(s.failed_reads, 1u);
    EXPECT_EQ(s.failed_writes, 2u);
    EXPECT_EQ(s.invalid_io, 3u);
    EXPECT_EQ(s.notify_free, 4u);
    EXPECT_EQ(s.read_bytes, 500u);
    EXPECT_EQ(s.write_bytes, 600u);
}

TEST(BlockCollectorZramTest, ParseZramIoStat_FourFields_MissingDefaultZero)
{
    // 旧内核仅 4 字段：read/write_bytes 补 0
    const std::string line = "1 2 3 4";
    ZramIoSample s;
    ASSERT_TRUE(ParseZramIoStat(line, &s));
    EXPECT_EQ(s.failed_reads, 1u);
    EXPECT_EQ(s.failed_writes, 2u);
    EXPECT_EQ(s.invalid_io, 3u);
    EXPECT_EQ(s.notify_free, 4u);
    EXPECT_EQ(s.read_bytes, 0u);
    EXPECT_EQ(s.write_bytes, 0u);
}

// ============================================================
// UpdateHangState（悬挂检测）
// ============================================================

TEST(BlockCollectorHangTest, UpdateHangState_ThreeConsecutive_True)
{
    HangState h;
    EXPECT_FALSE(UpdateHangState(&h, 100, 32, 1500.0, 1000.0));  // 1
    EXPECT_FALSE(UpdateHangState(&h, 100, 32, 1500.0, 1000.0));  // 2
    EXPECT_TRUE(UpdateHangState(&h, 100, 32, 1500.0, 1000.0));   // 3
    EXPECT_EQ(h.consecutive_high, 3u);
    // 继续满足仍 true（>=3）
    EXPECT_TRUE(UpdateHangState(&h, 100, 32, 1500.0, 1000.0));   // 4
    EXPECT_EQ(h.consecutive_high, 4u);
}

TEST(BlockCollectorHangTest, UpdateHangState_Interrupted_Reset)
{
    HangState h;
    UpdateHangState(&h, 100, 32, 1500.0, 1000.0);  // 1
    UpdateHangState(&h, 100, 32, 1500.0, 1000.0);  // 2
    // 写延迟低于阈值 → 清零
    EXPECT_FALSE(UpdateHangState(&h, 100, 32, 500.0, 1000.0));
    EXPECT_EQ(h.consecutive_high, 0u);
    // 重新计数至触发
    EXPECT_FALSE(UpdateHangState(&h, 100, 32, 1500.0, 1000.0));  // 1
    EXPECT_FALSE(UpdateHangState(&h, 100, 32, 1500.0, 1000.0));  // 2
    EXPECT_TRUE(UpdateHangState(&h, 100, 32, 1500.0, 1000.0));   // 3
}

TEST(BlockCollectorHangTest, UpdateHangState_ThresholdBoundary_Ge)
{
    HangState h;
    // 等于阈值（>= 语义）也计数
    EXPECT_FALSE(UpdateHangState(&h, 32, 32, 1000.0, 1000.0));
    EXPECT_EQ(h.consecutive_high, 1u);
    // inflight 低于阈值 → 清零
    EXPECT_FALSE(UpdateHangState(&h, 31, 32, 1000.0, 1000.0));
    EXPECT_EQ(h.consecutive_high, 0u);
    // 延迟低于阈值 → 清零
    EXPECT_FALSE(UpdateHangState(&h, 32, 32, 999.0, 1000.0));
    EXPECT_EQ(h.consecutive_high, 0u);
}

TEST(BlockCollectorHangTest, UpdateHangState_NullState_False)
{
    EXPECT_FALSE(UpdateHangState(nullptr, 100, 32, 1500.0, 1000.0));
}

// ============================================================
// EvaluateSlowDiskRule（规则二）
// ============================================================

TEST(BlockCollectorRuleTest, EvaluateSlowDiskRule_ThresholdBoundary_StrictGt)
{
    SlowDiskState s;
    // 等于阈值不计数（严格大于）→ 保持 0
    EXPECT_FALSE(EvaluateSlowDiskRule(&s, 200.0, 200.0, 3));
    EXPECT_EQ(s.consecutive, 0u);
    // 低于阈值清零
    EXPECT_FALSE(EvaluateSlowDiskRule(&s, 100.0, 200.0, 3));
    EXPECT_EQ(s.consecutive, 0u);
    // 高于阈值计数，3 连触发
    EXPECT_FALSE(EvaluateSlowDiskRule(&s, 200.001, 200.0, 3));  // 1
    EXPECT_FALSE(EvaluateSlowDiskRule(&s, 210.0, 200.0, 3));    // 2
    EXPECT_TRUE(EvaluateSlowDiskRule(&s, 220.0, 200.0, 3));     // 3
    EXPECT_EQ(s.consecutive, 3u);
}

TEST(BlockCollectorRuleTest, EvaluateSlowDiskRule_RequireConsecutiveOne)
{
    SlowDiskState s;
    EXPECT_TRUE(EvaluateSlowDiskRule(&s, 201.0, 200.0, 1));
    EXPECT_EQ(s.consecutive, 1u);
}

TEST(BlockCollectorRuleTest, EvaluateSlowDiskRule_TriggerDoesNotReset)
{
    // 触发后纯函数不自行重置（由主循环调用方收敛事件频率）
    SlowDiskState s;
    for (int i = 0; i < 3; ++i)
        EvaluateSlowDiskRule(&s, 300.0, 200.0, 3);
    EXPECT_TRUE(EvaluateSlowDiskRule(&s, 300.0, 200.0, 3));  // 第 4 次仍 true
    EXPECT_EQ(s.consecutive, 4u);
    // 条件回落 → 清零
    EXPECT_FALSE(EvaluateSlowDiskRule(&s, 50.0, 200.0, 3));
    EXPECT_EQ(s.consecutive, 0u);
}

TEST(BlockCollectorRuleTest, EvaluateSlowDiskRule_NullState_False)
{
    EXPECT_FALSE(EvaluateSlowDiskRule(nullptr, 300.0, 200.0, 3));
}
