/*
 * ============================================================
 * parse.h — CLI 命令解析模型定义
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 定义 CLI 子命令枚举、命令参数结构体和解析函数。
 *
 * 命令模型:
 *   fv_command_kind — 9 种子命令（stats/config/event/check 系列）
 *   fv_command      — 解析后的命令参数，包含设备路径、超时、
 *                     断言阈值、事件期望值等
 *
 * 解析入口: fv_parse_args() — 将 argc/argv 转换为 fv_command
 * ============================================================
 */
#ifndef PARSE_H
#define PARSE_H

#include <stdint.h>

/*
 * enum fv_command_kind — CLI 子命令枚举
 * 每个值对应一个操作模式。
 */
enum fv_command_kind {
    FV_CMD_STATS_GET,    /* 获取设备统计快照 */
    FV_CMD_STATS_RESET,  /* 重置设备统计计数器 */
    FV_CMD_CONFIG_GET,   /* 获取设备运行时配置 */
    FV_CMD_CONFIG_SET,   /* 设置设备运行时配置 */
    FV_CMD_EVENT_READ,   /* 读取一条事件（非阻塞或带超时） */
    FV_CMD_EVENT_WAIT,   /* 等待匹配指定类型的事件 */
    FV_CMD_CHECK_STATS,  /* 断言统计阈值（如 stall_count >= N） */
    FV_CMD_CHECK_EVENT,  /* 等待并断言事件类型匹配 */
    FV_CMD_CHECK_DEGRADE,/* 断言降级指标（速率下降/延迟上升） */
};

/*
 * struct fv_command — 解析后的命令参数
 * 所有字段在 fv_parse_args() 中填充。
 */
struct fv_command {
    enum fv_command_kind kind; /* 子命令类型 */
    const char *device_path;   /* 设备节点路径，默认 "/dev/vendor_lechao_usbd" */
    int timeout_ms;            /* 超时时间（毫秒），默认 5000 */
    uint32_t expect_event_type;/* event wait/check 时期望的事件类型 */
    uint64_t stall_ge;         /* 断言: stall_count >= 此值 */
    uint64_t timeout_ge;       /* 断言: timeout_count >= 此值 */
    uint64_t corrupt_ge;       /* 断言: corrupt_count >= 此值 */
    uint64_t disconnect_ge;    /* 断言: disconnect_count >= 此值 */
    uint64_t probe_ge;         /* 断言: probe_count >= 此值 */
    uint64_t rate_drop_ge;     /* 断言: peak_rate - current_rate >= 此值 */
    uint64_t latency_rise_ge;  /* 断言: last_transport_latency_ns >= 此值 */
    int json_output;           /* 是否使用 JSON 格式输出：0=文本表格，1=JSON */
    uint8_t config_enabled;    /* config set 时的 enabled 值 */
    uint32_t config_flags;     /* config set 时的 flags 值 */
};

/* 打印使用帮助信息到 stderr */
void usage(const char *prog);

/*
 * fv_parse_args — 解析命令行参数
 * @argc, @argv: main() 的参数
 * @cmd: 输出参数，解析结果写入此结构体
 * 返回: 0 成功，-1 失败（已打印帮助信息）
 */
int fv_parse_args(int argc, char **argv, struct fv_command *cmd);

#endif
