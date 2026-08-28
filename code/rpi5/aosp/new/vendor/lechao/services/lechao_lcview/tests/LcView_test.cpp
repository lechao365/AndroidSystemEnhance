// LcView_test.cpp — 分支覆盖测试
// 拦截：S7（故障可见性）
//
// 分支覆盖目标（readerLoop 路径）：
//   1. open 失败但未超限 → 重试循环（最终成功）
//   2. open 失败且超限 → fatalExit exit(1)（CXX-004 四步退出）
//   3. open 成功 + waitAndRead 返回 -1 → fatalExit exit(1)
//   4. open 成功 + waitAndRead 持续返回 0 → timeout 正常循环
//   5. open 成功 + waitAndRead 返回 >0 → flush 到队列（满/超时/年龄触发）
//   6. 析构 mRunning=false → 正常退出标记死亡
// 以及 getBatch 在 reader 死亡时返回 DEAD_OBJECT（不伪装空数据）

#include <gtest/gtest.h>
#include <gmock/gmock.h>
#include <chrono>
#include <thread>
#include <vector>
#include <memory>
#include <atomic>

#define private public
#define protected public
#include "LcView.h"
#undef private
#undef protected

using ::testing::_;
using ::testing::Return;
using ::testing::AtLeast;
using ::testing::Sequence;
using ::testing::InSequence;
// Note: WillOnce/WillRepeatedly are in ::testing:: (exported by gmock namespace)
using namespace vendor::lechao::lcview;
using namespace std::chrono_literals;

class MockDeviceReader : public DeviceReader {
public:
    MOCK_METHOD(bool, open, (), (override));
    MOCK_METHOD(ssize_t, waitAndRead,
                (uint8_t* buf, size_t offset, size_t cap, int timeoutMs), (override));
    MOCK_METHOD(uint32_t, getOverrun, (), (override));
    MOCK_METHOD(uint32_t, getTotalRecords, (), (override));
    MOCK_METHOD(void, close, (), (override));
};

namespace {
// SharedRefBase 派生对象（BnLcView 链）禁止 new/栈构造——析构断言
// "no ref created during lifetime" 会 abort（曾致 hal_test 0/10 全灭），
// 必须经 ndk::SharedRefBase::make 创建返回 shared_ptr
// start() 在构造后显式调用（与生产 main 的注册后启动时序一致）
std::shared_ptr<LcView> makeLcView(std::unique_ptr<MockDeviceReader> reader,
                                    int maxWait = 2) {
    auto lv = ndk::SharedRefBase::make<LcView>(std::move(reader), maxWait);
    lv->start();
    return lv;
}

bool waitReaderDead(LcView& lv, std::chrono::milliseconds timeout) {
    auto start = std::chrono::steady_clock::now();
    while (std::chrono::steady_clock::now() - start < timeout) {
        if (!lv.isReaderAlive()) return true;
        std::this_thread::sleep_for(10ms);
    }
    return false;
}
}  // namespace

// ============================================================
// 分支 1: open 失败后重试，最终成功 → reader 保持 alive
// ============================================================

TEST(LcViewReaderLoopTest, OpenRetryThenSuccess_ReaderStaysAlive) {
    auto reader = std::make_unique<MockDeviceReader>();
    Sequence seq;
    EXPECT_CALL(*reader, open()).InSequence(seq)
        .WillOnce(Return(false))   // 第一次失败
        .WillOnce(Return(true));   // 第二次成功
    EXPECT_CALL(*reader, waitAndRead(_, _, _, _))
        .InSequence(seq)
        .WillRepeatedly(Return(0));  // 持续 timeout
    EXPECT_CALL(*reader, getOverrun()).Times(AtLeast(0));
    EXPECT_CALL(*reader, close()).Times(AtLeast(1));

    auto lv = makeLcView(std::move(reader), 10);
    // open 重试间隔 1s（100ms×10），等第二次 open 成功后再断言
    std::this_thread::sleep_for(1500ms);
    // 重试成功后应保持 alive
    EXPECT_TRUE(lv->isReaderAlive());
}

// ============================================================
// 分支 2: open 永久失败 → 超限 fatalExit(1)（CXX-004）
// 用 EXPECT_DEATH 隔离，maxWait=0 立即超限
// ============================================================

TEST(LcViewReaderLoopTest, OpenNeverReady_ExitsProcess) {
    EXPECT_DEATH_IF_SUPPORTED(
        {
            auto reader = std::make_unique<MockDeviceReader>();
            EXPECT_CALL(*reader, open()).WillRepeatedly(Return(false));
            EXPECT_CALL(*reader, close()).Times(AtLeast(0));
            auto lv = ndk::SharedRefBase::make<LcView>(std::move(reader), 0);
            lv->start();
            // 块内等待线程跑到 open 失败超限 → fatalExit exit(1)；
            // 不等待会因块立即析构（mRunning=false）致线程未跑就退出（死测试假失败）
            std::this_thread::sleep_for(300ms);
        },
        "");
}

// ============================================================
// 分支 3: open 成功 + waitAndRead 返回 -1 → fatalExit(1)
// （CXX-004: 致命错误必须 exit 交 init 重启，禁止静默僵尸态）
// ============================================================

TEST(LcViewReaderLoopTest, FatalReadError_ExitsProcess) {
    EXPECT_DEATH_IF_SUPPORTED(
        {
            auto reader = std::make_unique<MockDeviceReader>();
            EXPECT_CALL(*reader, open()).WillOnce(Return(true));
            EXPECT_CALL(*reader, waitAndRead(_, _, _, _)).WillOnce(Return(-1));
            EXPECT_CALL(*reader, getOverrun()).Times(AtLeast(0));
            EXPECT_CALL(*reader, close()).Times(AtLeast(0));
            auto lv = ndk::SharedRefBase::make<LcView>(std::move(reader));
            lv->start();
            // 块内等待线程跑到 waitAndRead 返回 -1 → fatalExit exit(1)
            std::this_thread::sleep_for(300ms);
        },
        "");
}

// ============================================================
// 分支 4: open 成功 + waitAndRead 持续返回 0 → timeout 正常循环
// reader 保持 alive，析构时退出
// ============================================================

TEST(LcViewReaderLoopTest, TimeoutNoData_ReaderStaysAlive) {
    auto reader = std::make_unique<MockDeviceReader>();
    EXPECT_CALL(*reader, open()).WillOnce(Return(true));
    EXPECT_CALL(*reader, waitAndRead(_, _, _, _)).WillRepeatedly(Return(0));
    EXPECT_CALL(*reader, getOverrun()).Times(AtLeast(0));
    EXPECT_CALL(*reader, close()).Times(AtLeast(1));

    auto lv = makeLcView(std::move(reader));
    std::this_thread::sleep_for(300ms);
    EXPECT_TRUE(lv->isReaderAlive());
}

// ============================================================
// 分支 5: open 成功 + waitAndRead 返回 >0 → flush 到队列
// ============================================================

TEST(LcViewReaderLoopTest, NormalRead_BatchQueued) {
    auto reader = std::make_unique<MockDeviceReader>();
    Sequence seq;
    EXPECT_CALL(*reader, open()).InSequence(seq).WillOnce(Return(true));
    // 第一次返回假数据
    std::vector<uint8_t> fakeData(64, 0xAA);
    EXPECT_CALL(*reader, waitAndRead(_, _, _, _))
        .InSequence(seq)
        .WillOnce([&fakeData](uint8_t* buf, size_t offset, size_t cap, int) {
            size_t n = std::min(fakeData.size(), cap - offset);
            memcpy(buf + offset, fakeData.data(), n);
            return static_cast<ssize_t>(n);
        });
    // 后续返回 0 触发 timeout flush
    EXPECT_CALL(*reader, waitAndRead(_, _, _, _))
        .InSequence(seq)
        .WillRepeatedly(Return(0));
    EXPECT_CALL(*reader, getOverrun()).Times(AtLeast(0));
    EXPECT_CALL(*reader, close()).Times(AtLeast(1));

    auto lv = makeLcView(std::move(reader));
    // 等待 flush 发生（ageExpired 500ms + 余量）
    std::this_thread::sleep_for(1500ms);

    std::lock_guard<std::mutex> lock(lv->mBatchMutex);
    EXPECT_FALSE(lv->mBatchQueue.empty());
    if (!lv->mBatchQueue.empty()) {
        EXPECT_FALSE(lv->mBatchQueue.front().empty());
    }
}

// ============================================================
// 分支 5 变体: 缓冲区满 → flush
// ============================================================

TEST(LcViewReaderLoopTest, BufferFull_TriggersFlush) {
    auto reader = std::make_unique<MockDeviceReader>();
    Sequence seq;
    EXPECT_CALL(*reader, open()).InSequence(seq).WillOnce(Return(true));
    // 持续返回数据直到填满 kHalBufSize(64KB) → bufferFull flush
    EXPECT_CALL(*reader, waitAndRead(_, _, _, _))
        .InSequence(seq)
        .WillRepeatedly([](uint8_t* buf, size_t offset, size_t cap, int) {
            // 每次填 16KB
            size_t n = std::min(static_cast<size_t>(16 * 1024), cap - offset);
            if (n == 0) return static_cast<ssize_t>(0);
            memset(buf + offset, 0xBB, n);
            return static_cast<ssize_t>(n);
        });
    EXPECT_CALL(*reader, getOverrun()).Times(AtLeast(0));
    EXPECT_CALL(*reader, close()).Times(AtLeast(1));

    auto lv = makeLcView(std::move(reader));
    std::this_thread::sleep_for(500ms);

    std::lock_guard<std::mutex> lock(lv->mBatchMutex);
    EXPECT_FALSE(lv->mBatchQueue.empty());
}

// ============================================================
// 分支 5 变体: 队列满 → 丢弃最旧
// ============================================================

TEST(LcViewReaderLoopTest, QueueFull_DropsOldest) {
    auto reader = std::make_unique<MockDeviceReader>();
    EXPECT_CALL(*reader, open()).WillOnce(Return(true));
    // 快速填满缓冲和队列
    EXPECT_CALL(*reader, waitAndRead(_, _, _, _))
        .WillRepeatedly([](uint8_t* buf, size_t offset, size_t cap, int) {
            size_t n = std::min(static_cast<size_t>(64 * 1024), cap - offset);
            if (n == 0) return static_cast<ssize_t>(0);
            memset(buf + offset, 0xCC, n);
            return static_cast<ssize_t>(n);
        });
    EXPECT_CALL(*reader, getOverrun()).Times(AtLeast(0));
    EXPECT_CALL(*reader, close()).Times(AtLeast(1));

    auto lv = makeLcView(std::move(reader));
    // 等待队列溢出（kMaxQueueDepth=4）
    std::this_thread::sleep_for(1s);

    EXPECT_GT(lv->mDroppedBatches.load(), 0);
}

// ============================================================
// 分支 6: 正常析构 → readerLoop 退出 → alive=false
// ============================================================

TEST(LcViewReaderLoopTest, Destructor_JoinsThread) {
    auto reader = std::make_unique<MockDeviceReader>();
    EXPECT_CALL(*reader, open()).WillOnce(Return(true));
    EXPECT_CALL(*reader, waitAndRead(_, _, _, _)).WillRepeatedly(Return(0));
    EXPECT_CALL(*reader, getOverrun()).Times(AtLeast(0));
    EXPECT_CALL(*reader, close()).Times(AtLeast(1));

    {
        auto lv = makeLcView(std::move(reader));
        std::this_thread::sleep_for(100ms);
        EXPECT_TRUE(lv->isReaderAlive());
    }  // 析构
    SUCCEED();  // 析构未死锁即通过
}

// ============================================================
// getBatch 在 reader 死亡时返回 DEAD_OBJECT（CXX-004 核心断言）
// 不 start()，手工置 mReaderAlive=false 模拟线程死亡：
// 验证显式报错而非伪装空数据正常返回
// ============================================================

TEST(LcViewGetBatchTest, ReaderDead_ReturnsDeadObject) {
    auto reader = std::make_unique<MockDeviceReader>();
    EXPECT_CALL(*reader, open()).Times(0);
    EXPECT_CALL(*reader, waitAndRead(_, _, _, _)).Times(0);
    EXPECT_CALL(*reader, getOverrun()).Times(0);
    EXPECT_CALL(*reader, close()).Times(AtLeast(0));

    auto lv = ndk::SharedRefBase::make<LcView>(std::move(reader));
    lv->mReaderAlive = false;  // #define private public 已开启
    std::vector<uint8_t> out;
    auto st = lv->getBatch(&out);
    EXPECT_FALSE(st.isOk());
}

// ============================================================
// getOverrunCount 累加
// ============================================================

TEST(LcViewReaderLoopTest, GetOverrunCount_AccumulatesFromReader) {
    auto reader = std::make_unique<MockDeviceReader>();
    Sequence seq;
    EXPECT_CALL(*reader, open()).InSequence(seq).WillOnce(Return(true));
    EXPECT_CALL(*reader, waitAndRead(_, _, _, _))
        .InSequence(seq)
        .WillOnce(Return(0));  // 一次 timeout 让 beat++
    // beat 到 30 的倍数时调 getOverrun（mock 立即返回，beat 快速递增；
    // 首次返回 5，后续返回 0 避免超出 gmock 期望基数）
    EXPECT_CALL(*reader, getOverrun())
        .WillOnce(Return(5))
        .WillRepeatedly(Return(0));
    EXPECT_CALL(*reader, waitAndRead(_, _, _, _))
        .InSequence(seq)
        .WillRepeatedly(Return(0));
    EXPECT_CALL(*reader, close()).Times(AtLeast(1));

    auto lv = makeLcView(std::move(reader));
    // mock 的 waitAndRead 立即返回，beat 很快达到 30
    std::this_thread::sleep_for(500ms);

    int32_t cnt = lv->mOverrun.load();
    EXPECT_EQ(cnt, 5);
}

// ============================================================
// getTotalRecords 透传内核累计记录数（守恒校验数据源）
// ============================================================

TEST(LcViewReaderLoopTest, GetTotalRecords_PassesThroughFromReader) {
    auto reader = std::make_unique<MockDeviceReader>();
    Sequence seq;
    EXPECT_CALL(*reader, open()).InSequence(seq).WillOnce(Return(true));
    EXPECT_CALL(*reader, waitAndRead(_, _, _, _))
        .InSequence(seq)
        .WillRepeatedly(Return(0));  // 持续 timeout，读线程空转
    EXPECT_CALL(*reader, getOverrun()).Times(AtLeast(0));
    EXPECT_CALL(*reader, getTotalRecords())
        .WillRepeatedly(Return(123456u));
    EXPECT_CALL(*reader, close()).Times(AtLeast(1));

    auto lv = makeLcView(std::move(reader));
    std::this_thread::sleep_for(200ms);

    int64_t out = 0;
    auto st = lv->getTotalRecords(&out);
    EXPECT_TRUE(st.isOk());
    EXPECT_EQ(out, 123456LL);
}
