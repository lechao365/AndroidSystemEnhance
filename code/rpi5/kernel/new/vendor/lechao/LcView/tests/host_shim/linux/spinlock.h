// host_shim/linux/spinlock.h — LcView host 单测：自旋锁 shim（空操作）
// flags 在 lock 写入、unlock 读取，避免 -Werror=unused-but-set-variable。
#pragma once
typedef int spinlock_t;
#define spin_lock_init(l) do { *(l) = 0; } while (0)
#define spin_lock(l) do {} while (0)
#define spin_unlock(l) do {} while (0)
#define spin_lock_irqsave(l, f) do { (f) = 1; } while (0)
#define spin_unlock_irqrestore(l, f) do { (void)(f); } while (0)
#define spin_lock_irq(l) do {} while (0)
#define spin_unlock_irq(l) do {} while (0)
