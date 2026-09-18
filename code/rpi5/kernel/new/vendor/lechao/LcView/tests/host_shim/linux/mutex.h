// host_shim/linux/mutex.h — LcView host 单测：互斥锁 shim（空操作）
// lcview_ring_read 的 read 串行化 mutex（方向 3）在 host 侧为无操作：
// host 单测单线程串行执行，无需真实互斥语义，仅满足编译。
// 仅 LcView/tests 构建使用，绝不进入内核编译。
#pragma once
struct mutex { int unused; };
#define mutex_init(m) do { (void)(m); } while (0)
#define mutex_lock(m) do { (void)(m); } while (0)
#define mutex_unlock(m) do { (void)(m); } while (0)
