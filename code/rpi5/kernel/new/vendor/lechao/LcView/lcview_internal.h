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
#include <linux/mutex.h>
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
 * KRN-008：单次 write 的驱逐条数预算（见 lcview_ring_write）。
 * 限制 spinlock 持有时间（256 条 ≈ 26µs 上限），超限返回 -ENOSPC
 * 丢弃本次写入（该场景本就属于 overrun，计数递增可观测）。
 */
#define LCVIEW_EVICT_MAX_RECORDS 256

/*
 * lcview_ring — spinlock 保护的环形缓冲区（KRN-011：非"无锁 SPSC"）
 *
 * 并发模型：写者（lcview_ring_write）可能在多个上下文并发调用
 * （USB 中断回调、lciod notifier 等），靠 spin_lock_irqsave 互斥；
 * 读者（lcview_ring_read）因设备单打开限制为单消费者，但多个打开
 * 实例经单打开 cmpxchg 限制前仍可能并发调用 read——read 会睡眠
 * （wait_event_interruptible / copy_to_user）不能持 spinlock，故
 * 用 read_mutex 串行化 read 调用，保护 read_pos 与 read_buf 不被
 * 并发读者竞争撕裂。读者锁内拷贝记录头到 read_buf，随后解锁执行
 * copy_to_user 以减少持锁时间。
 *
 * 空间不足时写者自动驱逐最旧记录 (ring_evict_one)，保证最新事件不丢失。
 * 适用于"最新 N 条"日志场景，而非可靠传输。
 *
 * 生命周期防护（防 UAF）：lcview_ring_read 入口 atomic_inc(readers)，
 * 出口 atomic_dec_and_test 归零时 wake_up(exit_wait)；lcview_ring_destroy
 * 置 shutdown 后经 wait_event(exit_wait) 等 readers 归零，才 vfree buf/
 * read_buf。readers 计数在 mutex_lock 之前 inc——等待 read_mutex 的
 * reader 同样计入在途读，destroy 的 wait_event 会等其拿到锁后因
 * shutdown 直返归零，杜绝"持锁等待者越过归零判定后访问已释放内存"。
 * 写者路径由 spin_lock 与 shutdown 检查互斥闭环（销毁前持锁
 * 置 shutdown，后续写者查 shutdown 拒绝），无需计数。
 */
struct lcview_ring {
    uint8_t      *buf;         /* 环形缓冲区内存（vmalloc 分配） */
    uint8_t      *read_buf;    /* 读取临时缓冲区，锁内 memcpy 后解锁 copy_to_user */
    uint32_t      size;        /* 缓冲区总大小（字节） */
    uint32_t      write_pos;   /* 写指针（由 spin_lock 保护，指向下条写入位置） */
    uint32_t      read_pos;    /* 读指针（读时持锁修改，指向下条读取位置） */
    /* R-13 方向 3：统计计数升 atomic64_t——total_records 在 I/O 洪水下
     * u32 最快 ~71 分钟回绕（1M events/s），长期运行 + 守恒增量比较场景
     * 回绕风险真实存在；升 u64 后 daemon 侧无需再防 uint32 回绕（内核
     * 重载检测仍保留）。overrun_cnt 边读边清（增量语义）但同升 u64
     * 一劳永逸，dropped_cnt 不清零累计同 total 生命周期同风险。 */
    atomic64_t    overrun_cnt; /* 溢出逐出累计计数（边读边清） */
    atomic64_t    total_records; /* 累计写入记录数（仅统计，不清零） */
    atomic64_t    dropped_cnt; /* ENOSPC 丢弃累计计数（方向 7：驱逐预算超限丢弃，
                                * 与 total_records 同步递增——该记录同样被内核收到，
                                * 守恒左式 totalΔ = overrunΔ + droppedΔ + jsonlΔ + invalidΔ
                                * 由此闭合，避免丢弃时守恒负向误报） */
    /* 说明：R-07 方向 1 的 producer_dropped_cnt 不再放本结构——builder kmalloc
     * 失败点（lcview_builder.c）与 level 过滤点（lcview_main.c）计数，host 单测
     * 编 builder.c 不编 lcview_main.c（无全局 lcview_ring 实体），若挂在 ring 结构
     * 会致 host 链接 undefined reference。改为 lcview_builder.c 模块级静态计数 +
     * getter/setter 导出（见 lcview_builder_producer_dropped_*），经 sysfs 导出
     * 供守恒右式吸收，不动 struct lcview_stats 防 ABI 断言破坏。 */
    spinlock_t    lock;        /* 保护 write_pos/read_pos 的自旋锁 */
    struct mutex  read_mutex;  /* 串行化 read 调用（方向 3）：并发读者防 read_pos 撕裂 */
    wait_queue_head_t waitq;   /* 读取等待队列，写完后 wake_up 唤醒 reader */
    bool          shutdown;    /* destroy 标记，通知等待中的 reader 退出 */
    atomic_t      readers;     /* 在途读调用计数，destroy 等其归零再释放内存（防 UAF） */
    wait_queue_head_t exit_wait; /* 读调用归零等待队列，destroy 睡眠等所有 reader 退出 */
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
    uint64_t total_records;    /* 累计写入记录总数（含 ENOSPC 丢弃，见 lcview_ring_write） */
    uint64_t overrun_cnt;      /* 溢出逐出记录数 */
    uint64_t dropped_cnt;      /* ENOSPC 丢弃累计（方向 7：驱逐预算超限丢弃，只读不清零） */
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

/* ========== 生产者丢弃计数（R-07 方向 1） ========== */

/*
 * 生产端丢弃累计计数（builder kmalloc 失败 + level 过滤）。
 * 与环级 dropped_cnt（ENOSPC，计 total_records）区分：producer_dropped
 * 的记录从未写入 ring、不计入 total_records，守恒左式不含它，经 sysfs
 * 导出供守恒右式吸收。模块级静态计数（lcview_builder.c），host 单测编
 * 本文件即自带，不依赖全局 lcview_ring 实体。
 */
void lcview_builder_producer_dropped_inc(void);
uint32_t lcview_builder_producer_dropped_get(void);

/* ========== 事件序号游标（R-13 方向 2） ========== */

/*
 * 当前全局事件序号游标（模块级 atomic64 递增，只读不消费）。
 * daemon 心跳 gap 判定用：本心跳读到游标较上心跳增量 - 落盘条数 =
 * 序列间隙（含 ring 驱逐/ENOSPC 丢弃/FileWriter DROP 的真实丢事件量）。
 * 与 ring->total_records 同源（每 commit 递增一次），但独立生成器不随
 * ring 驱逐归零，gap 判定不依赖 GET_STATS 缓存（sysfs 直读）。
 */
uint64_t lcview_event_seq_cur(void);

#endif /* LCVIEW_INTERNAL_H */
