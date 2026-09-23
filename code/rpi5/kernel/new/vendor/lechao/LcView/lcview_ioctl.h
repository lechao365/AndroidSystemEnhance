/*
 * lcview_ioctl.h — LcView 字符设备 ioctl 命令码定义
 *
 * 本文件定义用户态通过 /dev/vendor_lechao_lcview 下发控制命令的 ioctl 接口。
 * 采用标准 Linux _IOR/_IOW 宏生成命令码，确保与内核 ioctl 子系统兼容。
 *
 * 命令分类：
 *   GET_*   — 查询状态（只读，IOR），用户态分配缓冲区、内核填充
 *   SET_*   — 配置（只写，IOW），用户态传入参数、内核读取
 *
 * 魔数 'V' (LCVIEW_IOC_MAGIC) 用于 ioctl 号命名空间隔离，
 * 避免与内核中其他驱动命令码冲突。
 */

#ifndef LCVIEW_IOCTL_H
#define LCVIEW_IOCTL_H

#include <linux/ioctl.h>

/* ioctl 魔数，用于生成唯一命令号 */
#define LCVIEW_IOC_MAGIC  'V'

/*
 * 【ABI 版本（R-13 方向 1，UAPI 世代重建）】
 * 本头文件为内核-用户态共享 ABI 的真相源。AOSP daemon（lechao_lcview）的
 * 镜像副本（include/lcview_ioctl.h）必须与本文件保持同步。每次 ABI 变更
 * （ioctl 命令号/载荷类型/事件 hdr 布局/struct lcview_stats 字段）必须递增
 * 本版本号并双侧整体重编。
 * daemon 启动时经 LCVIEW_GET_ABI_VERSION ioctl 协商：不匹配（旧内核缺命令
 * 返 ENOTTY，或版本号低于预期）则显式退出判红，禁止静默降级运行。
 */
#define LCVIEW_ABI_VERSION 2

/*
 * 查询环形缓冲区中当前可读字节数
 * 用户态传入 uint32_t*，内核填入可用字节数
 */
#define LCVIEW_GET_AVAIL_BYTES  _IOR(LCVIEW_IOC_MAGIC, 1, uint32_t)

/*
 * 查询并清零溢出计数
 * 读完后内核自动将 overrun_cnt 重置为 0，实现"边读边清"语义
 * 载荷 uint64_t（R-13 方向 3：计数升 atomic64_t 消 uptime 回绕）
 */
#define LCVIEW_GET_OVERRUN _IOR(LCVIEW_IOC_MAGIC, 2, uint64_t)

/*
 * 查询当前 ABI 版本（R-13 方向 1）
 * 用户态传入 uint32_t*，内核填入 LCVIEW_ABI_VERSION。
 * 旧内核未实现本命令时 ioctl 返 -ENOTTY——daemon 启动协商即判红，
 * 消"新用户态 + 旧内核"的静默降级/字段错读（与 GET_STATS 同判据面）。
 */
#define LCVIEW_GET_ABI_VERSION _IOR(LCVIEW_IOC_MAGIC, 5, uint32_t)

/*
 * 查询完整统计信息（记录总数、溢出数、环形缓冲区大小与使用量）
 * 用于监控面板或调试工具做健康检查
 */
#define LCVIEW_GET_STATS        _IOR(LCVIEW_IOC_MAGIC, 3, struct lcview_stats)

/*
 * 设置最低日志等级
 * 传入 uint8_t 级别值 (LCVIEW_LEVEL_*)，低于此级别的事件被丢弃
 * 默认值为 LCVIEW_LEVEL_DEBUG（不过滤任何级别）
 */
#define LCVIEW_SET_LEVEL        _IOW(LCVIEW_IOC_MAGIC, 4, uint8_t)

#endif /* LCVIEW_IOCTL_H */
