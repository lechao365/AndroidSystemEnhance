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

TEST_F(EpollDeviceReaderTest, ReadEof_ReturnsEnodev) {
    // 方向 1：read 返 0（EOF，内核 shutdown）→ -1 + errno=ENODEV（不再
    // 与 timeout 同返 0 伪装"本次无数据"，上层按设备不可用收尾）
    ASSERT_TRUE(mReader->open());
    ::close(mPipe[1]);  // 写端关闭 → 读端 read 返 0（EOF）
    mPipe[1] = -1;
    uint8_t buf[64];
    errno = 0;
    ssize_t n = mReader->waitAndRead(buf, 0, sizeof(buf), 500);
    EXPECT_EQ(n, -1);
    EXPECT_EQ(errno, ENODEV);
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

TEST_F(EpollDeviceReaderTest, InvalidOffsetCap_ReturnsEINVAL) {
    // 方向 1：offset >= cap 是调用方参数错误 → -1 + errno=EINVAL
    // （与 fd 未打开的设备状态错误 EBADF 解耦，不再混判）
    ASSERT_TRUE(mReader->open());
    uint8_t buf[64];
    errno = 0;
    EXPECT_EQ(mReader->waitAndRead(buf, 64, 64, 50), -1);
    EXPECT_EQ(errno, EINVAL);
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

TEST_F(EpollDeviceReaderTest, DroppedIoctlUnsupported_ReturnsZero) {
    // 方向 7：ioctl 失败（pipe 不支持 GET_STATS）→ 返 0（getDropped 容错
    // 语义，与 getTotalRecords 同源同容错）
    ASSERT_TRUE(mReader->open());
    EXPECT_EQ(mReader->getDropped(), 0u);
}

TEST_F(EpollDeviceReaderTest, RingSizeIoctlUnsupported_ReturnsZero) {
    // 方向 6：ioctl 失败（pipe 不支持 GET_STATS）→ 返 0（getRingSizeBytes
    // 容错语义；容差退化为最小档，由 ioctl 失败跳过守恒兜底）
    ASSERT_TRUE(mReader->open());
    EXPECT_EQ(mReader->getRingSizeBytes(), 0u);
}

// R-10 方向 2：GET_STATS 合并——refreshStats 单次 ioctl 拉取缓存，getter
// 从缓存分发（心跳消四次 GET_STATS）。pipe 注入不支持 GET_STATS（ioctl
// 恒失败），此处覆盖"refreshStats 失败 → 缓存无效 → getter 回退单次
// ioctl 保容错语义"（缓存命中路径依赖真实设备，由板端 verify case 覆盖）
TEST_F(EpollDeviceReaderTest, RefreshStatsFail_GetterFallsBackToIoctl) {
    // refreshStats：pipe 不支持 GET_STATS → 清缓存有效位 + ioctlErr+1
    ASSERT_TRUE(mReader->open());
    mReader->refreshStats();
    EXPECT_EQ(mReader->ioctlErr(), 1u);
    // 缓存无效：getter 回退单次 ioctl（仍失败）→ 返 0 容错 + ioctlErr 累计
    EXPECT_EQ(mReader->getTotalRecords(), 0u);
    EXPECT_EQ(mReader->getDropped(), 0u);
    EXPECT_EQ(mReader->getRingSizeBytes(), 0u);
    EXPECT_EQ(mReader->getRingUsageBytes(), 0u);
    EXPECT_EQ(mReader->ioctlErr(), 5u);
    // 刷新失败后 close 幂等，无异常
    mReader->close();
    SUCCEED();
}

TEST_F(EpollDeviceReaderTest, CloseIdempotent) {
    // close 幂等：显式 close 与析构都可能触发
    ASSERT_TRUE(mReader->open());
    mReader->close();
    mReader->close();
    SUCCEED();
}

/* 方向 2：read errno 可恢复白名单（R-07 方向 2 移除 EMSGSIZE；不加 EINVAL） */

TEST(RecoverableErrnoTest, EmsgsizeIsNotRecoverable) {
    // R-07 方向 2：EMSGSIZE 移出可恢复白名单——它语义是"内核有数据但剩余
    // 缓冲放不下首条记录"，与"本次无数据"（EINTR/EAGAIN）截然不同。
    // 原并入白名单后 waitAndRead 返 0，上层不 flush 不消费 → epoll LT
    // 忙旋转；现由 waitAndRead 返回 -EMSGSIZE 专门信号（见 waitAndRead
    // 契约），限频日志 + msgTooBig 计数 + offset>0 强制 flush 清缓冲闭环。
    EXPECT_FALSE(isRecoverableReadErrno(EMSGSIZE));
}

TEST(RecoverableErrnoTest, EagainAndEintrRecoverable) {
    EXPECT_TRUE(isRecoverableReadErrno(EAGAIN));
    EXPECT_TRUE(isRecoverableReadErrno(EINTR));
}

TEST(RecoverableErrnoTest, EinvalIsFatal) {
    // 刻意不加 EINVAL：真参数错误吞掉会让 daemon 对坏参数静默成环
    EXPECT_FALSE(isRecoverableReadErrno(EINVAL));
}

TEST(RecoverableErrnoTest, OtherErrnosFatal) {
    EXPECT_FALSE(isRecoverableReadErrno(EBADF));
    EXPECT_FALSE(isRecoverableReadErrno(EPIPE));
    EXPECT_FALSE(isRecoverableReadErrno(EIO));
}
