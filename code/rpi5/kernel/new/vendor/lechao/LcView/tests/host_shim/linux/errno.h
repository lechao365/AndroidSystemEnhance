// host_shim/linux/errno.h — LcView host 单测：内核 errno shim（转发 libc + 补齐）
#pragma once
#include <errno.h>
#ifndef ENOSPC
#define ENOSPC 28
#endif
#ifndef EINVAL
#define EINVAL 22
#endif
#ifndef ENOMEM
#define ENOMEM 12
#endif
#ifndef EFAULT
#define EFAULT 14
#endif
#ifndef EMSGSIZE
#define EMSGSIZE 90
#endif
#ifndef ESHUTDOWN
#define ESHUTDOWN 108
#endif
#ifndef ERESTARTSYS
#define ERESTARTSYS 512
#endif
