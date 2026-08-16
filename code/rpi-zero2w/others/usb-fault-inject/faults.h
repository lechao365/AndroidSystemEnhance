#ifndef FAULTS_H
#define FAULTS_H

#include "raw-gadget.h"
#include "bot.h"
#include <stdint.h>

/*
 * 11 类故障标识（F4 已删除：CBW sig 在 Device 侧物理不可注入）
 *
 * F9 (ABORT) 重定义为 STALL+TIMEOUT 复合：
 *   收到 CBW 后 STALL IN 端点 + 不响应直到超时
 */
enum fault_id {
    FAULT_STALL_IN = 0,     /* F1:  STALL IN endpoint */
    FAULT_STALL_OUT,        /* F2:  STALL OUT endpoint */
    FAULT_TIMEOUT,          /* F3:  No response timeout */
    /* F4 (corrupt-cbw-sig) 已删除 — CBW 是 Host→Device 方向 */
    FAULT_CORRUPT_CSW_SIG,  /* F5:  CSW Signature corrupted */
    FAULT_CORRUPT_CSW_TAG,  /* F6:  CSW Tag mismatch */
    FAULT_CORRUPT_CSW_STA,  /* F7:  CSW Status = Phase Error */
    FAULT_SHORT,            /* F8:  Short transfer */
    FAULT_ABORT,            /* F9:  STALL+TIMEOUT 复合 (原 ABORT 重定义) */
    FAULT_HOTPLUG,          /* F10: VBUS hot-plug cycle */
    FAULT_DISCONNECT,       /* F11: Physical disconnect */
    FAULT_DEGRADE,          /* F12: Per-CBW rate degradation */
    FAULT__MAX,
};

/* 故障参数 */
struct fault_args {
    int ep;                  /* STALL 用：0x81=IN / 0x02=OUT */
    int duration_ms;         /* TIMEOUT/ABORT 用 */
    int short_bytes;         /* SHORT 用：少发的字节数 */
    int cycles;              /* HOTPLUG 用：循环次数 */
    int offline_ms;          /* HOTPLUG 用：单次离线时长 */
    int delay_ms;            /* DEGRADE 用：每 CBW 处理延迟 */
};

/*
 * 执行故障注入
 *
 * rg:  raw-gadget 实例（必须已完成枚举）
 * fid: 故障 ID
 * a:   故障参数
 * fi:  BOT 循环的故障注入配置（用于 BULK 层故障）
 *
 * 对于 BULK 层故障（STALL/TIMEOUT/CORRUPT/SHORT/ABORT/DEGRADE），
 * 此函数设置 fi 钩子后调用 bot_main_loop 进入 BOT 循环。
 *
 * 对于物理层故障（HOTPLUG/DISCONNECT），直接执行 VBUS 控制。
 *
 * 返回 0 成功，-1 失败。
 */
int fault_execute(struct raw_gadget *rg, enum fault_id fid,
                  const struct fault_args *a, struct fault_injection *fi);

/* 将 fault_id 转换为名称字符串 */
const char *fault_id_to_name(enum fault_id id);

#endif /* FAULTS_H */
