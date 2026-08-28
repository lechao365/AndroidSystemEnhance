// DeviceReader_test.cpp — EpollDeviceReader 生产实现分支覆盖
// 拦截：S7（故障可见性）补充——注入 fd 可测缝覆盖此前被 Mock 顶替的
// 生产路径（open 重试/幂等、poll 超时、部分读、EBADF 错误码、ioctl 失败）
//
// 可测缝：EpollDeviceReader(int fd) 注入 pipe 读端，open() 跳过设备 ::open
// 直接走 epoll 注册，覆盖真实 epoll_wait/read 路径（非 gmock 模拟）

#include <gtest/gtest.h>

#include <cerrno>
#include <cstring>
#include <memory>
#include <unistd.h>

#include "DeviceReader.h"

using namespace vendor::lechao::lcview;

class EpollDeviceReaderTest : public ::testing::Test {
protected:
    void SetUp() override {
        ASSERT_EQ(pipe(mPipe), 0);
        mReader.reset(new EpollDeviceReader(mPipe[0]));
    }

    void TearDown() override {
        // 注：close() 已关注入 fd，此处 close 容错（EBADF 无害）
        if (mPipe[0] >= 0)
            ::close(mPipe[0]);
        if (mPipe[1] >= 0)
            ::close(mPipe[1]);
    }

    int mPipe[2] = {-1, -1};
    std::unique_ptr<EpollDeviceReader> mReader;
};

TEST_F(EpollDeviceReaderTest, OpenSuccess_WithInjectedFd) {
    EXPECT_TRUE(mReader->open());
}

TEST_F(EpollDeviceReaderTest, OpenIdempotent_SecondCallTrue) {
    // open 重试路径：已打开（注入 fd）二次 open 幂等 true，不重复注册
    ASSERT_TRUE(mReader->open());
    EXPECT_TRUE(mReader->open());
}

TEST_F(EpollDeviceReaderTest, PollTimeout_ReturnsZero) {
    // poll 超时：pipe 无数据 → waitAndRead 返 0（非致命，上层继续循环）
    ASSERT_TRUE(mReader->open());
    uint8_t buf[64];
    ssize_t n = mReader->waitAndRead(buf, 0, sizeof(buf), 50);
    EXPECT_EQ(n, 0);
}

TEST_F(EpollDeviceReaderTest, PartialRead_ReturnsWrittenBytes) {
    // 部分读：pipe 写入 3B → 读回 3B（LT 模式剩数据下轮再读）
    ASSERT_TRUE(mReader->open());
    const char data[] = "abc";
    ASSERT_EQ(::write(mPipe[1], data, sizeof(data) - 1), 3);
    uint8_t buf[64];
    ssize_t n = mReader->waitAndRead(buf, 0, sizeof(buf), 500);
    EXPECT_EQ(n, 3);
    EXPECT_EQ(memcmp(buf, data, 3), 0);
}

TEST_F(EpollDeviceReaderTest, ReadFromClosedFd_ReturnsEBADF) {
    // 错误码：close 后读取 → -1 + errno=EBADF（致命，透传 errno）
    ASSERT_TRUE(mReader->open());
    mReader->close();
    uint8_t buf[64];
    errno = 0;
    ssize_t n = mReader->waitAndRead(buf, 0, sizeof(buf), 50);
    EXPECT_EQ(n, -1);
    EXPECT_EQ(errno, EBADF);
}

TEST_F(EpollDeviceReaderTest, InvalidOffsetCap_ReturnsEBADF) {
    // 错误码：offset >= cap → -1 + errno=EBADF（参数防御）
    ASSERT_TRUE(mReader->open());
    uint8_t buf[64];
    errno = 0;
    EXPECT_EQ(mReader->waitAndRead(buf, 64, 64, 50), -1);
    EXPECT_EQ(errno, EBADF);
}

TEST_F(EpollDeviceReaderTest, OverrunIoctlUnsupported_ReturnsZero) {
    // ioctl 失败（pipe 不支持 GET_OVERRUN）→ 返 0（getOverrun 容错语义）
    ASSERT_TRUE(mReader->open());
    EXPECT_EQ(mReader->getOverrun(), 0u);
}

TEST_F(EpollDeviceReaderTest, TotalRecordsIoctlUnsupported_ReturnsZero) {
    // ioctl 失败（pipe 不支持 GET_STATS）→ 返 0（getTotalRecords 容错语义）
    ASSERT_TRUE(mReader->open());
    EXPECT_EQ(mReader->getTotalRecords(), 0u);
}

TEST_F(EpollDeviceReaderTest, CloseIdempotent) {
    // close 幂等：显式 close 与析构都可能触发
    ASSERT_TRUE(mReader->open());
    mReader->close();
    mReader->close();
    SUCCEED();
}