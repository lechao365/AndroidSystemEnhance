/* ============================================================
 * lciod_sd_host_test.c — lciod SD 健康剥位/降档纯逻辑 host 单测
 * 编译执行：make -C tests（gcc host 编译，无内核依赖）
 * 覆盖：lciod_sd_next_lower_speed 逐级降档、lciod_sd_strip_modes_above
 *       按 sd_bus_speed 剥除高位模式位（A 批一方向 4）
 * ============================================================ */

#include <stdio.h>
#include <stdint.h>
#include "lciod_sd.h"

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

/* 全部模式位（SDR12|SDR25|SDR50|SDR104|DDR50 = 0x1F） */
#define ALL_MODES   (LCIOD_SD_MODE_UHS_SDR12 | LCIOD_SD_MODE_UHS_SDR25 | \
                     LCIOD_SD_MODE_UHS_SDR50 | LCIOD_SD_MODE_UHS_SDR104 | \
                     LCIOD_SD_MODE_UHS_DDR50)

/* 逐级降档：4→3→2→1→0，最低档保持，未知档位回落到 MAX-1 */
static void test_next_lower_speed(void)
{
    CHECK(lciod_sd_next_lower_speed(LCIOD_SD_SPEED_UHS_DDR50) == 3u);
    CHECK(lciod_sd_next_lower_speed(LCIOD_SD_SPEED_UHS_SDR104) == 2u);
    CHECK(lciod_sd_next_lower_speed(LCIOD_SD_SPEED_UHS_SDR50) == 1u);
    CHECK(lciod_sd_next_lower_speed(LCIOD_SD_SPEED_UHS_SDR25) == 0u);
    /* 已至最低档：保持 0 */
    CHECK(lciod_sd_next_lower_speed(LCIOD_SD_SPEED_UHS_SDR12) == 0u);
    /* 未知档位（>MAX）：回落到 MAX-1，避免越界降档 */
    CHECK(lciod_sd_next_lower_speed(LCIOD_SD_SPEED_MAX + 1u) ==
          LCIOD_SD_SPEED_MAX - 1u);
}

/* 剥位：仅保留位 0..sd_bus_speed，高于该档的模式位剥除 */
static void test_strip_modes_above(void)
{
    /* 全档位保留（当前为最高档 DDR50） */
    CHECK(lciod_sd_strip_modes_above(ALL_MODES, LCIOD_SD_SPEED_UHS_DDR50) ==
          ALL_MODES);
    CHECK(lciod_sd_strip_modes_above(ALL_MODES, LCIOD_SD_SPEED_UHS_SDR104) ==
          0x0Fu);
    CHECK(lciod_sd_strip_modes_above(ALL_MODES, LCIOD_SD_SPEED_UHS_SDR50) ==
          0x07u);
    CHECK(lciod_sd_strip_modes_above(ALL_MODES, LCIOD_SD_SPEED_UHS_SDR25) ==
          0x03u);
    CHECK(lciod_sd_strip_modes_above(ALL_MODES, LCIOD_SD_SPEED_UHS_SDR12) ==
          0x01u);

    /* 仅 DDR50 位（bit4），降到 SDR104 档即被剥除 */
    CHECK(lciod_sd_strip_modes_above(LCIOD_SD_MODE_UHS_DDR50,
                                     LCIOD_SD_SPEED_UHS_SDR104) == 0u);
    /* SDR104 位（bit3）在 SDR104 档保留、在 SDR50 档剥除 */
    CHECK(lciod_sd_strip_modes_above(LCIOD_SD_MODE_UHS_SDR104,
                                     LCIOD_SD_SPEED_UHS_SDR104) ==
          LCIOD_SD_MODE_UHS_SDR104);
    CHECK(lciod_sd_strip_modes_above(LCIOD_SD_MODE_UHS_SDR104,
                                     LCIOD_SD_SPEED_UHS_SDR50) == 0u);

    /* 未知档位（>MAX）：不剥除，原样返回（防御性） */
    CHECK(lciod_sd_strip_modes_above(ALL_MODES, LCIOD_SD_SPEED_MAX + 1u) ==
          ALL_MODES);
    /* 输入 0：恒 0 */
    CHECK(lciod_sd_strip_modes_above(0u, LCIOD_SD_SPEED_UHS_SDR50) == 0u);
}

/* 降档组合：逐级降档 + 剥位（模拟一次 STUCK 处理） */
static void test_downgrade_combo(void)
{
    unsigned int speed = LCIOD_SD_SPEED_UHS_DDR50;
    unsigned int mode = ALL_MODES;

    /* 第一次 STUCK：DDR50(4) → SDR104(3)，剥去 bit4 */
    speed = lciod_sd_next_lower_speed(speed);
    mode = lciod_sd_strip_modes_above(mode, speed);
    CHECK(speed == LCIOD_SD_SPEED_UHS_SDR104);
    CHECK(mode == 0x0Fu);

    /* 第二次 STUCK：SDR104(3) → SDR50(2)，剥去 bit3 */
    speed = lciod_sd_next_lower_speed(speed);
    mode = lciod_sd_strip_modes_above(mode, speed);
    CHECK(speed == LCIOD_SD_SPEED_UHS_SDR50);
    CHECK(mode == 0x07u);

    /* 逐级降到 SDR12：仅剩 bit0 */
    speed = lciod_sd_next_lower_speed(speed);
    mode = lciod_sd_strip_modes_above(mode, speed);
    speed = lciod_sd_next_lower_speed(speed);
    mode = lciod_sd_strip_modes_above(mode, speed);
    CHECK(speed == LCIOD_SD_SPEED_UHS_SDR12);
    CHECK(mode == 0x01u);
}

int main(void)
{
    test_next_lower_speed();
    test_strip_modes_above();
    test_downgrade_combo();
    if (g_fails) {
        printf("FAIL: %d/%d checks failed\n", g_fails, g_checks);
        return 1;
    }
    printf("OK: all %d checks passed\n", g_checks);
    return 0;
}
