// ============================================================
// WritebackGuard_test.cpp — 拔盘韧性守护纯函数单元测试
// 所属模块：LcIod IO 健康监控 — 测试
// 覆盖（无 I/O，确定性）：
//   ParseBackingTarget — 正常路径/删除标记/none/空串/空白；
//   EvaluateWritebackHealthy — 健康/目标缺失/deleted/none；
//   FormatWritebackGuardJson — lost/recovered 事件 JSON 字段契约；
//   常量契约 — 事件 id=21、巡检周期 60s。
// 注：QueryWritebackSupply 依赖真实 sysfs，交由板端
//     lciod_check --mode writeback-guard 闭环验证。
// ============================================================

#include "writeback_guard.h"

#include <gtest/gtest.h>

#include <cstring>
#include <string>

namespace lechao {
namespace lciod {
namespace {

TEST(WritebackGuardParse, BasicPath) {
    EXPECT_EQ(ParseBackingTarget("/dev/block/sdb"), "sdb");
    EXPECT_EQ(ParseBackingTarget("  /dev/block/sdb\n"), "sdb");
}

TEST(WritebackGuardParse, DeletedSuffix) {
    // 内核设备移除后追加 " (deleted)"，设备名仍可解析
    EXPECT_EQ(ParseBackingTarget("/dev/block/sdb (deleted)"), "sdb");
}

TEST(WritebackGuardParse, EmptyAndNone) {
    EXPECT_EQ(ParseBackingTarget(""), "");
    EXPECT_EQ(ParseBackingTarget("   \n"), "");
    EXPECT_EQ(ParseBackingTarget("none"), "");
}

TEST(WritebackGuardHealthy, HealthyWhenTargetPresent) {
    EXPECT_TRUE(EvaluateWritebackHealthy("/dev/block/sdb", true));
}

TEST(WritebackGuardHealthy, LostWhenTargetMissing) {
    EXPECT_FALSE(EvaluateWritebackHealthy("/dev/block/sdb", false));
}

TEST(WritebackGuardHealthy, LostWhenDeleted) {
    // 即便 /sys/block/sdb 仍存在，deleted 标记表示写回目标已失效
    EXPECT_FALSE(EvaluateWritebackHealthy("/dev/block/sdb (deleted)", true));
}

TEST(WritebackGuardHealthy, LostWhenNone) {
    EXPECT_FALSE(EvaluateWritebackHealthy("none", true));
    EXPECT_FALSE(EvaluateWritebackHealthy("", true));
}

TEST(WritebackGuardJson, LostEventContract) {
    char line[384] = {0};
    const int n = FormatWritebackGuardJson(true, "/dev/block/sdb (deleted)",
                                           false, line, sizeof(line));
    ASSERT_GT(n, 0);
    EXPECT_NE(std::string(line).find("\"rule\":\"writeback_guard\""),
              std::string::npos);
    EXPECT_NE(std::string(line).find("\"state\":\"lost\""), std::string::npos);
    EXPECT_NE(std::string(line).find("\"lost\":1"), std::string::npos);
    EXPECT_NE(std::string(line).find("\"target_present\":0"), std::string::npos);
}

TEST(WritebackGuardJson, RecoveredEventContract) {
    char line[384] = {0};
    const int n = FormatWritebackGuardJson(false, "/dev/block/sdc", true,
                                           line, sizeof(line));
    ASSERT_GT(n, 0);
    EXPECT_NE(std::string(line).find("\"state\":\"recovered\""),
              std::string::npos);
    EXPECT_NE(std::string(line).find("\"lost\":0"), std::string::npos);
    EXPECT_NE(std::string(line).find("\"target_present\":1"), std::string::npos);
}

TEST(WritebackGuardJson, InvalidBufferRejected) {
    EXPECT_EQ(FormatWritebackGuardJson(true, "x", false, nullptr, 0), -1);
}

TEST(WritebackGuardContract, EventIdAndInterval) {
    EXPECT_EQ(kEventWritebackGuardId, 21u);
    EXPECT_EQ(kWritebackGuardIntervalSec, 60u);
}

}  // namespace
}  // namespace lciod
}  // namespace lechao
