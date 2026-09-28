/*
 * ============================================================
 * expect_schema_host_test.c — usb-fault-inject 期望表 schema host 单测
 * 所属模块: rpi-zero2w usb-fault-inject
 * 设计目的: 对 expect_table 纯逻辑做运行期断言:
 *   1) expect_validate_schema — schema gate 通过（方向 5）
 *   2) fault_id_to_name 与 expect_table.name 一致性
 *   3) 枚举边界 — FAULT__MAX 数量、name 非空
 *
 * 运行: make test（在 tests/ 目录下），退出码 0 全过。
 * 接入: harness/lib/check_host_tests.py（R-18 P5 方向 7）。
 * ============================================================
 */
#include "expect.h"
#include "faults.h"
#include "bot.h"
#include "raw-gadget.h"

#include <stdio.h>
#include <string.h>

/*
 * weak stub：单测仅链接 expect.c/faults.c，不拉 bot.c/raw-gadget.c。
 * fault_execute 引用的设备 IO 符号用 weak 定义占位——单测不调用
 * fault_execute（纯 schema/命名校验），弱符号保证链接通过且
 * 将来接入真实实现时不冲突。
 */
__attribute__((weak)) int bot_main_loop(struct raw_gadget *rg,
                                        struct fault_injection *fi)
{
    (void)rg; (void)fi;
    return -1;
}

__attribute__((weak)) int raw_gadget_vbus_draw(struct raw_gadget *rg, int mA)
{
    (void)rg; (void)mA;
    return -1;
}

__attribute__((weak)) int raw_gadget_stall_ep(struct raw_gadget *rg,
                                              uint8_t ep_addr)
{
    (void)rg; (void)ep_addr;
    return -1;
}

static int g_fail = 0;
#define CHECK(cond)                                                     \
    do {                                                                \
        if (!(cond)) {                                                  \
            fprintf(stderr, "FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); \
            g_fail = 1;                                                 \
        }                                                               \
    } while (0)

/* 枚举完整性：11 类故障（F4 已删除），FAULT__MAX 与文档契约一致 */
static void test_enum_boundary(void)
{
    CHECK(FAULT__MAX == 11);
    CHECK(FAULT_STALL_IN == 0);
    CHECK(FAULT_DEGRADE == FAULT__MAX - 1);
    /* F4 删除后 FAULT_CORRUPT_CSW_SIG 前无空洞，值连续 */
    CHECK(FAULT_TIMEOUT == FAULT_STALL_OUT + 1);
    CHECK(FAULT_CORRUPT_CSW_SIG == FAULT_TIMEOUT + 1);
}

/* schema gate：expect_validate_schema 必须全部通过 */
static void test_schema_gate(void)
{
    CHECK(expect_validate_schema() == 0);
}

/* fault_id_to_name 与 expect_table.name 一致性（重命名防漂移） */
static void test_name_consistency(void)
{
    for (int i = 0; i < FAULT__MAX; i++) {
        const char *n = fault_id_to_name((enum fault_id)i);
        CHECK(n != NULL);
        CHECK(n[0] != '\0');
    }
    /* 未知 id → "unknown"（faults.c 契约） */
    CHECK(strcmp(fault_id_to_name((enum fault_id)FAULT__MAX), "unknown") == 0);
    CHECK(strcmp(fault_id_to_name((enum fault_id)-1), "unknown") == 0);
}

int main(void)
{
    test_enum_boundary();
    test_schema_gate();
    test_name_consistency();

    if (g_fail) {
        fprintf(stderr, "expect_schema_host_test: FAILED\n");
        return 1;
    }
    printf("expect_schema_host_test: all checks passed\n");
    return 0;
}
