// ============================================================
// DeviceReader.cpp — EpollDeviceReader 生产实现
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：封装 /dev/vendor_lechao_lcview 的打开、epoll(LT) 等待
//   读取、overrun ioctl 查询与关闭。可恢复错误（EINTR/EAGAIN）在本层
//   消化为返回 0；EMSGSIZE（R-07 方向 2）单独返回 -EMSGSIZE 信号（内核
//   有数据但剩余缓冲放不下，须由上层 flush 闭环）；致命错误透传 errno
//   返回 -1，使 daemon 主循环的错误处理保持极简。
// ============================================================

#include "DeviceReader.h"
#include "lechao_log.h"
#include <android-base/logging.h>
#include <fcntl.h>
#include <unistd.h>
#include <cerrno>
#include <cstring>
#include <sys/epoll.h>
#include <sys/ioctl.h>

// ioctl 命令号 / struct lcview_stats 统一取自用户态镜像头
// （真相源为内核 lcview_ioctl.h + lcview_internal.h，禁单侧改，见
// lcview_ioctl.h 头注释；原本地副本宏/结构定义已收敛到镜像头）
#include "../include/lcview_ioctl.h"

using namespace vendor::lechao::lcview;

EpollDeviceReader::EpollDeviceReader(int fd) : mFd(fd)
{
}

namespace vendor {
namespace lechao {
namespace lcview {

bool isRecoverableReadErrno(int e)
{
    // EINTR：信号打断瞬时噪声；EAGAIN：非阻塞无数据。
    // R-07 方向 2：EMSGSIZE 移出可恢复白名单——它语义是"内核有数据但
    // 剩余缓冲放不下首条记录"，与"本次无数据"截然不同。原并入白名单后
    // waitAndRead 返 0，主循环把本轮当无数据，offset 不动、不 flush、
    // 不消费内核数据 → epoll LT 立即再报可读 → 无限忙旋转。现由
    // waitAndRead 返回 -EMSGSIZE 专门信号，readOnce 限频日志 + msgTooBig
    // 计数 + 退避，offset>0 时强制 flush 清空缓冲（读端契约闭环）。
    // 刻意不加 EINVAL：真参数错误吞掉会让 daemon 对坏参数静默成环。
    return e == EINTR || e == EAGAIN;
}

}  // namespace lcview
}  // namespace lechao
}  // namespace vendor

EpollDeviceReader::~EpollDeviceReader()
{
    close();
}

bool EpollDeviceReader::open()
{
    // 已打开（含注入 fd）幂等返回，供重试路径复用
    if (mFd >= 0 && mEpfd >= 0)
        return true;
    if (mFd < 0) {
        // 单次尝试；重试节奏（间隔/上限）由 LcView::readerLoop 统一控制
        mFd = ::open("/dev/vendor_lechao_lcview", O_RDONLY);
        if (mFd < 0) {
            LOG(WARNING) << "EpollDeviceReader: open failed, errno=" << errno
                         << " (" << strerror(errno) << ")";
            return false;
        }
    }

    mEpfd = epoll_create1(0);
    if (mEpfd < 0) {
        int saved = errno;
        LOG(ERROR) << "EpollDeviceReader: epoll_create1 failed, errno=" << saved;
        ::close(mFd);
        mFd = -1;
        errno = saved;
        return false;
    }

    struct epoll_event ev = {};
    ev.events = EPOLLIN;  // 水平触发（LT）
    ev.data.fd = mFd;
    if (epoll_ctl(mEpfd, EPOLL_CTL_ADD, mFd, &ev) < 0) {
        int saved = errno;
        LOG(ERROR) << "EpollDeviceReader: epoll_ctl failed, errno=" << saved;
        ::close(mEpfd);
        mEpfd = -1;
        ::close(mFd);
        mFd = -1;
        errno = saved;
        return false;
    }

    // 打开成功后记录 ring 初始状态（启动诊断现场）
    struct lcview_stats stats = {};
    if (ioctl(mFd, LCVIEW_GET_STATS, &stats) == 0)
        LOG(INFO) << "EpollDeviceReader: ring init total_records="
                  << stats.total_records << " overrun=" << stats.overrun_cnt
                  << " usage=" << stats.ring_usage_bytes << "B/"
                  << stats.ring_size_bytes << "B";

    LOG(INFO) << "EpollDeviceReader: opened, fd=" << mFd;
    return true;
}

ssize_t EpollDeviceReader::waitAndRead(uint8_t* buf, size_t offset,
                                        size_t cap, int timeoutMs)
{
    // 参数防御拆分（方向 1）：fd 未打开/未注册是设备状态错误 → EBADF；
    // offset >= cap 是调用方参数错误 → EINVAL（与设备状态解耦，语义明确）
    if (mFd < 0 || mEpfd < 0) {
        errno = EBADF;
        return -1;
    }
    if (offset >= cap) {
        errno = EINVAL;
        return -1;
    }

    struct epoll_event events[1];
    int nfds = epoll_wait(mEpfd, events, 1, timeoutMs);
    if (nfds < 0) {
        // EINTR 视为本次无数据，交由上层继续循环（非致命）
        if (errno == EINTR)
            return 0;
        return -1;
    }
    if (nfds == 0)
        return 0;  // 超时，无数据

    ssize_t n = ::read(mFd, buf + offset, cap - offset);
    // R-07 方向 2：EMSGSIZE 单独信号化（返回 -EMSGSIZE），不再并入
    // isRecoverableReadErrno 的"返回 0=本次无数据"——EMSGSIZE 语义是内核
    // 有数据但剩余缓冲放不下首条记录，返 0 会让上层误判无数据 → 不 flush
    // 不消费 → epoll LT 忙旋转。调用方（readOnce/runMainLoop）据此限频
    // 日志 + msgTooBig 计数 + 强制 flush 清缓冲，闭环读端契约。
    if (n < 0 && errno == EMSGSIZE)
        return -EMSGSIZE;
    if (n < 0 && isRecoverableReadErrno(errno))
        return 0;  // 可恢复（EINTR/EAGAIN），视作本次无数据
    // n == 0：EOF（内核 shutdown 后期望用户态退出，模块卸载场景；
    // LCV-17：与正常 timeout 同返 0 会伪装正常——置 ENODEV 返回 -1，
    // 上层按设备不可用收尾，不再被当作"本次无数据"）
    if (n == 0) {
        mEofCount++;
        LOG(ERROR) << "EpollDeviceReader: read returned EOF (kernel shutdown?)";
        errno = ENODEV;
        return -1;
    }
    // n > 0：读到数据；n < 0：致命错误，透传 errno
    return n;
}

uint32_t EpollDeviceReader::getOverrun()
{
    // 内核语义：读取即清零，返回值为本次增量
    uint32_t overrun = 0;
    if (mFd >= 0 && ioctl(mFd, LCVIEW_GET_OVERRUN, &overrun) == 0)
        return overrun;
    mIoctlErr++;  // LCV-16：失败计数，心跳可见（返 0 与真实 0 可区分）
    LC_LOGE("ioctl GET_OVERRUN failed: errno=" << errno);
    return 0;
}

uint32_t EpollDeviceReader::getTotalRecords()
{
    // 查询内核累计记录总数（含被 overrun 覆盖的），供守恒校验；
    // ioctl 失败容错返 0（与 getOverrun 语义一致，不静默抛错）
    struct lcview_stats stats = {};
    if (mFd >= 0 && ioctl(mFd, LCVIEW_GET_STATS, &stats) == 0)
        return stats.total_records;
    mIoctlErr++;  // LCV-16：失败计数，心跳可见
    LC_LOGE("ioctl GET_STATS failed: errno=" << errno);
    return 0;
}

uint32_t EpollDeviceReader::getDropped()
{
    // 查询内核 ENOSPC 丢弃累计（方向 7），与 getTotalRecords 同源
    // GET_STATS；失败容错返 0 并计 ioctlErr（心跳 ioctl 失败跳过守恒）
    struct lcview_stats stats = {};
    if (mFd >= 0 && ioctl(mFd, LCVIEW_GET_STATS, &stats) == 0)
        return stats.dropped_cnt;
    mIoctlErr++;
    LC_LOGE("ioctl GET_STATS failed: errno=" << errno);
    return 0;
}

uint32_t EpollDeviceReader::getRingSizeBytes()
{
    // 查询内核 ring 总大小（方向 6：守恒容差按环推导的数据源）；
    // 失败容错返 0 并计 ioctlErr（ioctl 失败时守恒整体跳过）
    struct lcview_stats stats = {};
    if (mFd >= 0 && ioctl(mFd, LCVIEW_GET_STATS, &stats) == 0)
        return stats.ring_size_bytes;
    mIoctlErr++;
    LC_LOGE("ioctl GET_STATS failed: errno=" << errno);
    return 0;
}

uint32_t EpollDeviceReader::getRingUsageBytes()
{
    // 查询内核 ring 当前已用字节数（R-09 方向 1：心跳输出环水位，
    // 背压可见性——ring_usage/size 越接近越接近溢出）；失败容错返 0
    // 并计 ioctlErr（与 getRingSizeBytes 同源同容错口径）
    struct lcview_stats stats = {};
    if (mFd >= 0 && ioctl(mFd, LCVIEW_GET_STATS, &stats) == 0)
        return stats.ring_usage_bytes;
    mIoctlErr++;
    LC_LOGE("ioctl GET_STATS failed: errno=" << errno);
    return 0;
}

void EpollDeviceReader::close()
{
    // 幂等：析构与显式调用都可能触发
    if (mEpfd >= 0) {
        ::close(mEpfd);
        mEpfd = -1;
    }
    if (mFd >= 0) {
        ::close(mFd);
        mFd = -1;
    }
}
