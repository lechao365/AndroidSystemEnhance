// ============================================================
// DeviceReader.h — 内核设备读取抽象接口
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：把"打开设备 / epoll 等待读取 / overrun 查询"抽象为
//   独立接口，使 daemon 主循环只依赖抽象而非真实设备：
//     - 生产环境注入 EpollDeviceReader（封装 /dev/vendor_lechao_lcview）
//     - 单元测试注入 MockDeviceReader（gmock，隔离设备依赖）
//   抽象边界同时收口了 epoll 的 EINTR/EAGAIN 等可恢复错误，
//   daemon 主循环只需处理三种返回值：>0 数据 / 0 超时 / -1 致命。
//   架构演进：DeviceReader 由 HAL 层迁入 daemon（daemon 直读内核，
//   HAL 停用），原 HAL 侧 getBatch/waitForHal 链路随之取消。
// ============================================================

#pragma once

#include <cstddef>
#include <cstdint>
#include <sys/types.h>

namespace vendor {
namespace lechao {
namespace lcview {

// read 返回码"可恢复"白名单判定（EINTR/EAGAIN）
// 可恢复错误在 EpollDeviceReader 层消化为返回 0（本次无数据，继续循环）；
// 其余 errno 透传致命错误。注意不含 EINVAL——那是真实参数错误，
// 吞掉会让 daemon 对坏参数静默，故不得加入白名单。
// R-07 方向 2：EMSGSIZE 移出白名单——由 waitAndRead 返回 -EMSGSIZE 专门
// 信号（见 waitAndRead 文档），避免"内核有数据但缓冲放不下"被当作
// "本次无数据"而忙轮询。
bool isRecoverableReadErrno(int e);

// 设备读取抽象接口（LcView::readerLoop 的唯一设备依赖）
class DeviceReader {
public:
    virtual ~DeviceReader() = default;

    // 打开设备（单次尝试，不含重试节奏；重试策略由 readerLoop 决定）
    virtual bool open() = 0;

    // 等待并读取一次数据：
    //   返回 >0 = 本次读到的字节数（写入 buf[offset..offset+n)）
    //   返回  0 = timeoutMs 内无可读数据（含 EINTR/EAGAIN 可恢复情形）
    //   返回 -EMSGSIZE = 内核 read 返回 EMSGSIZE（有数据但剩余缓冲放不下
    //   首条记录，R-07 方向 2）——调用方须限频日志 + 计数 + 若 offset>0
    //   强制 flush 清空缓冲后重试（消 epoll 忙轮询），不得视为致命错误
    //   返回 -1 = 致命错误（fd 失效/epoll 未注册 → errno=EBADF；
    //             offset >= cap 调用方参数错误 → errno=EINVAL）
    virtual ssize_t waitAndRead(uint8_t* buf, size_t offset, size_t cap,
                                int timeoutMs) = 0;

    // 查询并清零内核 ring buffer 溢出计数（失败返回 0）
    virtual uint32_t getOverrun() = 0;

    // 查询内核 ring buffer 累计产生的记录总数（自驱动初始化起，含被
    // overrun 覆盖的记录）；与 getOverrun 互补支撑守恒校验（失败返回 0）
    virtual uint32_t getTotalRecords() = 0;

    // 查询内核 ring buffer ENOSPC 丢弃累计（方向 7：驱逐预算超限丢弃，
    // 与 getTotalRecords 同源 GET_STATS；失败返回 0）。守恒左式
    // totalΔ = overrunΔ + droppedΔ + jsonlΔ + invalidΔ 由它闭合——丢弃
    // 的记录同样计入 total_records，右式吸收 dropped 后不误报负偏差。
    virtual uint32_t getDropped() { return 0; }

    // 查询内核 ring buffer 总大小（方向 6：守恒容差按环推导，避免固定
    // 容差在 ring 配置变化时失配；失败返回 0，ioctlErr 计数区分）。非
    // 纯虚默认 0：不强制旧 mock 实现，容差退化由 ioctl 失败跳过兜底。
    virtual uint32_t getRingSizeBytes() { return 0; }

    // 查询内核 ring buffer 当前已用字节数（R-09 方向 1：心跳输出环水位
    // ring_usage，背压直接可见——ring_usage 越接近 ring_size 越接近积压
    // 溢出）。与 getRingSizeBytes 同源 GET_STATS；失败返回 0 并计
    // ioctlErr（ioctl 失败时心跳水位归 0，由 ioctlErr 区分真 0 与失败）。
    virtual uint32_t getRingUsageBytes() { return 0; }

    // R-10 方向 2：单次 GET_STATS ioctl 拉取全部统计字段并缓存，供
    // getTotalRecords/getDropped/getRingSizeBytes/getRingUsageBytes 从
    // 缓存分发——心跳每 30s 四次 GET_STATS（total/dropped/ringSize/
    // ringUsage）合并为一次 ioctl，消热路径 ioctl 放大。默认空实现
    // （不强制 mock 覆盖，未 refresh 的 getter 仍回退单次 ioctl）；
    // 生产 EpollDeviceReader 实现为缓存语义。失败计 ioctlErr 并清缓存
    // 有效位（getter 回退单次 ioctl 保容错语义）。
    virtual void refreshStats() {}

    // LCV-16/17：诊断计数（心跳可见性，失败返 0 与真实 0 可区分）。
    // 提上抽象接口：emitHeartbeat 经抽象 DeviceReader 注入（main_loop
    // 可测边界）即可读取，不再依赖具体 EpollDeviceReader。
    virtual uint64_t ioctlErr() const = 0;
    virtual uint64_t eofCount() const = 0;

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
    uint32_t getDropped() override;
    uint32_t getRingSizeBytes() override;
    uint32_t getRingUsageBytes() override;
    void refreshStats() override;
    void close() override;

    // LCV-16/17：诊断计数（心跳可见性，失败返 0 与真实 0 可区分）
    uint64_t ioctlErr() const override { return mIoctlErr; }
    uint64_t eofCount() const override { return mEofCount; }

private:
    int mFd = -1;
    int mEpfd = -1;
    // LCV-16/17：ioctl 失败与 EOF 计数（失败返 0 与真实 0 在心跳中
    // 不可区分的根因修复——心跳输出 ioctl_err/eof 字段供判红）
    uint64_t mIoctlErr = 0;
    uint64_t mEofCount = 0;
    // R-10 方向 2：GET_STATS 缓存（refreshStats 单次 ioctl 拉取后，
    // getTotalRecords/getDropped/getRingSizeBytes/getRingUsageBytes
    // 从缓存分发）。mStatsValid 标记缓存有效：false 时 getter 回退单次
    // ioctl（未 refresh / refresh 失败均保容错语义），true 时全走缓存
    // 不再发 ioctl。逐字段标量缓存（不入 struct lcview_stats 类型——
    // 头文件不依赖 ioctl 镜像头，测试 TU 无 ioctl 依赖）
    uint32_t mCachedTotal = 0;
    uint32_t mCachedDropped = 0;
    uint32_t mCachedRingSize = 0;
    uint32_t mCachedRingUsage = 0;
    bool mStatsValid = false;
};

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor
