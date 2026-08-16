// ============================================================
// main_lcview_hal.cpp — LcView HAL 服务进程入口
// 所属模块：LcView 事件日志系统 — HAL 层
// 设计目的：HAL 守护进程的 main 函数，负责：
//   1) 初始化日志系统
//   2) 设置 Binder 线程池（4 线程）
//   3) 创建 LcView 服务实例
//   4) 向 ServiceManager 注册 AIDL 服务
//   5) 加入 Binder 线程池等待远程调用
//
// 为什么 Binder 线程数设为 4：
//   HAL 只对外提供 getBatch/getOverrunCount 两个方法，
//   调用者是单一的 daemon 进程，实际并发需求很低。
//   4 个线程足以应对小并发，同时不会浪费系统资源。
// ============================================================

#include "LcView.h"
#include "lechao_log.h"
#include <android/binder_process.h>
#include <android/binder_manager.h>
#include <android-base/logging.h>

using namespace vendor::lechao::lcview;

int main(int argc, char* argv[])
{
    android::base::InitLogging(argv, android::base::LogdLogger(android::base::SYSTEM));
    LOG(INFO) << "LcView HAL: starting, loglevel="
              << (::lechao::debugVerbose() ? "debug" : "production");
    android::base::SetDefaultTag("lechao_lcview_hal");

    // 设置 Binder 线程池最大线程数为 4
    ABinderProcess_setThreadPoolMaxThreadCount(4);

    // 创建 LcView 服务实例（SharedRefBase 管理生命周期）
    // 构造函数会自动打开内核设备并启动后台读取线程
    auto lcview = ndk::SharedRefBase::make<LcView>();

    // 注册服务到 ServiceManager（名称与 AIDL 定义一致）
    // daemon 端使用 AServiceManager_checkService 查找此名称
    const std::string name = "vendor.lechao.lcview.ILcView/default";
    binder_status_t status = AServiceManager_addService(
        lcview->asBinder().get(), name.c_str());
    if (status != STATUS_OK) {
        LOG(ERROR) << "LcView HAL: failed to register " << name;
        return 1;
    }

    LOG(INFO) << "LcView HAL: registered " << name;
    // 加入 Binder 线程池，等待远程 IPC 调用
    // 此调用会阻塞当前线程直到进程退出
    ABinderProcess_joinThreadPool();
    return 0;
}
