/*
 * ============================================================
 * types.h — fault-verify 通用数据结构定义
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 定义断言检查结果的通用数据结构，
 *           被 stats_check 和 event_check 共用。
 *
 * 数据模型:
 *   fv_check_entry  — 单条断言结果（字段名/实际值/期望值/是否通过）
 *   fv_check_report — 整体断言报告（多条 entry 的容器）
 *
 * 容量: FV_MAX_CHECK_ENTRIES=16，超过此数的断言会被忽略
 * ============================================================
 */
#ifndef FV_TYPES_H
#define FV_TYPES_H

#include "fv_ioctl_compat.h"

/* 单条断言的最大数量，超过会被忽略 */
#define FV_MAX_CHECK_ENTRIES 16

/*
 * struct fv_check_entry — 单条断言检查结果
 * 用于记录一次"实际值 vs 期望值"的比较。
 */
struct fv_check_entry {
    const char *field_name; /* 断言字段名（如 "stall_count"），用于输出 */
    uint64_t actual;        /* 实际值（来自内核 stats） */
    uint64_t expected;      /* 期望阈值（来自命令行参数） */
    int passed;             /* 是否通过：1=通过，0=失败 */
};

/*
 * struct fv_check_report — 断言报告
 * 一组 fv_check_entry 的容器，汇总断言结果。
 */
struct fv_check_report {
    struct fv_check_entry entries[FV_MAX_CHECK_ENTRIES]; /* 断言结果数组 */
    int count;   /* 已记录的断言数量 */
    int failed;  /* 失败的断言数量；0 表示全部通过 */
};

#endif
