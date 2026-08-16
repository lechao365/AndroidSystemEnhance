#include "raw-gadget.h"
#include "raw-gadget-internal.h"
#include "usb-descriptors.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <sys/ioctl.h>
#include <poll.h>

/* MSD class request 常量 */
#define USB_REQ_GET_MAX_LUN   0xFE
#define USB_REQ_BULK_RESET    0xFF

/* ============================================================
 * Layer 0: open/close + INIT + RUN
 * ============================================================ */

struct raw_gadget *raw_gadget_open(const char *udc_name)
{
    if (!udc_name || !*udc_name) {
        fprintf(stderr, "[rg] udc_name is empty\n");
        return NULL;
    }

    struct raw_gadget *rg = calloc(1, sizeof(*rg));
    if (!rg)
        return NULL;

    rg->fd = -1;
    rg->ep_in_handle = -1;
    rg->ep_out_handle = -1;
    rg->enumerated = false;
    rg->running = false;

    /* Step 1: open /dev/raw-gadget */
    rg->fd = open("/dev/raw-gadget", O_RDWR);
    if (rg->fd < 0) {
        perror("[rg] open /dev/raw-gadget");
        free(rg);
        return NULL;
    }

    /* Step 2: INIT — 绑定到指定 UDC */
    struct usb_raw_init init;
    memset(&init, 0, sizeof(init));
    /* dwc2 的 driver_name 和 device_name 相同，都是 UDC 名称 */
    snprintf((char *)init.driver_name, sizeof(init.driver_name), "%s", udc_name);
    snprintf((char *)init.device_name, sizeof(init.device_name), "%s", udc_name);
    init.speed = USB_SPEED_HIGH;  /* USB 2.0 High Speed */

    if (ioctl(rg->fd, USB_RAW_IOCTL_INIT, &init) < 0) {
        perror("[rg] USB_RAW_IOCTL_INIT");
        close(rg->fd);
        free(rg);
        return NULL;
    }

    fprintf(stderr, "[rg] INIT ok (udc=%s, speed=HIGH)\n", udc_name);

    /* Step 3: RUN — 启动 gadget，开始响应 Host 枚举 */
    if (ioctl(rg->fd, USB_RAW_IOCTL_RUN) < 0) {
        perror("[rg] USB_RAW_IOCTL_RUN");
        close(rg->fd);
        free(rg);
        return NULL;
    }

    rg->running = true;
    fprintf(stderr, "[rg] RUN ok, gadget is live\n");

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

/* ============================================================
 * Layer 1: EP0 控制传输辅助
 * ============================================================ */

int rg_ep0_write(struct raw_gadget *rg, const void *data, size_t len)
{
    /* usb_raw_ep_io 是 flexible array 结构体，需要堆分配 */
    size_t total = sizeof(struct usb_raw_ep_io) + len;
    struct usb_raw_ep_io *io = calloc(1, total);
    if (!io) {
        fprintf(stderr, "[rg] ep0_write: OOM\n");
        return -1;
    }

    io->length = (__u32)len;
    if (len > 0)
        memcpy(io->data, data, len);

    int ret = ioctl(rg->fd, USB_RAW_IOCTL_EP0_WRITE, io);
    free(io);

    if (ret < 0) {
        perror("[rg] EP0_WRITE");
        return -1;
    }
    return ret;
}

int rg_ep0_read(struct raw_gadget *rg, void *buf, size_t len)
{
    size_t total = sizeof(struct usb_raw_ep_io) + len;
    struct usb_raw_ep_io *io = calloc(1, total);
    if (!io) {
        fprintf(stderr, "[rg] ep0_read: OOM\n");
        return -1;
    }

    io->length = (__u32)len;

    int ret = ioctl(rg->fd, USB_RAW_IOCTL_EP0_READ, io);
    if (ret < 0) {
        perror("[rg] EP0_READ");
        free(io);
        return -1;
    }

    __u32 actual = io->length;
    if (actual > len) actual = (__u32)len;
    if (actual > 0 && buf)
        memcpy(buf, io->data, actual);

    free(io);
    return (int)actual;
}

int rg_ep0_stall(struct raw_gadget *rg)
{
    if (ioctl(rg->fd, USB_RAW_IOCTL_EP0_STALL) < 0) {
        perror("[rg] EP0_STALL");
        return -1;
    }
    return 0;
}

/* ============================================================
 * Layer 1: EP_ENABLE — 使能 Bulk 端点
 * ============================================================ */

int rg_enable_bulk_eps(struct raw_gadget *rg)
{
    /* 使用 EPS_INFO 查询可用端点 */
    struct usb_raw_eps_info info;
    memset(&info, 0, sizeof(info));
    int num = ioctl(rg->fd, USB_RAW_IOCTL_EPS_INFO, &info);
    if (num < 0) {
        perror("[rg] EPS_INFO");
        return -1;
    }
    fprintf(stderr, "[rg] UDC has %d non-control endpoints\n", num);

    /* 构造 Bulk IN 和 Bulk OUT 端点描述符 */
    struct usb_endpoint_descriptor ep_in = {
        .bLength          = USB_DT_ENDPOINT_SIZE,
        .bDescriptorType  = USB_DT_ENDPOINT,
        .bEndpointAddress = 0x81,  /* EP1 IN */
        .bmAttributes     = USB_ENDPOINT_XFER_BULK,
        .wMaxPacketSize   = __cpu_to_le16(512),
    };
    struct usb_endpoint_descriptor ep_out = {
        .bLength          = USB_DT_ENDPOINT_SIZE,
        .bDescriptorType  = USB_DT_ENDPOINT,
        .bEndpointAddress = 0x02,  /* EP2 OUT */
        .bmAttributes     = USB_ENDPOINT_XFER_BULK,
        .wMaxPacketSize   = __cpu_to_le16(512),
    };

    /* 使能 Bulk IN */
    int handle = ioctl(rg->fd, USB_RAW_IOCTL_EP_ENABLE, &ep_in);
    if (handle < 0) {
        perror("[rg] EP_ENABLE (IN)");
        return -1;
    }
    rg->ep_in_handle = handle;
    fprintf(stderr, "[rg] BULK IN enabled, handle=%d\n", handle);

    /* 使能 Bulk OUT */
    handle = ioctl(rg->fd, USB_RAW_IOCTL_EP_ENABLE, &ep_out);
    if (handle < 0) {
        perror("[rg] EP_ENABLE (OUT)");
        return -1;
    }
    rg->ep_out_handle = handle;
    fprintf(stderr, "[rg] BULK OUT enabled, handle=%d\n", handle);

    return 0;
}

/* ============================================================
 * Layer 1: EP0 控制请求处理
 * ============================================================ */

int rg_handle_ep0_request(struct raw_gadget *rg, const struct usb_ctrlrequest *ctrl)
{
    uint8_t  req_type = ctrl->bRequestType;
    uint8_t  req      = ctrl->bRequest;
    uint16_t val      = ctrl->wValue;
    uint16_t len      = ctrl->wLength;
    (void)ctrl->wIndex;  /* wIndex 当前未直接使用，由 class request 内部处理 */

    uint8_t req_dir   = req_type & USB_DIR_IN;   /* 0x80 = IN, 0 = OUT */
    uint8_t req_type_mask = req_type & USB_TYPE_MASK; /* 0x00=Std, 0x20=Class, 0x40=Vendor */

    /* ----- Standard Requests ----- */
    if (req_type_mask == USB_TYPE_STANDARD) {

        /* GET_DESCRIPTOR — IN 方向 */
        if (req == USB_REQ_GET_DESCRIPTOR && req_dir == USB_DIR_IN) {
            uint8_t desc_type  = (val >> 8) & 0xFF;
            uint8_t desc_index = val & 0xFF;

            switch (desc_type) {
            case USB_DT_DEVICE: {
                const struct usb_device_descriptor *dev = msd_get_device_descriptor();
                size_t send = sizeof(*dev);
                if (send > len) send = len;
                fprintf(stderr, "[rg] GET_DESCRIPTOR(DEVICE)\n");
                return rg_ep0_write(rg, dev, send);
            }

            case USB_DT_CONFIG: {
                const uint8_t *cfg = msd_get_config_descriptor();
                uint16_t cfg_len = msd_get_config_descriptor_len();
                size_t send = cfg_len;
                if (send > len) send = len;
                fprintf(stderr, "[rg] GET_DESCRIPTOR(CONFIG) sending %zu/%u bytes\n", send, len);
                return rg_ep0_write(rg, cfg, send);
            }

            case USB_DT_STRING: {
                uint16_t str_len = 0;
                const uint8_t *str_body = msd_get_string_descriptor(desc_index, &str_len);
                if (!str_body) {
                    fprintf(stderr, "[rg] GET_DESCRIPTOR(STRING,%d): not found, STALL\n", desc_index);
                    return rg_ep0_stall(rg);
                }
                /* 构造完整 string descriptor: bLength + bDescriptorType + data */
                size_t total = 2 + str_len;
                uint8_t buf[256];
                buf[0] = (uint8_t)total;       /* bLength */
                buf[1] = USB_DT_STRING;        /* bDescriptorType = 0x03 */
                if (str_len > 0)
                    memcpy(buf + 2, str_body, str_len);
                if (total > len) total = len;
                fprintf(stderr, "[rg] GET_DESCRIPTOR(STRING,%d) sending %zu bytes\n", desc_index, total);
                return rg_ep0_write(rg, buf, total);
            }

            default:
                /* BOS (0x0F)、DEVICE_QUALIFIER (0x06) 等：STALL */
                fprintf(stderr, "[rg] GET_DESCRIPTOR(type=0x%02x): STALL\n", desc_type);
                return rg_ep0_stall(rg);
            }
        }

        /* SET_CONFIGURATION — OUT 方向，无数据阶段 */
        if (req == USB_REQ_SET_CONFIGURATION && req_dir == USB_DIR_OUT) {
            fprintf(stderr, "[rg] SET_CONFIGURATION(%d)\n", val);

            /* 回复零长度 ACK */
            rg_ep0_write(rg, NULL, 0);

            if (val > 0) {
                /* 进入 Configured 状态 */
                ioctl(rg->fd, USB_RAW_IOCTL_CONFIGURE);

                /* 使能 Bulk 端点 */
                if (rg->ep_in_handle < 0 || rg->ep_out_handle < 0) {
                    if (rg_enable_bulk_eps(rg) < 0) {
                        fprintf(stderr, "[rg] FATAL: EP_ENABLE failed\n");
                        return -1;
                    }
                }

                /* 上报 VBUS 电流 */
                __u32 vbus = 250; /* 500mA (单位 2mA) */
                ioctl(rg->fd, USB_RAW_IOCTL_VBUS_DRAW, &vbus);

                rg->enumerated = true;
                fprintf(stderr, "[rg] Device configured and enumerated!\n");
            }
            return 0;
        }

        /* SET_INTERFACE, CLEAR_FEATURE, SET_FEATURE 等 — 回复 ACK */
        if (req_dir == USB_DIR_OUT) {
            rg_ep0_write(rg, NULL, 0);
            return 0;
        }

        /* GET_INTERFACE, SYNCH_FRAME 等 — 返回 1 字节 0 */
        if (req_dir == USB_DIR_IN && len > 0) {
            uint8_t zero = 0;
            return rg_ep0_write(rg, &zero, 1);
        }

        /* 未处理的标准请求 */
        fprintf(stderr, "[rg] unhandled std req 0x%02x, STALL\n", req);
        return rg_ep0_stall(rg);
    }

    /* ----- Class Requests (MSD Bulk-Only) ----- */
    if (req_type_mask == USB_TYPE_CLASS) {

        /* Get Max LUN (0xA1 0xFE) — IN 方向，返回 1 字节 */
        if (req == USB_REQ_GET_MAX_LUN && req_dir == USB_DIR_IN) {
            fprintf(stderr, "[rg] Get Max LUN → 0\n");
            uint8_t max_lun = 0; /* 单 LUN 设备 */
            return rg_ep0_write(rg, &max_lun, 1);
        }

        /* Bulk-Only Mass Storage Reset (0x21 0xFF) — OUT 方向，无数据 */
        if (req == USB_REQ_BULK_RESET && req_dir == USB_DIR_OUT) {
            fprintf(stderr, "[rg] Bulk-Only Reset received\n");
            rg_ep0_write(rg, NULL, 0); /* ACK */
            return 0;
        }

        fprintf(stderr, "[rg] unhandled class req 0x%02x, STALL\n", req);
        return rg_ep0_stall(rg);
    }

    /* Vendor Requests — STALL */
    fprintf(stderr, "[rg] vendor req 0x%02x, STALL\n", req);
    return rg_ep0_stall(rg);
}

/* ============================================================
 * Layer 1: 枚举主循环
 * ============================================================ */

int raw_gadget_enumerate(struct raw_gadget *rg)
{
    if (!rg || !rg->running) {
        fprintf(stderr, "[rg] enumerate: gadget not running\n");
        return -1;
    }

    fprintf(stderr, "[rg] entering enumeration loop...\n");

    while (!rg->enumerated) {
        /* EVENT_FETCH — 阻塞等待事件 */
        char ev_buf[1024] __attribute__((aligned(8)));
        struct usb_raw_event *ev = (struct usb_raw_event *)ev_buf;
        ev->type = 0;
        ev->length = sizeof(ev_buf) - sizeof(*ev);

        if (ioctl(rg->fd, USB_RAW_IOCTL_EVENT_FETCH, ev) < 0) {
            if (errno == EINTR) continue;
            perror("[rg] EVENT_FETCH");
            return -1;
        }

        switch (ev->type) {
        case USB_RAW_EVENT_CONNECT:
            fprintf(stderr, "[rg] CONNECT event\n");
            break;

        case USB_RAW_EVENT_CONTROL: {
            if (ev->length < sizeof(struct usb_ctrlrequest)) {
                fprintf(stderr, "[rg] CONTROL event too short (%u)\n", ev->length);
                rg_ep0_stall(rg);
                break;
            }
            const struct usb_ctrlrequest *ctrl =
                (const struct usb_ctrlrequest *)ev->data;
            if (rg_handle_ep0_request(rg, ctrl) < 0) {
                fprintf(stderr, "[rg] EP0 handler failed\n");
                return -1;
            }
            break;
        }

        default:
            fprintf(stderr, "[rg] unknown event type %u\n", ev->type);
            break;
        }
    }

    fprintf(stderr, "[rg] enumeration complete!\n");
    return 0;
}

/* ============================================================
 * Layer 2: BULK 端点读写（使用 handle）
 * ============================================================ */

int raw_gadget_ep_write(struct raw_gadget *rg, const void *buf, size_t len)
{
    if (!rg || rg->ep_in_handle < 0) {
        fprintf(stderr, "[rg] ep_write: IN endpoint not enabled\n");
        return -1;
    }
    if (len > 0x10000) {
        fprintf(stderr, "[rg] ep_write: len %zu too large\n", len);
        return -1;
    }

    size_t total = sizeof(struct usb_raw_ep_io) + len;
    struct usb_raw_ep_io *io = calloc(1, total);
    if (!io) return -1;

    io->ep = (__u16)rg->ep_in_handle;
    io->length = (__u32)len;
    if (len > 0 && buf)
        memcpy(io->data, buf, len);

    int ret = ioctl(rg->fd, USB_RAW_IOCTL_EP_WRITE, io);
    if (ret < 0) {
        /* STALL/EPIPE 不是致命错误（故障注入时预期发生） */
        if (errno != EPIPE && errno != ESHUTDOWN)
            perror("[rg] EP_WRITE");
        free(io);
        return -errno;
    }

    __u32 actual = io->length;
    free(io);
    return (int)actual;
}

int raw_gadget_ep_read(struct raw_gadget *rg, void *buf, size_t len)
{
    if (!rg || rg->ep_out_handle < 0) {
        fprintf(stderr, "[rg] ep_read: OUT endpoint not enabled\n");
        return -1;
    }
    if (len > 0x10000) {
        fprintf(stderr, "[rg] ep_read: len %zu too large\n", len);
        return -1;
    }

    size_t total = sizeof(struct usb_raw_ep_io) + len;
    struct usb_raw_ep_io *io = calloc(1, total);
    if (!io) return -1;

    io->ep = (__u16)rg->ep_out_handle;
    io->length = (__u32)len;

    int ret = ioctl(rg->fd, USB_RAW_IOCTL_EP_READ, io);
    if (ret < 0) {
        if (errno != EPIPE && errno != ESHUTDOWN)
            perror("[rg] EP_READ");
        free(io);
        return -errno;
    }

    __u32 actual = io->length;
    if (actual > len) actual = (__u32)len;
    if (actual > 0 && buf)
        memcpy(buf, io->data, actual);

    free(io);
    return (int)actual;
}

/* ============================================================
 * 端点控制
 * ============================================================ */

int raw_gadget_stall_ep(struct raw_gadget *rg, uint8_t ep_addr)
{
    int handle;
    if (ep_addr == 0x81)
        handle = rg->ep_in_handle;
    else if (ep_addr == 0x02)
        handle = rg->ep_out_handle;
    else {
        fprintf(stderr, "[rg] stall: invalid ep addr 0x%02x\n", ep_addr);
        return -1;
    }

    __u32 h = (__u32)handle;
    if (ioctl(rg->fd, USB_RAW_IOCTL_EP_SET_HALT, &h) < 0) {
        perror("[rg] EP_SET_HALT");
        return -1;
    }
    fprintf(stderr, "[rg] STALL ep 0x%02x (handle=%d)\n", ep_addr, handle);
    return 0;
}

int raw_gadget_clear_halt_ep(struct raw_gadget *rg, uint8_t ep_addr)
{
    int handle;
    if (ep_addr == 0x81)
        handle = rg->ep_in_handle;
    else if (ep_addr == 0x02)
        handle = rg->ep_out_handle;
    else {
        fprintf(stderr, "[rg] clear_halt: invalid ep addr 0x%02x\n", ep_addr);
        return -1;
    }

    __u32 h = (__u32)handle;
    if (ioctl(rg->fd, USB_RAW_IOCTL_EP_CLEAR_HALT, &h) < 0) {
        perror("[rg] EP_CLEAR_HALT");
        return -1;
    }
    return 0;
}

/* ============================================================
 * VBUS 控制
 * ============================================================ */

int raw_gadget_vbus_draw(struct raw_gadget *rg, int mA)
{
    __u32 val = (__u32)(mA / 2);  /* 单位 2mA */
    if (ioctl(rg->fd, USB_RAW_IOCTL_VBUS_DRAW, &val) < 0) {
        perror("[rg] VBUS_DRAW");
        return -1;
    }
    fprintf(stderr, "[rg] VBUS set to %dmA\n", mA);
    return 0;
}

/* ============================================================
 * 状态查询
 * ============================================================ */

bool raw_gadget_is_enumerated(const struct raw_gadget *rg)
{
    return rg ? rg->enumerated : false;
}

int raw_gadget_get_fd(const struct raw_gadget *rg)
{
    return rg ? rg->fd : -1;
}
