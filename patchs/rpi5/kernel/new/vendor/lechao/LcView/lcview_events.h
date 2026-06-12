/*
 * lcview_events.h — LcView 结构化事件日志子系统：共享事件定义
 *
 * 本文件定义内核模块与用户态共享的事件 ID、日志级别、字段类型编码以及
 * 记录头结构。用户态程序 (如 logcat 工具) 可通过包含本文件来解析
 * 从 /dev/vendor_lechao_lcview 读取的二进制事件数据。
 *
 * 设计原则：
 * - 类型编码 (LCVIEW_TYPE_*) 采用固定长度或长度前缀自描述格式，
 *   解析器无需预知 schema 即可遍历字段。
 * - 记录头固定 16 字节，packed 对齐，确保内核与用户态布局一致。
 * - __KERNEL__ 宏区分内核态和用户态编译路径，仅 pack 属性语法不同。
 */

#ifndef LCVIEW_EVENTS_H
#define LCVIEW_EVENTS_H

#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdint.h>
#endif

/* --- 日志级别 --- */
#define LCVIEW_LEVEL_DEBUG  0
#define LCVIEW_LEVEL_INFO   1
#define LCVIEW_LEVEL_WARN   2
#define LCVIEW_LEVEL_ERROR  3

/* --- 字段类型编码 --- */
#define LCVIEW_TYPE_INT32   1
#define LCVIEW_TYPE_INT64   2
#define LCVIEW_TYPE_FLOAT   3
#define LCVIEW_TYPE_STRING  4
#define LCVIEW_TYPE_BINARY  5

/* --- 事件 ID ---
 *
 * 每个事件 ID 对应一个特定的系统行为，由对应模块在特定时机发射。
 * 用户态解析器通过 event_id 匹配 schema 来解码字段列表。
 *
 * 事件来源分布：
 *   - USB 1, 4-13：vendor_lechao_usbd-stats.c（USB 存储监控）
 *   - GPIO 2：lechao_gpio_irq 驱动（GPIO 中断监控）
 *   - SENSOR 3：lechao_sensor 驱动（传感器数据采集）
 */
#define LCVIEW_EVENT_USB_CONNECT         1  /* USB 设备连接（预留，当前由 PROBE 替代） */
#define LCVIEW_EVENT_GPIO_IRQ            2  /* GPIO 中断触发（来源：lechao_gpio_irq 驱动） */
#define LCVIEW_EVENT_SENSOR_DATA         3  /* 传感器数据上报（来源：lechao_sensor 驱动） */
#define LCVIEW_EVENT_USB_TRANSPORT_START 4  /* USB 传输开始（来源：vendor_lechao_usbd-stats.c，
                                             * 触发场景：usb_stor_invoke_transport 入口） */
#define LCVIEW_EVENT_USB_TRANSPORT_END   5  /* USB 传输结束（来源：vendor_lechao_usbd-stats.c，
                                             * 触发场景：成功/失败/abort/no_sense 的统一出口） */
#define LCVIEW_EVENT_USB_TRANSPORT_ERROR 6  /* USB 传输层错误（来源：vendor_lechao_usbd-stats.c，
                                             * 触发场景：transport() 返回 TRANSPORT_ERROR） */
#define LCVIEW_EVENT_USB_RESET           7  /* USB 设备重置（来源：vendor_lechao_usbd-stats.c，
                                             * 触发场景：Handle_Errors 路径执行 reset 后） */
#define LCVIEW_EVENT_USB_PROBE           8  /* USB 设备探测（来源：vendor_lechao_usbd.c，
                                             * 触发场景：PROBE notifier 或 usb_dev_scan 发现新设备） */
#define LCVIEW_EVENT_USB_DISCONNECT      9  /* USB 设备断开（来源：vendor_lechao_usbd.c，
                                             * 触发场景：DISCONNECT notifier 或模块卸载） */
#define LCVIEW_EVENT_USB_STALL           10 /* USB STALL 事件（来源：vendor_lechao_usbd-stats.c，
                                             * 触发场景：URB 返回 -EPIPE） */
#define LCVIEW_EVENT_USB_TIMEOUT         11 /* USB 传输超时（来源：vendor_lechao_usbd-stats.c，
                                             * 触发场景：URB 等待超时或信号中断） */
#define LCVIEW_EVENT_USB_DATA_CORRUPT    12 /* USB 数据损坏（来源：vendor_lechao_usbd-stats.c，
                                             * 触发场景：URB 返回 -EOVERFLOW 即 babble） */
#define LCVIEW_EVENT_USB_RATE_DEGRADED   13 /* USB 性能降级（来源：vendor_lechao_usbd-stats.c，
                                             * 触发场景：瞬时速率下降或延迟上升超过阈值） */

/* --- 记录魔数 --- */
#define LCVIEW_MAGIC  0x4C56

/* --- 记录头结构（16B 固定头 + 变长字段区） --- */
#ifdef __KERNEL__
struct lcview_record_hdr {
    uint16_t magic;
    uint16_t event_id;
    uint8_t  level;
    uint8_t  field_count;
    uint16_t reserved;
    uint64_t timestamp_ns;
} __attribute__((packed));

struct lcview_field_hdr {
    uint8_t  type;
    /* value follows: type-dependent length */
} __attribute__((packed));
#else
#pragma pack(push, 1)
struct lcview_record_hdr {
    uint16_t magic;
    uint16_t event_id;
    uint8_t  level;
    uint8_t  field_count;
    uint16_t reserved;
    uint64_t timestamp_ns;
};
struct lcview_field_hdr {
    uint8_t  type;
};
#pragma pack(pop)
#endif

#endif /* _LCVIEW_EVENTS_H */
