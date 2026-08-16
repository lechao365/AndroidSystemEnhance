/*
 * ============================================================
 * parse.c — CLI 命令行参数解析实现
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 将 argc/argv 解析为 struct fv_command。
 *
 * 解析流程:
 *   1) 解析子命令（stats get/reset, config get/set, event read/wait, check stats/event/degrade）
 *   2) 解析选项参数（--device, --timeout-ms, --type, --stall-ge 等）
 *   3) 参数缺失时打印 usage 并返回 -1
 *
 * 命令格式: fv <command> <subcommand> [options]
 * ============================================================
 */
#include "parse.h"
#include "fv_ioctl_compat.h"

#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/*
 * usage — 打印使用帮助信息
 * 列出所有子命令和选项参数的说明。
 */
void usage(const char *prog)
{
    fprintf(stderr,
        "Usage: %s <command> [options]\n"
        "\n"
        "Commands:\n"
        "  stats get                    Get device statistics\n"
        "  stats reset                  Reset device statistics\n"
        "  config get                   Get device config\n"
        "  config set                   Set device config\n"
        "  event read                   Read next event\n"
        "  event wait                   Wait for matching event\n"
        "  check stats                  Assert statistics thresholds\n"
        "  check event                  Check event match\n"
        "  check degrade                Check degradation thresholds\n"
        "\n"
        "Options:\n"
        "  --device <path>              Device path (default: /dev/vendor_lechao_usbd)\n"
        "  --timeout-ms <ms>            Timeout in milliseconds\n"
        "  --type <type>                Event type: stall|timeout|corrupt|disconnect|probe|degrade|reset|transport_error\n"
        "  --stall-ge <n>               Assert stall_count >= n\n"
        "  --timeout-ge <n>             Assert timeout_count >= n\n"
        "  --corrupt-ge <n>             Assert corrupt_count >= n\n"
        "  --disconnect-ge <n>          Assert disconnect_count >= n\n"
        "  --probe-ge <n>               Assert probe_count >= n\n"
        "  --rate-drop-ge <n>           Assert rate drop >= n\n"
        "  --latency-rise-ge <n>        Assert latency rise >= n\n"
        "  --enable <0|1>               Config: enabled flag\n"
        "  --flags <hex>                Config: flags value\n"
        "  --json                       Output in JSON format\n"
        "  --help                       Show this help\n",
        prog);
}

/*
 * parse_event_type — 将字符串事件类型名转换为内核枚举值
 * @s: 事件类型名称（如 "stall", "timeout" 等）
 * 返回: 对应的 enum vendor_lechao_usbd_event_type 值，未知类型返回 0xFFFFFFFF
 *
 * 注意: "disconnect" 映射到 RESET（内核没有独立的 DISCONNECT 事件类型），
 *       "probe" 映射到 NONE（probe 不是异步事件，而是统计计数器）。
 */
static uint32_t parse_event_type(const char *s)
{
    if (strcmp(s, "stall") == 0)           return VENDOR_LECHAO_USBD_EVENT_STALL;
    if (strcmp(s, "timeout") == 0)         return VENDOR_LECHAO_USBD_EVENT_TIMEOUT;
    if (strcmp(s, "corrupt") == 0)         return VENDOR_LECHAO_USBD_EVENT_DATA_CORRUPT;
    if (strcmp(s, "reset") == 0)           return VENDOR_LECHAO_USBD_EVENT_RESET;
    if (strcmp(s, "transport_error") == 0) return VENDOR_LECHAO_USBD_EVENT_TRANSPORT_ERROR;
    if (strcmp(s, "disconnect") == 0)      return VENDOR_LECHAO_USBD_EVENT_RESET;
    if (strcmp(s, "probe") == 0)           return VENDOR_LECHAO_USBD_EVENT_NONE;
    if (strcmp(s, "degrade") == 0)         return VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED;
    return 0xFFFFFFFF;
}

/* parse_u64 — 解析无符号 64 位整数字符串（支持 0x 十六进制前缀），失败返回 0 并设 *ok=0 */
static uint64_t parse_u64(const char *s, int *ok)
{
    char *endptr = NULL;
    errno = 0;
    unsigned long long val = strtoull(s, &endptr, 0);
    if (errno != 0 || *endptr != '\0') {
        if (ok) *ok = 0;
        return 0;
    }
    if (ok) *ok = 1;
    return val;
}

/*
 * fv_parse_args — 两阶段解析器
 *
 * 阶段 1: 解析 "命令 子命令" 部分（如 "stats get"）
 *   - 确定 fv_command_kind
 *   - 消耗 2 个 argv 参数
 *
 * 阶段 2: 解析选项参数（--device, --timeout-ms 等）
 *   - 循环处理直到 argc 耗尽
 *   - 每个选项后必须跟一个值参数
 *
 * 默认值:
 *   device_path = "/dev/vendor_lechao_usbd"
 *   timeout_ms  = 5000
 */
int fv_parse_args(int argc, char **argv, struct fv_command *cmd)
{
    memset(cmd, 0, sizeof(*cmd));
    cmd->device_path = "/dev/vendor_lechao_usbd";
    cmd->timeout_ms = 5000;

    if (argc < 2) {
        usage(argv[0]);
        return -1;
    }

    const char *first = argv[1];
    if (strcmp(first, "--help") == 0 || strcmp(first, "-h") == 0) {
        usage(argv[0]);
        return -1;
    }

    int i = 1;

    /* 阶段 1: 解析命令和子命令 */
    if (strcmp(first, "stats") == 0) {
        if (i + 1 >= argc) { usage(argv[0]); return -1; }
        if (strcmp(argv[i + 1], "get") == 0)       cmd->kind = FV_CMD_STATS_GET;
        else if (strcmp(argv[i + 1], "reset") == 0) cmd->kind = FV_CMD_STATS_RESET;
        else { usage(argv[0]); return -1; }
        i += 2;
    } else if (strcmp(first, "config") == 0) {
        if (i + 1 >= argc) { usage(argv[0]); return -1; }
        if (strcmp(argv[i + 1], "get") == 0)      cmd->kind = FV_CMD_CONFIG_GET;
        else if (strcmp(argv[i + 1], "set") == 0)  cmd->kind = FV_CMD_CONFIG_SET;
        else { usage(argv[0]); return -1; }
        i += 2;
    } else if (strcmp(first, "event") == 0) {
        if (i + 1 >= argc) { usage(argv[0]); return -1; }
        if (strcmp(argv[i + 1], "read") == 0)      cmd->kind = FV_CMD_EVENT_READ;
        else if (strcmp(argv[i + 1], "wait") == 0)  cmd->kind = FV_CMD_EVENT_WAIT;
        else { usage(argv[0]); return -1; }
        i += 2;
    } else if (strcmp(first, "check") == 0) {
        if (i + 1 >= argc) { usage(argv[0]); return -1; }
        if (strcmp(argv[i + 1], "stats") == 0)           cmd->kind = FV_CMD_CHECK_STATS;
        else if (strcmp(argv[i + 1], "event") == 0)      cmd->kind = FV_CMD_CHECK_EVENT;
        else if (strcmp(argv[i + 1], "degrade") == 0)    cmd->kind = FV_CMD_CHECK_DEGRADE;
        else { usage(argv[0]); return -1; }
        i += 2;
    } else {
        usage(argv[0]);
        return -1;
    }

    /* 阶段 2: 解析选项参数 */
    while (i < argc) {
        if (strcmp(argv[i], "--device") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            if (strncmp(argv[i], "/dev/", 5) != 0) {
                fprintf(stderr, "Device path must start with /dev/: %s\n", argv[i]);
                return -1;
            }
            cmd->device_path = argv[i++];
        } else if (strcmp(argv[i], "--timeout-ms") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            {
                char *endptr = NULL;
                errno = 0;
                long val = strtol(argv[i], &endptr, 10);
                if (errno != 0 || *endptr != '\0' || val < 0) {
                    fprintf(stderr, "Invalid timeout value: %s\n", argv[i]);
                    return -1;
                }
                cmd->timeout_ms = (uint32_t)val;
            }
            i++;
        } else if (strcmp(argv[i], "--type") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            cmd->expect_event_type = parse_event_type(argv[i]);
            if (cmd->expect_event_type == 0xFFFFFFFF) {
                fprintf(stderr, "Unknown event type: %s\n", argv[i]);
                return -1;
            }
            i++;
        } else if (strcmp(argv[i], "--stall-ge") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            { int ok = 0; cmd->stall_ge = parse_u64(argv[i], &ok);
              if (!ok) { fprintf(stderr, "Invalid threshold value: %s\n", argv[i]); return -1; } i++; }
        } else if (strcmp(argv[i], "--timeout-ge") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            { int ok = 0; cmd->timeout_ge = parse_u64(argv[i], &ok);
              if (!ok) { fprintf(stderr, "Invalid threshold value: %s\n", argv[i]); return -1; } i++; }
        } else if (strcmp(argv[i], "--corrupt-ge") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            { int ok = 0; cmd->corrupt_ge = parse_u64(argv[i], &ok);
              if (!ok) { fprintf(stderr, "Invalid threshold value: %s\n", argv[i]); return -1; } i++; }
        } else if (strcmp(argv[i], "--disconnect-ge") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            { int ok = 0; cmd->disconnect_ge = parse_u64(argv[i], &ok);
              if (!ok) { fprintf(stderr, "Invalid threshold value: %s\n", argv[i]); return -1; } i++; }
        } else if (strcmp(argv[i], "--probe-ge") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            { int ok = 0; cmd->probe_ge = parse_u64(argv[i], &ok);
              if (!ok) { fprintf(stderr, "Invalid threshold value: %s\n", argv[i]); return -1; } i++; }
        } else if (strcmp(argv[i], "--rate-drop-ge") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            { int ok = 0; cmd->rate_drop_ge = parse_u64(argv[i], &ok);
              if (!ok) { fprintf(stderr, "Invalid threshold value: %s\n", argv[i]); return -1; } i++; }
        } else if (strcmp(argv[i], "--latency-rise-ge") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            { int ok = 0; cmd->latency_rise_ge = parse_u64(argv[i], &ok);
              if (!ok) { fprintf(stderr, "Invalid threshold value: %s\n", argv[i]); return -1; } i++; }
        } else if (strcmp(argv[i], "--enable") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            {
                char *endptr = NULL;
                errno = 0;
                unsigned long val = strtoul(argv[i], &endptr, 0);
                if (errno != 0 || *endptr != '\0' || val > UINT8_MAX) {
                    fprintf(stderr, "Invalid enable value: %s\n", argv[i]);
                    return -1;
                }
                cmd->config_enabled = (uint8_t)val;
            }
            i++;
        } else if (strcmp(argv[i], "--flags") == 0) {
            if (++i >= argc) { usage(argv[0]); return -1; }
            {
                char *endptr = NULL;
                errno = 0;
                unsigned long val = strtoul(argv[i], &endptr, 0);
                if (errno != 0 || *endptr != '\0' || val > UINT32_MAX) {
                    fprintf(stderr, "Invalid flags value: %s\n", argv[i]);
                    return -1;
                }
                cmd->config_flags = (uint32_t)val;
            }
            i++;
        } else if (strcmp(argv[i], "--json") == 0) {
            cmd->json_output = 1;
            i++;
        } else {
            fprintf(stderr, "Unknown option: %s\n", argv[i]);
            usage(argv[0]);
            return -1;
        }
    }

    return 0;
}
