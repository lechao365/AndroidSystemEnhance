// ============================================================
// main_lciod_hal.cpp — LcIod Vendor HAL 进程入口
// 所属模块: lechao_lciod (vendor 分区)
// 设计目的: HAL 守护进程的 main 函数（自 hal_service.cpp 抽出，
//           使 IoHalImpl 源码可经 filegroup 编入单元测试，
//           与 lcview main_lcview_hal.cpp 模式对齐），负责：
//   1) 初始化 Android logging
//   2) 创建 IoHalImpl 实例并注册为 Binder 服务
//   3) 进入 Binder 线程池等待 RPC 调用
//
// 服务名称: vendor.lechao.lciod.IIoHal/default
// 线程数: 1（单线程处理，避免并发 ioctl 冲突）
// ============================================================

#include "hal_service.h"
#include <android/binder_manager.h>
#include <android/binder_process.h>
#include <android-base/logging.h>

using namespace ndk;

int main() {
    android::base::InitLogging(nullptr, android::base::LogdLogger(android::base::SYSTEM));
    android::base::SetDefaultTag("lechao_lciod_hal");

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
    ABinderProcess_joinThreadPool();
    LOG(ERROR) << "joinThreadPool returned unexpectedly";
    return 1;
}
