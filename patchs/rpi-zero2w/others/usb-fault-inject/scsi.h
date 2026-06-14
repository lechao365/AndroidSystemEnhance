#ifndef SCSI_H
#define SCSI_H

#include <stdint.h>
#include <stddef.h>

/* MSD 虚拟块设备参数 */
#define MSD_BLOCK_SIZE      512
#define MSD_BLOCK_COUNT     (64 * 1024 * 1024 / MSD_BLOCK_SIZE)  /* 131072 blocks = 64MB */
#define MSD_LUN_COUNT       1

/* SCSI Data 阶段方向 */
enum scsi_data_dir {
    SCSI_DIR_NONE = 0,  /* 无 Data 阶段（如 TEST UNIT READY） */
    SCSI_DIR_IN   = 1,  /* Device → Host（如 INQUIRY, READ） */
    SCSI_DIR_OUT  = 2,  /* Host → Device（如 WRITE） */
};

/* SCSI 命令处理结果 */
struct scsi_result {
    enum scsi_data_dir dir;
    uint32_t           data_len;    /* Data 阶段字节数 */
    uint8_t            csw_status;  /* CSW bCSWStatus: 0=Pass, 1=Fail */
};

/* 初始化 SCSI 层（分配 64MB 内存盘）
 * 返回 0 成功，-1 失败 */
int  scsi_init(void);

/* 销毁 SCSI 层（释放内存盘） */
void scsi_exit(void);

/* 处理一条 SCSI 命令
 * cbwcb:       CBW.CBWCB[16] 字段
 * cbwcb_len:   CBW.bCBWCBLength（1..16）
 * data_len:    CBW.dCBWDataTransferLength
 * out_buf:     IN 方向时，函数填充此缓冲区并返回数据指针
 *              OUT 方向时，函数从此缓冲区读取 Host 发来的数据
 * 返回:        SCSI 处理结果（方向、实际数据长度、CSW status）
 */
struct scsi_result scsi_handle_command(const uint8_t *cbwcb, uint8_t cbwcb_len,
                                        uint32_t data_len,
                                        uint8_t *data_buf, size_t buf_size);

#endif /* SCSI_H */
