// ============================================================
// DeviceIo_test.cpp — 设备节点底层 IO 封装全分支（pipe 注入）
// 所属模块：lechao_lciod HAL 测试
// 拦截：CXX-002（错误路径 errno 语义、失败清零防御）、
//       CXX-004（失败可见：超时/坏 fd 均显式报错不吞噬）
// 手法：对齐 lcview DeviceReader_test——注入 pipe 读端冒充设备 fd，
// 走真实 poll/read/ioctl 系统调用路径（非 gmock 模拟）。
// 另含 ABI 契约快照：镜像副本 vs 内核真相源的字段尺寸契约，
// 任一侧 ABI 变更未同步时在此判红（对齐 lcview record_codec_test 精神）。
// ============================================================

#include <gtest/gtest.h>

#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <unistd.h>

#include "device_io.h"
#include "minor_utils.h"

class DeviceIoTest : public ::testing::Test {
protected:
    void SetUp() override { ASSERT_EQ(pipe(mPipe), 0); }

    void TearDown() override {
        // 注：部分用例已主动 close 关注入 fd，此处 close 容错（EBADF 无害）
        if (mPipe[0] >= 0)
            ::close(mPipe[0]);
        if (mPipe[1] >= 0)
            ::close(mPipe[1]);
    }

    static vendor_lechao_usbd_event MakeEvent(uint64_t ts, uint32_t type, uint32_t value) {
        vendor_lechao_usbd_event ev{};
        ev.timestamp_ns = ts;
        ev.event_type = type;
        ev.event_value = value;
        ev.status = 0;
        ev.data_direction = 0;
        ev.valid = 1;
        return ev;
    }

    int mPipe[2] = {-1, -1};
};

/* --- read_event：pipe 注入真实 poll/read 路径 --- */

TEST_F(DeviceIoTest, ReadEvent_Success_SingleEventFieldsIntact) {
    const auto ev = MakeEvent(1234567890ULL, 5, 42);
    ASSERT_EQ(::write(mPipe[1], &ev, sizeof(ev)), (ssize_t)sizeof(ev));

    vendor_lechao_usbd_event out{};
    errno = 0;
    EXPECT_EQ(read_event(mPipe[0], &out, 500), 0);
    EXPECT_EQ(out.timestamp_ns, 1234567890ULL);
    EXPECT_EQ(out.event_type, 5u);
    EXPECT_EQ(out.event_value, 42u);
    EXPECT_EQ(out.valid, 1u);
}

TEST_F(DeviceIoTest, ReadEvent_DrainsMultiple_KeepsLatestOnly) {
    // 排空策略：环形缓冲积压多条时只保留最新一条（device_io.h 契约）
    for (uint32_t i = 1; i <= 3; ++i) {
        const auto ev = MakeEvent(i, i, i * 10);
        ASSERT_EQ(::write(mPipe[1], &ev, sizeof(ev)), (ssize_t)sizeof(ev));
    }

    vendor_lechao_usbd_event out{};
    errno = 0;
    EXPECT_EQ(read_event(mPipe[0], &out, 500), 0);
    EXPECT_EQ(out.event_type, 3u);
    EXPECT_EQ(out.event_value, 30u);
}

TEST_F(DeviceIoTest, ReadEvent_Timeout_ReturnsETIMEDOUT) {
    // "暂无事件"正常语义：-1 + errno=ETIMEDOUT（上层 readEvent 转 valid=false）
    vendor_lechao_usbd_event out{};
    errno = 0;
    EXPECT_EQ(read_event(mPipe[0], &out, 30), -1);
    EXPECT_EQ(errno, ETIMEDOUT);
}

TEST_F(DeviceIoTest, ReadEvent_NegativeFd_PollIgnores_TimesOut) {
    // POSIX：poll 对 fd<0 条目忽略 → 等价超时语义
    vendor_lechao_usbd_event out{};
    errno = 0;
    EXPECT_EQ(read_event(-1, &out, 10), -1);
    EXPECT_EQ(errno, ETIMEDOUT);
}

TEST_F(DeviceIoTest, ReadEvent_ClosedFd_PollNval_ReturnsEIO) {
    // 坏 fd（已关闭但数值有效）→ poll 报 POLLNVAL（无 POLLIN）→ EIO 分支
    const int closed_fd = dup(mPipe[0]);
    ASSERT_GE(closed_fd, 0);
    ::close(closed_fd);

    vendor_lechao_usbd_event out{};
    errno = 0;
    EXPECT_EQ(read_event(closed_fd, &out, 30), -1);
    EXPECT_EQ(errno, EIO);
}

TEST_F(DeviceIoTest, ReadEvent_WriteEndClosed_ReturnsError) {
    // 写端全关 → 读端 EOF：Linux 下 poll 报 POLLHUP（无 POLLIN → EIO），
    // 个别内核报 POLLIN|POLLHUP → read 返 0（EOF）→ EAGAIN。两者皆失败态
    ::close(mPipe[1]);
    mPipe[1] = -1;

    vendor_lechao_usbd_event out{};
    errno = 0;
    EXPECT_EQ(read_event(mPipe[0], &out, 200), -1);
    EXPECT_TRUE(errno == EIO || errno == EAGAIN) << "errno=" << errno;
}

TEST_F(DeviceIoTest, ReadEvent_PartialEvent_ReturnsEAGAIN) {
    // 部分事件（不足一个 struct）→ read 短读退出排空循环 → count=0 → EAGAIN
    char half[sizeof(vendor_lechao_usbd_event) / 2];
    memset(half, 0xAB, sizeof(half));
    ASSERT_EQ(::write(mPipe[1], half, sizeof(half)), (ssize_t)sizeof(half));

    vendor_lechao_usbd_event out{};
    errno = 0;
    EXPECT_EQ(read_event(mPipe[0], &out, 200), -1);
    EXPECT_EQ(errno, EAGAIN);
}

/* --- open/close_device --- */

TEST_F(DeviceIoTest, OpenDevice_RetriesExhausted_ReturnsMinus1) {
    errno = 0;
    EXPECT_EQ(open_device("/nonexistent/vendor_lechao_usbd0", 2, 1), -1);
}

TEST_F(DeviceIoTest, OpenDevice_Success_ThenClose) {
    const int fd = open_device("/dev/null", 1, 1);
    ASSERT_GE(fd, 0);
    close_device(fd);
    SUCCEED();
}

TEST_F(DeviceIoTest, CloseDevice_NegativeFd_SafeSkip) {
    // 防御路径：fd<0 直接跳过，不得 crash
    close_device(-1);
    SUCCEED();
}

/* --- ioctl 封装：坏 fd（pipe 不支持 usbd ioctl）失败路径 --- */

TEST_F(DeviceIoTest, GetStats_IoctlUnsupported_FailsWithZeroedOutput) {
    // memset 清零防御：ioctl 失败时输出不得残留调用方脏数据（CXX-002）
    struct vendor_lechao_usbd_stats stats;
    memset(&stats, 0xFF, sizeof(stats));
    errno = 0;
    EXPECT_EQ(get_stats(mPipe[0], &stats), -1);
    EXPECT_EQ(errno, ENOTTY);

    const auto* bytes = reinterpret_cast<const uint8_t*>(&stats);
    for (size_t i = 0; i < sizeof(stats); ++i)
        ASSERT_EQ(bytes[i], 0) << "dirty byte at offset " << i;
}

TEST_F(DeviceIoTest, ResetState_IoctlUnsupported_ReturnsMinus1) {
    errno = 0;
    EXPECT_EQ(reset_state(mPipe[0]), -1);
    EXPECT_EQ(errno, ENOTTY);
}

TEST_F(DeviceIoTest, GetAndSetConfig_IoctlUnsupported_ReturnMinus1) {
    struct vendor_lechao_usbd_config cfg{};
    errno = 0;
    EXPECT_EQ(get_config(mPipe[0], &cfg), -1);
    EXPECT_EQ(errno, ENOTTY);
    errno = 0;
    EXPECT_EQ(set_config(mPipe[0], &cfg), -1);
    EXPECT_EQ(errno, ENOTTY);
}

/* --- list_devices：真实 glob（无设备环境空列表合法） --- */

TEST_F(DeviceIoTest, ListDevices_AllEntriesHaveUsbdPrefix) {
    for (const auto& path : list_devices()) {
        EXPECT_EQ(path.compare(0, std::strlen(lechao::lciod::kUsbdDevPrefix),
                               lechao::lciod::kUsbdDevPrefix), 0)
            << "unexpected device path: " << path;
    }
}

/* --- ABI 契约快照（vendor_lechao_usbd-ioctl.h 镜像副本） --- */

TEST(AbiContractTest, StructSizesFrozen) {
    // 字段增删/对齐变更未同步三方（内核/HAL/工具）时在此判红。
    // stats=248：232 后 flags(u32) 至 236，event_drop_count(u64) 需 8 对齐
    // → 4 字节尾部 padding → 248
    EXPECT_EQ(sizeof(vendor_lechao_usbd_stats), static_cast<size_t>(248));
    EXPECT_EQ(sizeof(vendor_lechao_usbd_event), static_cast<size_t>(24));
    EXPECT_EQ(sizeof(vendor_lechao_usbd_config), static_cast<size_t>(8));
}

TEST(AbiContractTest, AbiVersionAndBufSizeFrozen) {
    EXPECT_EQ(VENDOR_LECHAO_USBD_ABI_VERSION, 2u);  // v2: stats 追加 event_drop_count
    EXPECT_EQ(VENDOR_LECHAO_USBD_EVENT_BUF_SIZE, 32);
}
