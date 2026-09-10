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

/* lciod_event_tail_rollback_ok：copy_to_user 失败回滚守卫（方向 3 判红） */
static void test_tail_rollback_guard(void)
{
    /* 与内核 lciod_usbd-ioctl.h 的 VENDOR_LECHAO_USBD_EVENT_BUF_SIZE 同步（恒 32） */
    const uint32_t N = 32;

    /* 未驱逐：tail 仍为读后推进位置 → 可回滚 */
    CHECK(lciod_event_tail_rollback_ok((5 + 1) % N, 5, N) == 1);
    /* 驱逐推进：tail 被写者推进 → 禁止回滚（防 tail==head 判空致
     * 事件清零 / 多读者重复消费旧槽位） */
    CHECK(lciod_event_tail_rollback_ok((5 + 2) % N, 5, N) == 0);
    CHECK(lciod_event_tail_rollback_ok((5 + 3) % N, 5, N) == 0);
    /* 环绕边界：槽位 31 读后 tail 回绕 0 */
    CHECK(lciod_event_tail_rollback_ok(0, 31, N) == 1);
    /* 环绕下驱逐推进 → 禁止回滚 */
    CHECK(lciod_event_tail_rollback_ok(1, 31, N) == 0);
}

/* lciod_event_ring_push：事件环写入推进 + overflow 丢弃（方向 1 调用点判红） */
static void test_event_ring_push(void)
{
    const uint32_t N = 32;
    uint32_t new_tail;
    int dropped;

    /* 正常写入：head 0→1，tail 不变，无丢弃 */
    new_tail = 99;
    dropped = 99;
    CHECK(lciod_event_ring_push(0, 0, N, &new_tail, &dropped) == 1);
    CHECK(new_tail == 0);
    CHECK(dropped == 0);

    /* 环非满写入：head 5→6（tail=0 未追上），tail 不变 */
    new_tail = 99;
    dropped = 99;
    CHECK(lciod_event_ring_push(5, 0, N, &new_tail, &dropped) == 6);
    CHECK(new_tail == 0);
    CHECK(dropped == 0);

    /* 环满写入（head 追上前 tail）：head=10,tail=11 → 10+1=11==tail → overflow，
     * head→11，tail 丢弃最旧 +1→12（KRN-016/event_push overflow 语义） */
    new_tail = 99;
    dropped = 99;
    CHECK(lciod_event_ring_push(10, 11, N, &new_tail, &dropped) == 11);
    CHECK(new_tail == 12);
    CHECK(dropped == 1);

    /* 环绕边界：head=31 → 0（buf_size=32），tail=0 未追上（31+1=0==tail 才是满，
     * 此处 tail 取 5 表示非满）→ 无丢弃，head 绕回 0 */
    new_tail = 99;
    dropped = 99;
    CHECK(lciod_event_ring_push(31, 5, N, &new_tail, &dropped) == 0);
    CHECK(new_tail == 5);
    CHECK(dropped == 0);

    /* 环绕满：head=31, tail=0 → 31+1=0==tail → overflow，tail 丢弃 +1→1 */
    new_tail = 99;
    dropped = 99;
    CHECK(lciod_event_ring_push(31, 0, N, &new_tail, &dropped) == 0);
    CHECK(new_tail == 1);
    CHECK(dropped == 1);

    /* 出参可空：仅返回 head（调用方不需要 tail/dropped 时） */
    CHECK(lciod_event_ring_push(3, 7, N, NULL, NULL) == 4);
}

int main(void)
{
    test_empty_alive();
    test_empty_shutdown();
    test_nonempty_alive();
    test_nonempty_shutdown_drain();
    test_tail_rollback_guard();
    test_event_ring_push();
    if (g_fails) {
        printf("FAIL: %d/%d checks failed\n", g_fails, g_checks);
        return 1;
    }
    printf("OK: all %d checks passed\n", g_checks);
    return 0;
}
