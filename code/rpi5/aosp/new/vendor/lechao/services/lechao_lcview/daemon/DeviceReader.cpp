// ============================================================
// DeviceReader.cpp — EpollDeviceReader 生产实现
// 所属模块：LcView 事件日志系统 — Daemon 层
// 设计目的：封装 /dev/vendor_lechao_lcview 的打开、epoll(LT) 等待
//   读取、overrun ioctl 查询与关闭。可恢复错误（EINTR/EAGAIN/EMSGSIZE）
//   在本层消化为返回 0，致命错误透传 errno 返回 -1，
//   使 daemon 主循环的错误处理保持极简。
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

using namespace vendor::lechao::lcview;

// ioctl 命令号：用户态本地副本，与内核
// code/rpi5/kernel/new/vendor/lechao/LcView/lcview_ioctl.h 的宏保持一致。
// 两侧未走共享头（vendor include 与内核 include 目录隔离），命令号漂移
// 会导致 ioctl 失败/错配——改动任一侧须同步核对另一侧（ioctl 失败已由
// mIoctlErr 计数进心跳可见，LCV-16）
#define LCVIEW_IOC_MAGIC  'V'
#define LCVIEW_GET_OVERRUN _IOR(LCVIEW_IOC_MAGIC, 2, uint32_t)

// 内核 ring 统计结构（与内核 lcview_internal.h 的 struct lcview_stats 一致）
struct lcview_stats {
    uint32_t total_records;
    uint32_t overrun_cnt;
    uint32_t ring_usage_bytes;
    uint32_t ring_size_bytes;
};
// GET_STATS 承载 getTotalRecords（心跳守恒校验数据源）与启动诊断快照；
// total_records/overrun_cnt 为内核驱动自初始化起的累计计数（只读不清零，
// 与 GET_OVERRUN 的"读取即清零"语义不同，两者互补支撑守恒）
#define LCVIEW_GET_STATS _IOR(LCVIEW_IOC_MAGIC, 3, struct lcview_stats)

EpollDeviceReader::EpollDeviceReader(int fd) : mFd(fd)
{
}

namespace vendor {
namespace lechao {
namespace lcview {

bool isRecoverableReadErrno(int e)
{
    // EINTR：信号打断瞬时噪声；EAGAIN：非阻塞无数据；
    // EMSGSIZE：内核 read 首条记录放不下剩余读缓冲（KRN-001）。该路径
    // 已由读端契约闭合：用户态 kMinReadSize 恒不小于单条记录上限
    // LCVIEW_MAX_RECORD_SIZE，预防性 flush 保证剩余空间恒 >= 单条记录
    // 上限，正常路径不再触发——保留可恢复判定仅作防御兜底（真触发也
    // 不应致命退出）。刻意不加 EINVAL：真参数错误吞掉会让 daemon 对
    // 坏参数静默成环。
    return e == EINTR || e == EAGAIN || e == EMSGSIZE;
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
    if (n < 0 && isRecoverableReadErrno(errno))
        return 0;  // 可恢复（EINTR/EAGAIN/EMSGSIZE），视作本次无数据
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
