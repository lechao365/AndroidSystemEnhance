/*
 * ============================================================
 * vendor_lechao_usbd-ioctl.h — 用户态-内核态共享的 IOCTL 接口定义
 * ============================================================
 *
 * 此头文件定义了内核驱动 vendor_lechao_usbd 与用户态程序之间的
 * 二进制 ABI（Application Binary Interface）。三方必须保持严格一致：
 *   1) 内核驱动 — 实现 ioctl 处理和 miscdevice read/poll
 *   2) AOSP HAL 进程 (lechao_lciod_hal) — 通过 ioctl/read 与驱动交互
 *   3) 用户态测试工具 (rpi5-usb-verify) — 通过 ioctl/read 验证驱动行为
 *
 * 任何字段顺序、大小、对齐的变更都可能导致用户态程序读取错误数据。
 * 因此修改此文件后，所有使用方必须重新编译。
 *
 * 【ABI 真相源】
 *   内核源码树 vendor/lechao/LcIod/lciod_usbd-ioctl.h 是唯一真相源。
 *   本文件是 usb-verify 工具的镜像副本，必须与真相源 1:1 同步。
 *   - v1：初始版本
 *   - v2：stats 末尾追加 event_drop_count
 */

#ifndef _VENDOR_LECHAO_USBD_IOCTL_H
#define _VENDOR_LECHAO_USBD_IOCTL_H

#include <linux/types.h>

/* ABI 版本号：必须与内核真相源保持一致 */
#define VENDOR_LECHAO_USBD_ABI_VERSION  2

#ifdef __KERNEL__
/* 内核态：u8/u16/u32/u64/s32/s64 由 linux/types.h 直接提供 */
#elif !defined(U8_ALREADY_TYPEDEF)
/* 用户态：需要显式 typedef，因为 <linux/types.h> 只导出 __u8 等 */
typedef __u8  u8;
typedef __u16 u16;
typedef __u32 u32;
typedef __u64 u64;
typedef __s8  s8;
typedef __s16 s16;
typedef __s32 s32;
typedef __s64 s64;
#define U8_ALREADY_TYPEDEF
#endif

/*
 * struct vendor_lechao_usbd_stats — 单设备粒度的传输统计快照
 *
 * 通过 VENDOR_LECHAO_USBD_IOC_GET_STATS ioctl 获取。
 * GET_STATS 是纯读取接口，不会重置任何计数器；
 * 重置需通过独立的 VENDOR_LECHAO_USBD_IOC_RESET_STATE 触发。
 *
 * 所有 u64 计数器从设备 probe 后开始累积，RESET_STATE 后归零。
 */
struct vendor_lechao_usbd_stats {
	/* --- 读方向统计 --- */
	u64 read_bytes;      /* 累计读取字节数 */
	u64 write_bytes;     /* 累计写入字节数 */
	u64 read_ns;         /* 累计读取耗时（纳秒），用于计算平均读速率 */
	u64 write_ns;        /* 累计写入耗时（纳秒），用于计算平均写速率 */
	u64 read_cmds;       /* 累计读请求（URB）数量 */
	u64 write_cmds;      /* 累计写请求（URB）数量 */

	/* --- 错误/异常计数器 --- */
	u64 error_count;     /* 传输错误总次数（包括 stall/timeout/corrupt 等） */
	u64 reset_count;     /* USB 设备复位次数 */
	u64 probe_count;     /* 设备探测（接入）次数 */
	u64 disconnect_count;/* 设备断开次数 */
	u64 degrade_count;   /* 速率降级事件次数 */

	/* --- 性能指标 --- */
	u64 current_rate;    /* 当前传输速率（字节/秒），由内核实时计算 */
	u64 peak_rate;       /* 历史峰值速率（字节/秒） */
	u64 last_transport_latency_ns; /* 最近一次传输的端到端延迟（纳秒） */
	u64 last_event_ts_ns;/* 最近一次事件发生的单调时钟时间戳（纳秒） */
	s64 last_update;     /* 最近一次统计更新的时间（纳秒），有符号 */

	/* --- 设备标识 --- */
	u16 vid;             /* USB 厂商 ID */
	u16 pid;             /* USB 产品 ID */
	char vendor[32];     /* 厂商名称字符串，以 '\0' 结尾 */
	char product[32];    /* 产品名称字符串，以 '\0' 结尾 */

	/* --- 扩展错误计数器 --- */
	u64 stall_count;     /* USB 端点停滞（STALL）次数 */
	u64 corrupt_count;   /* 数据完整性校验失败次数 */
	u64 timeout_count;   /* 传输超时次数 */

	/* --- 状态字段 --- */
	u32 last_event_type; /* 最近一次事件类型，见 enum vendor_lechao_usbd_event_type */
	u8  enabled;         /* 设备监控/统计功能启用标志：0=禁用，1=启用 */
	u8  reserved[3];     /* 保留，保证 4 字节对齐 */
	u32 flags;           /* 配置标志位，保留给内核扩展使用 */
	u64 event_drop_count;/* 累计：环形缓冲区溢出丢弃的事件数 */
};

/*
 * struct vendor_lechao_usbd_config — 运行时配置（ioctl GET/SET_CONFIG）
 *
 * 通过 VENDOR_LECHAO_USBD_IOC_GET_CONFIG / SET_CONFIG 读写。
 * 用于在运行时启用/禁用监控功能或设置内核行为标志。
 */
struct vendor_lechao_usbd_config {
	u8  enabled;      /* 监控功能启用标志：0=禁用，1=启用 */
	u8  reserved[3];  /* 保留，保证 4 字节对齐 */
	u32 flags;        /* 配置标志位，保留给内核扩展使用 */
};

/*
 * 内核事件环形缓冲区大小
 * 当缓冲区满时，最旧的事件会被覆盖（覆盖写策略）。
 * 用户态通过 read() 系统调用逐条读取。
 */
#define VENDOR_LECHAO_USBD_EVENT_BUF_SIZE  32

/*
 * enum vendor_lechao_usbd_event_type — 异步事件类型枚举
 *
 * 每种事件类型对应一种 USB 子系统异常或状态变化：
 *   NONE            — 无事件（占位）
 *   TRANSPORT_ERROR — 批量传输失败（URB 返回错误状态）
 *   STALL           — USB 端点返回 STALL 握手
 *   DATA_CORRUPT    — 传输数据 CRC/完整性校验失败
 *   TIMEOUT         — 传输在规定时间内未完成
 *   RESET           — USB 设备发生总线复位
 *   RATE_DEGRADED   — 传输速率显著下降
 */
enum vendor_lechao_usbd_event_type {
	VENDOR_LECHAO_USBD_EVENT_NONE            = 0,
	VENDOR_LECHAO_USBD_EVENT_TRANSPORT_ERROR = 1,
	VENDOR_LECHAO_USBD_EVENT_STALL           = 2,
	VENDOR_LECHAO_USBD_EVENT_DATA_CORRUPT    = 3,
	VENDOR_LECHAO_USBD_EVENT_TIMEOUT         = 4,
	VENDOR_LECHAO_USBD_EVENT_RESET           = 5,
	VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED   = 6,
};

/*
 * struct vendor_lechao_usbd_event — 单条异步事件记录
 *
 * 当 USB 子系统发生异常时，内核将事件写入环形缓冲区。
 * 用户态通过 poll() 等待可读，然后 read() 读取。
 * 每次 read() 返回一条事件（sizeof(struct vendor_lechao_usbd_event) 字节）。
 */
struct vendor_lechao_usbd_event {
	u64 timestamp_ns;  /* 事件发生时的内核单调时钟时间戳（纳秒） */
	u32 event_type;    /* 事件类型，见 enum vendor_lechao_usbd_event_type */
	u32 event_value;   /* 事件附加数值（语义取决于 event_type） */
	s32 status;        /* 事件状态码：0=成功，负值=内核 errno */
	u8  data_direction; /* 数据传输方向：0=NONE, 1=READ, 2=WRITE */
	u8  valid;         /* 事件有效标志：1=有效，0=无效/占位 */
	u8  reserved[2];   /* 保留，保证结构体对齐 */
};

/* --- IOCTL 命令定义 --- */
/*
 * Magic number: 'R' (0x52)
 * 使用 _IOR/_IOW/_IO 宏编码方向、类型和数据大小。
 * 内核驱动通过 miscdevice.fops->unlocked_ioctl 分发。
 */
#define VENDOR_LECHAO_USBD_IOC_MAGIC        'R'

/* 获取设备传输统计快照（只读，不重置计数器） */
#define VENDOR_LECHAO_USBD_IOC_GET_STATS    _IOR(VENDOR_LECHAO_USBD_IOC_MAGIC, 0, struct vendor_lechao_usbd_stats)

/* 重置设备的所有统计计数器为 0 */
#define VENDOR_LECHAO_USBD_IOC_RESET_STATE  _IO(VENDOR_LECHAO_USBD_IOC_MAGIC, 1)

/* 获取设备运行时配置 */
#define VENDOR_LECHAO_USBD_IOC_GET_CONFIG   _IOR(VENDOR_LECHAO_USBD_IOC_MAGIC, 2, struct vendor_lechao_usbd_config)

/* 设置设备运行时配置 */
#define VENDOR_LECHAO_USBD_IOC_SET_CONFIG   _IOW(VENDOR_LECHAO_USBD_IOC_MAGIC, 3, struct vendor_lechao_usbd_config)

#endif /* _VENDOR_LECHAO_USBD_IOCTL_H */
