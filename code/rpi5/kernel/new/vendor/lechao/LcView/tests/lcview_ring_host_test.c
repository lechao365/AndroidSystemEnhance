/*
 * lcview_ring_host_test.c — lcview_ring_logic 纯逻辑 host 单测
 *
 * 直接编译 ../lcview_ring_logic.c（与内核 lcview_ring.o 共用同一份源码，
 * 不引内核头/KUnit——内核 API 已在抽取时剥离，等价 shim 语义），覆盖
 * ring_avail_write / ring_memcpy_out / ring_memcpy_in / ring_evict_one
 * 的索引环绕与驱逐逻辑，堵住 lcview_ring.c 零单测缺口。
 *
 * 编译运行：make test（本目录 Makefile）；退出码 0 全过。
 */

#include <stdio.h>
#include <stdint.h>
#include <string.h>
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

/* ring_avail_write：预留 1 字节区分空/满的边界 */
static void test_avail(void)
{
    CHECK(ring_avail_write_core(100, 0, 0) == 99);    /* 空环 */
    CHECK(ring_avail_write_core(100, 50, 50) == 99);  /* 空环（异位同值） */
    CHECK(ring_avail_write_core(100, 50, 0) == 49);   /* 直接已用 50 */
    CHECK(ring_avail_write_core(100, 10, 90) == 79);  /* 环绕已用 20 */
    CHECK(ring_avail_write_core(100, 99, 0) == 0);    /* 满环（w+1==r） */
    CHECK(ring_avail_write_core(100, 98, 99) == 0);   /* 满环（环绕） */
    CHECK(ring_avail_write_core(100, 0, 99) == 98);   /* 仅剩 1 字节已用 */
    CHECK(ring_avail_write_core(100, 0, 1) == 0);     /* 满环差 1 字节 */
    CHECK(ring_avail_write_core(100, 98, 0) == 1);    /* 仅剩 1 字节 */
}

/* ring_memcpy_out：跨尾部换行与边界 */
static void test_memcpy_out(void)
{
    uint8_t buf[16], dst[16];
    int i;

    for (i = 0; i < 16; i++)
        buf[i] = (uint8_t)i;

    /* 不跨尾部 */
    memset(dst, 0, sizeof(dst));
    ring_memcpy_out_core(buf, 16, dst, 4, 8);
    for (i = 0; i < 8; i++)
        CHECK(dst[i] == (uint8_t)(4 + i));

    /* 恰到尾（不跨） */
    memset(dst, 0, sizeof(dst));
    ring_memcpy_out_core(buf, 16, dst, 8, 8);
    {
        static const uint8_t exp[8] = {8, 9, 10, 11, 12, 13, 14, 15};
        CHECK(memcmp(dst, exp, 8) == 0);
    }

    /* 跨尾部：pos=12, len=8 → 12..15 + 0..3 */
    memset(dst, 0, sizeof(dst));
    ring_memcpy_out_core(buf, 16, dst, 12, 8);
    {
        static const uint8_t exp[8] = {12, 13, 14, 15, 0, 1, 2, 3};
        CHECK(memcmp(dst, exp, 8) == 0);
    }

    /* len=0 边界：不触碰 dst */
    memset(dst, 0xAA, sizeof(dst));
    ring_memcpy_out_core(buf, 16, dst, 4, 0);
    CHECK(dst[0] == 0xAA);
}

/* ring_memcpy_in：跨尾部换行与边界 */
static void test_memcpy_in(void)
{
    uint8_t buf[16], src[16];
    int i;

    for (i = 0; i < 16; i++)
        src[i] = (uint8_t)(0x10 + i);

    /* 不跨尾部 */
    memset(buf, 0, sizeof(buf));
    ring_memcpy_in_core(buf, 16, 4, src, 8);
    for (i = 0; i < 8; i++)
        CHECK(buf[4 + i] == (uint8_t)(0x10 + i));
    CHECK(buf[3] == 0 && buf[12] == 0);

    /* 跨尾部：pos=12, len=8 → 12..15 + 0..3 */
    memset(buf, 0, sizeof(buf));
    ring_memcpy_in_core(buf, 16, 12, src, 8);
    {
        static const uint8_t exp[16] = {
            0x14, 0x15, 0x16, 0x17,   /* src[4..7] 绕回 buf[0..3] */
            0, 0, 0, 0,
            0, 0, 0, 0,
            0x10, 0x11, 0x12, 0x13,   /* src[0..3] 落 buf[12..15] */
        };
        CHECK(memcmp(buf, exp, 16) == 0);
    }

    /* 跨尾部：pos=14, len=6 → 14..15 + 0..3 */
    memset(buf, 0, sizeof(buf));
    ring_memcpy_in_core(buf, 16, 14, src, 6);
    CHECK(buf[14] == 0x10 && buf[15] == 0x11);
    CHECK(buf[0] == 0x12 && buf[1] == 0x13);
    CHECK(buf[2] == 0x14 && buf[3] == 0x15);
}

/* ring_evict_one：推进、跨尾部前缀读取、损坏回退、空环 */
static void test_evict(void)
{
    uint8_t buf[16];
    uint32_t read_pos, out_len;
    int rc;

    /* 正常驱逐（不跨尾部）：read_pos += len */
    memset(buf, 0, sizeof(buf));
    put_u32(buf, 8);
    read_pos = 0;
    out_len = 99;
    rc = ring_evict_one_core(buf, 16, &read_pos, 8, 20, &out_len);
    CHECK(rc == 1);
    CHECK(read_pos == 8);
    CHECK(out_len == 8);

    /* 正常驱逐 + read_pos 环绕 % size */
    memset(buf, 0, sizeof(buf));
    put_u32(buf + 10, 12);                 /* 记录长度 12，前缀在 pos=10 */
    read_pos = 10;                         /* write=0 环内有 6 字节数据 */
    out_len = 0;
    rc = ring_evict_one_core(buf, 16, &read_pos, 0, 20, &out_len);
    CHECK(rc == 1);
    CHECK(read_pos == (10 + 12) % 16);     /* 6 */
    CHECK(out_len == 12);

    /* 长度前缀跨尾部读取：pos=14，4 字节 = buf[14..15]+buf[0..1] */
    memset(buf, 0, sizeof(buf));
    buf[14] = 0x0A;
    buf[15] = 0x00;
    buf[0] = 0x00;
    buf[1] = 0x00;                         /* old_len = 10 */
    read_pos = 14;
    out_len = 0;
    rc = ring_evict_one_core(buf, 16, &read_pos, 8, 20, &out_len);
    CHECK(rc == 1);
    CHECK(read_pos == (14 + 10) % 16);     /* 8 */
    CHECK(out_len == 10);

    /* 损坏：长度 0 → 保守跳过 default_record_len */
    memset(buf, 0, sizeof(buf));         /* 前缀全 0 → old_len == 0 */
    read_pos = 4;
    out_len = 99;
    rc = ring_evict_one_core(buf, 16, &read_pos, 12, 20, &out_len);
    CHECK(rc == 2);
    CHECK(read_pos == (4 + 20) % 16);    /* 8 */
    CHECK(out_len == 0);                 /* 出参为原始坏值 */

    /* 损坏：长度 > size → 保守跳过 */
    memset(buf, 0, sizeof(buf));
    put_u32(buf, 0xFFFF);                /* 65535 > 16 */
    read_pos = 0;
    out_len = 0;
    rc = ring_evict_one_core(buf, 16, &read_pos, 8, 20, &out_len);
    CHECK(rc == 2);
    CHECK(read_pos == 20 % 16);          /* 4 */
    CHECK(out_len == 0xFFFF);

    /* 空环：read_pos == write_pos → 不驱逐、不推进 */
    read_pos = 5;
    out_len = 77;
    rc = ring_evict_one_core(buf, 16, &read_pos, 5, 20, &out_len);
    CHECK(rc == 0);
    CHECK(read_pos == 5);
    CHECK(out_len == 0);
}

int main(void)
{
    test_avail();
    test_memcpy_out();
    test_memcpy_in();
    test_evict();
    if (g_fails) {
        printf("FAIL: %d/%d checks failed\n", g_fails, g_checks);
        return 1;
    }
    printf("OK: all %d checks passed\n", g_checks);
    return 0;
}
