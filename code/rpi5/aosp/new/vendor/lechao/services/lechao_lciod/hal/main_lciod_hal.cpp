// ============================================================
// main_lciod_hal.cpp — LcIod Vendor HAL 进程入口
// 所属模块: lechao_lciod (vendor 分区)
// 设计目的: HAL 守护进程的 main 函数（自 hal_service.cpp 抽出，
//           使 IoHalImpl 源码可经 filegroup 编入单元测试，
//           与 lcview main_lcview_hal.cpp 模式对齐），负责：
//   1) 初始化 Android logging
//   2) 创建 IoHalImpl 实例并注册为 Binder 服务
//   3) 进入单线程 poll 事件循环（binder fd + 内核 uevent fd）
//
// 服务名称: vendor.lechao.lciod.IIoHal/default
// 线程数: 1（单线程处理，避免并发 ioctl 冲突；LCD-010）
//
// R-16 P4 方向 3：由 ABinderProcess_joinThreadPool 改为
//   ABinderProcess_setupPolling + poll 循环——同一线程同时服务
//   binder RPC 与内核 uevent（设备上下线即时感知），保持单线程
//   串行化设计意图（mDeviceMap 无需加锁）。
// ============================================================

#include "hal_service.h"
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <android-base/logging.h>
#include <cerrno>
#include <poll.h>

using namespace ndk;

int main() {
    android::base::InitLogging(nullptr, android::base::LogdLogger(android::base::SYSTEM));
    android::base::SetDefaultTag("lechao_lciod_hal");

    /* LCD-010：线程池 = 1 为设计意图——HAL 内部 device fd 表与
     * read_event 状态未做并发审计，串行化所有 AIDL 调用规避竞态；
     * 扩容前须先为 per-device 访问加互斥。
     *
     * R-17 方向 1 决策固化：HAL 保持单线程"异步事件循环"而非
     * "阻塞单点"——(1) readEvent 已非阻塞化（daemon 传 timeout=0，
     * HAL clamp 上限 1000ms），不存在长时间占住 binder 线程的调用；
     * (2) 本线程同轮 poll binder fd + uevent fd 非阻塞轮转；
     * (3) daemon 侧已按 minor 分片线程池消费，单设备慢/故障只阻塞
     * 其所在分片，不再拖垮全局——HAL 单线程不再构成可用性单点。 */
    ABinderProcess_setThreadPoolMaxThreadCount(1);
    auto service = ndk::SharedRefBase::make<IoHalImpl>();
    const std::string instance = "default";
    const std::string name = std::string("vendor.lechao.lciod.IIoHal/") + instance;

    binder_status_t status = AServiceManager_addService(
        service->asBinder().get(), name.c_str());
    if (status != STATUS_OK) {
        LOG(ERROR) << "Failed to register " << name << ": " << status;
        return 1;
    }
    LOG(INFO) << "Registered " << name;

    /* R-16 P4 方向 3：binder polling 单线程事件循环。
     * binder fd 可读 → 处理 RPC；uevent fd 可读 → 增量维护设备表。
     * setupPolling 失败（API 不可用）时降级 joinThreadPool（glob-only
     * 兜底已由构造路径保证，uevent 感知丢失仅影响即时性不影响功能）。 */
    int binder_fd = -1;
    binder_status_t poll_status = ABinderProcess_setupPolling(&binder_fd);
    if (poll_status != STATUS_OK) {
        LOG(WARNING) << "setupPolling failed (" << poll_status
                     << "), fallback to joinThreadPool (glob-only)";
        ABinderProcess_joinThreadPool();
        LOG(ERROR) << "joinThreadPool returned unexpectedly";
        return 1;
    }

    int uevent_fd = service->uevent_fd();
    while (true) {
        struct pollfd fds[2];
        int nfds = 0;
        fds[nfds].fd = binder_fd;
        fds[nfds].events = POLLIN;
        nfds++;
        if (uevent_fd >= 0) {
            fds[nfds].fd = uevent_fd;
            fds[nfds].events = POLLIN;
            nfds++;
        }
        int pr = poll(fds, nfds, -1);
        if (pr < 0) {
            if (errno == EINTR)
                continue;
            int saved = errno;
            LOG(ERROR) << "poll failed: " << strerror(saved)
                       << " (fd=" << binder_fd << " uevent_fd=" << uevent_fd << ")";
            /* CXX-004：长生命周期主循环致命错误 4 步退出——置死循环不可达
             * 前以 ERROR 日志 + exit(1) 交 init 重启，不静默空转 */
            return 1;
        }
        if (fds[0].revents & (POLLIN | POLLERR | POLLHUP)) {
            ABinderProcess_handlePolledCommands();
            /* uevent fd 可能在处理 RPC 期间被重新感知（fd 生命周期不变，
             * 无需重取） */
        }
        if (uevent_fd >= 0 && fds[1].revents & (POLLIN | POLLERR | POLLHUP)) {
            service->on_uevent_readable();
            uevent_fd = service->uevent_fd();  /* 故障降级后 fd 可能置 -1 */
        }
    }
}
