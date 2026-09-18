// host_shim/linux/mutex.h — LcView host 单测：互斥锁 shim（带锁态断言）
// host 单测单线程串行执行，无需真实互斥语义，但须捕获锁协议违例：
// lcview_ring_read 用 read_mutex 串行化读调用，任何出口漏 unlock / 重复
// lock / 未初始化即用都会在 host 侧以断言失败判红（方向 4）。
// 栈上手工构造的 struct lcview_ring 必须显式 mutex_init，否则 lock 断言
// 失败（栈垃圾 locked 值）——据此强制栈 ring 构造补 mutex_init。
// 仅 LcView/tests 构建使用，绝不进入内核编译。
#pragma once
#include <stdio.h>
#include <stdlib.h>

struct mutex {
    int locked;     /* 1 = 已持有，0 = 空闲 */
    int init_done;  /* 1 = 已 mutex_init，0 = 未初始化 */
};

#define mutex_init(m) do { \
    (m)->init_done = 1; \
    (m)->locked = 0; \
} while (0)

#define mutex_lock(m) do { \
    if (!(m)->init_done) { \
        fprintf(stderr, "FAIL mutex_lock: uninitialized mutex (%s:%d)\n", \
                __FILE__, __LINE__); \
        abort(); \
    } \
    if ((m)->locked) { \
        fprintf(stderr, "FAIL mutex_lock: double lock (%s:%d)\n", \
                __FILE__, __LINE__); \
        abort(); \
    } \
    (m)->locked = 1; \
} while (0)

#define mutex_unlock(m) do { \
    if (!(m)->init_done || !(m)->locked) { \
        fprintf(stderr, "FAIL mutex_unlock: unlock without lock (%s:%d)\n", \
                __FILE__, __LINE__); \
        abort(); \
    } \
    (m)->locked = 0; \
} while (0)
