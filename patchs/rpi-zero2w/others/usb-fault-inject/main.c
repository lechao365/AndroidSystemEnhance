#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <getopt.h>
#include <errno.h>
#include "raw-gadget.h"
#include "faults.h"
#include "expect.h"

/* Pi Zero 2W 上的 dwc2 UDC 路径 */
#define DEFAULT_UDC "fe980000.usb"

/* ===== Help 信息 ===== */
static void usage(const char *prog)
{
    printf("Usage: %s <fault-type> [options]\n", prog);
    printf("\n");
    printf("Fault types (12 total, align with 10.01.02 kernel monitor events):\n");
    printf("  stall     --ep <in|out>                          F1/F2: STALL endpoint\n");
    printf("  timeout   --duration <ms>                        F3:   No response timeout\n");
    printf("  corrupt   --field <cbw-sig|csw-sig|csw-tag|csw-status>\n");
    printf("                                                  F4-F7: CBW/CSW field corruption\n");
    printf("  short     --bytes <n>                            F8:   Short transfer (Data phase)\n");
    printf("  abort     --ep <in|out>                          F9:   Bulk ABORT (ERR PID)\n");
    printf("  hotplug   --cycles <n> [--offline <ms>]          F10:  VBUS hot-plug cycle\n");
    printf("  disconnect                                       F11:  Physical disconnect\n");
    printf("  degrade   --delay <ms>                           F12:  Per-CBW rate degradation\n");
    printf("\n");
    printf("Common options:\n");
    printf("  --udc <path>        UDC device path (default: %s)\n", DEFAULT_UDC);
    printf("  --list              List all fault IDs and expected JSON\n");
    printf("  --show-expect <id|name>  Print expected JSON for a fault (no injection)\n");
    printf("  --help              Show this help\n");
    printf("\n");
    printf("Output:\n");
    printf("  stdout: JSON line '{\"fault\":\"<name>\",\"expect\":{...}}' for fault-verify\n");
    printf("\n");
    printf("Examples:\n");
    printf("  %s stall --ep in\n", prog);
    printf("  %s timeout --duration 5000\n", prog);
    printf("  %s corrupt --field csw-sig\n", prog);
    printf("  %s short --bytes 512\n", prog);
    printf("  %s hotplug --cycles 3 --offline 2000\n", prog);
}

/* ===== corrupt --field 字符串 → 枚举 ===== */
static enum corrupt_field parse_field(const char *s)
{
    if (!s) return CORRUPT_FIELD_NONE;
    if (strcmp(s, "cbw-sig") == 0)      return CORRUPT_FIELD_CBW_SIG;
    if (strcmp(s, "csw-sig") == 0)      return CORRUPT_FIELD_CSW_SIG;
    if (strcmp(s, "csw-tag") == 0)      return CORRUPT_FIELD_CSW_TAG;
    if (strcmp(s, "csw-status") == 0)   return CORRUPT_FIELD_CSW_STATUS;
    if (strcmp(s, "short") == 0)        return CORRUPT_FIELD_SHORT;
    return CORRUPT_FIELD_NONE;
}

/* ===== 解析 --ep in|out ===== */
static int parse_ep(const char *s)
{
    if (strcmp(s, "in") == 0)  return EP_BULK_IN;
    if (strcmp(s, "out") == 0) return EP_BULK_OUT;
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

    /* 特殊命令：--list / --help / --show-expect */
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
        /* 接受数字 ID 或名称 */
        int id = -1;
        char *endptr;
        long id_long = strtol(argv[2], &endptr, 10);
        if (*endptr == '\0' && id_long >= 0 && id_long < FAULT__MAX) {
            id = (int)id_long;
        } else {
            for (int i = 0; i < FAULT__MAX; i++) {
                /* 通过 expect_list 间接获取不方便，这里直接按名称查 */
                /* 简化：仅按 enum 顺序与名称对应关系查找 */
            }
            /* 通过依次输出 + grep 不优雅，改用查表 */
            /* fallback: 解析名称 */
            if (strcmp(argv[2], "stall") == 0)               id = FAULT_STALL;
            else if (strcmp(argv[2], "timeout") == 0)         id = FAULT_TIMEOUT;
            else if (strcmp(argv[2], "corrupt-cbw-sig") == 0) id = FAULT_CORRUPT_CBW_SIG;
            else if (strcmp(argv[2], "corrupt-csw-sig") == 0) id = FAULT_CORRUPT_CSW_SIG;
            else if (strcmp(argv[2], "corrupt-csw-tag") == 0) id = FAULT_CORRUPT_CSW_TAG;
            else if (strcmp(argv[2], "corrupt-csw-status") == 0) id = FAULT_CORRUPT_CSW_STA;
            else if (strcmp(argv[2], "short") == 0)           id = FAULT_SHORT;
            else if (strcmp(argv[2], "abort") == 0)            id = FAULT_ABORT;
            else if (strcmp(argv[2], "hotplug") == 0)          id = FAULT_HOTPLUG;
            else if (strcmp(argv[2], "disconnect") == 0)       id = FAULT_DISCONNECT;
            else if (strcmp(argv[2], "degrade") == 0)         id = FAULT_DEGRADE;
        }
        if (id < 0 || id >= FAULT__MAX) {
            fprintf(stderr, "Invalid fault id/name: %s\n", argv[2]);
            return 1;
        }
        expect_output_by_id((enum fault_id)id);
        return 0;
    }

    static struct option long_opts[] = {
        {"udc",          required_argument, 0, 'u'},
        {"ep",           required_argument, 0, 'e'},
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
    /* optind=2：跳过 argv[0] (程序名) 和 argv[1] (子命令)，
     * 假设命令行格式为: prog <subcommand> [--options ...]
     */
    optind = 2;
    while ((c = getopt_long(argc, argv, "u:e:d:f:b:c:o:l:LH", long_opts, NULL)) != -1) {
        switch (c) {
        case 'u': udc = optarg; break;
        case 'e':
            a.ep = parse_ep(optarg);
            if (a.ep < 0) {
                fprintf(stderr, "Error: invalid --ep '%s' (use in|out)\n", optarg);
                return 1;
            }
            break;
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
        case 'f': a.field = parse_field(optarg); break;
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

    /* 根据子命令设置 fault_id 与默认值 */
    if (strcmp(cmd, "stall") == 0) {
        if (a.ep < 0) {
            fprintf(stderr, "Error: --ep <in|out> required for stall\n");
            return 1;
        }
        fid = FAULT_STALL;
    } else if (strcmp(cmd, "timeout") == 0) {
        if (a.duration_ms <= 0) {
            fprintf(stderr, "Error: --duration <ms> required for timeout\n");
            return 1;
        }
        fid = FAULT_TIMEOUT;
    } else if (strcmp(cmd, "corrupt") == 0) {
        switch (a.field) {
        case CORRUPT_FIELD_CBW_SIG:    fid = FAULT_CORRUPT_CBW_SIG; break;
        case CORRUPT_FIELD_CSW_SIG:    fid = FAULT_CORRUPT_CSW_SIG; break;
        case CORRUPT_FIELD_CSW_TAG:    fid = FAULT_CORRUPT_CSW_TAG; break;
        case CORRUPT_FIELD_CSW_STATUS: fid = FAULT_CORRUPT_CSW_STA; break;
        default:
            fprintf(stderr, "Error: --field required for corrupt "
                            "(cbw-sig|csw-sig|csw-tag|csw-status)\n");
            return 1;
        }
    } else if (strcmp(cmd, "short") == 0) {
        if (a.short_bytes <= 0) {
            fprintf(stderr, "Error: --bytes <n> required for short\n");
            return 1;
        }
        fid = FAULT_SHORT;
        a.field = CORRUPT_FIELD_SHORT;
    } else if (strcmp(cmd, "abort") == 0) {
        if (a.ep < 0) {
            fprintf(stderr, "Error: --ep <in|out> required for abort\n");
            return 1;
        }
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

    /* 打开 Raw Gadget（部分命令不需要，如纯 STALL 也需要 fd） */
    struct raw_gadget *rg = raw_gadget_open(udc);
    if (!rg) {
        fprintf(stderr, "Failed to open raw gadget (udc=%s)\n", udc);
        return 1;
    }

    /* 执行故障注入 */
    int rc = 0;
    switch (fid) {
    case FAULT_STALL:            rc = fault_stall_ep(rg, &a); break;
    case FAULT_TIMEOUT:          rc = fault_timeout(rg, &a); break;
    case FAULT_CORRUPT_CBW_SIG:
    case FAULT_CORRUPT_CSW_SIG:
    case FAULT_CORRUPT_CSW_TAG:
    case FAULT_CORRUPT_CSW_STA:  rc = fault_corrupt(rg, &a); break;
    case FAULT_SHORT:            rc = fault_short_transfer(rg, &a); break;
    case FAULT_ABORT:            rc = fault_abort(rg, &a); break;
    case FAULT_HOTPLUG:          rc = fault_hotplug(rg, &a); break;
    case FAULT_DISCONNECT:       rc = fault_disconnect(rg, &a); break;
    case FAULT_DEGRADE:          rc = fault_degrade(rg, &a); break;
    default:                     rc = -1; break;
    }

    raw_gadget_close(rg);

    /* 关键：stdout 输出 JSON 供 fault-verify --expect 解析
     * 即便 rc != 0 也要输出，让 Host 侧能看到期望值的对齐关系
     */
    expect_output_by_id(fid);

    if (rc < 0) {
        fprintf(stderr, "Fault injection failed (id=%d, rc=%d)\n", fid, rc);
        return 1;
    }
    return 0;
}
