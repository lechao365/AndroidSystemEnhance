/*
 * lcview_ioctl.h — LcView 字符设备 ioctl 命令码用户态镜像
 *
 * 所属模块：LcView 事件日志系统 — 用户态共享头
 *
 * 镜像源（真相源，禁单侧改，须两侧同步）：
 *   - ioctl 魔数与命令号：内核
 *     code/rpi5/kernel/new/vendor/lechao/LcView/lcview_ioctl.h
 *   - struct lcview_stats 布局：内核
 *     code/rpi5/kernel/new/vendor/lechao/LcView/lcview_internal.h
 *
 * 同步纪律：内核侧改动命令号/结构体布局时，必须同步本镜像，禁止单侧
 * 修改——命令号漂移会导致 ioctl 失败/错配（DeviceReader mIoctlErr 计数
 * 进心跳可见，LCV-16），struct 布局漂移会致统计字段错读。本文件底部
 * static_assert 在编译期守住 struct 尺寸，命令号靠两侧对照评审。
 *
 * 使用：daemon/DeviceReader 经该头获取 ioctl 定义，不再本地重复拷贝。
 */

#ifndef LCVIEW_DAEMON_IOCTL_H
#define LCVIEW_DAEMON_IOCTL_H

#include <stddef.h>
#include <stdint.h>
#include <sys/ioctl.h>

/* ioctl 魔数，用于生成唯一命令号（与内核 LCVIEW_IOC_MAGIC 一致） */
#define LCVIEW_IOC_MAGIC  'V'

/*
 * 【ABI 版本（R-13 方向 1，UAPI 世代重建）】
 * 镜像源：内核 lcview_ioctl.h 的 LCVIEW_ABI_VERSION。每次 ABI 变更
 * （ioctl 命令号/载荷类型/事件 hdr 布局/struct lcview_stats 字段）必须
 * 递增版本号并双侧同步。daemon 启动经 LCVIEW_GET_ABI_VERSION ioctl 协商，
 * 不匹配（旧内核返 ENOTTY 或版本号低）显式退出判红，禁止静默降级。
 */
#define LCVIEW_ABI_VERSION  2

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
#define LCVIEW_GET_OVERRUN      _IOR(LCVIEW_IOC_MAGIC, 2, uint64_t)

/*
 * 内核 ring 统计结构（与内核 lcview_internal.h 的 struct lcview_stats
 * 逐字段一致；total_records/overrun_cnt 为内核自初始化起的累计计数，
 * 只读不清零，与 GET_OVERRUN 的"读取即清零"语义互补支撑守恒校验）。
 * R-13 方向 3：统计三字段升 u64（ring_usage/size 仍 u32，字节数最大 4MB）。
 */
struct lcview_stats {
    uint64_t total_records;
    uint64_t overrun_cnt;
    uint64_t dropped_cnt;
    uint32_t ring_usage_bytes;
    uint32_t ring_size_bytes;
};

/*
 * 查询完整统计信息（记录总数、溢出数、环形缓冲区大小与使用量）
 * 承载 getTotalRecords（心跳守恒校验数据源）与启动诊断快照
 */
#define LCVIEW_GET_STATS        _IOR(LCVIEW_IOC_MAGIC, 3, struct lcview_stats)

/*
 * 设置最低日志等级
 * 传入 uint8_t 级别值 (LCVIEW_LEVEL_*)，低于此级别的事件被丢弃
 */
#define LCVIEW_SET_LEVEL        _IOW(LCVIEW_IOC_MAGIC, 4, uint8_t)

/*
 * 查询当前 ABI 版本（R-13 方向 1）
 * 用户态传入 uint32_t*，内核填入 LCVIEW_ABI_VERSION。
 * 旧内核未实现本命令时 ioctl 返 -ENOTTY——daemon 启动协商即判红。
 */
#define LCVIEW_GET_ABI_VERSION  _IOR(LCVIEW_IOC_MAGIC, 5, uint32_t)

/* struct 尺寸守卫：与内核镜像（lcview_internal.h）漂移即编译期报错
 * 逐字段 offsetof 断言（方向 3）：不仅守总尺寸，还逐字段校验偏移与内核
 * lcview_internal.h 的 struct lcview_stats 一致——仅 sizeof 相等挡不住
 * 字段顺序/类型互换（同 20B 不同布局），offsetof 逐字段钉死对齐。
 * R-13 方向 3：前三字段升 u64 后偏移 0/8/16，后两字段 24/28。 */
static_assert(offsetof(struct lcview_stats, total_records) == 0,
              "lcview_stats.total_records offset drift");
static_assert(offsetof(struct lcview_stats, overrun_cnt) == 8,
              "lcview_stats.overrun_cnt offset drift");
static_assert(offsetof(struct lcview_stats, dropped_cnt) == 16,
              "lcview_stats.dropped_cnt offset drift");
static_assert(offsetof(struct lcview_stats, ring_usage_bytes) == 24,
              "lcview_stats.ring_usage_bytes offset drift");
static_assert(offsetof(struct lcview_stats, ring_size_bytes) == 28,
              "lcview_stats.ring_size_bytes offset drift");
static_assert(sizeof(struct lcview_stats) == 32,
              "lcview_stats must be 32 bytes (kernel mirror drift)");

#endif /* LCVIEW_DAEMON_IOCTL_H */
