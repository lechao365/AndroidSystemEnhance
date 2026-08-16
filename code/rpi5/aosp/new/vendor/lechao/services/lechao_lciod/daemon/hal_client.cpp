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

IoHalClient::IoHalClient() : connected_(false), lastRetryMs_(0), retryCount_(0) {
    connect();
}

IoHalClient::~IoHalClient() {
    std::lock_guard<std::mutex> lock(mtx_);
    if (currentCookie_) {
        AIBinder_DeathRecipient_delete(currentCookie_->recipient);
        delete currentCookie_;
        currentCookie_ = nullptr;
    }
}

void IoHalClient::connect() {
    auto hal = IIoHal::fromBinder(
        ndk::SpAIBinder(AServiceManager_checkService(kHalName)));
    if (hal) {
        hal_ = hal;
        connected_ = true;
        retryCount_ = 0;

        AIBinder_DeathRecipient *recipient = AIBinder_DeathRecipient_new(&onHalDied);
        AIBinder_DeathRecipient_setOnUnlinked(recipient, &onHalDiedUnlinked);

        auto *cookie = new DeathCookie{this, recipient};
        binder_status_t linkRet = AIBinder_linkToDeath(hal_->asBinder().get(),
            recipient, cookie);
        if (linkRet != STATUS_OK) {
            ALOGE("hal_client: linkToDeath failed: %d", linkRet);
            AIBinder_DeathRecipient_delete(recipient);
            delete cookie;
        } else {
            currentCookie_ = cookie;
        }
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
        int64_t interval = kRetryIntervalBaseMs * (1LL << std::min(retryCount_, 4));
        if (interval > kRetryIntervalMaxMs) interval = kRetryIntervalMaxMs;
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
