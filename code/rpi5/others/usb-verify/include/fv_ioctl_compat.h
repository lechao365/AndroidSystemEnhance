/*
 * ============================================================
 * fv_ioctl_compat.h — 用户态类型兼容层
 * 所属模块: rpi5-usb-verify (fault-verify)
 * 设计目的: 在用户态工具中模拟内核 __u8/__u16/__u32/__u64/__s8/...
 *           类型定义。内核头文件 <linux/types.h> 提供这些类型，
 *           但用户态应用应使用 <stdint.h>，此头文件做一层 typedef 桥接。
 *
 * 工作原理:
 *   1) 使用 <stdint.h> 的 uint8_t/.../int64_t 类型
 *   2) typedef 为 u8/u16/u32/u64/s8/s16/s32/s64
 *   3) 然后 #include "vendor_lechao_usbd-ioctl.h"
 *   4) ioctl 头文件中的 struct 字段使用 u8/u64 等类型名
 *
 * 这样保证了：
 *   - 用户态和内核态使用相同的类型名（u8/u64 等）
 *   - 二进制布局一致（sizeof/alignment 完全相同）
 *   - 编译器不同（host gcc vs kernel gcc）也能正确编译
 * ============================================================
 */
#ifndef FV_IOCTL_COMPAT_H
#define FV_IOCTL_COMPAT_H

#include <stdint.h>

/* 无符号整型别名，对应内核 __u8/__u16/__u32/__u64 */
typedef uint8_t  u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;

/* 有符号整型别名，对应内核 __s8/__s16/__s32/__s64 */
typedef int8_t   s8;
typedef int16_t  s16;
typedef int32_t  s32;
typedef int64_t  s64;

/* 告知 ioctl 头文件：用户态类型已定义，无需重复 typedef */
#define U8_ALREADY_TYPEDEF

/* 引入共享 ioctl 头文件 */
#include "vendor_lechao_usbd-ioctl.h"

#endif
