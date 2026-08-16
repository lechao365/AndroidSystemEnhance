/*
 * lcview_internal.h — LcView 内核模块内部数据结构与 API 声明
 *
 * 本文件包含环形缓冲区 (lcview_ring)、事件构建器 (lcview_builder) 和
 * 统计信息 (lcview_stats) 的核心数据结构定义，以及模块内部使用的所有
 * 函数声明（环形缓冲区操作 + Builder API）。
 *
 * 头文件依赖关系：
 *   lcview_internal.h → lcview_events.h（共享事件类型定义）
 *   lcview_main.c / lcview_ring.c / lcview_builder.c → lcview_internal.h
 *
 * 设计要点：
 * - lcview_ring 使用 spinlock 保护写指针/读指针，适用于中断上下文写入
 * - lcview_builder 使用 GFP_ATOMIC 分配，writer 可在 spinlock 保护区内
 *   创建并提交事件，无需额外工作队列
 * - read_buf 作为"锁内拷贝→锁外 copy_to_user"的中转，最小化持锁时间
 */

#ifndef LCVIEW_INTERNAL_H
#define LCVIEW_INTERNAL_H

#include <linux/kernel.h>
#include <linux/spinlock.h>
#include <linux/wait.h>
#include <linux/atomic.h>
#include "lcview_events.h"

/* 环形缓冲区默认大小 (256KB)，可通过模块参数 ring_size_kb 覆盖 */
#define LCVIEW_RING_DEFAULT_KB    256

/* 环形缓冲区最大大小 (4MB)，防止误配置导致 vmalloc 失败 */
#define LCVIEW_RING_MAX_KB       4096

/* Builder 内部缓冲最大容量 (4KB)，单条事件的硬上限 */
#define LCVIEW_BUILDER_MAX_SIZE  4096

/*
 * 环形缓冲区记录前缀长度 (4 字节)
 * 每条记录开头存 uint32_t 总长度（含前缀自身），用于读/写指针推进
 */
#define LCVIEW_LEN_PREFIX_SIZE   4

/*
 * lcview_ring — 无锁单生产者/单消费者环形缓冲区
 *
 * 写者 (lcview_ring_write) 在 spin_lock 保护下写入，支持中断上下文。
 * 读者 (lcview_ring_read) 在 spin_lock 保护下读取记录头到 read_buf，
 * 随后解锁执行 copy_to_user，减少持锁时间。
 *
 * 空间不足时写者自动驱逐最旧记录 (ring_evict_one)，保证最新事件不丢失。
 * 适用于"最新 N 条"日志场景，而非可靠传输。
 */
struct lcview_ring {
    uint8_t      *buf;         /* 环形缓冲区内存（vmalloc 分配） */
    uint8_t      *read_buf;    /* 读取临时缓冲区，锁内 memcpy 后解锁 copy_to_user */
    uint32_t      size;        /* 缓冲区总大小（字节） */
    uint32_t      write_pos;   /* 写指针（由 spin_lock 保护，指向下条写入位置） */
    uint32_t      read_pos;    /* 读指针（读时持锁修改，指向下条读取位置） */
    atomic_t      overrun_cnt; /* 溢出逐出累计计数（边读边清） */
    atomic_t      total_records; /* 累计写入记录数（仅统计，不清零） */
    spinlock_t    lock;        /* 保护 write_pos/read_pos 的自旋锁 */
    wait_queue_head_t waitq;   /* 读取等待队列，写完后 wake_up 唤醒 reader */
    bool          shutdown;    /* destroy 标记，通知等待中的 reader 退出 */
};

/* 全局环形缓冲区实例，在 lcview_main.c 中定义 */
extern struct lcview_ring lcview_ring;

/*
 * lcview_builder — 事件记录构建器（Builder 模式）
 *
 * 调用者使用 lcview_builder_new 创建构建器，通过 add_* 系列 API
 * 追加字段，最后调用 commit 将完整记录写入环形缓冲区。
 *
 * buf[LCVIEW_BUILDER_MAX_SIZE] 预分配 4KB 缓冲区存放序列化后的事件：
 *   [0..sizeof(hdr)-1]     — lcview_record_hdr（构建时预留，commit 时填充）
 *   [sizeof(hdr)..]        — TLV 格式字段序列（type + value）
 *
 * 设计理由：
 * - 预分配缓冲区避免动态增长，简化内存管理
 * - committed 标志防止重复提交导致环形缓冲区数据错乱
 */
struct lcview_builder {
    uint8_t  buf[LCVIEW_BUILDER_MAX_SIZE]; /* 序列化缓冲区 */
    uint16_t event_id;     /* 事件 ID */
    uint8_t  level;        /* 日志级别 */
    uint8_t  field_count;  /* 已添加字段数 */
    uint16_t data_offset;  /* 当前字段写入偏移（从 buf 起始计算） */
    bool     committed;    /* 是否已提交，防止重复 commit */
};

/*
 * lcview_stats — 环形缓冲区运行时统计信息
 * 通过 LCVIEW_GET_STATS ioctl 返回给用户态
 */
struct lcview_stats {
    uint32_t total_records;    /* 累计写入记录总数 */
    uint32_t overrun_cnt;      /* 溢出逐出记录数 */
    uint32_t ring_usage_bytes; /* 当前已使用字节数 */
    uint32_t ring_size_bytes;  /* 环形缓冲区总大小 */
};

/* ========== 环形缓冲区 API ========== */

/*
 * 初始化环形缓冲区
 * @ring:    lcview_ring 实例指针
 * @size_kb: 缓冲区大小（KB），0 或超出上限时使用默认值
 * 返回 0 成功，-ENOMEM vmalloc 失败
 */
int  lcview_ring_init(struct lcview_ring *ring, uint32_t size_kb);

/*
 * 销毁环形缓冲区
 * 设置 shutdown 标志 → wake_up reader → 释放内存
 * 调用者需确保销毁后不再有写入/读取操作
 */
void lcview_ring_destroy(struct lcview_ring *ring);

/*
 * 写入一条记录到环形缓冲区
 * 空间不足时自动驱逐最旧记录，直到有空间或缓冲区清空
 */
int  lcview_ring_write(struct lcview_ring *ring,
                       const uint8_t *data, uint32_t len);

/*
 * 从环形缓冲区读取最多 len 字节到用户缓冲区
 * 可能返回多条完整记录，不足一条时阻塞等待
 * 返回实际读取字节数，或负错误码
 */
int  lcview_ring_read(struct lcview_ring *ring,
                      uint8_t __user *buf, uint32_t len);

/* 查询环形缓冲区中当前可读字节数（非精确，用于 poll/select） */
uint32_t lcview_ring_avail_bytes(struct lcview_ring *ring);

/* 获取运行时统计信息 */
void lcview_ring_get_stats(struct lcview_ring *ring, struct lcview_stats *stats);

/* ========== Builder API ========== */

/* 内部分配构建器（GFP_ATOMIC），供 lcview_builder_start 调用 */
struct lcview_builder *lcview_builder_new(uint16_t event_id, uint8_t level);

/*
 * 公开入口：创建构建器（带日志级别过滤）
 * 级别低于当前 min_level 时返回 NULL，不分配内存
 * EXPORT_SYMBOL 供其他内核模块调用
 */
struct lcview_builder *lcview_builder_start(uint16_t event_id, uint8_t level);

/* 释放构建器内存 */
void lcview_builder_free(struct lcview_builder *b);

/* 添加 int64 字段 */
int  lcview_builder_add_int(struct lcview_builder *b, int64_t val);

/* 添加 int32 字段 */
int  lcview_builder_add_int32(struct lcview_builder *b, int32_t val);

/* 添加字符串字段（长度前缀编码：uint16_t + data） */
int  lcview_builder_add_str(struct lcview_builder *b, const char *val);

/* 添加 float 字段 */
int  lcview_builder_add_float(struct lcview_builder *b, uint32_t raw_float);

/* 添加二进制 blob 字段（uint16_t 长度前缀 + data） */
int  lcview_builder_add_binary(struct lcview_builder *b,
                               const void *ptr, uint16_t len);

/*
 * 提交构建的事件到环形缓冲区
 * 成功后释放构建器；失败时保留构建器以便重试或 cancel
 */
int  lcview_builder_commit(struct lcview_builder *b, struct lcview_ring *ring);

/* 取消构建并释放资源 */
void lcview_builder_cancel(struct lcview_builder *b);

#endif /* LCVIEW_INTERNAL_H */
