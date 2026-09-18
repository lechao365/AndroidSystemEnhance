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

#ifndef LCVIEW_IOCTL_H
#define LCVIEW_IOCTL_H

#include <stdint.h>
#include <sys/ioctl.h>

/* ioctl 魔数，用于生成唯一命令号（与内核 LCVIEW_IOC_MAGIC 一致） */
#define LCVIEW_IOC_MAGIC  'V'

/*
 * 查询环形缓冲区中当前可读字节数
 * 用户态传入 uint32_t*，内核填入可用字节数
 */
#define LCVIEW_GET_AVAIL_BYTES  _IOR(LCVIEW_IOC_MAGIC, 1, uint32_t)

/*
 * 查询并清零溢出计数
 * 读完后内核自动将 overrun_cnt 重置为 0，实现"边读边清"语义
 */
#define LCVIEW_GET_OVERRUN      _IOR(LCVIEW_IOC_MAGIC, 2, uint32_t)

/*
 * 内核 ring 统计结构（与内核 lcview_internal.h 的 struct lcview_stats
 * 逐字段一致；total_records/overrun_cnt 为内核自初始化起的累计计数，
 * 只读不清零，与 GET_OVERRUN 的"读取即清零"语义互补支撑守恒校验）
 */
struct lcview_stats {
    uint32_t total_records;
    uint32_t overrun_cnt;
    uint32_t dropped_cnt;
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

/* struct 尺寸守卫：与内核镜像（lcview_internal.h）漂移即编译期报错 */
static_assert(sizeof(struct lcview_stats) == 20,
              "lcview_stats must be 20 bytes (kernel mirror drift)");

#endif /* LCVIEW_IOCTL_H */
