#ifndef USB_MSD_PROTO_H
#define USB_MSD_PROTO_H

#include <stdint.h>

/* ===== Bulk-Only Transport (USB Mass Storage Class BOT) 常量 ===== */
#define USB_MS_CBW_SIGNATURE      0x43425355  /* "USBC" */
#define USB_MS_CSW_SIGNATURE      0x53425355  /* "USBS" */

#define USB_MS_CSW_STATUS_PASS    0x00
#define USB_MS_CSW_STATUS_FAIL    0x01
#define USB_MS_CSW_STATUS_PHASE   0x02

#define USB_MS_CBW_FLAGS_IN       0x80
#define USB_MS_CBW_FLAGS_OUT      0x00

#define USB_MS_CBW_LEN            31
#define USB_MS_CSW_LEN            13

/* ===== SCSI 操作码 (CBW.CBWCB) ===== */
#define SCSI_OP_TEST_UNIT_READY   0x00
#define SCSI_OP_REQUEST_SENSE     0x03
#define SCSI_OP_INQUIRY           0x12
#define SCSI_OP_MODE_SENSE_6      0x1A
#define SCSI_OP_START_STOP_UNIT   0x1B
#define SCSI_OP_READ_CAPACITY_10  0x25
#define SCSI_OP_READ_10           0x28
#define SCSI_OP_WRITE_10          0x2A
#define SCSI_OP_MODE_SENSE_10     0x5A

/* ===== SCSI 状态码 (CSW.bCSWStatus 已覆盖；以下为扩展用) ===== */
#define SCSI_STATUS_GOOD          0x00
#define SCSI_STATUS_CHECK_COND    0x02

/* ===== Bulk 端点地址 (与 Pi Zero 2W dwc2 gadget 配置一致) ===== */
#define EP_BULK_OUT               0x02  /* Host -> Device */
#define EP_BULK_IN                0x81  /* Device -> Host */

/* ===== 协议层结构体 ===== */
struct usb_ms_cbw {
    uint32_t dCBWSignature;
    uint32_t dCBWTag;
    uint32_t dCBWDataTransferLength;
    uint8_t  bmCBWFlags;
    uint8_t  bCBWLUN;
    uint8_t  bCBWCBLength;
    uint8_t  CBWCB[16];
} __attribute__((packed));

struct usb_ms_csw {
    uint32_t dCSWSignature;
    uint32_t dCSWTag;
    uint32_t dCSWDataResidue;
    uint8_t  bCSWStatus;
} __attribute__((packed));

/* ===== 移位式 LE32 读写 =====
 * CBW/CSW 的 32 位字段在线路上为小端序，用移位读写避免依赖主机字节序，
 * 也不经 packed 结构体字段直读（协议字段访问统一走此入口）。 */
static inline uint32_t msd_le32_get(const uint8_t *p)
{
    return ((uint32_t)p[0]) | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static inline void msd_le32_put(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xFF);
    p[1] = (uint8_t)((v >> 8) & 0xFF);
    p[2] = (uint8_t)((v >> 16) & 0xFF);
    p[3] = (uint8_t)((v >> 24) & 0xFF);
}

#endif
