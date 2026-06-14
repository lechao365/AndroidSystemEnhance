#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <getopt.h>
#include <errno.h>
#include "raw-gadget.h"
#include "faults.h"
#include "expect.h"
#include "scsi.h"
#include "bot.h"

/* Pi Zero 2W 上 dwc2 的 UDC 名称（driver_name 和 device_name 相同） */
#define DEFAULT_UDC "20980000.usb"

/* ===== Help 信息 ===== */
static void usage(const char *prog)
{
    printf("Usage: %s <fault-type> [options]\n", prog);
    printf("\n");
    printf("Fault types (11 total):\n");
    printf("  stall-in     [--duration <ms>]                 F1:  STALL IN endpoint\n");
    printf("  stall-out    [--duration <ms>]                 F2:  STALL OUT endpoint\n");
    printf("  timeout      --duration <ms>                   F3:  No response timeout\n");
    printf("  corrupt      --field <csw-sig|csw-tag|csw-status>\n");
    printf("                                                    F5-F7: CSW field corruption\n");
    printf("  short        --bytes <n>                       F8:  Short transfer (Data phase)\n");
    printf("  abort        --duration <ms>                   F9:  STALL+TIMEOUT composite\n");
    printf("  hotplug      --cycles <n> [--offline <ms>]     F10: VBUS hot-plug cycle\n");
    printf("  disconnect                                      F11: Physical disconnect\n");
    printf("  degrade      --delay <ms>                      F12: Per-CBW rate degradation\n");
    printf("\n");
    printf("Common options:\n");
    printf("  --udc <name>        UDC name (default: %s)\n", DEFAULT_UDC);
    printf("  --list              List all fault IDs and expected JSON\n");
    printf("  --show-expect <id|name>  Print expected JSON for a fault (no injection)\n");
    printf("  --help              Show this help\n");
    printf("\n");
    printf("Output:\n");
    printf("  stdout: JSON line '{\"fault\":\"<name>\",\"expect\":{...}}' for fault-verify\n");
    printf("\n");
    printf("Examples:\n");
    printf("  %s stall-in\n", prog);
    printf("  %s timeout --duration 35000\n", prog);
    printf("  %s corrupt --field csw-sig\n", prog);
    printf("  %s short --bytes 512\n", prog);
    printf("  %s hotplug --cycles 3 --offline 2000\n", prog);
}

/* ===== 解析 corrupt --field 字符串 → 枚举 ===== */
static enum fault_id parse_corrupt_field(const char *s)
{
    if (!s) return -1;
    if (strcmp(s, "csw-sig") == 0)      return FAULT_CORRUPT_CSW_SIG;
    if (strcmp(s, "csw-tag") == 0)      return FAULT_CORRUPT_CSW_TAG;
    if (strcmp(s, "csw-status") == 0)   return FAULT_CORRUPT_CSW_STA;
    return -1;
}

/* ===== 故障名称 → fault_id ===== */
static int parse_fault_name(const char *s)
{
    if (strcmp(s, "stall-in") == 0)          return FAULT_STALL_IN;
    if (strcmp(s, "stall-out") == 0)         return FAULT_STALL_OUT;
    if (strcmp(s, "timeout") == 0)           return FAULT_TIMEOUT;
    if (strcmp(s, "corrupt-csw-sig") == 0)   return FAULT_CORRUPT_CSW_SIG;
    if (strcmp(s, "corrupt-csw-tag") == 0)   return FAULT_CORRUPT_CSW_TAG;
    if (strcmp(s, "corrupt-csw-status") == 0)return FAULT_CORRUPT_CSW_STA;
    if (strcmp(s, "short") == 0)             return FAULT_SHORT;
    if (strcmp(s, "abort") == 0)             return FAULT_ABORT;
    if (strcmp(s, "hotplug") == 0)           return FAULT_HOTPLUG;
    if (strcmp(s, "disconnect") == 0)        return FAULT_DISCONNECT;
    if (strcmp(s, "degrade") == 0)           return FAULT_DEGRADE;
    return -1;
}

int main(int argc, char *argv[])
{
    const char *udc = DEFAULT_UDC;
    struct fault_args a = {0};
    enum fault_id fid = -1;

    if (argc < 2) {
        usage(argv[0]);
        return 1;
    }

    const char *cmd = argv[1];

    /* 特殊命令 */
    if (strcmp(cmd, "--list") == 0 || strcmp(cmd, "-l") == 0) {
        expect_list_all();
        return 0;
    }
    if (strcmp(cmd, "--help") == 0 || strcmp(cmd, "-h") == 0) {
        usage(argv[0]);
        return 0;
    }
    if (strcmp(cmd, "--show-expect") == 0) {
        if (argc < 3) {
            fprintf(stderr, "Usage: %s --show-expect <fault_id|name>\n", argv[0]);
            return 1;
        }
        int id = -1;
        char *endptr;
        long id_long = strtol(argv[2], &endptr, 10);
        if (*endptr == '\0' && id_long >= 0 && id_long < FAULT__MAX) {
            id = (int)id_long;
        } else {
            id = parse_fault_name(argv[2]);
        }
        if (id < 0 || id >= FAULT__MAX) {
            fprintf(stderr, "Invalid fault id/name: %s\n", argv[2]);
            return 1;
        }
        expect_output_by_id((enum fault_id)id);
        return 0;
    }

    /* 选项解析 */
    static struct option long_opts[] = {
        {"udc",          required_argument, 0, 'u'},
        {"duration",     required_argument, 0, 'd'},
        {"field",        required_argument, 0, 'f'},
        {"bytes",        required_argument, 0, 'b'},
        {"cycles",       required_argument, 0, 'c'},
        {"offline",      required_argument, 0, 'o'},
        {"delay",        required_argument, 0, 'l'},
        {"list",         no_argument,       0, 'L'},
        {"help",         no_argument,       0, 'H'},
        {0, 0, 0, 0}
    };

    int c;
    optind = 2;  /* 跳过 argv[0] 和 argv[1] */
    while ((c = getopt_long(argc, argv, "u:d:f:b:c:o:l:LH", long_opts, NULL)) != -1) {
        switch (c) {
        case 'u': udc = optarg; break;
        case 'd': {
            char *endp;
            errno = 0;
            long val = strtol(optarg, &endp, 10);
            if (errno != 0 || *endp != '\0' || val < 0) {
                fprintf(stderr, "Error: invalid --duration '%s'\n", optarg);
                return 1;
            }
            a.duration_ms = (int)val;
            break;
        }
        case 'f':
            fid = parse_corrupt_field(optarg);
            if (fid < 0) {
                fprintf(stderr, "Error: invalid --field '%s' (use csw-sig|csw-tag|csw-status)\n", optarg);
                return 1;
            }
            break;
        case 'b': {
            char *endp;
            errno = 0;
            long val = strtol(optarg, &endp, 10);
            if (errno != 0 || *endp != '\0' || val < 0) {
                fprintf(stderr, "Error: invalid --bytes '%s'\n", optarg);
                return 1;
            }
            a.short_bytes = (int)val;
            break;
        }
        case 'c': {
            char *endp;
            errno = 0;
            long val = strtol(optarg, &endp, 10);
            if (errno != 0 || *endp != '\0' || val < 0) {
                fprintf(stderr, "Error: invalid --cycles '%s'\n", optarg);
                return 1;
            }
            a.cycles = (int)val;
            break;
        }
        case 'o': {
            char *endp;
            errno = 0;
            long val = strtol(optarg, &endp, 10);
            if (errno != 0 || *endp != '\0' || val < 0) {
                fprintf(stderr, "Error: invalid --offline '%s'\n", optarg);
                return 1;
            }
            a.offline_ms = (int)val;
            break;
        }
        case 'l': {
            char *endp;
            errno = 0;
            long val = strtol(optarg, &endp, 10);
            if (errno != 0 || *endp != '\0' || val < 0) {
                fprintf(stderr, "Error: invalid --delay '%s'\n", optarg);
                return 1;
            }
            a.delay_ms = (int)val;
            break;
        }
        case 'L': expect_list_all(); return 0;
        case 'H': usage(argv[0]); return 0;
        default: usage(argv[0]); return 1;
        }
    }

    /* 根据子命令确定 fault_id（--field 选项已设置的部分除外） */
    if (fid < 0) {
        if (strcmp(cmd, "stall-in") == 0) {
            fid = FAULT_STALL_IN;
        } else if (strcmp(cmd, "stall-out") == 0) {
            fid = FAULT_STALL_OUT;
        } else if (strcmp(cmd, "timeout") == 0) {
            if (a.duration_ms <= 0) {
                fprintf(stderr, "Error: --duration <ms> required for timeout\n");
                return 1;
            }
            fid = FAULT_TIMEOUT;
        } else if (strcmp(cmd, "corrupt") == 0) {
            fprintf(stderr, "Error: --field required for corrupt "
                            "(csw-sig|csw-tag|csw-status)\n");
            return 1;
        } else if (strcmp(cmd, "short") == 0) {
            if (a.short_bytes <= 0) {
                fprintf(stderr, "Error: --bytes <n> required for short\n");
                return 1;
            }
            fid = FAULT_SHORT;
        } else if (strcmp(cmd, "abort") == 0) {
            fid = FAULT_ABORT;
        } else if (strcmp(cmd, "hotplug") == 0) {
            if (a.cycles <= 0) {
                fprintf(stderr, "Error: --cycles <n> required for hotplug\n");
                return 1;
            }
            if (a.offline_ms <= 0) a.offline_ms = 2000;
            fid = FAULT_HOTPLUG;
        } else if (strcmp(cmd, "disconnect") == 0) {
            fid = FAULT_DISCONNECT;
        } else if (strcmp(cmd, "degrade") == 0) {
            if (a.delay_ms <= 0) {
                fprintf(stderr, "Error: --delay <ms> required for degrade\n");
                return 1;
            }
            fid = FAULT_DEGRADE;
        } else {
            fprintf(stderr, "Error: unknown fault type '%s'\n", cmd);
            usage(argv[0]);
            return 1;
        }
    }

    /* ===== 打开 raw-gadget ===== */
    struct raw_gadget *rg = raw_gadget_open(udc);
    if (!rg) {
        fprintf(stderr, "Failed to open raw-gadget (udc=%s)\n", udc);
        return 1;
    }

    /* ===== 初始化 SCSI 内存盘 ===== */
    if (scsi_init() < 0) {
        fprintf(stderr, "Failed to initialize SCSI layer\n");
        raw_gadget_close(rg);
        return 1;
    }

    /* ===== 枚举：处理 EP0 控制请求直到 SET_CONFIGURATION ===== */
    fprintf(stderr, "[main] starting USB enumeration...\n");
    if (raw_gadget_enumerate(rg) < 0) {
        fprintf(stderr, "[main] enumeration failed\n");
        scsi_exit();
        raw_gadget_close(rg);
        return 1;
    }

    fprintf(stderr, "[main] enumeration complete, ready for fault injection\n");

    /* ===== 执行故障注入 ===== */
    struct fault_injection fi;
    int rc = fault_execute(rg, fid, &a, &fi);

    /* ===== 清理 ===== */
    scsi_exit();
    raw_gadget_close(rg);

    /* 输出期望值 JSON 到 stdout（供 fault-verify --expect 解析） */
    expect_output_by_id(fid);

    if (rc < 0) {
        fprintf(stderr, "Fault injection completed with errors (id=%d)\n", fid);
        return 1;
    }

    fprintf(stderr, "[main] fault injection completed successfully\n");
    return 0;
}
