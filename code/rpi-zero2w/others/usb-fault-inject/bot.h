#ifndef BOT_H
#define BOT_H

#include <stdint.h>
#include <stdbool.h>
#include "raw-gadget.h"

/* BOT 主循环中可注入的故障钩子类型 */
enum fault_hook {
    HOOK_NONE = 0,
    /* 钩子 A: CBW 接收前 — stall-out */
    HOOK_STALL_OUT,
    /* 钩子 B: CBW 接收后 — timeout (不响应) */
    HOOK_TIMEOUT,
    /* 钩子 C: Data IN 发送前 — short transfer */
    HOOK_SHORT,
    /* 钩子 D: Data IN 发送前 — stall-in */
    HOOK_STALL_IN,
    /* 钩子 E: CSW 发送前 — degrade delay */
    HOOK_DEGRADE,
    /* 钩子 F: CSW 发送前 — corrupt CSW 字段 */
    HOOK_CORRUPT_CSW_SIG,
    HOOK_CORRUPT_CSW_TAG,
    HOOK_CORRUPT_CSW_STATUS,
    /* 钩子 G: CBW 后 — abort = stall-in + 不响应 */
    HOOK_ABORT,
};

/* 故障注入配置（由 faults.c 设置，BOT 层读取） */
struct fault_injection {
    enum fault_hook  hook;
    int              duration_ms;   /* timeout/abort 持续时间 */
    int              short_bytes;   /* SHORT 故障少发的字节数 */
    int              delay_ms;      /* DEGRADE 每次延迟 */
    bool             active;        /* 是否激活（单次注入后自动清除） */
};

/*
 * bot_main_loop — BOT 协议主循环
 *
 * 完整流程：INIT → RUN → 枚举 → CBW/Data/CSW 循环。
 * 枚举完成后进入 BOT 状态机，在每个环节检查 fault_injection 钩子。
 *
 * 返回 0 正常退出（Host 断开），-1 错误。
 */
int bot_main_loop(struct raw_gadget *rg, struct fault_injection *fi);

#endif /* BOT_H */
