#ifndef FAULTS_H
#define FAULTS_H

#include "raw-gadget.h"
#include "usb-msd-proto.h"
#include <stdint.h>

/* ===== 故障标识（与 expect.c 中 expect_table 一一对应） ===== */
enum fault_id {
    FAULT_STALL = 0,        /* F1/F2  STALL 端点 */
    FAULT_TIMEOUT,          /* F3     传输超时 */
    FAULT_CORRUPT_CBW_SIG,  /* F4     CBW Signature 损坏 */
    FAULT_CORRUPT_CSW_SIG,  /* F5     CSW Signature 损坏 */
    FAULT_CORRUPT_CSW_TAG,  /* F6     CSW Tag 不匹配 */
    FAULT_CORRUPT_CSW_STA,  /* F7     CSW Status = Phase Error */
    FAULT_SHORT,            /* F8     短传输 */
    FAULT_ABORT,            /* F9     Bulk ABORT */
    FAULT_HOTPLUG,          /* F10    VBUS 热插拔 */
    FAULT_DISCONNECT,       /* F11    物理断开 */
    FAULT_DEGRADE,          /* F12    速率降级 */
    FAULT__MAX,
};

/* ===== 故障参数（打包传递给对应 fault_* 函数） ===== */
struct fault_args {
    int ep;                  /* STALL/ABORT 用：in/out → 0x81/0x02 */
    int duration_ms;         /* TIMEOUT 用 */
    enum corrupt_field field; /* CORRUPT 系列用 */
    int short_bytes;         /* SHORT 用：少发的字节数 */
    int cycles;              /* HOTPLUG 用：循环次数 */
    int offline_ms;          /* HOTPLUG 用：单次离线时长 */
    int delay_ms;            /* DEGRADE 用：每 CBW 处理延迟 */
};

/* ===== 12 类故障的实现入口 ===== */

/* F1/F2: STALL 端点
 * 触发内核事件: STALL + TRANSPORT_ERROR + RESET
 * 预期: error_count=1, reset_count=1, stall_count=1
 */
int fault_stall_ep(struct raw_gadget *rg, const struct fault_args *a);

/* F3: 传输超时
 * 触发内核事件: TIMEOUT + TRANSPORT_ERROR + RESET
 * 预期: error_count=1, reset_count=1, timeout_count=1
 */
int fault_timeout(struct raw_gadget *rg, const struct fault_args *a);

/* F4-F7: CBW/CSW 字段损坏
 * 触发内核事件: DATA_CORRUPT + TRANSPORT_ERROR + RESET
 * 预期: error_count=1, reset_count=1, corrupt_count=1
 */
int fault_corrupt(struct raw_gadget *rg, const struct fault_args *a);

/* F8: 短传输
 * 触发内核事件: DATA_CORRUPT + TRANSPORT_ERROR
 * 预期: error_count=1, corrupt_count=1
 */
int fault_short_transfer(struct raw_gadget *rg, const struct fault_args *a);

/* F9: Bulk ABORT（通过 STALL-then-delay 模拟 ERR PID）
 * 触发内核事件: TRANSPORT_ERROR + RESET
 * 预期: error_count=1, reset_count=1
 */
int fault_abort(struct raw_gadget *rg, const struct fault_args *a);

/* F10: VBUS 热插拔
 * 触发内核事件: DEVICE_DISCONNECT + DEVICE_PROBE（循环 N 次）
 * 预期: 设备节点消失/重现 N 次
 */
int fault_hotplug(struct raw_gadget *rg, const struct fault_args *a);

/* F11: 物理断开（VBUS 拉低后保持）
 * 触发内核事件: DEVICE_DISCONNECT
 * 预期: 设备节点消失
 */
int fault_disconnect(struct raw_gadget *rg, const struct fault_args *a);

/* F12: 速率降级（每 CBW 处理后插入延迟）
 * 触发内核事件: TRANSPORT_END（耗时增大）
 * 预期: current_rate < 50% × baseline
 */
int fault_degrade(struct raw_gadget *rg, const struct fault_args *a);

#endif
