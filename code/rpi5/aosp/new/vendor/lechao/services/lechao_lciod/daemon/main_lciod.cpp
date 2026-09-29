// ============================================================
// main_lciod.cpp — LcIod System Daemon 进程入口
// 所属模块: lechao_lciod (system 分区)
// 设计目的: System Daemon 的 main 函数（自 service.cpp 抽出，
//           使 IoServiceImpl 源码可经 filegroup 编入单元测试，
//           与 lcview 模式对齐），负责：
//   1) 创建 IoServiceImpl 实例并注册为 Binder 服务
//   2) 注册成功后启动后台监控线程
//   3) 进入 Binder 线程池等待 RPC 调用
//
// 服务名称: system.lechao.lciod.IIoService/default
// ============================================================

#include "service.h"
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include "lechao_log.h"

#define LOG_TAG "lechao_lciod"
#include <log/log.h>

using namespace ndk;

int main() {
    /* R-17 方向 1：Binder 线程池由 1 扩为 4——消 daemon RPC 单线程单点。
     * 原 LCD-010 "池=1" 的顾虑（hal_client_ 并发安全）已解除：hal_client_
     * 自 R-16 起由 std::mutex 保护 get()/connect()，其余 RPC 方法均以
     * 局部变量 + hal_client_ 转发，无共享可变状态；后台监控线程已按
     * per-minor 分片为独立 worker（见 start_monitor），与 Binder 池
     * 并行。多客户端并发 RPC（getStats/readIoEvent 等快返回调用）不再
     * 串行排队，单客户端长调用不再饿死其他客户端。 */
    ABinderProcess_setThreadPoolMaxThreadCount(4);

    auto service = ndk::SharedRefBase::make<IoServiceImpl>();
    const std::string instance = "default";
    const std::string name = std::string("system.lechao.lciod.IIoService/") + instance;

    binder_status_t status = AServiceManager_addService(
        service->asBinder().get(), name.c_str());
    if (status != STATUS_OK) {
        ALOGE("Failed to register %s: %d", name.c_str(), status);
        return 1;
    }
    ALOGI("Registered %s", name.c_str());

    /* R-16 P4 方向 4 修复：服务注册后立即非阻塞启动 binder 线程池，
     * 再启动后台监控线程。原实现仅靠末尾 joinThreadPool() 启动池，
     * monitor 线程（service->start()）启动后立即在 hal_client 构造中
     * 调 linkToDeath——此时池未启动，binder 报 "Thread Pool max thread
     * count is 0"，linkToDeath 失败 → onHalDied 永不触发 → HAL 崩溃/
     * 重启后 daemon 仍持有死 binder，后续 AIDL 事务永久悬挂（monitor
     * 卡死在 binder_ioctl_write_read，热插拔后事件无人消费、event_drop
     * 飙升）。startThreadPool() 为非阻塞（池线程就绪即返回），join 仅
     * 供主线程挂入池服务 RPC。 */
    ABinderProcess_startThreadPool();

    /* 服务注册成功后才启动后台监控线程 */
    service->start();

    ABinderProcess_joinThreadPool();
    return 1;
}
