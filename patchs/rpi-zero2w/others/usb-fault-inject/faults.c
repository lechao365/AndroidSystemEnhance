#include "faults.h"
#include "raw-gadget.h"
#include "bot.h"
#include "scsi.h"

#include <stdio.h>
#include <string.h>
#include <unistd.h>
#include <errno.h>
#include <stdlib.h>

/* fault_id → 名称映射表 */
static const struct {
    enum fault_id id;
    const char *name;
    const char *desc;
} fault_name_table[] = {
    { FAULT_STALL_IN,        "stall-in",         "F1: STALL IN endpoint" },
    { FAULT_STALL_OUT,       "stall-out",        "F2: STALL OUT endpoint" },
    { FAULT_TIMEOUT,         "timeout",          "F3: No response timeout" },
    { FAULT_CORRUPT_CSW_SIG, "corrupt-csw-sig",  "F5: CSW Signature corrupted" },
    { FAULT_CORRUPT_CSW_TAG, "corrupt-csw-tag",  "F6: CSW Tag mismatch" },
    { FAULT_CORRUPT_CSW_STA, "corrupt-csw-status","F7: CSW Status = Phase Error" },
    { FAULT_SHORT,           "short",            "F8: Short transfer" },
    { FAULT_ABORT,           "abort",            "F9: STALL+TIMEOUT composite" },
    { FAULT_HOTPLUG,         "hotplug",          "F10: VBUS hot-plug cycle" },
    { FAULT_DISCONNECT,      "disconnect",       "F11: Physical disconnect" },
    { FAULT_DEGRADE,         "degrade",          "F12: Per-CBW rate degradation" },
};

const char *fault_id_to_name(enum fault_id id)
{
    for (size_t i = 0; i < sizeof(fault_name_table)/sizeof(fault_name_table[0]); i++) {
        if (fault_name_table[i].id == id)
            return fault_name_table[i].name;
    }
    return "unknown";
}

/* 设置 BOT 层故障注入钩子并进入 BOT 主循环
 * (已内联到 fault_execute 各 case 中，保留此声明供将来扩展) */

int fault_execute(struct raw_gadget *rg, enum fault_id fid,
                  const struct fault_args *a, struct fault_injection *fi)
{
    if (!rg || !a || !fi) {
        fprintf(stderr, "[faults] NULL parameter\n");
        return -1;
    }

    memset(fi, 0, sizeof(*fi));

    switch (fid) {

    /* ===== F1: STALL IN ===== */
    case FAULT_STALL_IN:
        fi->hook = HOOK_STALL_IN;
        fi->active = true;
        fprintf(stderr, "[faults] F1: STALL IN — entering BOT loop\n");
        return bot_main_loop(rg, fi);

    /* ===== F2: STALL OUT ===== */
    case FAULT_STALL_OUT:
        fi->hook = HOOK_STALL_OUT;
        fi->active = true;
        fprintf(stderr, "[faults] F2: STALL OUT — entering BOT loop\n");
        return bot_main_loop(rg, fi);

    /* ===== F3: TIMEOUT ===== */
    case FAULT_TIMEOUT:
        fi->hook = HOOK_TIMEOUT;
        fi->duration_ms = a->duration_ms > 0 ? a->duration_ms : 35000;
        fi->active = true;
        fprintf(stderr, "[faults] F3: TIMEOUT %d ms — entering BOT loop\n", fi->duration_ms);
        return bot_main_loop(rg, fi);

    /* ===== F5: CORRUPT CSW SIGNATURE ===== */
    case FAULT_CORRUPT_CSW_SIG:
        fi->hook = HOOK_CORRUPT_CSW_SIG;
        fi->active = true;
        fprintf(stderr, "[faults] F5: CORRUPT CSW sig — entering BOT loop\n");
        return bot_main_loop(rg, fi);

    /* ===== F6: CORRUPT CSW TAG ===== */
    case FAULT_CORRUPT_CSW_TAG:
        fi->hook = HOOK_CORRUPT_CSW_TAG;
        fi->active = true;
        fprintf(stderr, "[faults] F6: CORRUPT CSW tag — entering BOT loop\n");
        return bot_main_loop(rg, fi);

    /* ===== F7: CORRUPT CSW STATUS ===== */
    case FAULT_CORRUPT_CSW_STA:
        fi->hook = HOOK_CORRUPT_CSW_STATUS;
        fi->active = true;
        fprintf(stderr, "[faults] F7: CORRUPT CSW status — entering BOT loop\n");
        return bot_main_loop(rg, fi);

    /* ===== F8: SHORT TRANSFER ===== */
    case FAULT_SHORT:
        fi->hook = HOOK_SHORT;
        fi->short_bytes = a->short_bytes;
        fi->active = true;
        fprintf(stderr, "[faults] F8: SHORT %d bytes — entering BOT loop\n", fi->short_bytes);
        return bot_main_loop(rg, fi);

    /* ===== F9: ABORT (STALL IN + TIMEOUT) ===== */
    case FAULT_ABORT:
        fi->hook = HOOK_ABORT;
        fi->duration_ms = a->duration_ms > 0 ? a->duration_ms : 35000;
        fi->active = true;
        fprintf(stderr, "[faults] F9: ABORT (STALL+TIMEOUT %d ms) — entering BOT loop\n",
                fi->duration_ms);
        return bot_main_loop(rg, fi);

    /* ===== F10: HOTPLUG (VBUS 周期性插拔) ===== */
    case FAULT_HOTPLUG:
        for (int i = 0; i < a->cycles; i++) {
            fprintf(stderr, "[faults] F10: HOTPLUG cycle %d/%d: OFF\n", i + 1, a->cycles);
            raw_gadget_vbus_draw(rg, 0);
            usleep(a->offline_ms * 1000);

            fprintf(stderr, "[faults] F10: HOTPLUG cycle %d/%d: ON\n", i + 1, a->cycles);
            raw_gadget_vbus_draw(rg, 500);
            usleep(500 * 1000);
        }
        return 0;

    /* ===== F11: DISCONNECT (永久断开) ===== */
    case FAULT_DISCONNECT:
        fprintf(stderr, "[faults] F11: DISCONNECT — pulling VBUS low\n");
        return raw_gadget_vbus_draw(rg, 0);

    /* ===== F12: DEGRADE (持续延迟注入) ===== */
    case FAULT_DEGRADE:
        fi->hook = HOOK_DEGRADE;
        fi->delay_ms = a->delay_ms;
        fi->active = true;
        fprintf(stderr, "[faults] F12: DEGRADE %d ms/CBW — entering BOT loop\n", fi->delay_ms);
        return bot_main_loop(rg, fi);

    default:
        fprintf(stderr, "[faults] unknown fault id %d\n", fid);
        return -1;
    }
}
