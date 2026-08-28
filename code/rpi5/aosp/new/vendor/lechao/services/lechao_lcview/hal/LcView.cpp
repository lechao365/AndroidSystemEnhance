// ============================================================
// LcView.cpp — LcView HAL 服务类实现
// 所属模块：LcView 事件日志系统 — HAL 层
// 设计目的：实现 LcView 类，作为内核驱动和用户态 daemon
//   之间的桥梁。readerLoop 经 DeviceReader 抽象读取内核字符设备，
//   缓存到内部缓冲区，通过 getBatch() 以 Binder IPC 方式批量投递。
//
// 架构设计考量：
//   - 设备访问全部经 DeviceReader（生产 EpollDeviceReader，测试可注入）
//   - 64KB 内部缓冲区用于临时累积，减少 IPC 调用次数
//   - 每 30 次循环输出心跳（含 overrun/dropped），便于判断 HAL 线程状态
//   - 每个批量数据前 4 字节为总长度，方便 daemon 端解析
//   - readerLoop 致命错误 fatalExit 4 步退出（CXX-004）：
//     置存活标志 → 交付残留批次并 notify → ERROR 日志 → exit(1)
// ============================================================

#include "LcView.h"
#include "DeviceReader.h"
#include "lechao_log.h"
#include <android-base/logging.h>
#include <android/binder_status.h>
#include <chrono>
#include <condition_variable>
#include <cstdlib>
#include <cstring>

using namespace std::chrono;

using namespace vendor::lechao::lcview;

LcView::LcView(std::unique_ptr<DeviceReader> reader, int openRetryLimitSec)
    : mReader(std::move(reader)), mOpenRetryLimitSec(openRetryLimitSec)
{
    LOG(INFO) << "LcView: constructor done (reader thread deferred to start())";
}

void LcView::start()
{
    LOG(INFO) << "LcView: launching readerLoop thread";
    mRunning = true;
    mReaderAlive = true;
    mReaderThread = std::thread(&LcView::readerLoop, this);
}

LcView::~LcView()
{
    mRunning = false;
    if (mReaderThread.joinable())
        mReaderThread.join();
    if (mReader)
        mReader->close();
}

// CXX-004: 致命错误统一 4 步退出（调用方为 readerLoop 线程）：
//   (1) 置 mReaderAlive=false → getBatch 快速感知线程死亡
//   (2) 交付残留批次 + notify_all 唤醒阻塞的 getBatch（残留数据不丢，CXX-002）
//   (3) ERROR 日志含 errno/现场上下文
//   (4) exit(1) 交 init 默认策略重启（rc 非 oneshot），禁止静默 return 僵尸态
void LcView::fatalExit(const char* where)
{
    // 首行快照 errno：后续锁操作/日志可能改写 errno，现场会失真
    int savedErrno = errno;
    mReaderAlive.store(false, std::memory_order_relaxed);
    {
        std::lock_guard<std::mutex> lock(mBatchMutex);
        if (mHalOffset > 0) {
            // 错误路径交付已从内核消费但未 flush 的残留数据
            if (mBatchQueue.size() >= kMaxQueueDepth) {
                mBatchQueue.pop_front();
                mDroppedBatches.fetch_add(1, std::memory_order_relaxed);
            }
            mBatchQueue.emplace_back(mHalBuf, mHalBuf + mHalOffset);
            mHalOffset = 0;
        }
    }
    mBatchCv.notify_all();
    LOG(ERROR) << "LcView: fatal error in " << where
               << ", errno=" << savedErrno << " (" << strerror(savedErrno) << ")"
               << ", buffered bytes delivered, exiting for init restart";
    std::exit(1);
}

void LcView::readerLoop()
{
    LOG(INFO) << "LcView: readerLoop starting";

    // 阶段 1: 打开设备（带重试）。
    // 生产 openRetryLimitSec<0 无限重试（设备节点可能晚于 HAL 就绪）；
    // 测试注入有限值，超限走 fatalExit（EXPECT_DEATH 用例覆盖）
    const auto openStart = steady_clock::now();
    bool opened = false;
    while (mRunning) {
        if (mReader->open()) {
            opened = true;
            break;
        }
        if (mOpenRetryLimitSec >= 0 &&
            steady_clock::now() - openStart >= seconds(mOpenRetryLimitSec)) {
            fatalExit("open (retry limit exceeded)");
        }
        // 100ms 粒度检查 mRunning，保证析构及时退出
        for (int i = 0; i < 10 && mRunning; i++)
            std::this_thread::sleep_for(milliseconds(100));
    }
    if (!opened) {
        // 正常停止（析构）：置存活标志并唤醒等待者，避免 getBatch 干等
        mReaderAlive.store(false, std::memory_order_relaxed);
        mBatchCv.notify_all();
        LOG(INFO) << "LcView: readerLoop exiting (stopped before device opened)";
        return;
    }

    LOG(INFO) << "LcView: readerLoop entering main loop";

    // 阶段 2: 主循环——epoll 等待读取 + 批次攒包 flush
    static constexpr auto kMaxBufferAge = milliseconds(500);
    auto dataArrivedAt = steady_clock::time_point::max();

    int readOk = 0, readEmpty = 0, readErr = 0, flushCount = 0;
    int beat = 0;

    while (mRunning) {
        ssize_t n = mReader->waitAndRead(mHalBuf, mHalOffset, kHalBufSize,
                                         kEpollTimeoutMs);

        if (n < 0) {
            readErr++;
            LOG(ERROR) << "LcView: read error, errno=" << errno
                       << " (" << strerror(errno) << "), buffered=" << mHalOffset;
            fatalExit("read");
        }

        ++beat;
        if (n > 0) {
            readOk++;
            if (mHalOffset == 0)
                dataArrivedAt = steady_clock::now();
            mHalOffset += n;
            LC_LOGD("read: got " << n << "B, offset=" << mHalOffset);
        } else {
            readEmpty++;
            LC_LOGD("read: no data (timeout)");
        }

        if (::lechao::debugVerbose()) {
            LOG(INFO) << "LcView: beat=" << beat << " buffered=" << mHalOffset
                      << "B readOk=" << readOk << " readEmpty=" << readEmpty
                      << " readErr=" << readErr << " flush=" << flushCount;
        } else if (beat % 30 == 0) {
            // CXX-004: 心跳带上 overrun 与队列满丢批计数，
            // 内核溢出 / HAL 侧丢批对上层可见，不再静默
            LOG(INFO) << "LcView: alive beat=" << beat << " buffered=" << mHalOffset
                      << "B overrun=" << mOverrun.load()
                      << " dropped=" << mDroppedBatches.load()
                      << " readOk=" << readOk << " readEmpty=" << readEmpty
                      << " readErr=" << readErr << " flush=" << flushCount;
        }

        // overrun 查询降频（每 30 拍一次；内核语义为读取即清零）
        if (beat % 30 == 0) {
            uint32_t overrun = mReader->getOverrun();
            if (overrun > 0)
                mOverrun.fetch_add(static_cast<int32_t>(overrun),
                                   std::memory_order_relaxed);
        }

        bool timedOut = (n == 0);
        bool bufferFull = (mHalOffset >= kHalBufSize);
        bool ageExpired = (mHalOffset > 0 &&
                           steady_clock::now() - dataArrivedAt > kMaxBufferAge);
        if (mHalOffset > 0 && (bufferFull || timedOut || ageExpired)) {
            flushCount++;
            std::lock_guard<std::mutex> lock(mBatchMutex);
            if (mBatchQueue.size() >= kMaxQueueDepth) {
                LC_LOGW("batch queue full (depth=" << kMaxQueueDepth << "), dropping oldest");
                mBatchQueue.pop_front();
                mDroppedBatches.fetch_add(1, std::memory_order_relaxed);
            }
            mBatchQueue.emplace_back(mHalBuf, mHalBuf + mHalOffset);
            LC_LOGD("LcView: flush " << mHalOffset << "B q=" << mBatchQueue.size()
                      << (bufferFull ? " [full]" : "")
                      << (timedOut ? " [timeout]" : "")
                      << (ageExpired ? " [age]" : ""));
            mHalOffset = 0;
            dataArrivedAt = steady_clock::time_point::max();
            mBatchCv.notify_one();
        }
    }

    // 正常停止路径同样置存活标志并唤醒 getBatch 等待者（CXX-004 配套）
    mReaderAlive.store(false, std::memory_order_relaxed);
    mBatchCv.notify_all();
    LOG(INFO) << "LcView: readerLoop exiting, readOk=" << readOk
              << " readEmpty=" << readEmpty << " readErr=" << readErr
              << " flush=" << flushCount << " beat=" << beat;
}

ndk::ScopedAStatus LcView::getBatch(std::vector<uint8_t>* _aidl_return)
{
    LC_LOGD("getBatch: queue=" << mBatchQueue.size());
    std::unique_lock<std::mutex> lock(mBatchMutex);
    // 谓词加入 mReaderAlive：reader 线程死亡时立即唤醒，
    // 不让调用方干等 1s 超时（CXX-004）
    mBatchCv.wait_for(lock, std::chrono::seconds(1),
                      [this]{ return !mBatchQueue.empty()
                                   || !mReaderAlive.load(std::memory_order_relaxed); });
    if (!mBatchQueue.empty()) {
        // mGetBatchBeat 只在持锁期间访问（Binder 线程池并发调用安全）
        if (++mGetBatchBeat % 30 == 0)
            LOG(INFO) << "LcView: getBatch returning " << mBatchQueue.front().size()
                      << "B, queue=" << mBatchQueue.size();
        *_aidl_return = std::move(mBatchQueue.front());
        mBatchQueue.pop_front();
    } else if (!mReaderAlive.load(std::memory_order_relaxed)) {
        // CXX-004: reader 线程已死亡，显式返回 DEAD_OBJECT 错误，
        // 禁止伪装正常空数据——daemon 侧据此感知 HAL 故障走重连/退出
        LOG(ERROR) << "LcView: getBatch: reader thread dead, returning DEAD_OBJECT";
        return ndk::ScopedAStatus::fromStatus(STATUS_DEAD_OBJECT);
    } else {
        LC_LOGD("getBatch: returning empty (timeout)");
        _aidl_return->clear();
    }
    return ndk::ScopedAStatus::ok();
}

ndk::ScopedAStatus LcView::getOverrunCount(int32_t* _aidl_return)
{
    *_aidl_return = mOverrun.load(std::memory_order_relaxed);
    LC_LOGD("getOverrunCount: " << *_aidl_return);
    return ndk::ScopedAStatus::ok();
}

ndk::ScopedAStatus LcView::getTotalRecords(int64_t* _aidl_return)
{
    // 直接透传内核累计记录总数（DeviceReader ioctl 查询，失败返 0），
    // 供 daemon 心跳守恒校验比对 JSONL 落盘条数
    *_aidl_return = mReader->getTotalRecords();
    LC_LOGD("getTotalRecords: " << *_aidl_return);
    return ndk::ScopedAStatus::ok();
}
