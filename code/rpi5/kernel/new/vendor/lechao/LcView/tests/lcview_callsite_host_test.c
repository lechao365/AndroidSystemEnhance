/*
 * lcview_callsite_host_test.c — LcView 调用点真实代码 host 判红单测
 *
 * 与 lcview_ring_host_test.c（纯 logic.c 函数）互补：本文件把调用点所在
 * 源文件（../lcview_builder.c、../lcview_ring.c）连同 host_shim 头一并编入
 * host 测试，直接调用真实调用点函数（lcview_builder_add_str /
 * lcview_builder_add_binary / lcview_ring_read 判损坏跳过），使调用点改坏
 * （漏扣 4B 前缀 / 判损坏跳过只前移前缀+头）在 host 层判红，不再"改坏照绿"。
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
     * 该用例在 4096 边界上方区分，边界处判红见下面 data_offset 精确用例。 */
    char blob[4074];
    memset(blob, 'x', sizeof(blob));
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

    char blob[4077];
    memset(blob, 'y', sizeof(blob));
    /* data_offset=16：4(前缀)+3(type/len)+4077 = 4100 > 4096 → -ENOSPC */
    int rc = lcview_builder_add_str(b, blob);
    CHECK(rc == -ENOSPC);

    /* 4073B 恰好：4+3+4073 = 4080 ≤ 4096 → 装得下 */
    char small[4073];
    memset(small, 'z', sizeof(small));
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

    /* 一条损坏记录：长度前缀 4100（> MAX 判损坏，但 ≤ ring->size 可信） */
    put_u32(ringbuf, 4100);

    ring.buf = ringbuf;
    ring.read_buf = readbuf;
    ring.size = sizeof(ringbuf);
    ring.write_pos = 4100;   /* 假想写者已写入 4100B 损坏记录 */
    ring.read_pos = 0;
    ring.shutdown = true;    /* 判损坏跳过排空后返回 EOF（0），不阻塞等待 */
    atomic_set(&ring.overrun_cnt, 0);
    atomic_set(&ring.total_records, 0);

    /* read 判损坏后按 record_len=4100 前移 read_pos（% size） */
    int n = lcview_ring_read(&ring, user, sizeof(user));
    /* 4100 > 4096（用户缓冲），首条判损坏跳过，ring 已排空 → 返回 0（EOF） */
    CHECK(n == 0);
    CHECK(ring.read_pos == (0 + 4100) % sizeof(ringbuf)); /* 4100，修复前为 20 */
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

    /* 垃圾前缀 9000 > ring->size=8192 → 不可信，回落保守默认 20
     * write_pos=20 表示该记录实际仅占 20B（前缀4+头16），read 跳过
     * 后 read_pos 追上 write_pos，shutdown 下返回 EOF（0）。 */
    put_u32(ringbuf, 9000);

    ring.buf = ringbuf;
    ring.read_buf = readbuf;
    ring.size = sizeof(ringbuf);
    ring.write_pos = 20;
    ring.read_pos = 0;
    ring.shutdown = true;
    atomic_set(&ring.overrun_cnt, 0);
    atomic_set(&ring.total_records, 0);

    int n = lcview_ring_read(&ring, user, sizeof(user));
    CHECK(n == 0);
    /* 默认跳过量 = 前缀 4 + 记录头 16 = 20 */
    CHECK(ring.read_pos == (0 + 20) % sizeof(ringbuf));
}

int main(void)
{
    test_add_str_callsite_overflow();
    test_add_binary_callsite_overflow();
    test_add_str_callsite_boundary();
    test_ring_read_callsite_corrupt_skip();
    test_ring_read_callsite_corrupt_garbage();
    if (g_fails) {
        printf("FAIL: %d/%d checks failed\n", g_fails, g_checks);
        return 1;
    }
    printf("OK: all %d checks passed (callsite)\n", g_checks);
    return 0;
}
