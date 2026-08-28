// ============================================================
// HalClient_test.cpp — HAL 客户端重连退避计算
// 所属模块：lechao_lciod 单元测试
// 拦截：CXX-002（边界/防御）——指数退避公式收敛到
// IoHalClient::RetryIntervalMs 静态纯函数（hal_client.h），
// 验证 500ms × 2^min(n,4) 封顶 5s 全分支及负数 clamp
// （负数移位在 C++ 中是未定义行为）。
// 注：DeathRecipient 生命周期与 binder 重连依赖真实 service
// manager 环境，由板上 liveness 用例与故障注入演练兜底。
// ============================================================

#include <gtest/gtest.h>

#include "hal_client.h"

TEST(RetryIntervalMsTest, FirstRetry_500ms) {
    EXPECT_EQ(IoHalClient::RetryIntervalMs(0), 500);
}

TEST(RetryIntervalMsTest, ExponentialBackoff_Uncapped) {
    EXPECT_EQ(IoHalClient::RetryIntervalMs(1), 1000);
    EXPECT_EQ(IoHalClient::RetryIntervalMs(2), 2000);
    EXPECT_EQ(IoHalClient::RetryIntervalMs(3), 4000);
}

TEST(RetryIntervalMsTest, FourthRetry_CappedTo5s) {
    // 500 * 2^4 = 8000 → 封顶 5000
    EXPECT_EQ(IoHalClient::RetryIntervalMs(4), 5000);
}

TEST(RetryIntervalMsTest, BeyondCap_StaysAt5s) {
    EXPECT_EQ(IoHalClient::RetryIntervalMs(5), 5000);
    EXPECT_EQ(IoHalClient::RetryIntervalMs(100), 5000);
}

TEST(RetryIntervalMsTest, NegativeCount_ClampedToBase) {
    // 防御：负 retryCount 不得触发负数移位 UB，按 0 处理
    EXPECT_EQ(IoHalClient::RetryIntervalMs(-1), 500);
}
