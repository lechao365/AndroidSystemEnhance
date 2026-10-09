#include "scsi.h"
#include "usb-msd-proto.h"
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <stdbool.h>

/* 64MB 内存盘后端 */
static uint8_t *g_disk = NULL;

/* ===== Sense 状态机（记录最近一次 CHECK CONDITION 的 SK/ASC） =====
 * 命令失败时 sense_record() 存档；REQUEST SENSE 应答后清除。 */
static uint8_t g_sense_key   = 0x00; /* Sense Key（默认 NO SENSE） */
static uint8_t g_sense_asc   = 0x00; /* Additional Sense Code */
static bool    g_sense_valid = false;

static void sense_record(uint8_t key, uint8_t asc)
{
    g_sense_key = key;
    g_sense_asc = asc;
    g_sense_valid = true;
}

/* ===== INQUIRY 响应数据（36B 标准） ===== */
static const uint8_t inquiry_data[36] = {
    /* [0] Peripheral Qualifier(0)<<5 | Device Type(0=Direct Access) */
    0x00,
    /* [1] RMB=1 (Removable) */
    0x80,
    /* [2] SPC-4 版本 */
    0x06,
    /* [3] Response Format = 0x02 */
    0x02,
    /* [4] Additional Length = 31 (36-5) */
    31,
    /* [5..7] 保留 */
    0x00, 0x00, 0x00,
    /* [8..15] T10 Vendor ID (8 bytes) */
    'F', 'a', 'u', 'l', 't', 'I', 'n', 'j',
    /* [16..31] Product ID (16 bytes) */
    'P', 'i', '0', '2', 'W', ' ', 'R', 'a',
    'w', 'G', 'a', 'd', 'g', 'e', 't', ' ',
    /* [32..35] Product Revision (4 bytes) */
    '0', '0', '0', '1',
};

/* ===== READ CAPACITY(10) 响应数据（8B big-endian） ===== */
static void build_read_capacity(uint8_t *buf)
{
    /* last LBA = block_count - 1 = 131071 (big-endian) */
    uint32_t last_lba = MSD_BLOCK_COUNT - 1;
    uint32_t blk_size = MSD_BLOCK_SIZE;
    /* SCSI 使用 big-endian */
    buf[0] = (last_lba >> 24) & 0xFF;
    buf[1] = (last_lba >> 16) & 0xFF;
    buf[2] = (last_lba >> 8)  & 0xFF;
    buf[3] = (last_lba >> 0)  & 0xFF;
    buf[4] = (blk_size >> 24) & 0xFF;
    buf[5] = (blk_size >> 16) & 0xFF;
    buf[6] = (blk_size >> 8)  & 0xFF;
    buf[7] = (blk_size >> 0)  & 0xFF;
}

/* ===== REQUEST SENSE 响应数据（18B Fixed Format） ===== */
static void build_request_sense(uint8_t *buf)
{
    memset(buf, 0, 18);
    buf[0]  = 0xF0;  /* Valid bit + Response Code: 0x70 current error */
    buf[2]  = g_sense_valid ? g_sense_key : 0x00; /* Sense Key */
    buf[7]  = 10;    /* Additional Sense Length */
    if (g_sense_valid) {
        buf[12] = g_sense_asc;  /* Additional Sense Code */
        buf[13] = 0x00;         /* Additional Sense Code Qualifier */
        g_sense_valid = false;  /* 读取后清除 */
    }
}

/* ===== MODE SENSE(6) 响应数据（4B 最简） ===== */
static const uint8_t mode_sense_data[4] = {
    0x03,  /* Mode Data Length = 3 */
    0x00,  /* Medium Type = 0 */
    0x00,  /* Device-specific (WP=0) */
    0x00,  /* Block Descriptor Length = 0 */
};

int scsi_init(void)
{
    if (g_disk) return 0;
    g_disk = calloc(1, (size_t)MSD_BLOCK_COUNT * MSD_BLOCK_SIZE);
    if (!g_disk) {
        fprintf(stderr, "[scsi] failed to allocate %dMB disk\n",
                (int)((size_t)MSD_BLOCK_COUNT * MSD_BLOCK_SIZE / (1024*1024)));
        return -1;
    }
    fprintf(stderr, "[scsi] initialized 64MB virtual disk (%d blocks × %dB)\n",
            MSD_BLOCK_COUNT, MSD_BLOCK_SIZE);
    return 0;
}

void scsi_exit(void)
{
    free(g_disk);
    g_disk = NULL;
}

struct scsi_result scsi_handle_command(const uint8_t *cbwcb, uint8_t cbwcb_len,
                                        uint32_t data_len,
                                        uint8_t *data_buf, size_t buf_size)
{
    struct scsi_result r = { .dir = SCSI_DIR_NONE, .data_len = 0, .csw_status = 0 };

    if (!cbwcb || cbwcb_len == 0) {
        r.csw_status = 1; /* FAIL */
        return r;
    }

    uint8_t opcode = cbwcb[0];

    switch (opcode) {
    case SCSI_OP_TEST_UNIT_READY:
        /* 无 Data 阶段，直接返回 Pass */
        break;

    case SCSI_OP_REQUEST_SENSE:
        r.dir = SCSI_DIR_IN;
        r.data_len = data_len < 18 ? data_len : 18;
        if (data_buf && r.data_len > 0)
            build_request_sense(data_buf);
        break;

    case SCSI_OP_INQUIRY: {
        r.dir = SCSI_DIR_IN;
        /* EVPD (bit0 of byte 1) 或 page code != 0 → VPD 查询，暂不支持 */
        if (cbwcb_len >= 2 && (cbwcb[1] & 0x01)) {
            /* VPD 暂不支持：INVALID FIELD IN CDB（5/0x24），CSW FAIL */
            sense_record(0x05, 0x24);
            r.csw_status = 1;
            r.data_len = 0;
        } else {
            /* 标准 INQUIRY */
            r.data_len = data_len < 36 ? data_len : 36;
            if (data_buf && r.data_len > 0)
                memcpy(data_buf, inquiry_data, r.data_len);
        }
        break;
    }

    case SCSI_OP_MODE_SENSE_6:
        r.dir = SCSI_DIR_IN;
        r.data_len = data_len < 4 ? data_len : 4;
        if (data_buf && r.data_len > 0)
            memcpy(data_buf, mode_sense_data, r.data_len);
        break;

    case SCSI_OP_READ_CAPACITY_10:
        r.dir = SCSI_DIR_IN;
        r.data_len = data_len < 8 ? data_len : 8;
        if (data_buf && r.data_len > 0)
            build_read_capacity(data_buf);
        break;

    case SCSI_OP_START_STOP_UNIT:
        /*loej=0, start=0 → 停止；直接返回 Pass */
        break;

    case 0x1E: /* PREVENT ALLOW MEDIUM REMOVAL */
        /* 返回 Pass */
        break;

    case SCSI_OP_READ_10: {
        if (cbwcb_len < 10) {
            sense_record(0x05, 0x24); /* INVALID FIELD IN CDB */
            r.csw_status = 1;
            break;
        }
        /* CDB[2..5] = LBA (big-endian), CDB[7..8] = transfer length (big-endian) */
        uint32_t lba = ((uint32_t)cbwcb[2] << 24) | ((uint32_t)cbwcb[3] << 16) |
                       ((uint32_t)cbwcb[4] << 8)  | (uint32_t)cbwcb[5];
        uint16_t blocks = ((uint16_t)cbwcb[7] << 8) | (uint16_t)cbwcb[8];
        /* 先判后算：lba 或 lba+blocks 越出 MSD_BLOCK_COUNT 即判失败（防 LBA 回绕越界） */
        if (lba >= MSD_BLOCK_COUNT || blocks > MSD_BLOCK_COUNT - lba) {
            sense_record(0x05, 0x21); /* LOGICAL BLOCK ADDRESS OUT OF RANGE */
            r.csw_status = 1;
            r.data_len = 0;
            break;
        }
        uint32_t total = (uint32_t)blocks * MSD_BLOCK_SIZE;

        r.dir = SCSI_DIR_IN;
        r.data_len = data_len < total ? data_len : total;
        /* data_len 与 buf_size 取小，防越界读 */
        if (r.data_len > buf_size)
            r.data_len = (uint32_t)buf_size;

        if (g_disk && data_buf && r.data_len > 0)
            memcpy(data_buf, g_disk + (size_t)lba * MSD_BLOCK_SIZE, r.data_len);
        break;
    }

    case SCSI_OP_WRITE_10: {
        if (cbwcb_len < 10) {
            sense_record(0x05, 0x24); /* INVALID FIELD IN CDB */
            r.csw_status = 1;
            break;
        }
        uint32_t lba = ((uint32_t)cbwcb[2] << 24) | ((uint32_t)cbwcb[3] << 16) |
                       ((uint32_t)cbwcb[4] << 8)  | (uint32_t)cbwcb[5];
        uint16_t blocks = ((uint16_t)cbwcb[7] << 8) | (uint16_t)cbwcb[8];
        /* 先判后算：lba 或 lba+blocks 越出 MSD_BLOCK_COUNT 即判失败（防 LBA 回绕越界） */
        if (lba >= MSD_BLOCK_COUNT || blocks > MSD_BLOCK_COUNT - lba) {
            sense_record(0x05, 0x21); /* LOGICAL BLOCK ADDRESS OUT OF RANGE */
            r.csw_status = 1;
            r.data_len = 0;
            break;
        }
        uint32_t total = (uint32_t)blocks * MSD_BLOCK_SIZE;

        r.dir = SCSI_DIR_OUT;
        r.data_len = data_len < total ? data_len : total;
        /* data_len 与 buf_size 取小，防越界读 */
        if (r.data_len > buf_size)
            r.data_len = (uint32_t)buf_size;

        if (g_disk && data_buf && r.data_len > 0)
            memcpy(g_disk + (size_t)lba * MSD_BLOCK_SIZE, data_buf, r.data_len);
        break;
    }

    case SCSI_OP_MODE_SENSE_10:
        /* 与 MODE SENSE(6) 类似，但响应头是 8 字节而非 4 字节 */
        r.dir = SCSI_DIR_IN;
        r.data_len = data_len < 8 ? data_len : 8;
        if (data_buf)
            memset(data_buf, 0, r.data_len);
        break;

    default:
        /* 未识别的 SCSI 命令：返回 FAIL + CHECK CONDITION */
        fprintf(stderr, "[scsi] unsupported opcode 0x%02x\n", opcode);
        sense_record(0x05, 0x20); /* INVALID COMMAND OPERATION CODE */
        r.csw_status = 1; /* CSW FAIL */
        break;
    }

    return r;
}
