/*
 * ============================================================
 * error.h — fault-verify 错误码定义
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 统一的退出码枚举，main.c 根据 ioctl/parse/check
 *           阶段的错误返回对应的 FV_ERR_* 值。
 *
 * 错误码语义:
 *   FV_OK          — 成功
 *   FV_ERR_ARGS    — 命令行参数解析失败
 *   FV_ERR_DEVICE  — 无法打开设备节点
 *   FV_ERR_IOCTL   — ioctl/read 系统调用失败
 *   FV_ERR_TIMEOUT — poll 超时（等待事件未到达）
 *   FV_ERR_CHECK   — 断言检查失败（如 stall_count 低于阈值）
 *
 * 这些值作为进程退出码，shell 可通过 $? 判断。
 * ============================================================
 */
#ifndef FV_ERROR_H
#define FV_ERROR_H

#define FV_OK             0  /* 成功 */
#define FV_ERR_ARGS       1  /* 命令行参数解析失败 */
#define FV_ERR_DEVICE     2  /* 设备节点打开失败 */
#define FV_ERR_IOCTL      3  /* ioctl/read 调用失败 */
#define FV_ERR_TIMEOUT    4  /* 等待事件超时 */
#define FV_ERR_CHECK      5  /* 断言检查失败 */

#endif
