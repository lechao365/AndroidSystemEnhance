#include "bot.h"
#include "raw-gadget.h"
#include "raw-gadget-internal.h"
#include "scsi.h"
#include "usb-msd-proto.h"
#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <stdlib.h>

/* Data 阶段最大缓冲区大小（限制单次传输上限，避免过度分配） */
#define DATA_BUF_MAX   (512 * 1024)  /* 512KB */

/*
 * BOT 主循环 — CBW → Data → CSW 状态机
 *
 * 钩子注入点说明：
 *   A. STALL_OUT:  EP_READ 之前对 OUT 端点 SET_HALT
 *   B. TIMEOUT:    收到 CBW 后不发送 Data/CSW，sleep duration_ms
 *   C. SHORT:      Data IN 发送时少发 short_bytes
 *   D. STALL_IN:   Data IN 发送前对 IN 端点 SET_HALT
 *   E. DEGRADE:    CSW 发送前 sleep delay_ms
 *   F. CORRUPT_*:  CSW 构造时修改字段
 *   G. ABORT:      收到 CBW 后 STALL IN + sleep duration_ms 不响应
 */
int bot_main_loop(struct raw_gadget *rg, struct fault_injection *fi)
{
    /* 分配 Data 阶段缓冲区 */
    uint8_t *data_buf = malloc(DATA_BUF_MAX);
    if (!data_buf) {
        fprintf(stderr, "[bot] failed to allocate data buffer\n");
        return -1;
    }

    int ret = 0;

    while (1) {
        /* 读取 fault 配置快照（可能在此期间被 main.c 修改） */
        enum fault_hook current_hook = fi->active ? fi->hook : HOOK_NONE;

        /* ===== 钩子 A: STALL OUT (在 CBW 接收前) ===== */
        if (current_hook == HOOK_STALL_OUT) {
            fprintf(stderr, "[bot] F2: STALL OUT endpoint before CBW\n");
            raw_gadget_stall_ep(rg, 0x02);
            fi->active = false;
            /* STALL 后 Host 会 ClearHalt 重试，继续循环 */
            continue;
        }

        /* ===== Step 1: 接收 CBW (31 字节) ===== */
        struct usb_ms_cbw cbw;
        memset(&cbw, 0, sizeof(cbw));

        int n = raw_gadget_ep_read(rg, &cbw, sizeof(cbw));
        if (n < 0) {
            if (errno == ESHUTDOWN || errno == ECONNRESET) {
                fprintf(stderr, "[bot] device disconnected (read)\n");
                break;
            }
            /* EPIPE = 端点被 STALL（可能是我们注入的），继续 */
            if (errno != EPIPE)
                fprintf(stderr, "[bot] CBW read error: %s (n=%d)\n", strerror(errno), n);
            continue;
        }

        if (n != (int)sizeof(cbw)) {
            fprintf(stderr, "[bot] short CBW read: %d/%zu\n", n, sizeof(cbw));
            continue;
        }

        /* 校验 CBW 签名 */
        if (cbw.dCBWSignature != USB_MS_CBW_SIGNATURE) {
            fprintf(stderr, "[bot] bad CBW signature: 0x%08x\n", cbw.dCBWSignature);
            continue;
        }

        fprintf(stderr, "[bot] CBW: tag=0x%08x flags=0x%02x datalen=%u cdb[0]=0x%02x cdblen=%u\n",
                cbw.dCBWTag, cbw.bmCBWFlags, cbw.dCBWDataTransferLength,
                cbw.CBWCB[0], cbw.bCBWCBLength);

        /* ===== 钩子 B: TIMEOUT (收到 CBW 后不响应) ===== */
        if (current_hook == HOOK_TIMEOUT) {
            fprintf(stderr, "[bot] F3: TIMEOUT — holding %d ms after CBW\n", fi->duration_ms);
            fi->active = false;
            usleep(fi->duration_ms * 1000);
            /* 不发送 Data/CSW，Host 超时后触发 reset recovery */
            continue;
        }

        /* ===== 钩子 G: ABORT (STALL IN + 不响应) ===== */
        if (current_hook == HOOK_ABORT) {
            fprintf(stderr, "[bot] F9: ABORT — STALL IN + hold %d ms\n", fi->duration_ms);
            fi->active = false;
            raw_gadget_stall_ep(rg, 0x81);
            usleep(fi->duration_ms * 1000);
            continue;
        }

        /* ===== Step 2: SCSI 命令分发 ===== */
        struct scsi_result sr = scsi_handle_command(
            cbw.CBWCB, cbw.bCBWCBLength,
            cbw.dCBWDataTransferLength,
            data_buf, DATA_BUF_MAX);

        /* ===== Step 3: Data 阶段 ===== */
        uint32_t actually_transferred = 0;

        if (sr.dir == SCSI_DIR_IN && sr.data_len > 0) {
            uint32_t to_send = sr.data_len;
            if (to_send > cbw.dCBWDataTransferLength)
                to_send = cbw.dCBWDataTransferLength;

            /* ===== 钩子 C: SHORT (少发 short_bytes) ===== */
            if (current_hook == HOOK_SHORT && fi->short_bytes > 0) {
                if ((uint32_t)fi->short_bytes >= to_send)
                    to_send = 0;
                else
                    to_send -= fi->short_bytes;
                fprintf(stderr, "[bot] F8: SHORT — sending %u/%u bytes\n",
                        to_send, sr.data_len);
                fi->active = false;
            }

            /* ===== 钩子 D: STALL IN (Data 发送前) ===== */
            if (current_hook == HOOK_STALL_IN) {
                fprintf(stderr, "[bot] F1: STALL IN before Data\n");
                raw_gadget_stall_ep(rg, 0x81);
                fi->active = false;
                /* Host 收到 STALL，触发 ClearHalt + reset recovery */
                /* 跳过 Data/CSW 发送 */
                continue;
            }

            /* 分块发送（每次最多 512B bulk 包） */
            uint32_t offset = 0;
            while (offset < to_send) {
                uint32_t chunk = to_send - offset;
                if (chunk > 512) chunk = 512;
                int sent = raw_gadget_ep_write(rg, data_buf + offset, chunk);
                if (sent < 0) {
                    fprintf(stderr, "[bot] Data IN write error: %s\n", strerror(errno));
                    break;
                }
                offset += (uint32_t)sent;
            }
            actually_transferred = offset;

            /* 如果发送量 < 请求量且非 512 整数倍，需要发短包终止 */
            if (to_send > 0 && (to_send % 512 == 0) &&
                to_send < cbw.dCBWDataTransferLength) {
                /* 发送零长度包标记结束 */
                raw_gadget_ep_write(rg, NULL, 0);
            }

        } else if (sr.dir == SCSI_DIR_OUT && cbw.dCBWDataTransferLength > 0) {
            uint32_t to_recv = cbw.dCBWDataTransferLength;
            if (to_recv > DATA_BUF_MAX)
                to_recv = DATA_BUF_MAX;

            uint32_t offset = 0;
            while (offset < to_recv) {
                uint32_t want = to_recv - offset;
                if (want > 512) want = 512;
                int got = raw_gadget_ep_read(rg, data_buf + offset, want);
                if (got < 0) {
                    fprintf(stderr, "[bot] Data OUT read error: %s\n", strerror(errno));
                    break;
                }
                offset += (uint32_t)got;
                if (got < (int)want)
                    break;  /* 短包结束 */
            }
            actually_transferred = offset;

            /* 将接收的数据传回 SCSI 层（WRITE 操作需要写入内存盘） */
            if (sr.data_len > 0)
                scsi_handle_command(cbw.CBWCB, cbw.bCBWCBLength,
                                    actually_transferred, data_buf, DATA_BUF_MAX);
        }

        /* ===== 钩子 E: DEGRADE (CSW 发送前延迟) ===== */
        if (current_hook == HOOK_DEGRADE && fi->delay_ms > 0) {
            fprintf(stderr, "[bot] F12: DEGRADE — delaying %d ms before CSW\n", fi->delay_ms);
            usleep(fi->delay_ms * 1000);
            /* 注意：DEGRADE 不清除 active 标志，持续注入 */
        }

        /* ===== Step 4: 构造并发送 CSW ===== */
        struct usb_ms_csw csw;
        memset(&csw, 0, sizeof(csw));
        csw.dCSWSignature  = USB_MS_CSW_SIGNATURE;
        csw.dCSWTag        = cbw.dCBWTag;
        csw.dCSWDataResidue = cbw.dCBWDataTransferLength - actually_transferred;
        csw.bCSWStatus     = sr.csw_status;

        /* ===== 钩子 F: CORRUPT CSW 字段 ===== */
        if (current_hook == HOOK_CORRUPT_CSW_SIG) {
            fprintf(stderr, "[bot] F5: CORRUPT CSW signature\n");
            csw.dCSWSignature = 0xDEADBEEF;
            fi->active = false;
        } else if (current_hook == HOOK_CORRUPT_CSW_TAG) {
            fprintf(stderr, "[bot] F6: CORRUPT CSW tag\n");
            csw.dCSWTag = cbw.dCBWTag + 1;
            fi->active = false;
        } else if (current_hook == HOOK_CORRUPT_CSW_STATUS) {
            fprintf(stderr, "[bot] F7: CORRUPT CSW status = Phase Error\n");
            csw.bCSWStatus = USB_MS_CSW_STATUS_PHASE;
            fi->active = false;
        }

        int csent = raw_gadget_ep_write(rg, &csw, sizeof(csw));
        if (csent < 0) {
            fprintf(stderr, "[bot] CSW write error: %s\n", strerror(errno));
        }
    }

    free(data_buf);
    return ret;
}
