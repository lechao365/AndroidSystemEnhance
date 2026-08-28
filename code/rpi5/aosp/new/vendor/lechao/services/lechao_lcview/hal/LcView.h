// ============================================================
// LcView.h — LcView HAL 服务类头文件
// 所属模块：LcView 事件日志系统 — HAL 层
// 设计目的：声明 LcView 类，继承自 AIDL 生成的 BnLcView 骨架类。
//   设备访问经 DeviceReader 抽象注入（生产 EpollDeviceReader /
//   测试 MockDeviceReader），readerLoop 通过独立线程驱动，
//   并以条件变量阻塞 getBatch() 供 Binder 调用者消费。
//
// v3.5 演进:
//   - H2: overrun 查询降频为每 30 次循环（经 DeviceReader::getOverrun）
//   - M2: 双端队列 mBatchQueue（最多 4 批次），消除数据丢失窗口
//   - H3: condition_variable，getBatch() 阻塞等待 reader 唤醒
//   - CXX-004: readerLoop 致命错误 fatalExit 4 步退出；
//     getBatch 在 reader 死亡时返回 DEAD_OBJECT 而非伪装空数据
// ============================================================

#pragma once

#include <aidl/vendor/lechao/lcview/BnLcView.h>
#include "DeviceReader.h"
#include <vector>
#include <deque>
#include <memory>
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
    // reader — 设备读取实现（生产 EpollDeviceReader，测试注入 mock）
    // openRetryLimitSec — open 重试上限（秒）；<0 表示无限重试（生产默认）
    explicit LcView(std::unique_ptr<DeviceReader> reader,
                    int openRetryLimitSec = -1);
    ~LcView();

    // 服务注册成功后显式启动 reader 线程（main 中 addService 之后再调），
    // 避免 readerLoop 致命退出早于服务注册的竞态（CXX-004 配套）
    void start();

    // reader 线程存活状态（getBatch 据此区分"无数据"与"采集线程已死"）
    bool isReaderAlive() const
    {
        return mReaderAlive.load(std::memory_order_relaxed);
    }

    ndk::ScopedAStatus getBatch(std::vector<uint8_t>* _aidl_return) override;
    ndk::ScopedAStatus getOverrunCount(int32_t* _aidl_return) override;
    ndk::ScopedAStatus getTotalRecords(int64_t* _aidl_return) override;

private:
    void readerLoop();
    // CXX-004: 致命错误统一 4 步退出（置存活标志 → 交付残留批次并
    // notify → ERROR 日志 → exit(1) 交 init 重启），禁止静默 return
    [[noreturn]] void fatalExit(const char* where);

    std::unique_ptr<DeviceReader> mReader;
    int mOpenRetryLimitSec;

    std::atomic<bool> mRunning{false};
    std::atomic<bool> mReaderAlive{false};

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
    // getBatch 心跳计数：Binder 线程池并发调用，只在 mBatchMutex 持有期访问
    int mGetBatchBeat = 0;
    std::thread mReaderThread;
};

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor
