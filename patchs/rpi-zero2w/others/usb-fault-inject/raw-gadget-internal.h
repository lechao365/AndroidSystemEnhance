#ifndef RAW_GADGET_INTERNAL_H
#define RAW_GADGET_INTERNAL_H

#include <stdint.h>
#include <stdbool.h>
#include <linux/usb/raw_gadget.h>
#include <linux/usb/ch9.h>

/* raw_gadget 内部状态：供 raw-gadget.c / bot.c / scsi.c 共享 */
struct raw_gadget {
    int      fd;             /* /dev/raw-gadget 文件描述符 */
    int      ep_in_handle;   /* EP_ENABLE 返回的 BULK IN 端点 handle */
    int      ep_out_handle;  /* EP_ENABLE 返回的 BULK OUT 端点 handle */
    bool     enumerated;     /* Host 是否已完成 SET_CONFIGURATION */
    bool     running;        /* gadget 是否已 RUN */
};

/* ===== 内部辅助函数（raw-gadget.c 内使用） ===== */

/* EP0 控制：发送数据响应 IN 请求 */
int rg_ep0_write(struct raw_gadget *rg, const void *data, size_t len);

/* EP0 控制：接收 OUT 请求的数据阶段 */
int rg_ep0_read(struct raw_gadget *rg, void *buf, size_t len);

/* EP0 控制：STALL 当前控制请求 */
int rg_ep0_stall(struct raw_gadget *rg);

/* 使能 Bulk 端点并保存 handle */
int rg_enable_bulk_eps(struct raw_gadget *rg);

/* 处理一次 EP0 控制请求（USB 标准请求 + MSD class 请求） */
int rg_handle_ep0_request(struct raw_gadget *rg, const struct usb_ctrlrequest *ctrl);

#endif /* RAW_GADGET_INTERNAL_H */
