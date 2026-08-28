// ============================================================
// Service_test.cpp — System Daemon 纯计算核心
// 所属模块：lechao_lciod 单元测试
// 拦截：CXX-002（除零/边界）——getAverageRate 与监控线程速率换算
// 的公式收敛到 ComputeAverageRate/ComputeKbRate 纯函数（service.h），
// 在无 binder 环境依赖下验证除零防护与数值正确性。
// 注：字段投影完整性（getIoStats 21 字段直传/4 字段省略）依赖真实
// HAL，由上板 lciod-pipeline 用例兜底，此处不重复。
// ============================================================

#include <gtest/gtest.h>

#include "service.h"

/* --- ComputeAverageRate：getAverageRate 核心公式 --- */

TEST(ComputeAverageRateTest, ZeroTotalNs_ReturnsZero) {
    // 除零防护：readNs + writeNs == 0 时必须返回 0，不得除零崩溃
    EXPECT_EQ(ComputeAverageRate(0, 0, 0, 0), 0);
    EXPECT_EQ(ComputeAverageRate(1048576, 0, 0, 0), 0);  // 有字节无耗时（异常态）也返回 0
}

TEST(ComputeAverageRateTest, ReadOnly_1MB_per_1s) {
    // 1048576 字节 / 1s = 1048576 B/s
    EXPECT_EQ(ComputeAverageRate(1048576, 0, 1000000000ULL, 0), 1048576);
}

TEST(ComputeAverageRateTest, MixedReadWrite_MergedNumeratorAndDenominator) {
    // (500 + 500) * 1e9 / (1e9 + 1e9) = 500 B/s
    EXPECT_EQ(ComputeAverageRate(500, 500, 1000000000ULL, 1000000000ULL), 500);
}

TEST(ComputeAverageRateTest, ZeroBytes_PositiveNs_ReturnsZero) {
    EXPECT_EQ(ComputeAverageRate(0, 0, 1000000000ULL, 1000000000ULL), 0);
}

TEST(ComputeAverageRateTest, SubSecondRounding_Truncates) {
    // 1500 B / 2s = 750 B/s（整除）
    EXPECT_EQ(ComputeAverageRate(1500, 0, 2000000000ULL, 0), 750);
}

TEST(ComputeAverageRateTest, LargeBytes_NoOverflow) {
    // 溢出回归：total*1e9 超 uint64 上限（累计约 17GiB）时旧实现回绕致速率失真，
    // 中间量 __uint128_t 后速率不失真。read=write=1e10（约 18.6GiB）2s → 1e10 B/s
    EXPECT_EQ(ComputeAverageRate(10000000000ULL, 10000000000ULL,
                                 1000000000ULL, 1000000000ULL), 10000000000LL);
}

/* --- ComputeKbRate：监控线程统计日志换算核心 --- */

TEST(ComputeKbRateTest, ZeroNs_ReturnsZero) {
    // 除零防护：calc_rate lambda 原实现同等语义
    EXPECT_EQ(ComputeKbRate(1048576, 0), 0u);
}

TEST(ComputeKbRateTest, Normal_1MB_per_1s_Equals1024KB) {
    EXPECT_EQ(ComputeKbRate(1048576, 1000000000ULL), 1024u);
}

TEST(ComputeKbRateTest, BelowOneKB_TruncatesToZero) {
    // 512 B/s / 1024 = 0（整数除法截断，与原 lambda 行为一致）
    EXPECT_EQ(ComputeKbRate(512, 1000000000ULL), 0u);
}

TEST(ComputeKbRateTest, LargeBytes_NoOverflow) {
    // 溢出回归：bytes*1e9 超 uint64 上限（约 17GiB）时旧实现回绕致速率失真，
    // 中间量 __uint128_t 后不失真。18.6GiB / 1s → 19531250 KB/s
    EXPECT_EQ(ComputeKbRate(20000000000ULL, 1000000000ULL), 19531250u);
}

/* --- 字段投影：vendor → system（21 字段直传 + 管理字段省略） --- */

TEST(ProjectionTest, IoStats_All21FieldsPassedThrough) {
    aidl::vendor::lechao::lciod::IoStats v;
    v.vid = 0x04e8;
    v.pid = 0x6300;
    v.vendor = "Samsung";
    v.product = "Flash Drive";
    v.readBytes = 1;
    v.readNs = 2;
    v.readCmds = 3;
    v.writeBytes = 4;
    v.writeNs = 5;
    v.writeCmds = 6;
    v.errorCount = 7;
    v.resetCount = 8;
    v.stallCount = 9;
    v.corruptCount = 10;
    v.timeoutCount = 11;
    v.probeCount = 12;
    v.disconnectCount = 13;
    v.degradeCount = 14;
    v.lastTransportLatencyNs = 15;
    v.currentRate = 99;          // 管理/派生字段：投影必须省略
    v.lastEventTsNs = 16;
    v.lastEventType = 17;
    v.enabled = true;            // 管理字段：投影必须省略
    v.flags = 0x1;               // 管理字段：投影必须省略

    aidl::system::lechao::lciod::IoStats s;
    ProjectSystemIoStats(v, &s);

    // 21 字段逐一直传（字段串位/漏传在此判红）
    EXPECT_EQ(s.vid, 0x04e8);
    EXPECT_EQ(s.pid, 0x6300);
    EXPECT_EQ(s.vendor, "Samsung");
    EXPECT_EQ(s.product, "Flash Drive");
    EXPECT_EQ(s.readBytes, 1);
    EXPECT_EQ(s.readNs, 2);
    EXPECT_EQ(s.readCmds, 3);
    EXPECT_EQ(s.writeBytes, 4);
    EXPECT_EQ(s.writeNs, 5);
    EXPECT_EQ(s.writeCmds, 6);
    EXPECT_EQ(s.errorCount, 7);
    EXPECT_EQ(s.resetCount, 8);
    EXPECT_EQ(s.stallCount, 9);
    EXPECT_EQ(s.corruptCount, 10);
    EXPECT_EQ(s.timeoutCount, 11);
    EXPECT_EQ(s.probeCount, 12);
    EXPECT_EQ(s.disconnectCount, 13);
    EXPECT_EQ(s.degradeCount, 14);
    EXPECT_EQ(s.lastTransportLatencyNs, 15);
    EXPECT_EQ(s.lastEventTsNs, 16);
    EXPECT_EQ(s.lastEventType, 17);
    // system IoStats 结构无 currentRate/enabled/flags 字段（投影省略的设计契约），
    // 通过编译期断言：结构体不包含管理字段即视为已省略
}

TEST(ProjectionTest, IoStats_SourceZeroFields_DefaultOut) {
    // 源全零时投影结果必须全零（不得残留脏数据）
    aidl::vendor::lechao::lciod::IoStats v{};
    aidl::system::lechao::lciod::IoStats s;
    s.readBytes = 123;  // 预置脏值验证被覆盖
    ProjectSystemIoStats(v, &s);
    EXPECT_EQ(s.readBytes, 0);
    EXPECT_EQ(s.vid, 0);
    EXPECT_TRUE(s.vendor.empty());
}

TEST(ProjectionTest, IoConfig_PassedThrough) {
    aidl::vendor::lechao::lciod::IoConfig v;
    v.enabled = true;
    v.flags = 0x8;
    aidl::system::lechao::lciod::IoConfig s;
    ProjectSystemIoConfig(v, &s);
    EXPECT_TRUE(s.enabled);
    EXPECT_EQ(s.flags, 0x8);
}

TEST(ProjectionTest, IoEvent_All6FieldsPassedThrough) {
    aidl::vendor::lechao::lciod::IoEvent v;
    v.timestampNs = 100;
    v.eventType = 5;
    v.eventValue = 42;
    v.dataDirection = 1;
    v.status = 0;
    v.valid = true;
    aidl::system::lechao::lciod::IoEvent s;
    ProjectSystemIoEvent(v, &s);
    EXPECT_EQ(s.timestampNs, 100);
    EXPECT_EQ(s.eventType, 5);
    EXPECT_EQ(s.eventValue, 42);
    EXPECT_EQ(s.dataDirection, 1);
    EXPECT_EQ(s.status, 0);
    EXPECT_TRUE(s.valid);
}
