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
 * 值 0x4C56（'L'=0x4C, 'V'=0x56）；小端线材上实际字节序为
 * [0x56, 0x4C]（低字节在前），独立解析器/抓包判断时注意。 */
#define LCVIEW_MAGIC  0x4C56

/* 字节序契约（CXX-001 / LCV-02 / KRN-002，与内核 lcview_events.h 同款守卫）：
 * 本头定义的线上格式（lcview_record_hdr + TLV 字段）为主机序裸 memcpy
 * 序列化——事实上的小端契约：内核写入端（lcview_builder/lcview_ring）
 * 与用户态解析端（record_codec/SchemaParser/FileWriter）同机同序
 * （ARM64 LE）三方自洽。
 * 若未来跨大小端设备传输或引入显式字节序转换，必须内核与用户态
 * 同步改造，禁止单侧修改。
 * 下面的编译守卫保证大端环境直接编译失败，防隐性错误。 */
#if defined(__BYTE_ORDER__) && defined(__ORDER_LITTLE_ENDIAN__) && \
    (__BYTE_ORDER__ != __ORDER_LITTLE_ENDIAN__)
#error "LcView 线上格式按小端契约裸 memcpy 序列化（LCV-02/KRN-002），不支持大端编译"
#endif

/* --- 单条记录大小上限 --- */
/* 单条事件序列化后的硬上限（4KB）。真相源：内核 lcview_internal.h 的
 * LCVIEW_BUILDER_MAX_SIZE（lcview_builder 预分配缓冲），内核写入端
 * （lcview_builder 边界检查）与读端（lcview_ring_read 判损坏上限）以及
 * 用户态读缓冲预算均以此为契约，改须内核+用户态两侧同步，禁止单侧修改。
 * 用户态 daemon 主循环的预防性 flush 阈值（kMinReadSize）据此闭合
 * EMSGSIZE：缓冲剩余空间恒 >= 本值，首条记录必放得下。 */
#define LCVIEW_MAX_RECORD_SIZE 4096

/* --- 记录头结构（16B 固定头 + 变长字段区） --- */
/* lcview_record_hdr：16 字节固定长度头部，所有事件共用。
 *   magic       — 魔数，用于快速校验
 *   event_id    — 事件类型 ID，映射到 JSON schema 定义
 *   level       — 日志级别
 *   field_count — 字段数量（与 schema 中的字段数匹配）
 *   reserved    — 保留字段，对齐用
 *   timestamp_ns— CLOCK_REALTIME 时钟纳秒时间戳（非单调，受 NTP 调整），
 *   用于跨设备日志时间对齐和延迟分析
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
