// ============================================================
// LcView.cpp — LcView HAL 服务类实现
// 所属模块：LcView 事件日志系统 — HAL 层
// 设计目的：实现 LcView 类，作为内核驱动和用户态 daemon
//   之间的桥梁。负责通过 epoll + read 从内核字符设备获取
//   二进制事件日志，缓存到环形缓冲区，通过 getBatch() 方法
//   以 Binder IPC 方式批量投递到 daemon 层。
//
// 架构设计考量：
//   - 使用 epoll 边缘触发模式可设超时，避免无限阻塞
//   - 64KB 内部缓冲区用于临时累积，减少 IPC 调用次数
//   - 每 30 次循环输出心跳，便于判断 HAL 线程状态
//   - 每个批量数据前 4 字节为总长度，方便 daemon 端解析
// ============================================================

#include "LcView.h"
#include "lechao_log.h"
#include <fcntl.h>
#include <unistd.h>
#include <cstring>
#include <sys/epoll.h>
#include <sys/ioctl.h>
#include <android-base/logging.h>
#include <chrono>
#include <condition_variable>

using namespace std::chrono;

#define LCVIEW_IOC_MAGIC  'V'
#define LCVIEW_GET_AVAIL_BYTES  _IOR(LCVIEW_IOC_MAGIC, 1, uint32_t)
#define LCVIEW_GET_OVERRUN      _IOR(LCVIEW_IOC_MAGIC, 2, uint32_t)

struct lcview_stats {
    uint32_t total_records;
    uint32_t overrun_cnt;
    uint32_t ring_usage_bytes;
    uint32_t ring_size_bytes;
};
#define LCVIEW_GET_STATS  _IOR(LCVIEW_IOC_MAGIC, 3, struct lcview_stats)

using namespace vendor::lechao::lcview;

LcView::LcView()
{
    LOG(INFO) << "LcView: constructor start, launching readerLoop";
    mRunning = true;
    mReaderThread = std::thread(&LcView::readerLoop, this);
    LOG(INFO) << "LcView: constructor done, readerLoop thread launched";
}

LcView::~LcView()
{
    mRunning = false;
    if (mReaderThread.joinable())
        mReaderThread.join();
    closeDevice();
}

#define OPEN_RETRY_MAX    10
#define OPEN_RETRY_DELAY_MS 200
bool LcView::openDevice()
{
    for (int i = 0; i < OPEN_RETRY_MAX; i++) {
        mDevFd = open("/dev/vendor_lechao_lcview", O_RDONLY);
        if (mDevFd >= 0) {
            LOG(INFO) << "LcView: open succeeded on attempt " << (i + 1)
                      << "/" << OPEN_RETRY_MAX << ", fd=" << mDevFd;
            return true;
        }
        LOG(WARNING) << "LcView: open attempt " << (i + 1) << "/" << OPEN_RETRY_MAX
                     << " failed, errno=" << errno << " (" << strerror(errno) << ")";
        if (i < OPEN_RETRY_MAX - 1)
            usleep(OPEN_RETRY_DELAY_MS * 1000);
    }
    LOG(ERROR) << "LcView: all " << OPEN_RETRY_MAX << " open attempts failed, last errno="
               << errno << " (" << strerror(errno) << ")";
    return false;
}

void LcView::closeDevice()
{
    if (mDevFd >= 0) {
        close(mDevFd);
        mDevFd = -1;
    }
}

void LcView::readerLoop()
{
    LOG(INFO) << "LcView: readerLoop starting";

    static constexpr int kDevRetryIntervalSec = 5;
    while (mRunning && !openDevice()) {
        LOG(WARNING) << "LcView: device not ready, retrying in "
                     << kDevRetryIntervalSec << "s...";
        for (int i = 0; i < kDevRetryIntervalSec * 10 && mRunning; i++)
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
    }
    if (!mRunning) {
        LOG(INFO) << "LcView: readerLoop exiting (stopped before device opened)";
        return;
    }
    LOG(INFO) << "LcView: device opened fd=" << mDevFd;

    int epfd = epoll_create1(0);
    if (epfd < 0) {
        LOG(ERROR) << "LcView: epoll_create1 failed, errno=" << errno
                   << " (" << strerror(errno) << ")";
        return;
    }

    struct epoll_event ev, events[1];
    ev.events = EPOLLIN;
    ev.data.fd = mDevFd;
    if (epoll_ctl(epfd, EPOLL_CTL_ADD, mDevFd, &ev) < 0) {
        LOG(ERROR) << "LcView: epoll_ctl failed, errno=" << errno
                   << " (" << strerror(errno) << ")";
        close(epfd);
        return;
    }

    LOG(INFO) << "LcView: readerLoop entering main loop, epfd=" << epfd
              << " devFd=" << mDevFd;

    {
        struct lcview_stats stats;
        if (ioctl(mDevFd, LCVIEW_GET_STATS, &stats) == 0)
            LOG(INFO) << "LcView: ring init total_records=" << stats.total_records
                      << " overrun=" << stats.overrun_cnt
                      << " usage=" << stats.ring_usage_bytes << "B/"
                      << stats.ring_size_bytes << "B";
    }

    static constexpr auto kMaxBufferAge = milliseconds(500);
    auto dataArrivedAt = steady_clock::time_point::max();

    int readOk = 0, readEmpty = 0, readErr = 0, flushCount = 0;
    int beat = 0;

    while (mRunning) {
        int nfds = epoll_wait(epfd, events, 1, kEpollTimeoutMs);
        LC_LOGD("epoll_wait: nfds=" << nfds);

        ++beat;
        if (::lechao::debugVerbose()) {
            LOG(INFO) << "LcView: beat=" << beat << " buffered=" << mHalOffset
                      << "B readOk=" << readOk << " readEmpty=" << readEmpty
                      << " readErr=" << readErr << " flush=" << flushCount;
        } else if (beat % 30 == 0) {
            LOG(INFO) << "LcView: alive beat=" << beat << " buffered=" << mHalOffset
                      << "B overrun=" << mOverrun.load()
                      << " readOk=" << readOk << " readEmpty=" << readEmpty
                      << " readErr=" << readErr << " flush=" << flushCount;
        }

        if (nfds < 0) {
            if (errno == EINTR)
                continue;
            LOG(ERROR) << "LcView: epoll_wait error, errno=" << errno
                       << " (" << strerror(errno) << ")";
            break;
        }

        if (nfds > 0) {
            ssize_t n = read(mDevFd, mHalBuf + mHalOffset,
                             sizeof(mHalBuf) - mHalOffset);
            if (n > 0) {
                readOk++;
                if (mHalOffset == 0)
                    dataArrivedAt = steady_clock::now();
                mHalOffset += n;
                LC_LOGD("read: got " << n << "B, offset=" << mHalOffset);
            } else if (n == 0) {
                readEmpty++;
                LC_LOGD("read: n=0 (empty)");
            } else if (n < 0 && errno != EAGAIN && errno != EINTR) {
                readErr++;
                LOG(ERROR) << "LcView: read error, errno=" << errno
                           << " (" << strerror(errno) << "), buffered=" << mHalOffset;
                break;
            }
        }

        if (beat % 30 == 0) {
            uint32_t overrun;
            if (ioctl(mDevFd, LCVIEW_GET_OVERRUN, &overrun) == 0)
                mOverrun.fetch_add((int32_t)overrun, std::memory_order_relaxed);
            else
                LC_LOGE("ioctl GET_OVERRUN failed: errno=" << errno);
        }

        bool timedOut = (nfds == 0);
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

    close(epfd);
    LOG(INFO) << "LcView: readerLoop exiting, readOk=" << readOk
              << " readEmpty=" << readEmpty << " readErr=" << readErr
              << " flush=" << flushCount << " beat=" << beat;
}

ndk::ScopedAStatus LcView::getBatch(std::vector<uint8_t>* _aidl_return)
{
    LC_LOGD("getBatch: queue=" << mBatchQueue.size());
    std::unique_lock<std::mutex> lock(mBatchMutex);
    mBatchCv.wait_for(lock, std::chrono::seconds(1),
                      [this]{ return !mBatchQueue.empty(); });
    if (!mBatchQueue.empty()) {
        static int getBatchBeat = 0;
        if (++getBatchBeat % 30 == 0)
            LOG(INFO) << "LcView: getBatch returning " << mBatchQueue.front().size()
                      << "B, queue=" << mBatchQueue.size();
        *_aidl_return = std::move(mBatchQueue.front());
        mBatchQueue.pop_front();
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
