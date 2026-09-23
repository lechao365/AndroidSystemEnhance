// host_shim/linux/kernel.h — LcView host 单测内核杂项 shim
// 最小日志/内存/errno 宏映射（仅 host 单测编译语义，绝不进入内核编译）。

#pragma once
#include "types.h"
#include <errno.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>

#define pr_info(...) do {} while (0)
#define pr_err(...) do {} while (0)
#define pr_warn(...) do {} while (0)
#define pr_debug(...) do {} while (0)
#define pr_err_ratelimited(...) do {} while (0)
#define pr_warn_ratelimited(...) do {} while (0)
#define pr_debug_ratelimited(...) do {} while (0)
#define BUILD_BUG_ON(x) do {} while (0)
#define ARRAY_SIZE(x) (sizeof(x) / sizeof((x)[0]))

#define GFP_ATOMIC 0
#define GFP_KERNEL 0

static inline void *kmalloc(size_t s, gfp_t g) { (void)g; return malloc(s); }
static inline void *kzalloc(size_t s, gfp_t g) { (void)g; void *p = malloc(s); if (p) memset(p, 0, s); return p; }
static inline void kfree(void *p) { free(p); }

#ifndef ENOSPC
#define ENOSPC 28
#endif
#ifndef EINVAL
#define EINVAL 22
#endif
#ifndef ENOMEM
#define ENOMEM 12
#endif
#ifndef EFAULT
#define EFAULT 14
#endif
#ifndef EMSGSIZE
#define EMSGSIZE 90
#endif
#ifndef ESHUTDOWN
#define ESHUTDOWN 108
#endif
#ifndef ERESTARTSYS
#define ERESTARTSYS 512
#endif

// xchg：builder 空闲池用，host 侧原子交换（单线程测试语义等价）
static inline void *xchg(void *ptr, void *val)
{
    return (void *)__atomic_exchange_n((uintptr_t *)ptr, (uintptr_t)val,
                                       __ATOMIC_SEQ_CST);
}

// cmpxchg：builder 空闲池用，host 侧原子比较交换——仅当 *ptr == old 时
// 写入 new_，返回旧值（成功时 == old）。与内核 cmpxchg 语义一致：
// builder_pool_put 以 cmpxchg(slot, NULL, b) 实现"仅槽空才存入"，
// 并发 get 在交换窗口内取走 b 后此处不可能再释放已取走的对象。
static inline void *cmpxchg(void *ptr, void *old, void *new_)
{
    void *cur = old;
    __atomic_compare_exchange_n((uintptr_t *)ptr, (uintptr_t *)&cur, (uintptr_t)new_, 0,
                                __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    return cur;
}
