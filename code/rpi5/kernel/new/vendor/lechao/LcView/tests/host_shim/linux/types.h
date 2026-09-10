// host_shim/linux/types.h — LcView host 单测内核类型 shim
// 用途：方向 1 要求调用点真实代码（lcview_builder.c / lcview_ring.c）进入
// host 编译判红——这两个文件按内核头（lcview_internal.h 引用 linux 内核头）
// 编写，host 侧无内核头。本 shim 头提供编译所需的最小内核类型/宏映射，
// 让调用点在 host 下可编译执行（仅编译语义，不模拟内核并发/内存语义，
// 单测只验证纯逻辑判红路径）。
// 编译期以 -D__KERNEL__ 关闭 lcview_*.h 的用户态分支，-I tests/host_shim
// 拦截 <linux/...> 引用。仅 LcView/tests 构建使用，绝不进入内核编译。

#pragma once
#include <stdint.h>
#include <stdbool.h>
#include <stddef.h>

typedef uint8_t  u8;
typedef uint16_t u16;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int8_t   s8;
typedef int16_t  s16;
typedef int32_t  s32;
typedef int64_t  s64;
typedef unsigned int gfp_t;

#define __user
#define __kernel
#define __force
#define __iomem
