#include "raw-gadget.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <limits.h>
#include <poll.h>
#include <sys/ioctl.h>
#include <linux/usb/raw_gadget.h>
#include <linux/usb/ch9.h>

struct raw_gadget {
    int fd;
};

/* Pi Zero 2W 上 dwc2 的 UDC 名称：'20980000.usb' (driver/device 同名)
 * 注意：实际运行时此值由调用者通过 open() 的 udc_path 传入
 */
struct raw_gadget *raw_gadget_open(const char *udc_path)
{
    struct raw_gadget *rg = calloc(1, sizeof(*rg));
    if (!rg)
        return NULL;

    rg->fd = open("/dev/raw-gadget", O_RDWR);
    if (rg->fd < 0) {
        perror("open /dev/raw-gadget");
        free(rg);
        return NULL;
    }

    struct usb_raw_init init;
    memset(&init, 0, sizeof(init));
    snprintf((char *)init.driver_name, sizeof(init.driver_name), "%s",
             "usb-fault-inject");
    snprintf((char *)init.device_name, sizeof(init.device_name), "%s",
             udc_path);
    init.speed = USB_SPEED_HIGH;

    if (ioctl(rg->fd, USB_RAW_IOCTL_INIT, &init) < 0) {
        perror("USB_RAW_IOCTL_INIT");
        close(rg->fd);
        free(rg);
        return NULL;
    }

    return rg;
}

void raw_gadget_close(struct raw_gadget *rg)
{
    if (!rg)
        return;
    if (rg->fd >= 0)
        close(rg->fd);
    free(rg);
}

int raw_gadget_configure(struct raw_gadget *rg,
                         uint16_t vid, uint16_t pid,
                         const char *vendor, const char *product)
{
    (void)rg; (void)vid; (void)pid; (void)vendor; (void)product;
    /* 占位实现：MSD gadget 描述符配置由 ConfigFS/g_mass_storage 在 UDC 侧完成。
     * Raw Gadget 仅作为协议层故障注入通道，复用上层 gadget 提供的端点。
     */
    return 0;
}

/* STALL 端点：使用 USB_RAW_IOCTL_EP_SET_HALT 触发 Halt 状态
 * 备注：Linux 6.12 头文件中 EP_STALL 不存在，正确接口是 EP_SET_HALT
 */
int raw_gadget_stall_now(struct raw_gadget *rg, int ep)
{
    __u32 ep_handle = (__u32)ep;
    if (ioctl(rg->fd, USB_RAW_IOCTL_EP_SET_HALT, &ep_handle) < 0) {
        perror("USB_RAW_IOCTL_EP_SET_HALT");
        return -1;
    }
    fprintf(stderr, "[raw-gadget] STALL injected on ep 0x%02x\n", ep);
    return 0;
}

/* 端点读：用于接收 Host 发来的 CBW
 * usb_raw_ep_io 是带 flexible array 的结构体，必须嵌入到一段连续内存中
 * 这里用堆分配 + 头 + 数据的紧凑布局
 */
int raw_gadget_ep_read(struct raw_gadget *rg, int ep,
                       void *buf, size_t len, int timeout_ms)
{
    if (len == 0 || len > 0x10000) {
        fprintf(stderr, "[raw-gadget] ep_read: invalid len %zu\n", len);
        return -1;
    }

    size_t total = sizeof(struct usb_raw_ep_io) + len;
    struct usb_raw_ep_io *ep_io = calloc(1, total);
    if (!ep_io) return -1;

    ep_io->ep = (__u16)ep;
    ep_io->length = (__u32)len;
    /* data[] 区域由调用方提供；read 后驱动会填充 */

    if (timeout_ms > 0) {
        struct pollfd pfd = { .fd = rg->fd, .events = POLLIN };
        int pr = poll(&pfd, 1, timeout_ms);
        if (pr <= 0) {
            if (pr < 0) perror("poll");
            else fprintf(stderr, "[raw-gadget] ep_read timeout on ep 0x%02x\n", ep);
            free(ep_io);
            return -1;
        }
    }

    int n = ioctl(rg->fd, USB_RAW_IOCTL_EP_READ, ep_io);
    if (n < 0) {
        perror("USB_RAW_IOCTL_EP_READ");
        free(ep_io);
        return -1;
    }

    if (ep_io->length > INT_MAX) {
        fprintf(stderr, "[raw-gadget] ep_read: length overflow %u\n", ep_io->length);
        free(ep_io);
        return -1;
    }
    int actual = (int)ep_io->length;
    if (actual > (int)len) actual = (int)len;
    if (actual > 0)
        memcpy(buf, ep_io->data, actual);
    free(ep_io);
    return actual;
}

/* 端点写：用于发送 CSW / Data */
int raw_gadget_ep_write(struct raw_gadget *rg, int ep,
                        const void *buf, size_t len)
{
    if (len == 0 || len > 0x10000) {
        fprintf(stderr, "[raw-gadget] ep_write: invalid len %zu\n", len);
        return -1;
    }

    size_t total = sizeof(struct usb_raw_ep_io) + len;
    struct usb_raw_ep_io *ep_io = calloc(1, total);
    if (!ep_io) return -1;

    ep_io->ep = (__u16)ep;
    ep_io->length = (__u32)len;
    memcpy(ep_io->data, buf, len);

    int n = ioctl(rg->fd, USB_RAW_IOCTL_EP_WRITE, ep_io);
    if (n < 0) {
        perror("USB_RAW_IOCTL_EP_WRITE");
        free(ep_io);
        return -1;
    }

    int actual = (int)ep_io->length;
    free(ep_io);
    return actual;
}

/* VBUS 模拟：mA=0 模拟拉低（Host 检测断开），mA>0 恢复
 * 备注：USB_RAW_IOCTL_VBUS_DRAW 单位是 2mA，但传入 mA 数时内核会自行转换
 */
int raw_gadget_vbus_draw(struct raw_gadget *rg, int mA)
{
    __u32 val = (__u32)mA;
    if (ioctl(rg->fd, USB_RAW_IOCTL_VBUS_DRAW, &val) < 0) {
        perror("USB_RAW_IOCTL_VBUS_DRAW");
        return -1;
    }
    fprintf(stderr, "[raw-gadget] VBUS set to %d mA\n", mA);
    return 0;
}

/* Gadget RUN 启动：从 STOP 状态恢复（实际通过 VBUS 恢复实现）*/
int raw_gadget_run_start(struct raw_gadget *rg)
{
    /* USB_RAW_IOCTL_RUN 是 _IO('U', 1)，无参数 */
    if (ioctl(rg->fd, USB_RAW_IOCTL_RUN) < 0) {
        perror("USB_RAW_IOCTL_RUN");
        return -1;
    }
    fprintf(stderr, "[raw-gadget] RUN started\n");
    return 0;
}

/* Gadget RUN 停止：模拟物理断开（通过 VBUS 拉低）*/
int raw_gadget_run_stop(struct raw_gadget *rg)
{
    /* USB_RAW_IOCTL_RUN 是启动接口，没有 STOP。
     * 实际断开通过 VBUS_DRAW=0 实现，更可靠（触发 Host 端断开检测）。
     */
    fprintf(stderr, "[raw-gadget] RUN stop (via VBUS=0)\n");
    return raw_gadget_vbus_draw(rg, 0);
}

/* Bulk ERR PID 发送：先 SET_HALT 再延迟，模拟单次 ABORT
 * 备注：Raw Gadget 没有直接的 ERR PID 发送接口，STALL-then-delay 是
 *       主机端最接近 Bulk ABORT 的实现（触发 STALL → ABORT 序列）
 */
int raw_gadget_send_bulk_err(struct raw_gadget *rg, int ep)
{
    __u32 ep_handle = (__u32)ep;
    if (ioctl(rg->fd, USB_RAW_IOCTL_EP_SET_HALT, &ep_handle) < 0) {
        perror("EP_SET_HALT (abort)");
        return -1;
    }
    /* 短暂延时后 Host 收到 STALL 并触发 ABORT 序列 */
    usleep(1000);
    fprintf(stderr, "[raw-gadget] Bulk ERR injected on ep 0x%02x\n", ep);
    return 0;
}

/* 事件获取：阻塞等待 USB 事件（CONNECT / CONTROL） */
int raw_gadget_event_fetch(struct raw_gadget *rg, int timeout_ms)
{
    struct pollfd pfd = { .fd = rg->fd, .events = POLLIN };
    int pr = poll(&pfd, 1, timeout_ms);
    if (pr <= 0) {
        if (pr < 0) perror("poll (event)");
        return -1;
    }

    /* 事件通过 read() 读取，使用固定大小缓冲区
     * usb_raw_event 头部 + data[]，256 字节足以容纳所有标准事件
     */
    char buf[256] __attribute__((aligned(8)));
    _Static_assert(sizeof(struct usb_raw_event) <= sizeof(buf),
                   "event buffer too small for usb_raw_event header");
    ssize_t n = read(rg->fd, buf, sizeof(buf));
    if (n < 0) {
        perror("read event");
        return -1;
    }
    if (n < (ssize_t)sizeof(struct usb_raw_event)) {
        fprintf(stderr, "[raw-gadget] short event read: %zd\n", n);
        return -1;
    }
    struct usb_raw_event *ev = (struct usb_raw_event *)buf;
    return (int)ev->type;
}

int raw_gadget_get_fd(struct raw_gadget *rg)
{
    return rg->fd;
}
