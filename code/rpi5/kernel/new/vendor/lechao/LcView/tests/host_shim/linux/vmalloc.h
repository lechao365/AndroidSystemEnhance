// host_shim/linux/vmalloc.h — LcView host 单测：vmalloc/vfree shim（malloc/free）
#pragma once
#include <stdlib.h>
static inline void *vmalloc(unsigned long s) { return malloc(s); }
static inline void vfree(void *p) { free(p); }
