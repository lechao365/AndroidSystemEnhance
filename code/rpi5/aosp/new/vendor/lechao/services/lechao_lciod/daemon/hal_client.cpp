// ============================================================
// hal_client.cpp — HAL 客户端实现
// 所属模块: lechao_lciod daemon
// 设计目的: 实现 HAL 客户端的连接管理逻辑。
//           使用 AServiceManager_checkService 而非 getService
//           的原因是：getService 会阻塞直到 HAL 就绪，而
//           checkService 可立即返回，配合延迟重连策略。
// ============================================================

#include "hal_client.h"
#include "lechao_log.h"
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <android/binder_ibinder.h>
#include <aidl/vendor/lechao/lciod/IIoHal.h>
#include <chrono>

#define LOG_TAG "lechao_lciod"
#include <log/log.h>

using aidl::vendor::lechao::lciod::IIoHal;

static const char *kHalName = "vendor.lechao.lciod.IIoHal/default";

static constexpr int64_t kRetryIntervalBaseMs = 500;
static constexpr int64_t kRetryIntervalMaxMs = 5000;
static constexpr int kRetryLogInterval = 10;

static int64_t nowMs() {
    auto d = std::chrono::steady_clock::now().time_since_epoch();
    return std::chrono::duration_cast<std::chrono::milliseconds>(d).count();
}

/*
 * RetryIntervalMs — 指数退避重连间隔（声明见 hal_client.h）
 * 500ms × 2^min(retryCount, 4)，封顶 5s；负数 clamp 到 0 防移位 UB
 */
int64_t IoHalClient::RetryIntervalMs(int retryCount) {
    int r = retryCount < 0 ? 0 : retryCount;
    int64_t interval = kRetryIntervalBaseMs * (1LL << std::min(r, 4));
    if (interval > kRetryIntervalMaxMs) interval = kRetryIntervalMaxMs;
    return interval;
}

IoHalClient::IoHalClient() : connected_(false), lastRetryMs_(0), retryCount_(0) {
    connect();
}

IoHalClient::~IoHalClient() {
    std::lock_guard<std::mutex> lock(mtx_);
    /* LCD-006：cookie 生命周期完全交给 onHalDiedUnlinked 回调清理——
     * 析构中显式 unlinkToDeath 触发回调（binder 线程异步 delete
     * recipient+cookie），此处不再 delete，消除"析构 delete 与回调
     * delete 并发"的 double-free/UAF 窗口（原顺序：先 delete cookie
     * 再析构 hal_ 触发 unlink 回调二次 delete）。
     * onHalDiedUnlinked 只访问 dc->recipient 与 dc 本身，不回引 self，
     * 对象析构后回调执行安全 */
    if (currentCookie_ && hal_) {
        AIBinder_unlinkToDeath(hal_->asBinder().get(),
                               currentCookie_->recipient, currentCookie_);
    }
    currentCookie_ = nullptr;
}

void IoHalClient::connect() {
    auto hal = IIoHal::fromBinder(
        ndk::SpAIBinder(AServiceManager_checkService(kHalName)));
    if (hal) {
        AIBinder_DeathRecipient *recipient = AIBinder_DeathRecipient_new(&onHalDied);
        AIBinder_DeathRecipient_setOnUnlinked(recipient, &onHalDiedUnlinked);

        auto *cookie = new DeathCookie{this, recipient};
        binder_status_t linkRet = AIBinder_linkToDeath(hal->asBinder().get(),
            recipient, cookie);
        if (linkRet != STATUS_OK) {
            /* LCD-003：linkToDeath 失败必须回滚连接状态——只置 connected_
             * 而 HAL 死亡收不到通知，get() 永远返回死 binder，重连机制
             * 整体失效且无日志根因（"活着但不工作"）。回滚后走重连退避 */
            ALOGE("hal_client: linkToDeath failed: %d, rolling back", linkRet);
            AIBinder_DeathRecipient_delete(recipient);
            delete cookie;
            return;  // hal_/connected_ 保持未连接态，get() 按退避重试
        }

        hal_ = hal;
        connected_ = true;
        retryCount_ = 0;
        currentCookie_ = cookie;
        ALOGI("Connected to HAL service");
    } else {
        connected_ = false;
        if (retryCount_ == 0 || retryCount_ % kRetryLogInterval == 0) {
            ALOGW("HAL service not available (retry #%d)", retryCount_);
        }
        retryCount_++;
    }
}

void IoHalClient::onHalDiedUnlinked(void *cookie) {
    auto *dc = static_cast<DeathCookie *>(cookie);
    if (!dc) return;
    AIBinder_DeathRecipient_delete(dc->recipient);
    delete dc;
}

void IoHalClient::onHalDied(void *cookie) {
    auto *dc = static_cast<DeathCookie *>(cookie);
    if (!dc) return;
    auto *self = dc->self;
    std::lock_guard<std::mutex> lock(self->mtx_);
    self->hal_.reset();
    self->connected_ = false;
    self->retryCount_ = 0;
    self->currentCookie_ = nullptr;
    ALOGW("HAL service died, will reconnect on next call");
}

std::shared_ptr<IIoHal> IoHalClient::get() {
    std::lock_guard<std::mutex> lock(mtx_);
    if (!connected_) {
        int64_t interval = RetryIntervalMs(retryCount_);
        int64_t elapsed = nowMs() - lastRetryMs_;
        if (elapsed >= interval) {
            lastRetryMs_ = nowMs();
            connect();
            if (connected_) {
                ALOGI("hal_client: reconnected to HAL");
            }
        }
    }
    return hal_;
}
