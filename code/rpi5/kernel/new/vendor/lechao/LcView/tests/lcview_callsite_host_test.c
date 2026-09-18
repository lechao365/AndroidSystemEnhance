/*
 * lcview_callsite_host_test.c — LcView 调用点真实代码 host 判红单测
 *
 * 与 lcview_ring_host_test.c（纯 logic.c 函数）互补：本文件把调用点所在
 * 源文件（../lcview_builder.c、../lcview_ring.c）连同 host_shim 头一并编入
 * host 测试，直接调用真实调用点函数（lcview_builder_add_str /
 * lcview_builder_add_binary / lcview_ring_read 判损坏跳过），使调用点改坏
 * （漏扣 4B 前缀 / 判损坏跳过只前移前缀+头）在 host 层判红，不再"改坏照绿"。
 *
 * UAF 修复判红（方向 2/3/7）：read 改为 readers 计数 + shutdown 入口检查的
 * 包装，destroy 经 wait_event(exit_wait) 等 readers 归零再 vfree。本文件
 * 构造 ring 均补 readers/exit_wait 两字段初始化；既有"判损坏跳过"用例改走
 * 内部读路径（shutdown=false + 后跟合法记录），并新增 destroy 后 read 返 0、
 * shutdown 含数据停交付、正常/EMSGSIZE 后 readers 归零四类判红。
 *
 * 编译运行：make test（Makefile 已链入调用点文件 + -D__KERNEL__ + shim 头）。
 * 退出码 0 全过。
 */

#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "lcview_internal.h"
#include "lcview_ring_logic.h"

static int g_checks = 0;
static int g_fails = 0;

#define CHECK(cond) do { \
    g_checks++; \
    if (!(cond)) { \
        g_fails++; \
        fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
    } \
} while (0)

/* 小端写入 4 字节 uint32 长度前缀 */
static void put_u32(uint8_t *p, uint32_t v)
{
    p[0] = (uint8_t)(v & 0xff);
    p[1] = (uint8_t)((v >> 8) & 0xff);
    p[2] = (uint8_t)((v >> 16) & 0xff);
    p[3] = (uint8_t)((v >> 24) & 0xff);
}

/*
 * 调用点判红（方向 1）：lcview_builder_add_str 在内容写满 4096 时
 * 必须返回 -ENOSPC（修复前漏扣 4B 记录前缀，恰好判"装得下"，
 * commit 后 record_len=4100 被读侧判损坏丢弃）。此处直接调真实调用点。
 */
static void test_add_str_callsite_overflow(void)
{
    struct lcview_builder *b = lcview_builder_new(LCVIEW_EVENT_USB_TRANSPORT_END,
                                                  LCVIEW_LEVEL_INFO);
    CHECK(b != NULL);
    if (!b)
        return;

    /* 填满 16B 头 + 3B(prefix/type) + 4B(prefix/len) 后 data_offset=23，
     * 剩余 LCVIEW_BUILDER_MAX_SIZE - 23 = 4073 字节可写变长数据。
     * 再加 1 字节 data 即需 data_offset+4+3+4074 > 4096 → -ENOSPC。
     * 修复前漏扣 4B 前缀：data_offset+3+4074=4100 > 4096 本也应超——
     * 该用例在 4096 边界上方区分，边界处判红见下面 data_offset 精确用例。
     * 缓冲须补 NUL 结尾：add_str 走 compute_str_len → strlen，未终止的
     * 栈缓冲导致 strlen 越界读（UB），长度不可预期用例失效。 */
    char blob[4075];
    memset(blob, 'x', 4074);
    blob[4074] = '\0';   /* strlen = 4074 → 4+3+4074 = 4097 > 4096 → -ENOSPC */
    int rc = lcview_builder_add_str(b, blob);
    CHECK(rc == -ENOSPC);

    lcview_builder_free(b);
}

/*
 * 调用点判红（方向 1）：add_binary 容量检查同样须扣 4B 记录前缀。
 * 直接调真实调用点函数验证超限返回 -ENOSPC。
 */
static void test_add_binary_callsite_overflow(void)
{
    struct lcview_builder *b = lcview_builder_new(LCVIEW_EVENT_USB_DATA_CORRUPT,
                                                  LCVIEW_LEVEL_WARN);
    CHECK(b != NULL);
    if (!b)
        return;

    uint8_t blob[4074];
    memset(blob, 0xAB, sizeof(blob));
    int rc = lcview_builder_add_binary(b, blob, sizeof(blob));
    CHECK(rc == -ENOSPC);

    lcview_builder_free(b);
}

/*
 * 调用点判红（方向 1 边界）：内容恰好填满 4096 边界时 add_str 应判超限
 * （data_offset=16 起，4B 前缀 + 3B 字段头 + 4073B 数据 = 4096 恰好，
 *  再加 1B → 4100 超限）。修复前按 data_offset+3+data 对比上限漏扣前缀：
 *  16+3+4077=4096 恰好"装得下"，误放行产生 4100 超限记录。
 */
static void test_add_str_callsite_boundary(void)
{
    struct lcview_builder *b = lcview_builder_new(LCVIEW_EVENT_USB_RATE_DEGRADED,
                                                  LCVIEW_LEVEL_INFO);
    CHECK(b != NULL);
    if (!b)
        return;

    char blob[4078];
    memset(blob, 'y', 4077);
    blob[4077] = '\0';   /* strlen = 4077（补 NUL 防 strlen 越界 UB） */
    /* data_offset=16：4(前缀)+3(type/len)+4077 = 4100 > 4096 → -ENOSPC */
    int rc = lcview_builder_add_str(b, blob);
    CHECK(rc == -ENOSPC);

    /* 4073B 恰好：4+3+4073 = 4080 ≤ 4096 → 装得下 */
    char small[4074];
    memset(small, 'z', 4073);
    small[4073] = '\0';  /* strlen = 4073（补 NUL 防 strlen 越界 UB） */
    struct lcview_builder *b2 = lcview_builder_new(LCVIEW_EVENT_USB_RATE_DEGRADED,
                                                   LCVIEW_LEVEL_INFO);
    CHECK(b2 != NULL);
    if (b2) {
        rc = lcview_builder_add_str(b2, small);
        CHECK(rc == 0);
        lcview_builder_free(b2);
    }

    lcview_builder_free(b);
}

/*
 * 调用点判红（方向 2）：lcview_ring_read 判损坏跳过须按 record_len（写侧
 * 写入的长度前缀）前移，而非只前移前缀+头（20B）。构造 record_len=4100
 * （合法：4 ≤ 4100 ≤ ring->size=8192，但 > LCVIEW_BUILDER_MAX_SIZE 判损坏）
 * 的损坏记录，验证 read 后 read_pos 前移 4100 而非 20。
 *
 * 修复前按固定 20 前移会让 read_pos 落进记录体中间，把后续记录当损坏
 * 撕裂整个流；修复后按 record_len 前移，跳过量正确。
 */
static void test_ring_read_callsite_corrupt_skip(void)
{
    uint8_t ringbuf[8192];
    uint8_t readbuf[4096];
    uint8_t user[4096];
    struct lcview_ring ring;

    memset(ringbuf, 0, sizeof(ringbuf));
    memset(readbuf, 0, sizeof(readbuf));
    memset(user, 0, sizeof(user));

    /* 一条损坏记录：长度前缀 4100（> MAX 判损坏，但 ≤ ring->size 可信），
     * 后跟一条合法记录 20B（前缀 4 + 记录头 16），跳过损坏后正常读完
     * 返回（copied_total>0 且 ring 空 → break，避免空环阻塞等待）。 */
    put_u32(ringbuf, 4100);
    put_u32(ringbuf + 4100, 20);

    ring.buf = ringbuf;
    ring.read_buf = readbuf;
    ring.size = sizeof(ringbuf);
    ring.write_pos = 4100 + 20;  /* 损坏记录 4100B + 合法记录 20B */
    ring.read_pos = 0;
    ring.shutdown = false;  /* UAF 修复新语义：shutdown=true 时 read 入口直返
                             * 0，须走内部读路径才能判红跳过前移量 */
    atomic_set(&ring.overrun_cnt, 0);
    atomic_set(&ring.total_records, 0);
    atomic_set(&ring.readers, 0);
    init_waitqueue_head(&ring.exit_wait);
    mutex_init(&ring.read_mutex);

    /* read 判损坏后按 record_len=4100 前移 read_pos（% size），再读合法记录 */
    int n = lcview_ring_read(&ring, user, sizeof(user));
    /* 跳过 4100B 损坏记录 + 读到 20B 合法记录，返回 20（修复前只跳 20 撕裂流） */
    CHECK(n == 20);
    CHECK(ring.read_pos == (0 + 4100 + 20) % sizeof(ringbuf)); /* 4120 */
    /* 读调用退出后 readers 归零（destroy 可安全释放内存） */
    CHECK(atomic_read(&ring.readers) == 0);
}

/*
 * 调用点判红（方向 2 边界）：垃圾前缀（> ring->size）时回落保守默认跳过
 * （前缀+头），验证 read_pos 按默认跳过量前移而非撕裂。
 */
static void test_ring_read_callsite_corrupt_garbage(void)
{
    uint8_t ringbuf[8192];
    uint8_t readbuf[4096];
    uint8_t user[4096];
    struct lcview_ring ring;

    memset(ringbuf, 0, sizeof(ringbuf));
    memset(readbuf, 0, sizeof(readbuf));
    memset(user, 0, sizeof(user));

    /* 垃圾前缀 9000 > ring->size=8192 → 不可信，回落保守默认 20；
     * 后跟合法记录 20B（前缀4+头16），跳过 20 后读合法记录正常返回。 */
    put_u32(ringbuf, 9000);
    put_u32(ringbuf + 20, 20);

    ring.buf = ringbuf;
    ring.read_buf = readbuf;
    ring.size = sizeof(ringbuf);
    ring.write_pos = 40;
    ring.read_pos = 0;
    ring.shutdown = false;
    atomic_set(&ring.overrun_cnt, 0);
    atomic_set(&ring.total_records, 0);
    atomic_set(&ring.readers, 0);
    init_waitqueue_head(&ring.exit_wait);
    mutex_init(&ring.read_mutex);

    int n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == 20);
    /* 默认跳过量 = 前缀 4 + 记录头 16 = 20，再读 20B 合法记录 → 40 */
    CHECK(ring.read_pos == (0 + 20 + 20) % sizeof(ringbuf));
    CHECK(atomic_read(&ring.readers) == 0);
}

/*
 * 调用点判红（方向 7/方向 1）：ring_write 巨 len 拒写。
 * len 接近 UINT32_MAX 时，若先算 total = 4 + len 会溢出回绕成小值，
 * 绕过 total > ring->size 检查进入写路径越界。修复后先判
 * len > ring->size - 4 直接拒 -EMSGSIZE。
 */
static void test_ring_write_callsite_huge_len(void)
{
    uint8_t ringbuf[8192];
    uint8_t readbuf[4096];
    struct lcview_ring ring;

    memset(ringbuf, 0, sizeof(ringbuf));
    memset(readbuf, 0, sizeof(readbuf));

    ring.buf = ringbuf;
    ring.read_buf = readbuf;
    ring.size = sizeof(ringbuf);
    ring.write_pos = 0;
    ring.read_pos = 0;
    ring.shutdown = false;
    atomic_set(&ring.overrun_cnt, 0);
    atomic_set(&ring.total_records, 0);
    atomic_set(&ring.readers, 0);
    init_waitqueue_head(&ring.exit_wait);
    mutex_init(&ring.read_mutex);
    spin_lock_init(&ring.lock);

    /* 巨 len：total = 4 + 0xFFFFFFFC 溢出回绕为 0，修复前绕过检查越界写 */
    int rc = lcview_ring_write(&ring, ringbuf, 0xFFFFFFFC);
    CHECK(rc == -EMSGSIZE);
}

/*
 * 调用点判红（方向 7/方向 4）：空指针 cancel 不崩。
 * 修复前 lcview_builder_cancel(NULL) 对 NULL 解引用 b->event_id 崩溃；
 * 修复后空指针容忍直接返回。
 */
static void test_builder_cancel_callsite_null(void)
{
    lcview_builder_cancel(NULL);
    CHECK(1);  /* 未崩溃即通过 */
}

/*
 * 调用点判红（方向 7/方向 5）：短前缀记录判损坏跳过。
 * 记录长度下限由 4 改为 default_skip 20（前缀+记录头），[4,20)
 * 前缀判损坏且长度不可信——ring_corrupt_skip_len(10, size, 20) = 20。
 * 修复前下限 4：10 ≥ 4 判合法，正常读给用户 n=10（非跳过）；
 * 且修复前 skip 下界也是 4，跳过 10 会落进记录体中间撕裂后续流。
 * 合法记录放在 pos 20，read 跳过默认 20 后读到。
 */
static void test_ring_read_callsite_short_prefix(void)
{
    uint8_t ringbuf[8192];
    uint8_t readbuf[4096];
    uint8_t user[4096];
    struct lcview_ring ring;

    memset(ringbuf, 0, sizeof(ringbuf));
    memset(readbuf, 0, sizeof(readbuf));
    memset(user, 0, sizeof(user));

    /* 短前缀 10：< 20（前缀+头）判损坏且不可信；
     * 后跟合法记录 20B（pos 20），跳过默认 20 后读合法记录正常返回。 */
    put_u32(ringbuf, 10);
    put_u32(ringbuf + 20, 20);

    ring.buf = ringbuf;
    ring.read_buf = readbuf;
    ring.size = sizeof(ringbuf);
    ring.write_pos = 40;   /* 损坏前缀 4B + 跳过空洞 16B + 合法记录 20B */
    ring.read_pos = 0;
    ring.shutdown = false;
    atomic_set(&ring.overrun_cnt, 0);
    atomic_set(&ring.total_records, 0);
    atomic_set(&ring.readers, 0);
    init_waitqueue_head(&ring.exit_wait);
    mutex_init(&ring.read_mutex);

    int n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == 20);
    /* 判损坏跳过 ring_corrupt_skip_len(10,...) = default 20，再读 20B → 40 */
    CHECK(ring.read_pos == (0 + 20 + 20) % sizeof(ringbuf));
    CHECK(atomic_read(&ring.readers) == 0);
}

/*
 * 调用点判红（方向 7/方向 4）：等长记录 record_len == ring->size 判损坏。
 * 修复前上界为 >：等长记录不判损坏，读到后 (rpos + size) % size == rpos
 * 零推进，写指针环绕重合时 read 死循环或重复交付。修复后
 * record_len >= ring->size 判损坏，corrupt_skip_len 判不可信回落默认
 * default_skip 前移，跳过损坏后正常读到后续合法记录。
 */
static void test_ring_read_callsite_equal_size(void)
{
    uint8_t ringbuf[64];
    uint8_t readbuf[4096];
    uint8_t user[4096];
    struct lcview_ring ring;

    memset(ringbuf, 0, sizeof(ringbuf));
    memset(readbuf, 0, sizeof(readbuf));
    memset(user, 0, sizeof(user));

    /* 等长前缀 64 == ring->size=64 → 判损坏（零推进消除）；
     * 后跟合法记录 20B（pos 20），跳过默认 20 后读到。 */
    put_u32(ringbuf, 64);
    put_u32(ringbuf + 20, 20);

    ring.buf = ringbuf;
    ring.read_buf = readbuf;
    ring.size = sizeof(ringbuf);
    ring.write_pos = 40;
    ring.read_pos = 0;
    ring.shutdown = false;
    atomic_set(&ring.overrun_cnt, 0);
    atomic_set(&ring.total_records, 0);
    atomic_set(&ring.readers, 0);
    init_waitqueue_head(&ring.exit_wait);
    mutex_init(&ring.read_mutex);

    int n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == 20);
    /* 等长判损坏 → ring_corrupt_skip_len(64,64,20) = 20，再读 20B → 40 */
    CHECK(ring.read_pos == (0 + 20 + 20) % sizeof(ringbuf));
    CHECK(atomic_read(&ring.readers) == 0);
}

/*
 * 方向 7（UAF 修复判红）：destroy 后 read 直返 0（EOF）。
 * 修复前 destroy 直接 vfree，reader 可能读已释放内存（UAF）；修复后
 * lcview_ring_read 入口 inc readers + 持锁查 shutdown，销毁后新读返回 0，
 * 且入口/出口的 readers 计数在 destroy 的 wait_event 保护下归零。
 * 用 lcview_ring_init 构造（vmalloc shim=malloc），destroy 的 vfree 安全。
 */
static void test_ring_destroy_read_zero(void)
{
    struct lcview_ring ring;
    uint8_t user[256];

    CHECK(lcview_ring_init(&ring, 1) == 0);
    /* destroy：置 shutdown → wait_event(exit_wait, readers==0)（未读，归零）→ vfree */
    lcview_ring_destroy(&ring);
    /* 销毁后新 read 入口查 shutdown 直返 0，不触碰已释放的 buf/read_buf */
    int n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == 0);
    CHECK(atomic_read(&ring.readers) == 0);
}

/*
 * 方向 7（UAF 修复判红）：shutdown 含数据停交付。
 * ring 中已有可读记录（68B），但 shutdown 置位后 read 入口立即停交付
 * 返回 0，不消费剩余记录——配合 destroy 的 wait_event 尽快收敛。
 */
static void test_ring_shutdown_stops_delivery(void)
{
    struct lcview_ring ring;
    uint8_t user[256];
    uint8_t payload[64];

    CHECK(lcview_ring_init(&ring, 1) == 0);
    memset(payload, 0x11, sizeof(payload));
    CHECK(lcview_ring_write(&ring, payload, sizeof(payload)) == 0);

    /* ring 含 68B 记录，但 shutdown 置位后 read 停交付（入口返 0） */
    ring.shutdown = true;
    int n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == 0);
    CHECK(atomic_read(&ring.readers) == 0);

    ring.shutdown = false;
    lcview_ring_destroy(&ring);
}

/*
 * 方向 7（UAF 修复判红）：正常 read 返回后 readers 归零。
 * read 包装出口 atomic_dec_and_test 归零，destroy 的 wait_event 据此
 * 判定"可安全释放内存"；归零失败即 destroy 提前 vfree 的 UAF 风险。
 */
static void test_ring_read_readers_zero(void)
{
    struct lcview_ring ring;
    uint8_t user[256];
    uint8_t payload[64];

    CHECK(lcview_ring_init(&ring, 1) == 0);
    memset(payload, 0x22, sizeof(payload));
    CHECK(lcview_ring_write(&ring, payload, sizeof(payload)) == 0);

    int n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == 68);   /* 4B 前缀 + 64B 数据 */
    CHECK(atomic_read(&ring.readers) == 0);   /* 正常路径出口归零 */
    lcview_ring_destroy(&ring);
}

/*
 * 方向 7（UAF 修复判红）：EMSGSIZE 错误路径后 readers 也归零。
 * 首条记录（4+100=104B）放不进 64B 用户缓冲 → -EMSGSIZE（KRN-001），
 * 该错误从内部函数提前 return，readers 须由包装出口归零，否则 destroy
 * wait_event 永等、销毁死锁。
 */
static void test_ring_read_emsgsize_readers_zero(void)
{
    struct lcview_ring ring;
    uint8_t user[64];
    uint8_t payload[100];

    CHECK(lcview_ring_init(&ring, 1) == 0);
    memset(payload, 0x33, sizeof(payload));
    CHECK(lcview_ring_write(&ring, payload, sizeof(payload)) == 0);

    int n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == -EMSGSIZE);
    CHECK(atomic_read(&ring.readers) == 0);   /* 错误路径出口归零 */
    lcview_ring_destroy(&ring);
}

/*
 * 方向 1 判红（池 cmpxchg 修复）：连续 put 后 get 不返已释放对象，
 * 复用先入槽者。
 *
 * 场景：b1 先入槽（先入槽者），b2 再 put 时槽已满被释放（池容量 1）。
 * 修复前 xchg 先存再 free：put(b2) 把 b2 存入槽覆盖 b1 并 kfree(b2)——
 * 槽中留下已释放的 b2，get 返回已释放对象（UAF）；修复后 cmpxchg
 * 仅槽空才存入，b2 从未入槽即 kfree，槽保持 b1（先入槽者，存活），
 * get 复用 b1。断言 b3 == b1（复用先入槽者）且 != b2（未返已释放）。
 */
static void test_pool_put_get_reuse_first(void)
{
    struct lcview_builder *b1, *b2, *b3;

    b1 = lcview_builder_new(LCVIEW_EVENT_USB_CONNECT, LCVIEW_LEVEL_INFO);
    b2 = lcview_builder_new(LCVIEW_EVENT_USB_CONNECT, LCVIEW_LEVEL_INFO);
    CHECK(b1 != NULL && b2 != NULL);
    if (!b1 || !b2)
        return;
    CHECK(b1 != b2);   /* 池空：两次 new 均走 kmalloc，指针不同 */

    /* 连续 put：b1 入槽（先入槽者），b2 再入槽被释放（池容量 1） */
    lcview_builder_free(b1);
    lcview_builder_free(b2);

    /* get 复用先入槽者 b1（存活），而非已释放的 b2 */
    b3 = lcview_builder_new(LCVIEW_EVENT_USB_CONNECT, LCVIEW_LEVEL_INFO);
    CHECK(b3 == b1);   /* 复用先入槽者（xchg 版本返回已释放的 b2，判红） */
    CHECK(b3 != b2);   /* 未返已释放对象 */
    if (b3) {
        /* b1 内存存活可用：add_str 写入成功证明未被释放 */
        int rc = lcview_builder_add_str(b3, "reuse-ok");
        CHECK(rc == 0);
        lcview_builder_free(b3);
    }
}

/*
 * 方向 2 判红（满长 4096 交付）：恰好 4096B 的满长记录（4B 前缀 +
 * 4092B 数据）写入后正常读出交付。
 * 修复前读侧 record_len >= LCVIEW_BUILDER_MAX_SIZE 判损坏，满长记录
 * 被跳过（误伤丢记录）；恢复严格大于后 record_len == 4096 合法，
 * 完整交付且不误伤。
 */
static void test_ring_read_callsite_max_record_delivery(void)
{
    struct lcview_ring ring;
    uint8_t user[8192];
    uint8_t payload[4092];

    CHECK(lcview_ring_init(&ring, 8) == 0);   /* 8KB 环，能容纳满长记录 */
    memset(payload, 0x44, sizeof(payload));
    CHECK(lcview_ring_write(&ring, payload, sizeof(payload)) == 0);

    int n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == 4096);                         /* 满长完整交付（修复前判损坏跳过返 0） */
    CHECK(memcmp(user + 4, payload, sizeof(payload)) == 0);
    CHECK(ring.read_pos == 4096 % ring.size);
    CHECK(atomic_read(&ring.readers) == 0);
    CHECK(ring.read_mutex.locked == 0);       /* 读后锁平衡 */
    lcview_ring_destroy(&ring);
}

/*
 * 方向 7 判红（读后锁平衡）：host_shim mutex 带锁态断言后，read 任何出口
 * 漏 unlock 都会判红。覆盖正常交付、EMSGSIZE 错误、shutdown 停交付、
 * destroy 后 EOF 四类出口，断言返回后 read_mutex 回到解锁态（locked == 0）。
 */
static void test_ring_read_lock_balanced(void)
{
    struct lcview_ring ring;
    uint8_t user[256];
    uint8_t small[64];
    uint8_t payload[64];

    CHECK(lcview_ring_init(&ring, 1) == 0);

    /* 正常出口：read 返回后锁平衡 */
    memset(payload, 0x55, sizeof(payload));
    CHECK(lcview_ring_write(&ring, payload, sizeof(payload)) == 0);
    int n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == 68);                           /* 4B 前缀 + 64B 数据 */
    CHECK(ring.read_mutex.locked == 0);
    CHECK(atomic_read(&ring.readers) == 0);

    /* EMSGSIZE 出口：首条放不下小缓冲，错误路径返回后锁平衡 */
    memset(payload, 0x66, sizeof(payload));
    CHECK(lcview_ring_write(&ring, payload, sizeof(payload)) == 0);
    n = lcview_ring_read(&ring, small, sizeof(small));
    CHECK(n == -EMSGSIZE);
    CHECK(ring.read_mutex.locked == 0);
    CHECK(atomic_read(&ring.readers) == 0);

    /* shutdown 出口：入口查 shutdown 直返 0，锁平衡 */
    ring.shutdown = true;
    n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == 0);
    CHECK(ring.read_mutex.locked == 0);
    CHECK(atomic_read(&ring.readers) == 0);
    ring.shutdown = false;

    /* destroy 后 EOF 出口：销毁后 read 直返 0，锁平衡 */
    lcview_ring_destroy(&ring);
    n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == 0);
    CHECK(ring.read_mutex.locked == 0);
    CHECK(atomic_read(&ring.readers) == 0);
}

int main(void)
{
    test_add_str_callsite_overflow();
    test_add_binary_callsite_overflow();
    test_add_str_callsite_boundary();
    test_ring_read_callsite_corrupt_skip();
    test_ring_read_callsite_corrupt_garbage();
    test_ring_write_callsite_huge_len();
    test_builder_cancel_callsite_null();
    test_ring_read_callsite_short_prefix();
    test_ring_read_callsite_equal_size();
    test_ring_destroy_read_zero();
    test_ring_shutdown_stops_delivery();
    test_ring_read_readers_zero();
    test_ring_read_emsgsize_readers_zero();
    test_pool_put_get_reuse_first();
    test_ring_read_callsite_max_record_delivery();
    test_ring_read_lock_balanced();
    if (g_fails) {
        printf("FAIL: %d/%d checks failed\n", g_fails, g_checks);
        return 1;
    }
    printf("OK: all %d checks passed (callsite)\n", g_checks);
    return 0;
}
