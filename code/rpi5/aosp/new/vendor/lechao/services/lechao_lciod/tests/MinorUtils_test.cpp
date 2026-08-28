// ============================================================
// MinorUtils_test.cpp — minor 解析/路径构造全边界
// 所属模块：lechao_lciod 单元测试
// 拦截：CXX-003（外部输入防御）——ParseMinorFromPath 对不可信路径的
// 严格校验（前缀/空后缀/非数字/±号/范围/strtol 溢出 ERANGE），
// 以及 minor=0 与解析失败的区分（消除 atoi 隐患的核心契约）
// ============================================================

#include <gtest/gtest.h>

#include "minor_utils.h"

using lechao::lciod::BuildDevicePath;
using lechao::lciod::kUsbdDevPrefix;
using lechao::lciod::ParseMinorFromPath;

/* --- 合法路径 --- */

TEST(ParseMinorFromPathTest, ValidZero_Minor0DistinguishableFromFailure) {
    int32_t minor = -1;
    EXPECT_TRUE(ParseMinorFromPath("/dev/vendor_lechao_usbd0", &minor));
    EXPECT_EQ(minor, 0);  // 解析成功且值为 0（atoi 方案无法区分）
}

TEST(ParseMinorFromPathTest, ValidMax_65535) {
    int32_t minor = -1;
    EXPECT_TRUE(ParseMinorFromPath("/dev/vendor_lechao_usbd65535", &minor));
    EXPECT_EQ(minor, 65535);
}

TEST(ParseMinorFromPathTest, ValidLeadingZeros) {
    int32_t minor = -1;
    EXPECT_TRUE(ParseMinorFromPath("/dev/vendor_lechao_usbd007", &minor));
    EXPECT_EQ(minor, 7);
}

/* --- 前缀防御 --- */

TEST(ParseMinorFromPathTest, WrongPrefix_Rejected) {
    int32_t minor = -1;
    EXPECT_FALSE(ParseMinorFromPath("/dev/other_node0", &minor));
}

TEST(ParseMinorFromPathTest, TruncatedPrefix_Rejected) {
    int32_t minor = -1;
    EXPECT_FALSE(ParseMinorFromPath("/dev/vendor_lechao_usb0", &minor));
}

TEST(ParseMinorFromPathTest, EmptyString_Rejected) {
    int32_t minor = -1;
    EXPECT_FALSE(ParseMinorFromPath("", &minor));
}

TEST(ParseMinorFromPathTest, BarePrefix_NoSuffix_Rejected) {
    int32_t minor = -1;
    EXPECT_FALSE(ParseMinorFromPath(kUsbdDevPrefix, &minor));
}

/* --- 后缀字符防御 --- */

TEST(ParseMinorFromPathTest, NonDigitSuffix_Rejected) {
    int32_t minor = -1;
    EXPECT_FALSE(ParseMinorFromPath("/dev/vendor_lechao_usbd12a", &minor));
}

TEST(ParseMinorFromPathTest, NegativeSuffix_Rejected) {
    int32_t minor = -1;
    EXPECT_FALSE(ParseMinorFromPath("/dev/vendor_lechao_usbd-1", &minor));
}

TEST(ParseMinorFromPathTest, PlusSuffix_Rejected) {
    int32_t minor = -1;
    EXPECT_FALSE(ParseMinorFromPath("/dev/vendor_lechao_usbd+5", &minor));
}

TEST(ParseMinorFromPathTest, HexSuffix_Rejected) {
    int32_t minor = -1;
    EXPECT_FALSE(ParseMinorFromPath("/dev/vendor_lechao_usbd0x1f", &minor));
}

TEST(ParseMinorFromPathTest, SpaceSuffix_Rejected) {
    int32_t minor = -1;
    EXPECT_FALSE(ParseMinorFromPath("/dev/vendor_lechao_usbd1 2", &minor));
}

/* --- 范围/溢出防御 --- */

TEST(ParseMinorFromPathTest, Overflow_65536_Rejected) {
    int32_t minor = -1;
    EXPECT_FALSE(ParseMinorFromPath("/dev/vendor_lechao_usbd65536", &minor));
}

TEST(ParseMinorFromPathTest, HugeNumber_ERANGE_Rejected) {
    int32_t minor = -1;
    // 超出 long 表示范围 → strtol 置 ERANGE → 必须拒绝而非截断
    EXPECT_FALSE(ParseMinorFromPath("/dev/vendor_lechao_usbd99999999999999999999", &minor));
}

/* --- BuildDevicePath --- */

TEST(BuildDevicePathTest, BuildsExpectedFormat) {
    EXPECT_EQ(BuildDevicePath(0), "/dev/vendor_lechao_usbd0");
    EXPECT_EQ(BuildDevicePath(123), "/dev/vendor_lechao_usbd123");
}

TEST(BuildDevicePathTest, RoundTrip_BuildThenParse) {
    int32_t minor = -1;
    EXPECT_TRUE(ParseMinorFromPath(BuildDevicePath(42), &minor));
    EXPECT_EQ(minor, 42);
}
