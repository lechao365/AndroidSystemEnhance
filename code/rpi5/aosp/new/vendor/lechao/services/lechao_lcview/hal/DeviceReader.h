// ============================================================
// DeviceReader.h — 内核设备读取抽象接口
// 所属模块：LcView 事件日志系统 — HAL 层
// 设计目的：把"打开设备 / epoll 等待读取 / overrun 查询"抽象为
//   独立接口，使 LcView::readerLoop 只依赖抽象而非真实设备：
//     - 生产环境注入 EpollDeviceReader（封装 /dev/vendor_lechao_lcview）
//     - 单元测试注入 MockDeviceReader（gmock，隔离设备依赖）
//   抽象边界同时收口了 epoll 的 EINTR/EAGAIN 等可恢复错误，
//   readerLoop 只需处理三种返回值：>0 数据 / 0 超时 / -1 致命。
// ============================================================

#pragma once

#include <cstddef>
#include <cstdint>
#include <sys/types.h>

namespace vendor {
namespace lechao {
namespace lcview {

// 设备读取抽象接口（LcView::readerLoop 的唯一设备依赖）
class DeviceReader {
public:
    virtual ~DeviceReader() = default;

    // 打开设备（单次尝试，不含重试节奏；重试策略由 readerLoop 决定）
    virtual bool open() = 0;

    // 等待并读取一次数据：
    //   返回 >0 = 本次读到的字节数（写入 buf[offset..offset+n)）
    //   返回  0 = timeoutMs 内无可读数据（含 EINTR/EAGAIN 可恢复情形）
    //   返回 -1 = 致命错误（fd 失效/epoll 损坏，errno 携带现场）
    virtual ssize_t waitAndRead(uint8_t* buf, size_t offset, size_t cap,
                                int timeoutMs) = 0;

    // 查询并清零内核 ring buffer 溢出计数（失败返回 0）
    virtual uint32_t getOverrun() = 0;

    // 查询内核 ring buffer 累计产生的记录总数（自驱动初始化起，含被
    // overrun 覆盖的记录）；与 getOverrun 互补支撑守恒校验（失败返回 0）
    virtual uint32_t getTotalRecords() = 0;

    // 关闭设备（幂等，可重复调用）
    virtual void close() = 0;
};

// 生产实现：/dev/vendor_lechao_lcview 的 epoll 读取器
// （水平触发 LT 模式 + timeout，未读净时下轮 epoll_wait 立即返回）
class EpollDeviceReader : public DeviceReader {
public:
    // fd >= 0 时为注入的可测缝（UT 用 pipe/eventfd 替代真实设备）：
    // open() 跳过设备 ::open 直接走 epoll 注册，覆盖 poll 超时/部分读/
    // 错误码等分支（此前 DeviceReader 生产路径被 Mock 顶替覆盖恒 0%）
    explicit EpollDeviceReader(int fd = -1);
    ~EpollDeviceReader() override;

    bool open() override;
    ssize_t waitAndRead(uint8_t* buf, size_t offset, size_t cap,
                        int timeoutMs) override;
    uint32_t getOverrun() override;
    uint32_t getTotalRecords() override;
    void close() override;

private:
    int mFd = -1;
    int mEpfd = -1;
};

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor
