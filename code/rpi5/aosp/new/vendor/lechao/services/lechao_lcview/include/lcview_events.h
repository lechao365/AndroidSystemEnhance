// ============================================================
// lcview_events.h — 结构化事件日志系统的核心数据结构头文件
// 所属模块：LcView（Log View）
// 设计目的：定义内核和用户态共享的事件日志二进制协议格式。
//   包含：日志级别、字段类型编码、事件 ID、魔数、记录头结构。
//   此头文件同时被内核模块和用户态程序引用，使用 __KERNEL__
//   宏区分两者之间的细微差异（如 packed 属性、头文件包含）。
//   用户态编译时使用 #pragma pack(push,1) 保证内存布局一致。
// ============================================================

#ifndef LCVIEW_EVENTS_H
#define LCVIEW_EVENTS_H

#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdint.h>
#endif

/* --- 日志级别 --- */
/* 事件严重等级，从调试到致命错误。级别越高，越应引起关注。 */
#define LCVIEW_LEVEL_DEBUG  0
#define LCVIEW_LEVEL_INFO   1
#define LCVIEW_LEVEL_WARN   2
#define LCVIEW_LEVEL_ERROR  3

/* --- 字段类型编码 --- */
/* 每个事件字段的二进制类型标识：
 *   INT32/INT64 — 定长整型，内存直接拷贝
 *   FLOAT      — IEEE 754 单精度浮点
 *   STRING     — 2 字节长度前缀 + UTF-8 文本
 *   BINARY     — 2 字节长度前缀 + 原始字节
 * 此编码必须与内核写入端完全一致。 */
#define LCVIEW_TYPE_INT32   1
#define LCVIEW_TYPE_INT64   2
#define LCVIEW_TYPE_FLOAT   3
#define LCVIEW_TYPE_STRING  4
#define LCVIEW_TYPE_BINARY  5

/* --- 事件 ID --- */
/* 每种 USB/GPIO/传感器事件的唯一标识。
 * 从 1 开始递增，预留前 3 个给通用事件。
 * 注意：id=1~3 暂未使用，id=4~9 为 USB 子系统事件，
 * 未来可扩展 GPIO/SENSOR 事件 ID 到 10+ */
#define LCVIEW_EVENT_USB_CONNECT         1
#define LCVIEW_EVENT_GPIO_IRQ            2
#define LCVIEW_EVENT_SENSOR_DATA         3
#define LCVIEW_EVENT_USB_TRANSPORT_START 4
#define LCVIEW_EVENT_USB_TRANSPORT_END   5
#define LCVIEW_EVENT_USB_TRANSPORT_ERROR 6
#define LCVIEW_EVENT_USB_RESET           7
#define LCVIEW_EVENT_USB_PROBE           8
#define LCVIEW_EVENT_USB_DISCONNECT      9
#define LCVIEW_EVENT_USB_STALL           10
#define LCVIEW_EVENT_USB_TIMEOUT         11
#define LCVIEW_EVENT_USB_DATA_CORRUPT    12
#define LCVIEW_EVENT_USB_RATE_DEGRADED   13

/* --- 记录魔数 --- */
/* 每条日志记录的起始固定标志，用于校验数据完整性。
 * 'LV' ASCII 编码（0x4C='L', 0x56='V'），在读端验证。 */
#define LCVIEW_MAGIC  0x4C56

/* --- 记录头结构（16B 固定头 + 变长字段区） --- */
/* lcview_record_hdr：16 字节固定长度头部，所有事件共用。
 *   magic       — 魔数，用于快速校验
 *   event_id    — 事件类型 ID，映射到 JSON schema 定义
 *   level       — 日志级别
 *   field_count — 字段数量（与 schema 中的字段数匹配）
 *   reserved    — 保留字段，对齐用
 *   timestamp_ns— 单调时钟纳秒时间戳，用于排序和延迟分析
 *
 * lcview_field_hdr：每个字段前 1 字节类型标识，
 *   后接类型相关的值（定长 4/8 字节，或 2 字节长度前缀+变长）。
 *   为保证最小对齐和跨语言解析一致性，结构体按 1 字节对齐。 */
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
