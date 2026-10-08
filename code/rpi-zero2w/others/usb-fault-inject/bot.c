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

/* usleep 参数上限：duration_ms × 1000 不溢出 32 位（2147483ms ≈ 35.8min） */
#define MAX_USLEEP_MS  2147483

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

    /* 启动 EP0 服务线程，持续应答控制请求（ClearHalt/BOMSR/重枚举） */
    if (raw_gadget_start_ep0_thread(rg) < 0) {
        fprintf(stderr, "[bot] failed to start EP0 service thread\n");
        free(data_buf);
        return -1;
    }

    while (1) {
        /* 读取 fault 配置快照（每轮 CBW 读取一次，事务内不变；
         * 配置由 faults.c 启动前设定，单次注入后由本函数清除 active） */
        enum fault_hook current_hook = fi->active ? fi->hook : HOOK_NONE;

        /* ===== 钩子 A: STALL OUT (在 CBW 接收前) ===== */
        if (current_hook == HOOK_STALL_OUT) {
            fprintf(stderr, "[bot] F2: STALL OUT endpoint before CBW\n");
            raw_gadget_stall_ep(rg, EP_BULK_OUT);
            fi->active = false;
            /* STALL 后 Host 会 ClearHalt 重试，继续循环 */
            continue;
        }

        /* ===== Step 1: 接收 CBW (31 字节) ===== */
        struct usb_ms_cbw cbw;
        memset(&cbw, 0, sizeof(cbw));

        int n = raw_gadget_ep_read(rg, &cbw, sizeof(cbw));
        if (n < 0) {
            if (errno == ESHUTDOWN || errno == ECONNRESET ||
                errno == ENODEV || errno == EBADF) {
                fprintf(stderr, "[bot] device disconnected (read)\n");
                break;
            }
            /* EPIPE = 端点被 STALL（可能是我们注入的）：10ms 退避后重试 */
            if (errno == EPIPE) {
                usleep(10 * 1000);
                continue;
            }
            fprintf(stderr, "[bot] CBW read error: %s (n=%d)\n", strerror(errno), n);
            continue;
        }

        if (n != (int)sizeof(cbw)) {
            fprintf(stderr, "[bot] short CBW read: %d/%zu\n", n, sizeof(cbw));
            /* 短 CBW 属协议异常：STALL OUT 端点触发 Host reset recovery */
            raw_gadget_stall_ep(rg, EP_BULK_OUT);
            continue;
        }

        /* 校验 CBW 签名 */
        if (msd_le32_get((const uint8_t *)&cbw.dCBWSignature) != USB_MS_CBW_SIGNATURE) {
            fprintf(stderr, "[bot] bad CBW signature: 0x%08x\n",
                    msd_le32_get((const uint8_t *)&cbw.dCBWSignature));
            continue;
        }

        /* 校验 CBW 字段：bCBWCBLength 须 1..16、bCBWLUN 须 0；违规 STALL 双端点 */
        if (cbw.bCBWCBLength < 1 || cbw.bCBWCBLength > 16 || cbw.bCBWLUN != 0) {
            fprintf(stderr, "[bot] invalid CBW fields: cdblen=%u lun=%u, STALL both EPS\n",
                    cbw.bCBWCBLength, cbw.bCBWLUN);
            raw_gadget_stall_ep(rg, EP_BULK_IN);
            raw_gadget_stall_ep(rg, EP_BULK_OUT);
            continue;
        }

        fprintf(stderr, "[bot] CBW: tag=0x%08x flags=0x%02x datalen=%u cdb[0]=0x%02x cdblen=%u\n",
                cbw.dCBWTag, cbw.bmCBWFlags, cbw.dCBWDataTransferLength,
                cbw.CBWCB[0], cbw.bCBWCBLength);

        /* ===== 钩子 B: TIMEOUT (收到 CBW 后不响应) ===== */
        if (current_hook == HOOK_TIMEOUT) {
            fprintf(stderr, "[bot] F3: TIMEOUT — holding %d ms after CBW\n", fi->duration_ms);
            fi->active = false;
            int hold_ms = fi->duration_ms;
            if (hold_ms > MAX_USLEEP_MS)
                hold_ms = MAX_USLEEP_MS;
            usleep((useconds_t)hold_ms * 1000);
            /* 不发送 Data/CSW，Host 超时后触发 reset recovery */
            continue;
        }

        /* ===== 钩子 G: ABORT (STALL IN + 不响应) ===== */
        if (current_hook == HOOK_ABORT) {
            fprintf(stderr, "[bot] F9: ABORT — STALL IN + hold %d ms\n", fi->duration_ms);
            fi->active = false;
            raw_gadget_stall_ep(rg, EP_BULK_IN);
            int hold_ms = fi->duration_ms;
            if (hold_ms > MAX_USLEEP_MS)
                hold_ms = MAX_USLEEP_MS;
            usleep((useconds_t)hold_ms * 1000);
            continue;
        }

        /* ===== Step 2: SCSI 命令解析（仅解析方向/长度，不执行数据 IO） =====
         * data_buf 传 NULL：对 OUT 命令（如 WRITE_10），若此处传入 data_buf，
         * scsi_handle_command 会用尚未接收数据的未初始化缓冲区立即写盘
         * （内存破坏级脏写），随后 OUT 分支接收真实数据后再派发一次——
         * 同一命令被双重派发。故此处仅解析，IN 数据在 Data IN 分支生成，
         * OUT 数据在接收真实数据后单次写盘。
         */
        struct scsi_result sr = scsi_handle_command(
            cbw.CBWCB, cbw.bCBWCBLength,
            cbw.dCBWDataTransferLength,
            NULL, DATA_BUF_MAX);

        /* 方向位一致性：CBW 声明方向与命令实际方向矛盾且带数据相位 → CSW Phase Error */
        int cbw_dir_in = (cbw.bmCBWFlags & USB_MS_CBW_FLAGS_IN) != 0;
        if (cbw.dCBWDataTransferLength > 0 &&
            (sr.dir == SCSI_DIR_IN || sr.dir == SCSI_DIR_OUT)) {
            int cmd_dir_in = (sr.dir == SCSI_DIR_IN);
            if (cbw_dir_in != cmd_dir_in) {
                fprintf(stderr, "[bot] direction mismatch: flags=%s cmd=%s datalen=%u, PHASE\n",
                        cbw_dir_in ? "IN" : "OUT", cmd_dir_in ? "IN" : "OUT",
                        cbw.dCBWDataTransferLength);
                sr.csw_status = USB_MS_CSW_STATUS_PHASE;
                sr.dir = SCSI_DIR_NONE; /* 跳过数据相位 */
                sr.data_len = 0;
            }
        }

        /* ===== Step 3: Data 阶段 ===== */
        uint32_t actually_transferred = 0;

        if (sr.dir == SCSI_DIR_IN && sr.data_len > 0) {
            /* 生成 IN 方向真实响应数据（Step 2 仅解析未填充缓冲区） */
            sr = scsi_handle_command(
                cbw.CBWCB, cbw.bCBWCBLength,
                cbw.dCBWDataTransferLength,
                data_buf, DATA_BUF_MAX);

            uint32_t to_send = sr.data_len;
            if (to_send > DATA_BUF_MAX)
                to_send = DATA_BUF_MAX;
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
                raw_gadget_stall_ep(rg, EP_BULK_IN);
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

            /* 发送量是 512 整数倍且小于请求量时，需发零长度包终止（标记传输结束） */
            if (to_send > 0 && (to_send % 512 == 0) &&
                to_send < cbw.dCBWDataTransferLength) {
                /* 发送零长度包标记结束 */
                raw_gadget_ep_write(rg, NULL, 0);
            }

        } else if (sr.dir == SCSI_DIR_OUT && cbw.dCBWDataTransferLength > 0) {
            uint32_t to_recv = cbw.dCBWDataTransferLength;
            int oversized = 0;
            if (to_recv > DATA_BUF_MAX) {
                to_recv = DATA_BUF_MAX;
                oversized = 1;
            }

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

            if (oversized) {
                /* OUT 声明超 DATA_BUF_MAX：收满缓冲区后 STALL OUT 且 CSW FAIL */
                fprintf(stderr, "[bot] OUT transfer %u > DATA_BUF_MAX, STALL OUT + FAIL\n",
                        cbw.dCBWDataTransferLength);
                raw_gadget_stall_ep(rg, EP_BULK_OUT);
                sr.csw_status = USB_MS_CSW_STATUS_FAIL;
            } else if (sr.data_len > 0) {
                /* 将接收的数据传回 SCSI 层（WRITE 操作需要写入内存盘） */
                scsi_handle_command(cbw.CBWCB, cbw.bCBWCBLength,
                                    actually_transferred, data_buf, DATA_BUF_MAX);
            }
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
        msd_le32_put((uint8_t *)&csw.dCSWSignature, USB_MS_CSW_SIGNATURE);
        msd_le32_put((uint8_t *)&csw.dCSWTag, cbw.dCBWTag);
        msd_le32_put((uint8_t *)&csw.dCSWDataResidue,
                     cbw.dCBWDataTransferLength - actually_transferred);
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
        } else if (csent != (int)sizeof(csw)) {
            fprintf(stderr, "[bot] CSW short write: %d/%zu bytes\n", csent, sizeof(csw));
        }
    }

    free(data_buf);
    return ret;
}
