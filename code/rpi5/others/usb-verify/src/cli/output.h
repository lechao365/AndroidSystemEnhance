/*
 * ============================================================
 * output.h — CLI 输出格式化接口
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 提供统一的输出函数，支持文本表格和 JSON 两种格式。
 *
 * 输出函数:
 *   output_stats         — 输出统计快照
 *   output_config        — 输出配置
 *   output_event         — 输出事件（带错误处理）
 *   output_check_report  — 输出断言报告
 *   output_degrade_check — 输出降级检查报告（含计算逻辑）
 *
 * 格式选择: 通过 json 参数控制（0=文本表格，1=JSON）
 * ============================================================
 */
#ifndef OUTPUT_H
#define OUTPUT_H

#include "types.h"
#include "parse.h"

/* 输出统计快照，json=0 为文本表格，json=1 为 JSON */
int output_stats(const struct vendor_lechao_usbd_stats *stats, int json);

/* 输出配置 */
int output_config(const struct vendor_lechao_usbd_config *config, int json);

/*
 * 输出事件，带错误处理
 * @rc: usbd_read_event 的返回值，非 0 时输出错误信息
 */
int output_event(const struct vendor_lechao_usbd_event *event, int json, int rc);

/* 输出断言报告（通用格式） */
int output_check_report(const struct fv_check_report *report, int json);

/*
 * 输出降级检查报告（内置降级计算逻辑）
 * 此函数会重新计算降级指标并填充报告，再调用 output_check_report 输出。
 */
int output_degrade_check(const struct vendor_lechao_usbd_stats *stats,
                         const struct fv_command *cmd, int json);

#endif
