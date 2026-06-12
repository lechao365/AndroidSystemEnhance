// ============================================================
// LcView.h — LcView HAL 服务类头文件
// 所属模块：LcView 事件日志系统 — HAL 层
// 设计目的：声明 LcView 类，继承自 AIDL 生成的 BnLcView 骨架类。
//   该类直接管理内核驱动的文件描述符，通过独立线程 epoll
//   读取事件日志，并通过条件变量阻塞 getBatch() 供 Binder 调用者消费。
//
// v3.4 优化:
//   - H2: overrun ioctl 查询降频为每 30 次循环
//   - M2: 单 mBatch 改为双端队列 mBatchQueue (最多 4 批次)，消除数据丢失窗口
//   - H3: 新增 condition_variable，getBatch() 阻塞等待有数据时被 reader 唤醒
// ============================================================

#pragma once

#include <aidl/vendor/lechao/lcview/BnLcView.h>
#include <vector>
#include <deque>
#include <mutex>
#include <condition_variable>
#include <thread>
#include <atomic>
#include <cstdint>

namespace vendor {
namespace lechao {
namespace lcview {

class LcView : public aidl::vendor::lechao::lcview::BnLcView {
public:
    LcView();
    ~LcView();

    ndk::ScopedAStatus getBatch(std::vector<uint8_t>* _aidl_return) override;
    ndk::ScopedAStatus getOverrunCount(int32_t* _aidl_return) override;

private:
    void readerLoop();
    bool openDevice();
    void closeDevice();

    int mDevFd = -1;
    std::atomic<bool> mRunning{false};

    static constexpr size_t kHalBufSize = 64 * 1024;
    static constexpr int kEpollTimeoutMs = 1000;
    static constexpr size_t kMaxQueueDepth = 4;   // v3.4: 最多缓存 4 批次

    uint8_t mHalBuf[kHalBufSize];
    size_t mHalOffset = 0;

    // v3.4 优化: 双端队列替代单 mBatch，避免 daemon 未取走时丢失数据
    std::deque<std::vector<uint8_t>> mBatchQueue;
    std::mutex mBatchMutex;
    std::condition_variable mBatchCv;              // v3.4: 阻塞 getBatch 等待
    std::atomic<int32_t> mOverrun{0};
    std::atomic<int32_t> mDroppedBatches{0};       // v3.5: 队列满丢弃计数
    std::thread mReaderThread;
};

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor
