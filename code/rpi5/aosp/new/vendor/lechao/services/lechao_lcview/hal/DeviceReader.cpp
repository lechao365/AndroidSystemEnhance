// ============================================================
// DeviceReader.cpp — EpollDeviceReader 生产实现
// 所属模块：LcView 事件日志系统 — HAL 层
// 设计目的：封装 /dev/vendor_lechao_lcview 的打开、epoll(LT) 等待
//   读取、overrun ioctl 查询与关闭。可恢复错误（EINTR/EAGAIN）
//   在本层消化为返回 0，致命错误透传 errno 返回 -1，
//   使 LcView::readerLoop 的错误处理保持极简。
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

// ioctl 命令号（与内核 lcview_ioctl.h 保持一致；
// 共享头改造属后续窗口，当前副本两侧一致）
#define LCVIEW_IOC_MAGIC  'V'
#define LCVIEW_GET_OVERRUN _IOR(LCVIEW_IOC_MAGIC, 2, uint32_t)

// 内核 ring 统计结构（与内核 lcview_internal.h 的 struct lcview_stats 一致）
struct lcview_stats {
    uint32_t total_records;
    uint32_t overrun_cnt;
    uint32_t ring_usage_bytes;
    uint32_t ring_size_bytes;
};
#define LCVIEW_GET_STATS _IOR(LCVIEW_IOC_MAGIC, 3, struct lcview_stats)

EpollDeviceReader::EpollDeviceReader(int fd) : mFd(fd)
{
}

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
    if (mFd < 0 || mEpfd < 0 || offset >= cap) {
        errno = EBADF;
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
    if (n < 0 && (errno == EAGAIN || errno == EINTR))
        return 0;  // 可恢复，视作本次无数据
    // n > 0：读到数据；n == 0：EOF（设备不会关闭，理论不可达）；
    // n < 0：致命错误，透传 errno
    return n;
}

uint32_t EpollDeviceReader::getOverrun()
{
    // 内核语义：读取即清零，返回值为本次增量
    uint32_t overrun = 0;
    if (mFd >= 0 && ioctl(mFd, LCVIEW_GET_OVERRUN, &overrun) == 0)
        return overrun;
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
