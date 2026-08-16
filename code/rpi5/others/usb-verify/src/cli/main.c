/*
 * ============================================================
 * main.c — fault-verify CLI 主入口
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 命令路由器，根据 fv_command_kind 分发到对应的
 *           子命令处理逻辑。
 *
 * 退出码语义（对应 error.h）:
 *   FV_OK          — 成功
 *   FV_ERR_ARGS    — 参数解析失败（usage 已打印）
 *   FV_ERR_DEVICE  — 设备节点打开失败
 *   FV_ERR_IOCTL   — ioctl/read 调用失败
 *   FV_ERR_TIMEOUT — 等待事件超时
 *   FV_ERR_CHECK   — 断言检查失败
 * ============================================================
 */
#include "parse.h"
#include "usbd_device.h"
#include "stats_check.h"
#include "event_check.h"
#include "output.h"
#include "error.h"

#include <errno.h>
#include <stdio.h>
#include <string.h>

/*
 * main — 命令路由器
 *
 * 流程:
 *   1) 解析参数 → 失败返回 FV_ERR_ARGS
 *   2) 打开设备 → 失败返回 FV_ERR_DEVICE
 *   3) 根据 cmd.kind 分发到对应 case
 *   4) 每个 case 执行 ioctl/check 并输出结果
 *   5) 关闭设备并返回退出码
 */
int main(int argc, char **argv)
{
    struct fv_command cmd;
    int rc = fv_parse_args(argc, argv, &cmd);
    if (rc != 0)
        return FV_ERR_ARGS;

    int fd = usbd_open(cmd.device_path);
    if (fd < 0) {
        fprintf(stderr, "Error: cannot open device '%s': %s\n",
                cmd.device_path, strerror(-fd));
        return FV_ERR_DEVICE;
    }

    int ret = FV_OK;

    switch (cmd.kind) {
    /* --- 统计命令 --- */
    case FV_CMD_STATS_GET: {
        struct vendor_lechao_usbd_stats stats;
        rc = usbd_get_stats(fd, &stats);
        if (rc < 0) {
            fprintf(stderr, "ioctl GET_STATS failed: %s\n", strerror(-rc));
            ret = FV_ERR_IOCTL;
            break;
        }
        output_stats(&stats, cmd.json_output);
        break;
    }
    case FV_CMD_STATS_RESET: {
        rc = usbd_reset_state(fd);
        if (rc < 0) {
            fprintf(stderr, "ioctl RESET_STATE failed: %s\n", strerror(-rc));
            ret = FV_ERR_IOCTL;
            break;
        }
        printf("State reset OK\n");
        break;
    }

    /* --- 配置命令 --- */
    case FV_CMD_CONFIG_GET: {
        struct vendor_lechao_usbd_config config;
        rc = usbd_get_config(fd, &config);
        if (rc < 0) {
            fprintf(stderr, "ioctl GET_CONFIG failed: %s\n", strerror(-rc));
            ret = FV_ERR_IOCTL;
            break;
        }
        output_config(&config, cmd.json_output);
        break;
    }
    case FV_CMD_CONFIG_SET: {
        struct vendor_lechao_usbd_config config;
        memset(&config, 0, sizeof(config));
        config.enabled = cmd.config_enabled;
        config.flags = cmd.config_flags;
        rc = usbd_set_config(fd, &config);
        if (rc < 0) {
            fprintf(stderr, "ioctl SET_CONFIG failed: %s\n", strerror(-rc));
            ret = FV_ERR_IOCTL;
            break;
        }
        printf("Config set OK\n");
        break;
    }

    /* --- 事件命令 --- */
    case FV_CMD_EVENT_READ: {
        struct vendor_lechao_usbd_event event;
        rc = usbd_read_event(fd, &event, cmd.timeout_ms);
        output_event(&event, cmd.json_output, rc);
        if (rc < 0)
            ret = (rc == -ETIMEDOUT) ? FV_ERR_TIMEOUT : FV_ERR_IOCTL;
        break;
    }
    case FV_CMD_EVENT_WAIT: {
        struct vendor_lechao_usbd_event matched;
        rc = fv_wait_for_event(fd, &cmd, &matched);
        if (rc < 0) {
            if (rc == -ETIMEDOUT) {
                fprintf(stderr, "Timeout waiting for event\n");
                ret = FV_ERR_TIMEOUT;
            } else {
                fprintf(stderr, "Event wait failed: %s\n", strerror(-rc));
                ret = FV_ERR_IOCTL;
            }
            break;
        }
        output_event(&matched, cmd.json_output, 0);
        break;
    }

    /* --- 断言检查命令 --- */
    case FV_CMD_CHECK_STATS: {
        struct vendor_lechao_usbd_stats stats;
        rc = usbd_get_stats(fd, &stats);
        if (rc < 0) {
            fprintf(stderr, "ioctl GET_STATS failed: %s\n", strerror(-rc));
            ret = FV_ERR_IOCTL;
            break;
        }
        struct fv_check_report report;
        rc = fv_check_stats(&stats, &cmd, &report);
        output_check_report(&report, cmd.json_output);
        if (rc < 0)
            ret = FV_ERR_CHECK;
        break;
    }
    case FV_CMD_CHECK_EVENT: {
        struct vendor_lechao_usbd_event matched;
        rc = fv_wait_for_event(fd, &cmd, &matched);
        if (rc < 0) {
            if (rc == -ETIMEDOUT) {
                fprintf(stderr, "Timeout waiting for event\n");
                ret = FV_ERR_TIMEOUT;
            } else {
                fprintf(stderr, "Event wait failed: %s\n", strerror(-rc));
                ret = FV_ERR_IOCTL;
            }
            break;
        }
        struct fv_check_report report;
        rc = fv_check_event(&matched, &cmd, &report);
        output_check_report(&report, cmd.json_output);
        if (rc < 0)
            ret = FV_ERR_CHECK;
        break;
    }
    case FV_CMD_CHECK_DEGRADE: {
        struct vendor_lechao_usbd_stats stats;
        rc = usbd_get_stats(fd, &stats);
        if (rc < 0) {
            fprintf(stderr, "ioctl GET_STATS failed: %s\n", strerror(-rc));
            ret = FV_ERR_IOCTL;
            break;
        }
        output_degrade_check(&stats, &cmd, cmd.json_output);
        break;
    }
    }

    usbd_close(fd);
    return ret;
}
