// ============================================================
// hal_client.h — HAL 客户端封装（System Daemon 侧）
// 所属模块: lechao_lciod daemon
// 设计目的: 封装 System Daemon 对 Vendor HAL 的连接管理。
//           提供延迟连接、自动重连和死亡通知功能。
// 设计考量: Vendor HAL 可能比 System Daemon 启动得晚，
//           所以需要延迟连接而非在构造函数中阻塞等待。
// ============================================================
#ifndef _LECHAO_LCIOD_HAL_CLIENT_H
#define _LECHAO_LCIOD_HAL_CLIENT_H

#include <cstdint>
#include <memory>
#include <mutex>

struct AIBinder_DeathRecipient;

namespace aidl::vendor::lechao::lciod {
class IIoHal;
}

// IoHalClient: HAL 连接管理器
// 负责管理与 Vendor HAL 进程的 Binder 连接，
// 包括连接建立、断开重连和进程死亡通知处理
class IoHalClient {
public:
    IoHalClient();
    ~IoHalClient();

    /*
     * RetryIntervalMs — 重连退避间隔（静态纯函数，供单测）
     * 公式: 500ms × 2^min(retryCount, 4)，封顶 5s（CXX-002 边界防御：
     * retryCount < 0 时按 0 处理，避免负数移位的未定义行为）
     */
    static int64_t RetryIntervalMs(int retryCount);

    std::shared_ptr<aidl::vendor::lechao::lciod::IIoHal> get();
private:
    /* DeathCookie 同时携带 self 和 recipient，便于 unlink 回调释放资源 */
    struct DeathCookie {
        IoHalClient *self;
        AIBinder_DeathRecipient *recipient;
    };

    std::mutex mtx_;
    std::shared_ptr<aidl::vendor::lechao::lciod::IIoHal> hal_;
    bool connected_;
    int64_t lastRetryMs_;
    int retryCount_;
    DeathCookie *currentCookie_ = nullptr;

    void connect();
    static void onHalDied(void *cookie);
    static void onHalDiedUnlinked(void *cookie);
};

#endif
