/* ============================================================
 * lciod_read_logic_host_test.c — lciod 读路径纯逻辑 host 单测
 * 编译执行：make -C tests（gcc host 编译，无内核依赖）
 * 覆盖：lciod_nonblock_read_decision 全四象限语义（KRN-004）
 * ============================================================ */

#include <stdio.h>
#include <stdint.h>
#include "lciod_read_logic.h"

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

/* 空环 + 未断连 → EAGAIN 重试 */
static void test_empty_alive(void)
{
    CHECK(lciod_nonblock_read_decision(1, 0) == 1);
}

/* 空环 + 已断连 → 0（EOF） */
static void test_empty_shutdown(void)
{
    CHECK(lciod_nonblock_read_decision(1, 1) == 0);
}

/* 非空 + 未断连 → 落到循环取事件 */
static void test_nonempty_alive(void)
{
    CHECK(lciod_nonblock_read_decision(0, 0) == -1);
}

/* KRN-004 核心：非空 + 已断连 → 仍取事件（drain），不得提前 EOF */
static void test_nonempty_shutdown_drain(void)
{
    CHECK(lciod_nonblock_read_decision(0, 1) == -1);
}

int main(void)
{
    test_empty_alive();
    test_empty_shutdown();
    test_nonempty_alive();
    test_nonempty_shutdown_drain();
    if (g_fails) {
        printf("FAIL: %d/%d checks failed\n", g_fails, g_checks);
        return 1;
    }
    printf("OK: all %d checks passed\n", g_checks);
    return 0;
}
