// ============================================================
// Service_test.cpp — System Daemon 纯计算核心
// 所属模块：lechao_lciod 单元测试
// 拦截：CXX-002（除零/边界）——getAverageRate 与监控线程速率换算
// 的公式收敛到 ComputeAverageRate/ComputeKbRate 纯函数（service.h），
// 在无 binder 环境依赖下验证除零防护与数值正确性。
// 注：字段投影完整性（getIoStats 24 字段直传/管理字段省略）依赖真实
// HAL，由上板 lciod-pipeline 用例兜底，此处不重复。
// ============================================================

#include <gtest/gtest.h>

#include "service.h"
/* R2 方向 5+6：规则一纯函数（供电不足归因）单测 */
#include "link_monitor.h"
/* 链路事件类型枚举名（单一事实源） */
#include "vendor_lechao_usbd-ioctl.h"
/* R2 方向 5+6：规则一纯函数位于 lechao::lciod 命名空间 */
using lechao::lciod::ApplyPowerSuspectRule;

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

/* --- ComputeWindowKbRate：10 秒 tick 差分时间桶吞吐（R-12 方向 3） --- */

TEST(ComputeWindowKbRateTest, NoSnapshot_FallsBackToCumulative)
{
    // 设备新接入（prev=0）：窗口增量即全程累计，等同旧行为
    EXPECT_EQ(ComputeWindowKbRate(1048576, 1000000000ULL, 0, 0), 1024u);
}

TEST(ComputeWindowKbRateTest, WindowDelta_ReflectsRecentWindow)
{
    // 全程累计 1MB/s，但本窗口（10s）只传了 10KB/10s = 1KB/s——差分桶
    // 应返回窗口即时速率 1KB/s（旧累计平均会摊平为 1MB/s 无法定位变慢）
    uint64_t prevBytes = 1024ULL * 1024ULL * 100;  // 100MB 累计
    uint64_t prevNs = 100ULL * 1000000000ULL;      // 100s
    uint64_t currBytes = prevBytes + 10 * 1024;    // 本窗口 +10KB
    uint64_t currNs = prevNs + 10 * 1000000000ULL; // 本窗口 +10s
    EXPECT_EQ(ComputeWindowKbRate(currBytes, currNs, prevBytes, prevNs), 1u);
}

TEST(ComputeWindowKbRateTest, WindowDelta_BelowOneKB_TruncatesToZero)
{
    // 本窗口 512B / 10s → 0（整数截断，与 ComputeKbRate 一致）
    uint64_t prevBytes = 1024 * 1024, prevNs = 100 * 1000000000ULL;
    EXPECT_EQ(ComputeWindowKbRate(prevBytes + 512, prevNs + 10 * 1000000000ULL, prevBytes, prevNs),
              0u);
}

TEST(ComputeWindowKbRateTest, CounterWrap_FallsBackToCumulative)
{
    // 计数回绕（curr < prev，容器/环重置）：差分无意义，回退全程累计
    EXPECT_EQ(ComputeWindowKbRate(1048576, 1000000000ULL, 2000000, 2000000000ULL),
              ComputeKbRate(1048576, 1000000000ULL));
}

/* --- 字段投影：vendor → system（24 字段直传 + 管理字段省略） --- */

TEST(ProjectionTest, IoStats_All24FieldsPassedThrough) {
    aidl::vendor::lechao::lciod::IoStats v;
    v.vid = 0x04e8;
    v.pid = 0x6300;
    v.protocol = 1;          // R1 UAS 维测：传输协议直传（BOT=0/UAS=1）
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
    v.readErrorCount = 25;          // R-14 方向 2：读方向错误分项（v3）
    v.writeErrorCount = 26;         // R-14 方向 2：写方向错误分项（v3）
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

    // 24 字段逐一直传（字段串位/漏传在此判红）
    EXPECT_EQ(s.vid, 0x04e8);
    EXPECT_EQ(s.pid, 0x6300);
    EXPECT_EQ(s.protocol, 1);
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
    EXPECT_EQ(s.readErrorCount, 25);
    EXPECT_EQ(s.writeErrorCount, 26);
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
    EXPECT_EQ(s.protocol, 0);
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

TEST(ProjectionTest, IoEvent_All11FieldsPassedThrough) {
    aidl::vendor::lechao::lciod::IoEvent v;
    v.timestampNs = 100;
    v.eventType = 5;
    v.eventValue = 42;
    v.dataDirection = 1;
    v.status = 0;
    v.valid = true;
    v.wallTimeNs = 200;   // R-14 方向 4：wall 双时间戳
    v.opcode = 0x28;      // R-14 方向 1：SCSI READ(10)
    v.lba = 0x1000;       // R-14 方向 1：起始 LBA
    v.bytes = 4096;       // R-14 方向 1：有效传输字节数
    v.retry = 2;          // R-14 方向 1：重试次数
    aidl::system::lechao::lciod::IoEvent s;
    ProjectSystemIoEvent(v, &s);
    EXPECT_EQ(s.timestampNs, 100);
    EXPECT_EQ(s.eventType, 5);
    EXPECT_EQ(s.eventValue, 42);
    EXPECT_EQ(s.dataDirection, 1);
    EXPECT_EQ(s.status, 0);
    EXPECT_TRUE(s.valid);
    EXPECT_EQ(s.wallTimeNs, 200);
    EXPECT_EQ(s.opcode, 0x28);
    EXPECT_EQ(s.lba, 0x1000);
    EXPECT_EQ(s.bytes, 4096);
    EXPECT_EQ(s.retry, 2);
}

/* --- ComputeErrorRate：读写方向 IO 错误率（R-14 方向 2） --- */

TEST(ComputeErrorRateTest, ZeroTotalIO_ReturnsZero) {
    // 无成功 IO 且无错误 → 总 IO=0，除零防护返回 0
    EXPECT_EQ(ComputeErrorRate(0, 0), 0u);
}

TEST(ComputeErrorRateTest, NoError_ReturnsZero) {
    EXPECT_EQ(ComputeErrorRate(0, 1000), 0u);
}

TEST(ComputeErrorRateTest, OneErrorPerTenIO_Returns909PerMille) {
    // 10 次成功 + 1 次错误 = 总 IO 11，错误占比 1/11 → 90.9‰ → 截断 90
    EXPECT_EQ(ComputeErrorRate(1, 10), 90u);
}

TEST(ComputeErrorRateTest, HalfErrors_Returns500PerMille) {
    EXPECT_EQ(ComputeErrorRate(5, 5), 500u);
}

TEST(ComputeErrorRateTest, AllErrors_Returns1000PerMille) {
    EXPECT_EQ(ComputeErrorRate(8, 0), 1000u);
}

TEST(ComputeErrorRateTest, LargeCounters_NoOverflow) {
    // 累计计数接近 uint64 上限时 errorCount*1000 不溢出（128 位中间量）
    uint64_t big = 9000000000000000000ULL;
    EXPECT_EQ(ComputeErrorRate(big, big), 500u);
}

/* --- ApplyPowerSuspectRule：供电不足归因规则一（R2 方向 5+6） --- */

TEST(ApplyPowerSuspectRuleTest, Disconnect_InWindow_ThrottledNonZero_IsSuspect) {
    // 掉线事件发生在 1s 前（5s 窗口内），throttled 非零 → 标 power_suspect
    const uint64_t now = 2000000000000ULL;
    EXPECT_TRUE(ApplyPowerSuspectRule(now - 1000000000ULL, now, 0x10000,
                                      VENDOR_LECHAO_USBD_EVENT_LINK_DISCONNECT));
}

TEST(ApplyPowerSuspectRuleTest, Disconnect_OutOfWindow_NotSuspect) {
    // 掉线事件发生在 10s 前（超 5s 窗口），即使 throttled 非零也不标
    const uint64_t now = 2000000000000ULL;
    EXPECT_FALSE(ApplyPowerSuspectRule(now - 10000000000ULL, now, 0x10000,
                                       VENDOR_LECHAO_USBD_EVENT_LINK_DISCONNECT));
}

TEST(ApplyPowerSuspectRuleTest, Overcurrent_InWindow_ThrottledNonZero_IsSuspect) {
    // 过流事件窗口内 + throttled 非零 → 标 power_suspect
    const uint64_t now = 2000000000000ULL;
    EXPECT_TRUE(ApplyPowerSuspectRule(now - 1000000000ULL, now, 0x1,
                                      VENDOR_LECHAO_USBD_EVENT_LINK_OVERCURRENT));
}

TEST(ApplyPowerSuspectRuleTest, NonLinkEvent_NotSuspect) {
    // 非链路事件（STALL）不参与供电归因，即使窗口内 throttled 非零
    const uint64_t now = 2000000000000ULL;
    EXPECT_FALSE(ApplyPowerSuspectRule(now - 1000000000ULL, now, 0x10000,
                                       VENDOR_LECHAO_USBD_EVENT_STALL));
}

TEST(ApplyPowerSuspectRuleTest, ThrottledZero_NotSuspect) {
    // throttled 采样为 0（供电正常）→ 掉线事件不标
    const uint64_t now = 2000000000000ULL;
    EXPECT_FALSE(ApplyPowerSuspectRule(now - 1000000000ULL, now, 0,
                                       VENDOR_LECHAO_USBD_EVENT_LINK_DISCONNECT));
}

TEST(ApplyPowerSuspectRuleTest, WindowBoundary_Exactly5s_IsSuspect) {
    // 契约 now-event<=5s：正好 5s 边界在窗口内 → 标
    const uint64_t now = 2000000000000ULL;
    EXPECT_TRUE(ApplyPowerSuspectRule(now - 5000000000ULL, now, 0x10000,
                                      VENDOR_LECHAO_USBD_EVENT_LINK_DISCONNECT));
}

TEST(ApplyPowerSuspectRuleTest, EventInFuture_NotSuspect) {
    // 防御：事件时间戳晚于当前（时钟异常）→ 按窗口外处理，防无符号回绕
    const uint64_t now = 2000000000000ULL;
    EXPECT_FALSE(ApplyPowerSuspectRule(now + 1000000000ULL, now, 0x10000,
                                       VENDOR_LECHAO_USBD_EVENT_LINK_DISCONNECT));
}
