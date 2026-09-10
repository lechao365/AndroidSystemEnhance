// host_shim/linux/timekeeping.h — LcView host 单测：时间戳 shim（返回 0）
#pragma once
#include <stdint.h>
static inline uint64_t ktime_get_real_ns(void) { return 0; }
static inline uint64_t ktime_get_ns(void) { return 0; }
