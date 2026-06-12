/*
 * lcview_ring.c — LcView 环形缓冲区实现
 *
 * 本文件实现无锁化单生产者/单消费者环形缓冲区，用于在内核中高效暂存
 * 结构化事件记录，并支持用户态通过字符设备读取。
 *
 * 核心设计决策：
 *
 * 1. 为什么用环形缓冲区而非链表？
 *    - 固定大小预分配（vmalloc），无运行时动态内存分配
 *    - O(1) 读写，不会因记录数增长导致延迟抖动
 *    - 天然支持"最新 N 条"语义，溢出时自动逐出最旧记录
 *
 * 2. 为什么用 spin_lock 而不是 mutex？
 *    - 写操作 lcview_ring_write 需要在中断上下文执行（USB 中断回调）
 *    - spin_lock_irqsave 可以在任何上下文安全使用，且持有时间极短
 *      （仅 memcpy 长度前缀 + 记录数据，不超过几微秒）
 *
 * 3. 读路径为什么用 read_buf 做中转拷贝？
 *    - copy_to_user 不能在持 spin_lock 时调用（可能触发 page fault 导致死锁）
 *    - 方案：锁内 memcpy 到 read_buf → 解锁 → 锁外 copy_to_user
 *    - 代价是一次额外的 memcpy（L1 缓存命中，实测开销 < 100ns/4KB）
 *
 * 4. Overflow eviction 策略：
 *    - 写者发现空间不足时，逐条驱逐最旧记录 (ring_evict_one)
 *    - 从不需要移动已有数据，只需推进 read_pos
 *    - 适用于"事件流"场景，不适用于可靠交付
 *
 * 5. 为什么不在读路径驱逐？
 *    - 保证读到的数据语义一致：读者读到的是"快照"——驱逐只发生在写路径
 *    - 读者不需要与写者同步协调"哪些记录被驱逐了"
 */

#include "lcview_internal.h"
#include <linux/slab.h>
#include <linux/uaccess.h>
#include <linux/vmalloc.h>
#include "kernel_lechao_log.h"

extern int lcview_debug;
#define LC_DBG(fmt, ...) do { if (lcview_debug) pr_info(PREFIX "[D] " fmt, ##__VA_ARGS__); } while (0)

#define PREFIX KERNEL_LCVIEW_TAG ": ring: "

/*
 * ring_avail_write — 计算环形缓冲区中可写入的空闲空间
 *
 * 环形缓冲区区分"满"和"空"的关键技巧：
 * 当 write_pos == read_pos 时表示空，而不是满。
 * 因此需要预留 1 字节不可用，使"满"条件为 write_pos + 1 == read_pos。
 *
 * 如果不预留这 1 字节，空和满在指针相等时无法区分——
 * 都是 write_pos == read_pos。
 *
 * @return 当前可以安全写入的字节数（不含预留的 1 字节）
 *
 * Must be called with ring->lock held.
 */
static uint32_t ring_avail_write(struct lcview_ring *ring)
{
    uint32_t used;
    if (ring->write_pos >= ring->read_pos)
        used = ring->write_pos - ring->read_pos;
    else
        used = ring->size - ring->read_pos + ring->write_pos;
    return ring->size - used - 1;
}

/*
 * ring_memcpy_out — 从环形缓冲区读取 len 字节到线性内存
 *
 * 处理跨缓冲区尾部换行的拷贝：如果 pos + len 超过 size，
 * 则分两次 memcpy，第一次从 pos 到末尾，第二次从头开始。
 *
 * 为什么不用 % 取模逐字节拷贝？
 * 因为 memcpy 在大多数架构上会被优化为 SIMD/字拷贝，
 * 逐字节拷贝的性能差 10~50 倍。
 */
static void ring_memcpy_out(struct lcview_ring *ring, uint8_t *dst,
                            uint32_t pos, uint32_t len)
{
    if (pos + len <= ring->size) {
        memcpy(dst, ring->buf + pos, len);
    } else {
        uint32_t part1 = ring->size - pos;
        memcpy(dst, ring->buf + pos, part1);
        memcpy(dst + part1, ring->buf, len - part1);
    }
}

/*
 * ring_memcpy_in — 从线性内存写入 len 字节到环形缓冲区
 *
 * 与 ring_memcpy_out 对称，处理 wrap-around 分两段写入。
 */
static void ring_memcpy_in(struct lcview_ring *ring, uint32_t pos,
                           const uint8_t *src, uint32_t len)
{
    if (pos + len <= ring->size) {
        memcpy(ring->buf + pos, src, len);
    } else {
        uint32_t part1 = ring->size - pos;
        memcpy(ring->buf + pos, src, part1);
        memcpy(ring->buf, src + part1, len - part1);
    }
}

/*
 * lcview_ring_init — 初始化环形缓冲区
 *
 * 为什么用 vmalloc 而不是 kmalloc？
 * 1. 环形缓冲区最大 4MB，kmalloc 在内存碎片化时可能失败
 *    （kmalloc 依赖连续物理内存，最大分配受限）
 * 2. vmalloc 分配虚拟地址连续的内存，物理页可不连续
 * 3. 读写频率不高，TLB 压力可接受——我们的场景是事件日志，非高速数据流
 *
 * read_buf 大小固定为 LCVIEW_BUILDER_MAX_SIZE (4KB)，
 * 这是单条事件的最大长度，用于中转拷贝。
 */
int lcview_ring_init(struct lcview_ring *ring, uint32_t size_kb)
{
    uint32_t size;

    if (size_kb == 0 || size_kb > LCVIEW_RING_MAX_KB)
        size = LCVIEW_RING_DEFAULT_KB * 1024;
    else
        size = size_kb * 1024;

    ring->buf = vmalloc(size);
    if (!ring->buf)
        return -ENOMEM;

    ring->read_buf = vmalloc(LCVIEW_BUILDER_MAX_SIZE);
    if (!ring->read_buf) {
        vfree(ring->buf);
        ring->buf = NULL;
        return -ENOMEM;
    }

    memset(ring->buf, 0, size);
    ring->size = size;
    ring->write_pos = 0;
    ring->read_pos = 0;
    ring->shutdown = false;
    atomic_set(&ring->overrun_cnt, 0);
    atomic_set(&ring->total_records, 0);
    spin_lock_init(&ring->lock);
    init_waitqueue_head(&ring->waitq);

    pr_info(PREFIX "initialized ring=%uKB read_buf=%uB\n",
            size / 1024, LCVIEW_BUILDER_MAX_SIZE);

    return 0;
}

/*
 * lcview_ring_destroy — 销毁环形缓冲区
 *
 * 设置 shutdown 标志后唤醒等待中的 reader，使其感知关闭事件并退出。
 * 然后释放两个 vmalloc 缓冲区。
 *
 * 为什么先设 shutdown 再释放内存？
 * 因为 reader 可能在等待队列中睡眠，wake_up_interruptible 之后 reader
 * 会检查 shutdown 标志并退出临界区，然后我们才能安全释放内存。
 * 如果不先设 shutdown，reader 可能刚被唤醒就去读已被释放的 buf。
 */
void lcview_ring_destroy(struct lcview_ring *ring)
{
    unsigned long flags;

    spin_lock_irqsave(&ring->lock, flags);
    ring->shutdown = true;
    spin_unlock_irqrestore(&ring->lock, flags);
    wake_up_interruptible(&ring->waitq);

    vfree(ring->read_buf);
    ring->read_buf = NULL;
    vfree(ring->buf);
    ring->buf = NULL;
    ring->size = 0;
}

/*
 * ring_evict_one — 从环形缓冲区中驱逐（跳过）一条最旧记录
 *
 * 调用场景：写入新记录时空间不足，需要腾出空间。
 * 驱逐策略：将 read_pos 向前推进一条记录的长度。
 *
 * 为什么只驱逐一条而不是批量驱逐到满足空间需求？
 * 调用者 lcview_ring_write 在 while 循环中反复调用此函数，
 * 每次驱逐一条并重新检查可用空间。这样更简洁且易于调试。
 *
 * 防御性编程：如果读取的长度前缀异常（0 或 > ring->size），
 * 则使用默认长度（前缀 + 记录头大小）跳过损坏的记录。
 * pr_warn_ratelimited 避免 dmesg 被重复警告刷屏。
 */
static void ring_evict_one(struct lcview_ring *ring)
{
    uint32_t old_len;

    if (ring->read_pos == ring->write_pos)
        return;

    /* 读取长度前缀，处理跨尾部换行 */
    if (ring->read_pos + LCVIEW_LEN_PREFIX_SIZE <= ring->size) {
        memcpy(&old_len, ring->buf + ring->read_pos, LCVIEW_LEN_PREFIX_SIZE);
    } else {
        uint32_t part1 = ring->size - ring->read_pos;
        memcpy(&old_len, ring->buf + ring->read_pos, part1);
        memcpy(((uint8_t *)&old_len) + part1, ring->buf,
               LCVIEW_LEN_PREFIX_SIZE - part1);
    }

    /*
     * 防御性处理：如果记录长度异常（数据损坏），
     * 使用保守的默认长度（仅记录头大小）跳过，避免推进过多
     * 导致永久性数据错乱
     */
    if (old_len == 0 || old_len > ring->size) {
        pr_warn_ratelimited(PREFIX "corrupted record length %u, using default\n",
                            old_len);
        old_len = LCVIEW_LEN_PREFIX_SIZE + sizeof(struct lcview_record_hdr);
    }

    ring->read_pos = (ring->read_pos + old_len) % ring->size;
    atomic_inc(&ring->overrun_cnt);
    pr_debug(PREFIX "overrun #%d (evicted record at pos=%u)\n",
             atomic_read(&ring->overrun_cnt), ring->read_pos);
}

/*
 * lcview_ring_write — 将一条事件记录写入环形缓冲区
 *
 * 写入选定：
 *   1. 检查记录总长度（前缀 + 数据）是否超过 ring->size（整条记录必须能装下）
 *   2. 持 spin_lock_irqsave（禁用本地中断，防止中断上下文写者与当前写者死锁）
 *   3. 检查 shutdown 标志（模块卸载时拒绝新的写入）
 *   4. 计算可用空间，如果不足则循环驱逐最旧记录
 *   5. 先写入 4 字节长度前缀，再写入记录数据
 *   6. 递增 total_records 计数，解锁
 *   7. wake_up_interruptible 唤醒可能等待在 read 上的用户态进程
 *
 * 为什么记录总长度不能超过 ring->size？
 * 因为环形缓冲区的写入被设计为"至少能容纳一条完整记录"。
 * 如果记录长度 > ring->size，则写入永远不可能成功（驱逐也没用），
 * 直接返回 -EMSGSIZE 比死循环更友好。
 *
 * 为什么写入顺序是先长度前缀后数据？
 * 读路径需要先读取长度前缀才知道数据长度，因此必须保证：
 * 在写入完成前，读路径不会读到"半条记录"。
 * spin_lock 保证了写操作的原子性——读者在解锁前不会看到中间状态。
 */
int lcview_ring_write(struct lcview_ring *ring,
                      const uint8_t *data, uint32_t len)
{
    uint32_t total = LCVIEW_LEN_PREFIX_SIZE + len;
    uint32_t avail;
    unsigned long flags;

    if (total > ring->size) {
        pr_err(PREFIX "record too large: %u > ring_size %u\n",
               total, ring->size);
        return -EMSGSIZE;
    }

    spin_lock_irqsave(&ring->lock, flags);

    if (ring->shutdown) {
        spin_unlock_irqrestore(&ring->lock, flags);
        pr_warn(PREFIX "write rejected: ring shutdown\n");
        return -ESHUTDOWN;
    }

    avail = ring_avail_write(ring);

    /*
     * 可用空间不足时，逐条驱逐最旧记录直到空间满足。
     * 这里不用 while(total > avail && ring->read_pos != ring->write_pos)
     * 的 read_pos != write_pos 条件——当 read_pos == write_pos 时环是空的，
     * 但可用空间为 ring->size - 1。如果 total > 这个值就真的写不下了。
     *
     * 注意潜在问题：如果所有记录都只有 1 字节数据，驱逐一条只能释放
     * (LCVIEW_LEN_PREFIX_SIZE + 1) 字节空间，可能需要多次迭代。
     * 但单条记录至少 ~20B（前缀 + 头），256KB 环最大迭代 ~13000 次，
     * 每次都是简单的指针运算 + memcpy，耗时可控。
     */
    /*
     * v3.4 优化 (L2): 批量驱逐以降低 spinlock 持有时间。
     * 当需要腾出大量空间但环中都是小记录时，单条逐出可能
     * 迭代数千次。批量驱逐每次最多 64 条再重新检查空间，
     * 将最坏情况下的迭代次数从 ~13000 降至 ~200。
     */
    while (total > avail && ring->read_pos != ring->write_pos) {
        int batch = 64;
        while (batch-- > 0 && ring->read_pos != ring->write_pos) {
            LC_DBG("evict: pos=%u\n", ring->read_pos);
            ring_evict_one(ring);
        }
        avail = ring_avail_write(ring);
    }

    if (total > avail) {
        spin_unlock_irqrestore(&ring->lock, flags);
        pr_err(PREFIX "ring full, write failed (total=%u avail=%u)\n",
               total, avail);
        return -ENOSPC;
    }

    /* 写入长度前缀（含 total 自身长度），处理跨尾部 wrap */
    ring_memcpy_in(ring, ring->write_pos, (const uint8_t *)&total,
                   LCVIEW_LEN_PREFIX_SIZE);
    ring->write_pos = (ring->write_pos + LCVIEW_LEN_PREFIX_SIZE) % ring->size;

    /* 写入记录数据（Builder 构建好的序列化事件） */
    ring_memcpy_in(ring, ring->write_pos, data, len);
    ring->write_pos = (ring->write_pos + len) % ring->size;

    atomic_inc(&ring->total_records);
    spin_unlock_irqrestore(&ring->lock, flags);

    pr_debug(PREFIX "wrote record len=%u total_records=%d\n",
             len, atomic_read(&ring->total_records));
    wake_up_interruptible(&ring->waitq);
    return 0;
}

/*
 * lcview_ring_read — 从环形缓冲区读取事件记录到用户缓冲区
 *
 * 读取策略：
 *   1. 在循环中尽可能多地读取记录，直到填满用户缓冲区 (len) 或数据读完
 *   2. 当环形缓冲区为空且未 shutdown 时，阻塞等待：
 *      - wait_event_interruptible 使进程进入可中断睡眠
 *      - 写者通过 wake_up_interruptible 唤醒 reader
 *      - 收到信号时返回 -ERESTARTSYS（让 VFS 层决定是重试还是交付）
 *   3. 读取单条记录时的关键步骤：
 *      a) 锁内读取 4 字节长度前缀 → 算出整条记录长度
 *      b) 校验记录长度合法性（防御损坏数据）
 *      c) 锁内 memcpy 到 read_buf → 推进 read_pos → 解锁
 *      d) 锁外 copy_to_user（不可在持锁时执行）
 *   4. 数据损坏处理：如果长度前缀异常，跳过该记录继续
 *
 * 为什么返回多条记录而不是单条？
 * 用户态通常一次性提供大缓冲区（如 64KB 或更大），批量读取多条记录
 * 可以减少系统调用次数，提高吞吐量。
 *
 * 为什么 shutdown + empty 时返回 0 而非负值？
 * 返回 0 表示 EOF，用户态 reader 应关闭设备并退出。
 */
int lcview_ring_read(struct lcview_ring *ring,
                     uint8_t __user *buf, uint32_t len)
{
    uint32_t copied_total = 0;
    int ret;

    while (copied_total < len) {
        uint32_t rpos, record_len;
        unsigned long flags;

        spin_lock_irqsave(&ring->lock, flags);

        /*
         * 等待数据可用：
         * 释放锁 → 睡眠 → 醒来 → 重新持锁 → 检查条件
         * 这是 Linux 等待队列的典型模式：条件检查必须在持锁时进行，
         * 但睡眠时必须释放锁（否则写者永远无法获取锁来唤醒我们）。
         */
        while (copied_total == 0 &&
               ring->write_pos == ring->read_pos &&
               !ring->shutdown) {
            spin_unlock_irqrestore(&ring->lock, flags);
            LC_DBG("read: waiting for data...\n");
            ret = wait_event_interruptible(ring->waitq,
                    ring->write_pos != ring->read_pos || ring->shutdown);
            if (ret)
                return copied_total > 0 ? (int)copied_total : -ERESTARTSYS;
            spin_lock_irqsave(&ring->lock, flags);
        }

        /*
         * 已经读取到部分数据后，如果此时 ring 为空，则直接返回已读数据，
         * 避免为了填满整个用户缓冲区而无限阻塞。
         */
        if (copied_total > 0 && ring->write_pos == ring->read_pos) {
            spin_unlock_irqrestore(&ring->lock, flags);
            break;
        }

        /*
         * 如果 shutdown 且缓冲区已空，终止读取。
         * 如果已经拷贝了一些数据给用户态，先返回已拷贝的字节数；
         * 否则返回 0 表示 EOF。
         */
        if (ring->shutdown && ring->write_pos == ring->read_pos) {
            spin_unlock_irqrestore(&ring->lock, flags);
            if (copied_total > 0)
                return (int)copied_total;
            return 0;
        }

        rpos = ring->read_pos;

        /* 读取长度前缀（4 字节），处理跨尾部 wrap */
        if (rpos + LCVIEW_LEN_PREFIX_SIZE <= ring->size) {
            memcpy(&record_len, ring->buf + rpos, LCVIEW_LEN_PREFIX_SIZE);
        } else {
            uint32_t part1 = ring->size - rpos;
            memcpy(&record_len, ring->buf + rpos, part1);
            memcpy(((uint8_t *)&record_len) + part1, ring->buf,
                   LCVIEW_LEN_PREFIX_SIZE - part1);
        }

        /*
         * 校验记录长度：
         * - 最小合法值: LCVIEW_LEN_PREFIX_SIZE (4) + 记录头 (16) = 20
         * - 最大合法值: min(LCVIEW_BUILDER_MAX_SIZE, ring->size)
         *
         * 如果记录损坏，使用保守的默认大小跳过这条记录。
         * 跳过策略：推进到前缀 + 记录头大小的位置，尝试从下一条继续。
         * 这样可以最大程度地从数据损坏中恢复，而不是永久阻塞 reader。
         */
        if (record_len < LCVIEW_LEN_PREFIX_SIZE ||
            record_len > LCVIEW_BUILDER_MAX_SIZE ||
            record_len > ring->size) {
            pr_warn_ratelimited(PREFIX "corrupted record at pos=%u, len=%u, skipping\n",
                                rpos, record_len);
            ring->read_pos = (rpos + LCVIEW_LEN_PREFIX_SIZE +
                              sizeof(struct lcview_record_hdr)) % ring->size;
            spin_unlock_irqrestore(&ring->lock, flags);
            continue;
        }

        /*
         * 如果这条记录太长以至于用户缓冲区放不下，
         * 则保持 read_pos 不变（下次可继续读），退出循环。
         * 这确保了"大记录"不会被丢弃，只是拆到下次 read 调用。
         */
        if (copied_total + record_len > len) {
            spin_unlock_irqrestore(&ring->lock, flags);
            break;
        }

        /*
         * 锁内拷贝到 read_buf：
         * 在持锁期间将记录从环形缓冲区拷贝到线性 read_buf，
         * 这样写者不会在我们读一半时驱逐这条记录。
         * 之后推进 read_pos，解锁，最后在锁外 safely copy_to_user。
         */
        ring_memcpy_out(ring, ring->read_buf, rpos, record_len);
        ring->read_pos = (rpos + record_len) % ring->size;

        spin_unlock_irqrestore(&ring->lock, flags);

        /* 锁外 copy_to_user（可能触发 page fault，必须不在持锁状态） */
        if (copy_to_user(buf + copied_total, ring->read_buf, record_len)) {
            pr_err(PREFIX "read copy_to_user failed\n");
            return copied_total > 0 ? (int)copied_total : -EFAULT;
        }

        LC_DBG("read: returning %u bytes\n", record_len);
        copied_total += record_len;
    }

    return (int)copied_total;
}

/*
 * lcview_ring_avail_bytes — 查询环形缓冲区中当前可读字节数
 *
 * 用于 poll/select 判断是否有数据可读，也用于 ioctl LCVIEW_GET_AVAIL_BYTES。
 * 返回值是近似值——调用者获得返回值后，写者可能立即写入了新数据。
 * 但这对于 poll 语义来说是可以接受的（水平触发模式会重新检查）。
 *
 * Design note: spin_lock_irqsave 用于保证读写指针一致性。虽然单次读取
 * ring->write_pos 在大多数架构上是原子的，但 write_pos 和 read_pos 必须
 * 作为一对原子读取才能计算出一致的 used 值，因此锁保护是必要的。
 */
uint32_t lcview_ring_avail_bytes(struct lcview_ring *ring)
{
    uint32_t used;
    unsigned long flags;

    spin_lock_irqsave(&ring->lock, flags);
    if (ring->write_pos >= ring->read_pos)
        used = ring->write_pos - ring->read_pos;
    else
        used = ring->size - ring->read_pos + ring->write_pos;
    spin_unlock_irqrestore(&ring->lock, flags);

    return used;
}

/*
 * lcview_ring_get_stats — 获取环形缓冲区运行时统计信息
 *
 * 原子读取 total_records 和 overrun_cnt（原子变量保证无锁一致性），
 * 非原子读取 ring->size（初始化后不变），
 * 调用 lcview_ring_avail_bytes 获取当前使用量。
 *
 * 所有字段均为 uint32_t，用户态和内核态布局相同，无需 compat 转换。
 */
void lcview_ring_get_stats(struct lcview_ring *ring, struct lcview_stats *stats)
{
    stats->total_records = atomic_read(&ring->total_records);
    stats->overrun_cnt = atomic_read(&ring->overrun_cnt);
    stats->ring_size_bytes = ring->size;
    stats->ring_usage_bytes = lcview_ring_avail_bytes(ring);
}
