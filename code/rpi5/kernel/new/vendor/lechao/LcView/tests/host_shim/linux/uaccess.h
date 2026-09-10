// host_shim/linux/uaccess.h — LcView host 单测：copy_to_user shim（memcpy）
#pragma once
#include <string.h>
static inline int copy_to_user(void *to, const void *from, unsigned long n)
{
    memcpy(to, from, n);
    return 0;
}
