#include "faults.h"

#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <stdlib.h>

/* ===== 内部辅助：标准 CBW 处理循环（用于 CORRUPT/SHORT/DEGRADE） ===== */

/*
 * 处理单个 CBW：根据 fault 类型决定是否损坏字段或插入延迟。
 * 返回 0 表示继续；返回 -1 表示出错。
 */
static int handle_one_cbw(struct raw_gadget *rg,
                          const struct fault_args *a,
                          int *cbw_count)
{
    struct usb_ms_cbw cbw;
    struct usb_ms_csw csw;
    uint8_t data_buf[4096] __attribute__((aligned(4)));
    int n;
    int actually_sent = 0;

    /* 1. 接收 CBW (31 字节) */
    n = raw_gadget_ep_read(rg, EP_BULK_OUT, &cbw, sizeof(cbw), 5000);
    if (n != (int)sizeof(cbw)) {
        fprintf(stderr, "[faults] CBW read failed: %d (errno=%d)\n", n, errno);
        return -1;
    }
    (*cbw_count)++;

    /* 2. 根据 fault 字段类型损坏 CBW */
    if (a->field == CORRUPT_FIELD_CBW_SIG) {
        cbw.dCBWSignature = 0xDEADBEEF;
        fprintf(stderr, "[faults] CBW signature corrupted\n");
    }

    /* 3. 检查 CBW 合法性 (除非被故意损坏) */
    if (cbw.dCBWSignature != USB_MS_CBW_SIGNATURE &&
        a->field != CORRUPT_FIELD_CBW_SIG) {
        fprintf(stderr, "[faults] Invalid CBW signature: 0x%08x\n",
                cbw.dCBWSignature);
        return -1;
    }

    /* 4. 处理 Data 阶段（IN 或 OUT）*/
    uint32_t data_len = cbw.dCBWDataTransferLength;
    if (data_len > sizeof(data_buf)) {
        fprintf(stderr, "[faults] CBW data length too large: %u, clamping to %zu\n",
                data_len, sizeof(data_buf));
        data_len = sizeof(data_buf);
    }
    if (data_len > 0) {
        if (cbw.bmCBWFlags & USB_MS_CBW_FLAGS_IN) {
            /* Device → Host: 我们发送 Data */
            int to_send = data_len;

            /* F8: 短传输 — 设计意图：只发送 data_len - short_bytes 字节，
             * 让 Host 检测到 Data 阶段未收满，触发重传。
             * CSW 的 dCSWDataResidue 设为 data_len - actually_sent 以反映差值。
             */
            if (a->field == CORRUPT_FIELD_SHORT && a->short_bytes > 0) {
                if (a->short_bytes >= (int)data_len) {
                    fprintf(stderr, "[faults] SHORT: short_bytes %d >= data_len %u, skipping data\n",
                            a->short_bytes, data_len);
                    to_send = 0;
                } else {
                    int reduced = (int)data_len - a->short_bytes;
                    to_send = reduced;
                    fprintf(stderr, "[faults] Short transfer: %d -> %d bytes\n",
                            data_len, to_send);
                }
            }

            int sent_bytes = 0;
            while (to_send > 0) {
                memset(data_buf, 0, sizeof(data_buf));
                int chunk = to_send < (int)sizeof(data_buf) ? to_send
                                                            : (int)sizeof(data_buf);
                int sent = raw_gadget_ep_write(rg, EP_BULK_IN, data_buf, chunk);
                if (sent < 0) {
                    fprintf(stderr, "[faults] Data write failed\n");
                    return -1;
                }
                sent_bytes += sent;
                to_send -= sent;
            }
            actually_sent = sent_bytes;
            if (a->field == CORRUPT_FIELD_SHORT) {
                fprintf(stderr, "[faults] Short transfer done: sent=%d / data_len=%u\n",
                        actually_sent, data_len);
            }
        } else {
            /* Host → Device: 我们接收 Data */
            uint32_t remaining = data_len;
            while (remaining > 0) {
                int chunk = remaining < sizeof(data_buf) ? remaining
                                                          : sizeof(data_buf);
                n = raw_gadget_ep_read(rg, EP_BULK_OUT, data_buf, chunk, 5000);
                if (n <= 0) {
                    fprintf(stderr, "[faults] Data read failed: %d\n", n);
                    return -1;
                }
                if ((uint32_t)n > remaining) {
                    fprintf(stderr, "[faults] Read more than expected: %d > %u\n",
                            n, remaining);
                    return -1;
                }
                remaining -= n;
            }
        }
    }

    /* 5. F12: 速率降级 — 在每个 CBW 的 Data 阶段完成后、CSW 发送前插入延迟。
     * 延迟位置在 CSW 发送之前，确保 Data 阶段完整但整体传输耗时增大。
     */
    if (a->field == CORRUPT_FIELD_NONE && a->delay_ms > 0) {
        usleep(a->delay_ms * 1000);
    }

    /* 6. 构造 CSW */
    memset(&csw, 0, sizeof(csw));
    csw.dCSWSignature = USB_MS_CSW_SIGNATURE;
    csw.dCSWTag = cbw.dCBWTag;
    csw.dCSWDataResidue = data_len - (uint32_t)actually_sent;
    csw.bCSWStatus = USB_MS_CSW_STATUS_PASS;

    /* F5-F7: 损坏 CSW 字段 */
    if (a->field == CORRUPT_FIELD_CSW_SIG) {
        csw.dCSWSignature = 0xDEADBEEF;
        fprintf(stderr, "[faults] CSW signature corrupted\n");
    } else if (a->field == CORRUPT_FIELD_CSW_TAG) {
        csw.dCSWTag = cbw.dCBWTag + 1;
        fprintf(stderr, "[faults] CSW tag mismatch (0x%08x != 0x%08x)\n",
                csw.dCSWTag, cbw.dCBWTag);
    } else if (a->field == CORRUPT_FIELD_CSW_STATUS) {
        csw.bCSWStatus = USB_MS_CSW_STATUS_PHASE;
        fprintf(stderr, "[faults] CSW status = Phase Error\n");
    } else if (a->field == CORRUPT_FIELD_SHORT) {
        /* Residue 已在上方通过 data_len - actually_sent 正确计算 */
        csw.bCSWStatus = USB_MS_CSW_STATUS_PASS;
    }

    /* 7. 发送 CSW (13 字节) */
    n = raw_gadget_ep_write(rg, EP_BULK_IN, &csw, sizeof(csw));
    if (n != (int)sizeof(csw)) {
        fprintf(stderr, "[faults] CSW write failed: %d\n", n);
        return -1;
    }

    return 0;
}

/* ===== F1/F2: STALL 端点 ===== */
int fault_stall_ep(struct raw_gadget *rg, const struct fault_args *a)
{
    if (a->ep != EP_BULK_IN && a->ep != EP_BULK_OUT) {
        fprintf(stderr, "[faults] STALL: invalid ep 0x%02x\n", a->ep);
        return -1;
    }
    return raw_gadget_stall_now(rg, a->ep);
}

/* ===== F3: 传输超时 ===== */
int fault_timeout(struct raw_gadget *rg, const struct fault_args *a)
{
    struct usb_ms_cbw cbw;
    fprintf(stderr, "[faults] TIMEOUT: ignoring CBW for %d ms\n",
            a->duration_ms);

    /* 接收一次 CBW（让 Host 有请求可超时）
     * 设计意图：收到 CBW 后故意不发送 Data/CSW 响应，
     * 触发 Host 端传输超时。CBW 不需要清理，下次 Host 重试会发新 CBW。
     */
    int n = raw_gadget_ep_read(rg, EP_BULK_OUT, &cbw, sizeof(cbw),
                               a->duration_ms + 1000);
    if (n > 0) {
        /* 收到 CBW 后故意不返回任何响应，触发 Host 端超时 */
        fprintf(stderr, "[faults] TIMEOUT: CBW received, holding %d ms...\n",
                a->duration_ms);
        usleep(a->duration_ms * 1000);
    } else {
        fprintf(stderr, "[faults] TIMEOUT: no CBW received, sleeping anyway\n");
        usleep(a->duration_ms * 1000);
    }
    return 0;
}

/* ===== F4-F7: CBW/CSW 字段损坏 ===== */
int fault_corrupt(struct raw_gadget *rg, const struct fault_args *a)
{
    int count = 0;
    int rc = handle_one_cbw(rg, a, &count);
    if (rc < 0 && count == 0) {
        fprintf(stderr, "[faults] CORRUPT: no CBW processed\n");
        return -1;
    }
    return 0;
}

/* ===== F8: 短传输 ===== */
int fault_short_transfer(struct raw_gadget *rg, const struct fault_args *a)
{
    if (a->short_bytes <= 0) {
        fprintf(stderr, "[faults] SHORT: --bytes must be > 0\n");
        return -1;
    }
    int count = 0;
    return handle_one_cbw(rg, a, &count);
}

/* ===== F9: Bulk ABORT（通过单次 STALL 序列模拟 ERR PID） ===== */
int fault_abort(struct raw_gadget *rg, const struct fault_args *a)
{
    if (a->ep != EP_BULK_IN && a->ep != EP_BULK_OUT) {
        fprintf(stderr, "[faults] ABORT: invalid ep 0x%02x\n", a->ep);
        return -1;
    }
    return raw_gadget_send_bulk_err(rg, a->ep);
}

/* ===== F10: VBUS 热插拔 ===== */
int fault_hotplug(struct raw_gadget *rg, const struct fault_args *a)
{
    for (int i = 0; i < a->cycles; i++) {
        fprintf(stderr, "[faults] HOTPLUG cycle %d/%d: OFF\n", i + 1, a->cycles);
        raw_gadget_vbus_draw(rg, 0);
        usleep(a->offline_ms * 1000);

        fprintf(stderr, "[faults] HOTPLUG cycle %d/%d: ON\n", i + 1, a->cycles);
        raw_gadget_vbus_draw(rg, 500);
        usleep(500 * 1000);
    }
    return 0;
}

/* ===== F11: 物理断开 ===== */
int fault_disconnect(struct raw_gadget *rg, const struct fault_args *a)
{
    (void)a;
    fprintf(stderr, "[faults] DISCONNECT: pulling VBUS low\n");
    return raw_gadget_run_stop(rg);
}

/* ===== F12: 速率降级 ===== */
int fault_degrade(struct raw_gadget *rg, const struct fault_args *a)
{
    if (a->delay_ms <= 0) {
        fprintf(stderr, "[faults] DEGRADE: --delay must be > 0\n");
        return -1;
    }
    int count = 0;
    fprintf(stderr, "[faults] DEGRADE: %d ms/CBW, processing 5 CBWs...\n",
            a->delay_ms);
    for (int i = 0; i < 5; i++) {
        if (handle_one_cbw(rg, a, &count) < 0) {
            fprintf(stderr, "[faults] DEGRADE: stop after %d CBWs\n", count);
            break;
        }
    }
    fprintf(stderr, "[faults] DEGRADE: processed %d CBWs total\n", count);
    return 0;
}
