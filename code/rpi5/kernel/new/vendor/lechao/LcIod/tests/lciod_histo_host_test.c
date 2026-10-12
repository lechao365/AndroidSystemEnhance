/* ============================================================
 * lciod_histo_host_test.c — lciod 传输延迟/块大小二维直方图纯函数 host 单测
 * 编译执行：make -C tests（gcc host 编译，无内核依赖）
 * 覆盖：lciod_histo_size_bucket / lciod_histo_latency_bucket 逐档边界、
 *       lciod_histo_add 入桶自增/NULL 防御/方向钳制/重复累加，
 *       以及档位上界饱和桶不变式（C 批一方向 4）
 * ============================================================ */

#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include "lciod_histo.h"

static int g_checks = 0;
static int g_fails = 0;

#define CHECK(cond)                                                     \
    do {                                                                \
        g_checks++;                                                     \
        if (!(cond)) {                                                  \
            g_fails++;                                                  \
            printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond);      \
        }                                                               \
    } while (0)

/* 统计矩阵中非零单元个数（用于验证"仅目标单元自增"） */
static int histo_nonzero_cells(const struct lciod_histo *h)
{
    unsigned int d, s, l;
    int n = 0;

    for (d = 0; d < LCIOD_HISTO_DIRS; d++)
        for (s = 0; s < LCIOD_HISTO_SIZE_BINS; s++)
            for (l = 0; l < LCIOD_HISTO_LAT_BINS; l++)
                if (h->counts[d][s][l])
                    n++;
    return n;
}

/* 块大小档边界：<= 上界落当档，越界落末饱和桶 */
static void test_size_bucket(void)
{
    /* 边界值与期望档位（0..7）逐对校验 */
    static const struct {
        uint64_t bytes;
        unsigned int expect;
    } cases[] = {
        {0ull, 0u},
        {1ull, 0u},
        {512ull, 0u},
        {513ull, 1u},
        {4096ull, 1u},
        {4097ull, 2u},
        {16384ull, 2u},
        {65536ull, 3u},
        {262144ull, 4u},
        {1048576ull, 5u},
        {8388608ull, 6u},
        {8388609ull, 7u},
        {(uint64_t)~0ull, 7u},
    };
    unsigned int i;

    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
        CHECK(lciod_histo_size_bucket(cases[i].bytes) == cases[i].expect);

    /* 档位上界本身应落当档（上界含于本档） */
    for (i = 0; i < LCIOD_HISTO_SIZE_BINS; i++)
        CHECK(lciod_histo_size_bucket(lciod_histo_size_bounds[i]) == i);
}

/* 延迟档边界：<= 上界落当档，越界落末饱和桶 */
static void test_latency_bucket(void)
{
    static const struct {
        uint64_t elapsed_ns;
        unsigned int expect;
    } cases[] = {
        {0ull, 0u},
        {1ull, 0u},
        {1000ull, 0u},
        {1001ull, 1u},
        {5000ull, 1u},
        {5001ull, 2u},
        {10000ull, 2u},
        {50000ull, 3u},
        {100000ull, 4u},
        {500000ull, 5u},
        {1000000ull, 6u},
        {5000000ull, 7u},
        {10000000ull, 8u},
        {50000000ull, 9u},
        {100000000ull, 10u},
        {100000001ull, 11u},
        {(uint64_t)~0ull, 11u},
    };
    unsigned int i;

    for (i = 0; i < sizeof(cases) / sizeof(cases[0]); i++)
        CHECK(lciod_histo_latency_bucket(cases[i].elapsed_ns) ==
              cases[i].expect);

    /* 档位上界本身应落当档（上界含于本档） */
    for (i = 0; i < LCIOD_HISTO_LAT_BINS; i++)
        CHECK(lciod_histo_latency_bucket(lciod_histo_latency_bounds[i]) == i);
}

/* 档位数组不变式：末档为饱和桶（全 1） */
static void test_bounds_saturation(void)
{
    CHECK(LCIOD_HISTO_SIZE_BINS == 8u);
    CHECK(LCIOD_HISTO_LAT_BINS == 12u);
    CHECK(LCIOD_HISTO_DIRS == 2u);
    CHECK(sizeof(lciod_histo_size_bounds) / sizeof(lciod_histo_size_bounds[0]) ==
          LCIOD_HISTO_SIZE_BINS);
    CHECK(sizeof(lciod_histo_latency_bounds) /
              sizeof(lciod_histo_latency_bounds[0]) ==
          LCIOD_HISTO_LAT_BINS);
    CHECK(lciod_histo_size_bounds[LCIOD_HISTO_SIZE_BINS - 1u] == (uint64_t)~0ull);
    CHECK(lciod_histo_latency_bounds[LCIOD_HISTO_LAT_BINS - 1u] ==
          (uint64_t)~0ull);

    /* 非饱和档上界须严格单调递增 */
    {
        unsigned int i;

        for (i = 1; i < LCIOD_HISTO_SIZE_BINS - 1u; i++)
            CHECK(lciod_histo_size_bounds[i - 1u] < lciod_histo_size_bounds[i]);
        for (i = 1; i < LCIOD_HISTO_LAT_BINS - 1u; i++)
            CHECK(lciod_histo_latency_bounds[i - 1u] <
                  lciod_histo_latency_bounds[i]);
    }
}

/* add：NULL 防御（不崩、无副作用） */
static void test_add_null(void)
{
    lciod_histo_add(NULL, LCIOD_HISTO_DIR_READ, 100ull, 500ull);
    CHECK(1); /* 到达此处即证明未崩溃 */
}

/* add：已知 (dir,bytes,elapsed) 精确落格，其余单元保持 0 */
static void test_add_known_cell(void)
{
    struct lciod_histo h;

    memset(&h, 0, sizeof(h));
    /* bytes=100→size 0；elapsed=500→lat 0；读方向 */
    lciod_histo_add(&h, LCIOD_HISTO_DIR_READ, 100ull, 500ull);
    CHECK(h.counts[LCIOD_HISTO_DIR_READ][0][0] == 1u);
    CHECK(histo_nonzero_cells(&h) == 1);

    memset(&h, 0, sizeof(h));
    /* bytes=4097→size 2；elapsed=1001→lat 1；写方向 */
    lciod_histo_add(&h, LCIOD_HISTO_DIR_WRITE, 4097ull, 1001ull);
    CHECK(h.counts[LCIOD_HISTO_DIR_WRITE][2][1] == 1u);
    CHECK(h.counts[LCIOD_HISTO_DIR_READ][2][1] == 0u);
    CHECK(histo_nonzero_cells(&h) == 1);

    memset(&h, 0, sizeof(h));
    /* 饱和桶：UINT64_MAX 块大小 + UINT64_MAX 延迟 → size 7 / lat 11 */
    lciod_histo_add(&h, LCIOD_HISTO_DIR_READ, (uint64_t)~0ull, (uint64_t)~0ull);
    CHECK(h.counts[LCIOD_HISTO_DIR_READ][LCIOD_HISTO_SIZE_BINS - 1u]
                  [LCIOD_HISTO_LAT_BINS - 1u] == 1u);
    CHECK(histo_nonzero_cells(&h) == 1);
}

/* add：方向越界钳制到末方向（LCIOD_HISTO_DIR_WRITE） */
static void test_add_dir_clamp(void)
{
    struct lciod_histo h;

    memset(&h, 0, sizeof(h));
    lciod_histo_add(&h, LCIOD_HISTO_DIRS, 100ull, 500ull);
    CHECK(LCIOD_HISTO_DIR_WRITE == LCIOD_HISTO_DIRS - 1u);
    CHECK(h.counts[LCIOD_HISTO_DIR_WRITE][0][0] == 1u);
    CHECK(h.counts[LCIOD_HISTO_DIR_READ][0][0] == 0u);
    CHECK(histo_nonzero_cells(&h) == 1);

    /* 更远超界的方向值同样钳制到末方向 */
    memset(&h, 0, sizeof(h));
    lciod_histo_add(&h, 999u, 100ull, 500ull);
    CHECK(h.counts[LCIOD_HISTO_DIR_WRITE][0][0] == 1u);
    CHECK(histo_nonzero_cells(&h) == 1);
}

/* add：重复入同一格累加计数 */
static void test_add_accumulate(void)
{
    struct lciod_histo h;
    unsigned int i;

    memset(&h, 0, sizeof(h));
    for (i = 0; i < 5u; i++)
        lciod_histo_add(&h, LCIOD_HISTO_DIR_READ, 65536ull, 1000000ull);
    /* bytes=65536→size 3；elapsed=1e6→lat 6 */
    CHECK(h.counts[LCIOD_HISTO_DIR_READ][3][6] == 5u);
    CHECK(histo_nonzero_cells(&h) == 1);
}

int main(void)
{
    test_size_bucket();
    test_latency_bucket();
    test_bounds_saturation();
    test_add_null();
    test_add_known_cell();
    test_add_dir_clamp();
    test_add_accumulate();
    if (g_fails) {
        printf("FAIL: %d/%d checks failed\n", g_fails, g_checks);
        return 1;
    }
    printf("OK: all %d checks passed\n", g_checks);
    return 0;
}
