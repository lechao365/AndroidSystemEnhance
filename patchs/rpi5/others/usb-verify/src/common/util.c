#include "util.h"
#include <time.h>

uint64_t fv_timespec_to_ms(const struct timespec *ts)
{
    return (uint64_t)ts->tv_sec * 1000UL + (uint64_t)ts->tv_nsec / 1000000UL;
}
