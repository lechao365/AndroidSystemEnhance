/*
 * kernel_lechao_log.h — Lechao 内核模块日志标签定义
 *
 * 本文件定义 Lechao vendor 内核模块的 pr_info/pr_err 日志前缀标签。
 * 所有 Lechao 内核子系统共享此头文件，确保日志输出格式统一，
 * 便于 dmesg 过滤和自动化日志分析工具按标签归类。
 *
 * 使用方式：
 *   #define PREFIX KERNEL_LCVIEW_TAG ": "
 *   pr_info(PREFIX "initialized\n");
 *
 * 当前定义的子系统标签：
 *   KERNEL_USB_TAG    — Lechao USB 功能模块
 *   KERNEL_LCVIEW_TAG — LcView 日志子系统
 */

#ifndef KERNEL_LECHAO_LOG_H
#define KERNEL_LECHAO_LOG_H

/* USB 功能模块日志前缀 */
#define KERNEL_USB_TAG     "kernel_lechao_usb"

/* LcView 结构化事件日志子系统日志前缀 */
#define KERNEL_LCVIEW_TAG  "kernel_lechao_lcview"

#endif /* KERNEL_LECHAO_LOG_H */
