// host_shim/linux/atomic.h — LcView host 单测：原子量 shim（非并发直访）
#pragma once
#include <stdint.h>
typedef struct { int counter; } atomic_t;
typedef struct
{
    long long counter;
} atomic64_t;
#define ATOMIC_INIT(i) { (i) }
#define ATOMIC64_INIT(i)                                                                           \
    {                                                                                              \
        (i)                                                                                        \
    }
static inline void atomic_set(atomic_t *v, int i) { v->counter = i; }
static inline int atomic_read(const atomic_t *v) { return v->counter; }
static inline void atomic_inc(atomic_t *v) { v->counter++; }
static inline void atomic_dec(atomic_t *v) { v->counter--; }
static inline int atomic_xchg(atomic_t *v, int i) { int o = v->counter; v->counter = i; return o; }
static inline int atomic_dec_and_test(atomic_t *v) { return --v->counter == 0; }
/* R-13 方向 3：统计计数升 atomic64_t，host 单测 shim 同步（非并发直访） */
static inline void atomic64_set(atomic64_t *v, long long i) { v->counter = i; }
static inline long long atomic64_read(const atomic64_t *v) { return v->counter; }
static inline void atomic64_inc(atomic64_t *v) { v->counter++; }
static inline void atomic64_add(long long i, atomic64_t *v) { v->counter += i; }
static inline long long atomic64_xchg(atomic64_t *v, long long i)
{
    long long o = v->counter;
    v->counter = i;
    return o;
}
static inline long long atomic64_inc_return(atomic64_t *v) { return ++v->counter; }
