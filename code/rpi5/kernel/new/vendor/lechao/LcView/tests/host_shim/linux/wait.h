// host_shim/linux/wait.h — LcView host 单测：等待队列 shim（空操作）
#pragma once
typedef struct { int unused; } wait_queue_head_t;
typedef struct { int unused; } wait_queue_entry_t;
#define init_waitqueue_head(w) do {} while (0)
#define wake_up_interruptible(w) do {} while (0)
#define wake_up(w) do {} while (0)
#define wait_event_interruptible(w, c) ({ int __r = 0; (void)(c); __r; })
