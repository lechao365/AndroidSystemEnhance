#ifndef FV_UTIL_H
#define FV_UTIL_H

#include <stdint.h>
#include <time.h>

uint64_t fv_timespec_to_ms(const struct timespec *ts);

#endif
