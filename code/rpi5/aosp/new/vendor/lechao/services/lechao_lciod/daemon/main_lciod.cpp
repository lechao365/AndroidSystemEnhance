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
    ABinderProcess_setThreadPoolMaxThreadCount(1);

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

    /* 服务注册成功后才启动后台监控线程 */
    service->start();

    ABinderProcess_joinThreadPool();
    return 1;
}
