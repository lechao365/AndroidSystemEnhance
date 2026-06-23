/*
 * ============================================================
 * vendor_lechao_usbd-ioctl.h — 用户态-内核态共享的 IOCTL 接口定义
 * ============================================================
 *
 * 【文件用途】
 *   定义 USB 存储速率监控驱动 (vendor_lechao_usbd) 与用户态之间的
 *   ABI 接口，包括统计结构体、配置结构体、事件类型枚举、ioctl 命令码。
 *   用户态通过 /dev/vendor_lechao_usbdN 字符设备使用这些接口。
 *
 * 【所属模块】
 *   Lechao vendor USB 存储监控子系统 (VENDOR_LECHAO_USBD)
 *
 * 【共享范围】
 *   本头文件被以下三方包含，修改时必须保持 ABI 兼容：
 *   - Kernel 端：vendor_lechao_usbd 驱动（内核模块）
 *   - AOSP 端：lechao_lciod HAL 服务（用户态守护进程）
 *   - Host 端：调试/开发工具（Linux 用户态程序）
 *
 * 【版本约定】
 *   结构体中预留 reserved 字段用于未来扩展，新增字段应追加到末尾，
 *   不得修改已有字段的偏移和大小。
 *
 * 【ABI 版本】
 *   本头文件为内核-用户态共享 ABI 的真相源。AOSP HAL 与 usb-verify
 *   的镜像副本必须与本文件保持 1:1 同步。每次 ABI 变更必须递增
 *   VENDOR_LECHAO_USBD_ABI_VERSION 并三方整体重编。
 *   - v1：初始版本
 *   - v2：stats 末尾追加 event_drop_count
 * ============================================================
 */

#ifndef _VENDOR_LECHAO_USBD_IOCTL_H
#define _VENDOR_LECHAO_USBD_IOCTL_H

#include <linux/types.h>

/* ABI 版本号：与 AOSP HAL / usb-verify 镜像副本必须一致 */
#define VENDOR_LECHAO_USBD_ABI_VERSION  2

/*
 * struct vendor_lechao_usbd_stats — 单设备粒度的传输统计快照
 *
 * 【用途】通过 IOC_GET_STATS ioctl 返回给用户态，提供设备的完整运行状态。
 * 【生命周期】每次 ioctl 调用时从内核设备结构体原子拷贝，是调用时刻的快照。
 * 【分类说明】
 *   - 累计计数器：read_bytes ~ timeout_count（单调递增，reset 时清零）
 *   - 最近快照：current_rate, peak_rate, last_transport_latency_ns,
 *               last_event_ts_ns, last_event_type（反映最近一次传输/事件）
 *   - 设备标识：vid, pid, vendor[], product[]（分配时读取，之后不变）
 *   - 配置状态：enabled, flags（反映当前运行时配置）
 *
 * GET_STATS 是纯读取接口；重置动作通过独立的
 * VENDOR_LECHAO_USBD_IOC_RESET_STATE 触发。
 */
struct vendor_lechao_usbd_stats {
	u64 read_bytes;                  /* 累计：读取总字节数 */
	u64 write_bytes;                 /* 累计：写入总字节数 */
	u64 read_ns;                     /* 累计：所有读命令的传输耗时之和（纳秒） */
	u64 write_ns;                    /* 累计：所有写命令的传输耗时之和（纳秒） */
	u64 read_cmds;                   /* 累计：成功完成的读命令计数 */
	u64 write_cmds;                  /* 累计：成功完成的写命令计数 */
	u64 error_count;                 /* 累计：USB 传输层错误次数（transport error） */
	u64 reset_count;                 /* 累计：USB 设备 reset 事件次数 */
	u64 probe_count;                 /* 累计：设备探测次数（通常为 1） */
	u64 disconnect_count;            /* 累计：设备断开次数 */
	u64 degrade_count;               /* 累计：检测到性能降级的次数（速率下降或延迟上升） */
	u64 current_rate;                /* 快照：最近一次成功传输的瞬时速率（字节/秒） */
	u64 peak_rate;                   /* 快照：历史最高瞬时速率（字节/秒） */
	u64 last_transport_latency_ns;   /* 快照：最近一次传输的端到端延迟（纳秒） */
	u64 last_event_ts_ns;            /* 快照：最近一次异常事件的时间戳（CLOCK_MONOTONIC 纳秒） */
	s64 last_update;                 /* 快照：最近一次 TRANSPORT_END 事件的时间戳（纳秒） */
	u16 vid;                         /* 标识：USB Vendor ID（如 0x0781 = SanDisk） */
	u16 pid;                         /* 标识：USB Product ID */
	char vendor[32];                 /* 标识：制造商字符串（来自 USB iManufacturer 描述符） */
	char product[32];                /* 标识：产品名称字符串（来自 USB iProduct 描述符） */
	u64 stall_count;                 /* 累计：USB STALL 事件次数 */
	u64 corrupt_count;               /* 累计：数据损坏事件次数（EOVERFLOW / babble） */
	u64 timeout_count;               /* 累计：USB 传输超时次数 */
	u32 last_event_type;             /* 快照：最近一次异常事件的类型（见 event_type 枚举） */
	u8 enabled;                      /* 配置：监控是否启用（1=启用, 0=禁用） */
	u8 reserved[3];                  /* 预留：对齐填充，未来扩展用 */
	u32 flags;                       /* 配置：运行时标志位，预留扩展 */
	u64 event_drop_count;            /* 累计：环形缓冲区溢出丢弃的事件数（event_lock 保护） */
};

/*
 * struct vendor_lechao_usbd_config — 运行时配置（ioctl GET/SET_CONFIG）
 *
 * 【用途】用户态通过 SET_CONFIG 动态启用/禁用监控或设置标志位，
 *        通过 GET_CONFIG 读取当前配置。
 * 【线程安全】SET_CONFIG 在自旋锁保护下原子生效。
 */
struct vendor_lechao_usbd_config {
	u8 enabled;                      /* 监控开关：1=启用统计和事件推送, 0=暂停 */
	u8 reserved[3];                  /* 预留：对齐填充 */
	u32 flags;                       /* 运行时标志位，预留扩展（当前未使用特定位） */
};

/*
 * 环形事件缓冲区容量
 *
 * 定义每个设备的事件队列深度。当队列满时，最旧的事件被覆盖丢弃。
 * 32 条足够覆盖典型的突发异常场景（如连续 stall + timeout + degrade）。
 */
#define VENDOR_LECHAO_USBD_EVENT_BUF_SIZE  32

/*
 * enum vendor_lechao_usbd_event_type — 异常事件类型枚举
 *
 * 每个值对应一种 USB 传输异常场景，通过 read() 推送给用户态。
 */
enum vendor_lechao_usbd_event_type {
	VENDOR_LECHAO_USBD_EVENT_NONE            = 0, /* 无事件（初始状态） */
	VENDOR_LECHAO_USBD_EVENT_TRANSPORT_ERROR = 1, /* USB 传输层错误：transport() 返回 USB_STOR_TRANSPORT_ERROR */
	VENDOR_LECHAO_USBD_EVENT_STALL           = 2, /* USB STALL：URB 返回 -EPIPE，端点被挂起 */
	VENDOR_LECHAO_USBD_EVENT_DATA_CORRUPT    = 3, /* 数据损坏：URB 返回 -EOVERFLOW（babble） */
	VENDOR_LECHAO_USBD_EVENT_TIMEOUT         = 4, /* 传输超时：URB 等待超时或被信号中断后 kill */
	VENDOR_LECHAO_USBD_EVENT_RESET           = 5, /* 设备重置：usb_stor_invoke_transport 错误恢复路径触发 */
	VENDOR_LECHAO_USBD_EVENT_RATE_DEGRADED   = 6, /* 性能降级：瞬时速率下降或延迟上升时触发 */
};

/*
 * struct vendor_lechao_usbd_event — 单条异步事件记录
 *
 * 【用途】通过 read() 系统调用返回给用户态的定长事件记录。
 *        每条记录描述一次 USB 传输异常的详细信息。
 * 【数据流向】内核 notifier → 环形缓冲区 → read() → 用户态监控进程
 */
struct vendor_lechao_usbd_event {
	u64 timestamp_ns;                /* 事件发生时间（CLOCK_MONOTONIC 纳秒） */
	u32 event_type;                  /* 事件类型（见 vendor_lechao_usbd_event_type 枚举） */
	u32 event_value;                 /* 事件附加值：STALL/CORRUPT/TIMEOUT 为 0，TRANSPORT_ERROR 为 result 码 */
	s32 status;                      /* 原始错误码：STALL 为 -EPIPE，TIMEOUT 为 URB status 等 */
	u8 data_direction;               /* 传输方向：0=无, 1=读(DMA_FROM_DEVICE), 2=写(DMA_TO_DEVICE) */
	u8 valid;                        /* 有效标志：1=该条目包含有效事件数据 */
	u8 reserved[2];                  /* 预留：对齐填充 */
};

/* ---- ioctl 命令码定义 ---- */

/*
 * ioctl 魔数：'R'（ASCII 0x52）
 * 选择 'R' 是为了避免与内核中已有的字符设备 ioctl 魔数冲突。
 */
#define VENDOR_LECHAO_USBD_IOC_MAGIC        'R'

/*
 * IOC_GET_STATS (cmd 0) — 读取设备统计快照
 *   参数：struct vendor_lechao_usbd_stats（内核→用户态）
 *   用途：用户态监控程序定期轮询获取传输统计
 */
#define VENDOR_LECHAO_USBD_IOC_GET_STATS    _IOR(VENDOR_LECHAO_USBD_IOC_MAGIC, 0, struct vendor_lechao_usbd_stats)

/*
 * IOC_RESET_STATE (cmd 1) — 重置所有统计计数器
 *   参数：无
 *   用途：清零所有累计计数器（bytes/cmds/errors/degrade 等），
 *         不影响配置（enabled/flags）和设备标识（vid/pid）
 */
#define VENDOR_LECHAO_USBD_IOC_RESET_STATE  _IO(VENDOR_LECHAO_USBD_IOC_MAGIC, 1)

/*
 * IOC_GET_CONFIG (cmd 2) — 读取运行时配置
 *   参数：struct vendor_lechao_usbd_config（内核→用户态）
 *   用途：读取当前 enabled/flags 配置
 */
#define VENDOR_LECHAO_USBD_IOC_GET_CONFIG   _IOR(VENDOR_LECHAO_USBD_IOC_MAGIC, 2, struct vendor_lechao_usbd_config)

/*
 * IOC_SET_CONFIG (cmd 3) — 修改运行时配置
 *   参数：struct vendor_lechao_usbd_config（用户态→内核）
 *   用途：动态启用/禁用监控，或修改标志位
 */
#define VENDOR_LECHAO_USBD_IOC_SET_CONFIG   _IOW(VENDOR_LECHAO_USBD_IOC_MAGIC, 3, struct vendor_lechao_usbd_config)

#endif /* _VENDOR_LECHAO_USBD_IOCTL_H */
