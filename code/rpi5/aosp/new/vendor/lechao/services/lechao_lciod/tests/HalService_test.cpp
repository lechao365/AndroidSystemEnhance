// ============================================================
// HalService_test.cpp — IoHalImpl 缓存一致性与 AIDL 错误分支
// 所属模块：lechao_lciod HAL 测试（device-tests，真实 /dev 节点）
// 拦截：CXX-002（mDeviceMap 缓存一致性、未知 minor 防御）、
//       CXX-003（不存在设备显式 ENODEV，不伪装空数据——CXX-004 同源）
// 真实设备分支在无 usbd 节点环境自动 GTEST_SKIP，不假红。
// 注：getStats 全字段映射与投影完整性由上板 lciod-pipeline 用例
// 兜底（probe 输出全字段校验），此处聚焦错误分支与缓存行为。
// ============================================================

#include <gtest/gtest.h>

#include <cstring>
#include <memory>
#include <string>
#include <vector>
#include <poll.h>

#include "hal_service.h"
#include "minor_utils.h"

using aidl::vendor::lechao::lciod::IoEvent;
using aidl::vendor::lechao::lciod::IoStats;
using lechao::lciod::kUsbdDevPrefix;
using lechao::lciod::ParseMinorFromPath;

class IoHalImplTest : public ::testing::Test {
protected:
    void SetUp() override {
        // SharedRefBase 私有化 operator new：必须经 make 构造（NDK binder 契约）
        hal_ = ndk::SharedRefBase::make<IoHalImpl>();
    }

    // 取首个在线设备 minor；无设备返回 false（调用方 GTEST_SKIP）
    bool FirstMinor(int32_t* minor) {
        std::vector<std::string> devices;
        if (!hal_->listDevices(&devices).isOk() || devices.empty())
            return false;
        return ParseMinorFromPath(devices[0], minor);
    }

    std::shared_ptr<IoHalImpl> hal_;
};

/* --- 构造与缓存行为 --- */

TEST_F(IoHalImplTest, Construct_RefreshDevices_NoCrash) {
    // 构造即 refresh_devices（真实 glob + ParseMinorFromPath），进程不崩即通过
    SUCCEED();
}

TEST_F(IoHalImplTest, RefreshDevices_Idempotent_SameDeviceCount) {
    std::vector<std::string> first, second;
    hal_->refresh_devices();
    ASSERT_TRUE(hal_->listDevices(&first).isOk());
    hal_->refresh_devices();
    ASSERT_TRUE(hal_->listDevices(&second).isOk());
    EXPECT_EQ(first.size(), second.size());
}

TEST_F(IoHalImplTest, ListDevices_SortedAscendingByMinor) {
    std::vector<std::string> devices;
    ASSERT_TRUE(hal_->listDevices(&devices).isOk());
    int32_t prev = -1;
    for (const auto& path : devices) {
        EXPECT_EQ(path.compare(0, std::strlen(kUsbdDevPrefix), kUsbdDevPrefix), 0)
            << "unexpected path: " << path;
        int32_t minor = -1;
        ASSERT_TRUE(ParseMinorFromPath(path, &minor));
        EXPECT_LT(prev, minor) << "listDevices not sorted: " << path;
        prev = minor;
    }
}

TEST_F(IoHalImplTest, ResolveDevice_UnknownMinor_ReturnsNull) {
    EXPECT_EQ(hal_->resolve_device(9999), nullptr);
}

/* --- 未知 minor 错误分支（7 个 AIDL 方法全防御） --- */

TEST_F(IoHalImplTest, GetStats_UnknownMinor_ENODEV) {
    IoStats out;
    auto st = hal_->getStats(9999, &out);
    EXPECT_FALSE(st.isOk());
    EXPECT_EQ(st.getServiceSpecificError(), -ENODEV);
}

TEST_F(IoHalImplTest, ResetState_UnknownMinor_ENODEV) {
    auto st = hal_->resetState(9999);
    EXPECT_FALSE(st.isOk());
    EXPECT_EQ(st.getServiceSpecificError(), -ENODEV);
}

TEST_F(IoHalImplTest, GetConfig_UnknownMinor_ENODEV) {
    aidl::vendor::lechao::lciod::IoConfig cfg;
    auto st = hal_->getConfig(9999, &cfg);
    EXPECT_FALSE(st.isOk());
    EXPECT_EQ(st.getServiceSpecificError(), -ENODEV);
}

TEST_F(IoHalImplTest, SetConfig_UnknownMinor_ENODEV) {
    aidl::vendor::lechao::lciod::IoConfig cfg;
    bool ok = true;
    auto st = hal_->setConfig(9999, cfg, &ok);
    EXPECT_FALSE(st.isOk());
    EXPECT_EQ(st.getServiceSpecificError(), -ENODEV);
    EXPECT_FALSE(ok);  // 失败时输出 bool 必须为 false（不伪装成功）
}

TEST_F(IoHalImplTest, ReadEvent_UnknownMinor_ENODEV) {
    IoEvent ev;
    auto st = hal_->readEvent(9999, 50, &ev);
    EXPECT_FALSE(st.isOk());
    EXPECT_EQ(st.getServiceSpecificError(), -ENODEV);
}

/* --- 真实设备分支（无节点环境跳过） --- */

TEST_F(IoHalImplTest, GetStats_RealDevice_StringBoundsRespected) {
    int32_t minor = -1;
    if (!FirstMinor(&minor))
        GTEST_SKIP() << "no vendor_lechao_usbd device on board";

    IoStats out;
    ASSERT_TRUE(hal_->getStats(minor, &out).isOk());
    // 字符串映射经 strnlen 限长，不得越界（CXX-002）
    EXPECT_LE(out.vendor.size(), 32u);
    EXPECT_LE(out.product.size(), 32u);
}

TEST_F(IoHalImplTest, ConfigRoundTrip_EnabledStatePreserved) {
    int32_t minor = -1;
    if (!FirstMinor(&minor))
        GTEST_SKIP() << "no vendor_lechao_usbd device on board";

    aidl::vendor::lechao::lciod::IoConfig orig{};
    ASSERT_TRUE(hal_->getConfig(minor, &orig).isOk());

    // 写回原值（无副作用往返），验证 set→get 一致
    bool ok = false;
    ASSERT_TRUE(hal_->setConfig(minor, orig, &ok).isOk());
    EXPECT_TRUE(ok);

    aidl::vendor::lechao::lciod::IoConfig now{};
    ASSERT_TRUE(hal_->getConfig(minor, &now).isOk());
    EXPECT_EQ(now.enabled, orig.enabled);
    EXPECT_EQ(now.flags, orig.flags);
}

TEST_F(IoHalImplTest, ReadEvent_TimeoutOrEvent_OkStatus) {
    int32_t minor = -1;
    if (!FirstMinor(&minor))
        GTEST_SKIP() << "no vendor_lechao_usbd device on board";

    IoEvent ev{};
    // 超时语义：ok + valid=false（"暂无事件"）；有事件则 ok + valid=true。
    // 关键契约：任何情况下都是 isOk()，不伪装错误也不伪装数据
    ASSERT_TRUE(hal_->readEvent(minor, 100, &ev).isOk());
}

/* --- R-16 P4 方向 3：uevent 增量维护（handle_device_add/remove） --- */

TEST_F(IoHalImplTest, DeviceAdd_CreatesEntry_LazyFd) {
    hal_->handle_device_add(77);
    DeviceEntry* entry = hal_->resolve_device(77);
    ASSERT_NE(entry, nullptr);
    EXPECT_EQ(entry->path, "/dev/vendor_lechao_usbd77");
    EXPECT_EQ(entry->fd, -1);  // 懒打开，不主动 open
}

TEST_F(IoHalImplTest, DeviceAdd_AlreadyPresent_Idempotent) {
    hal_->handle_device_add(78);
    hal_->handle_device_add(78);  // 重复 add 幂等，不覆盖已有 entry
    DeviceEntry* entry = hal_->resolve_device(78);
    ASSERT_NE(entry, nullptr);
    EXPECT_EQ(entry->path, "/dev/vendor_lechao_usbd78");
}

TEST_F(IoHalImplTest, DeviceRemove_ClearsEntry) {
    hal_->handle_device_add(79);
    ASSERT_NE(hal_->resolve_device(79), nullptr);
    hal_->handle_device_remove(79);
    EXPECT_EQ(hal_->resolve_device(79), nullptr);
}

TEST_F(IoHalImplTest, DeviceRemove_NotPresent_Idempotent) {
    // remove 未感知的设备幂等跳过，不崩
    hal_->handle_device_remove(8888);
    SUCCEED();
}

TEST_F(IoHalImplTest, UeventFd_Constructed_OpenedOrFallback) {
    // 构造时打开 uevent socket：>0 为正常；-1 为降级 glob-only（无 netlink
    // 权限/环境），两种情况都不应视为致命——功能由 glob 兜底
    int fd = hal_->uevent_fd();
    EXPECT_GE(fd, -1);
    if (fd >= 0) {
        // fd 有效且可被 poll 监听（POLLIN 事件位正常）
        struct pollfd pfd{fd, POLLIN, 0};
        EXPECT_GE(::poll(&pfd, 1, 0), 0);
    }
}

/* --- R-16 P4 方向 4：dupEventFd（epoll 多路复用 fd 透出） --- */

TEST_F(IoHalImplTest, DupEventFd_UnknownMinor_ENODEV) {
    ndk::ScopedFileDescriptor fd;
    auto st = hal_->dupEventFd(9999, &fd);
    EXPECT_FALSE(st.isOk());
    EXPECT_EQ(st.getServiceSpecificError(), -ENODEV);
}

TEST_F(IoHalImplTest, DupEventFd_RealDevice_ReturnsPollableFd) {
    int32_t minor = -1;
    if (!FirstMinor(&minor))
        GTEST_SKIP() << "no vendor_lechao_usbd device on board";

    ndk::ScopedFileDescriptor fd;
    ASSERT_TRUE(hal_->dupEventFd(minor, &fd).isOk());
    ASSERT_GE(fd.get(), 0);

    // 返回的 fd 可被 poll 监听（内核 .poll 支持，供 daemon epoll 注册）
    struct pollfd pfd{fd.get(), POLLIN, 0};
    EXPECT_GE(::poll(&pfd, 1, 0), 0);
    // 无事件时不应阻塞且不报 POLLERR（EPOLLHUP 表示断开，正常空转无事件）
    EXPECT_EQ((pfd.revents & POLLERR), 0);
}
