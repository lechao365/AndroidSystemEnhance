// host_shim/linux/atomic.h — LcView host 单测：原子量 shim（非并发直访）
#pragma once
#include <stdint.h>
typedef struct { int counter; } atomic_t;
#define ATOMIC_INIT(i) { (i) }
static inline void atomic_set(atomic_t *v, int i) { v->counter = i; }
static inline int atomic_read(const atomic_t *v) { return v->counter; }
static inline void atomic_inc(atomic_t *v) { v->counter++; }
static inline void atomic_dec(atomic_t *v) { v->counter--; }
static inline int atomic_xchg(atomic_t *v, int i) { int o = v->counter; v->counter = i; return o; }
