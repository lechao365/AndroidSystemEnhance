/* ============================================================
 * lciod_sd.h — LcIod SD/MMC 块层健康监控（host test 可覆盖）
 *
 * 【设计目的】
 *   订阅 drivers/mmc/core 的全局 SD/MMC 块层健康事件原子 notifier 链
 *   （lechao_sd_notifier.h，A 批一方向 1 产物）：COMPLETE 事件累加 10s
 *   窗字节/请求数并经 LcView 上报；STUCK 事件依据 card->sd_bus_speed 逐级
 *   剥 caps 降档（纯函数在本头内联，host 单测直接覆盖，防漂移）。
 *   暴露 sysfs 开关（默认开）与窗口/卡死计数。
 *
 * 【文件关系】
 *   - lciod_sd.c：内核实现（notifier 回调 + 10s delayed_work + sysfs）
 *   - lechao_sd_notifier.h：block.c 侧事件载荷与链头接口
 *   - lcview_builder.h/lcview_events.h：聚合事件上报通道（LCVIEW_EVENT_SD_*）
 *   - tests/lciod_sd_host_test.c：剥位/降档纯函数 host 单测
 * ============================================================ */

#ifndef LCIOD_SD_H
#define LCIOD_SD_H

/* 双环境（仿 lciod_read_logic.h）：内核用 linux/types.h，host 用 stdint.h */
#ifdef __KERNEL__
#include <linux/types.h>
#include <linux/notifier.h>
#else
#include <stdint.h>
#endif

/*
 * SD 总线速度档位（值对齐 include/linux/mmc/card.h 的 UHS_*_BUS_SPEED）
 * 与 SD 模式位（值对齐 SD_MODE_UHS_*）——剥 caps 纯函数的输入契约，
 * 内核侧与本头同源，禁止单侧改值。
 */
#define LCIOD_SD_SPEED_UHS_SDR12   0u /* UHS_SDR12_BUS_SPEED */
#define LCIOD_SD_SPEED_UHS_SDR25   1u /* UHS_SDR25_BUS_SPEED / HIGH_SPEED */
#define LCIOD_SD_SPEED_UHS_SDR50   2u /* UHS_SDR50_BUS_SPEED */
#define LCIOD_SD_SPEED_UHS_SDR104  3u /* UHS_SDR104_BUS_SPEED */
#define LCIOD_SD_SPEED_UHS_DDR50   4u /* UHS_DDR50_BUS_SPEED */
#define LCIOD_SD_SPEED_MAX         4u /* 合法档位上限 */

#define LCIOD_SD_MODE_UHS_SDR12    (1u << 0)
#define LCIOD_SD_MODE_UHS_SDR25    (1u << 1)
#define LCIOD_SD_MODE_UHS_SDR50    (1u << 2)
#define LCIOD_SD_MODE_UHS_SDR104   (1u << 3)
#define LCIOD_SD_MODE_UHS_DDR50    (1u << 4)

/*
 * lciod_sd_next_lower_speed — 依据当前速度档位降一档（逐级降档）
 * @sd_bus_speed: 当前档位（LCIOD_SD_SPEED_*）
 * @return 降档后档位；已是最低档（SDR12）时保持 0，未知档位按上限处理
 *
 * 语义：仅做档位数值逐级递减（4→3→2→1→0），不在此处做 DDR50/SDR104
 * 的物理等价归并——物理兼容由 block/mmc 核心重协商承担，本函数只决定
 * "下一档"的速度档位。无内核 API 依赖，host 与内核共用。
 */
static inline unsigned int lciod_sd_next_lower_speed(unsigned int sd_bus_speed)
{
	if (sd_bus_speed == 0u)
		return 0u;
	if (sd_bus_speed > LCIOD_SD_SPEED_MAX)
		return LCIOD_SD_SPEED_MAX - 1u;
	return sd_bus_speed - 1u;
}

/*
 * lciod_sd_strip_modes_above — 按速度档位剥除高于该档的模式位
 * @sd3_bus_mode: 卡片 sd3_bus_mode 模式位掩码（SD_MODE_UHS_*）
 * @sd_bus_speed: 允许保留的最高速度档位（LCIOD_SD_SPEED_*）
 * @return 掩码中仅保留位 0..sd_bus_speed 的值；未知档位（>MAX）不剥除
 *
 * 语义：位下标与速度档位同序（SD_MODE_UHS_* == 1<<对应速度档位），故
 * "保留 <= 当前档位" 即 (1 << (sd_bus_speed+1)) - 1 掩码。逐级降档配合
 * next_lower_speed 使用：每次 STUCK 先降档再剥高位，达到渐进降速目的。
 * 无内核 API 依赖，host 与内核共用同一实现。
 */
static inline unsigned int lciod_sd_strip_modes_above(unsigned int sd3_bus_mode,
						      unsigned int sd_bus_speed)
{
	unsigned int keep;

	if (sd_bus_speed > LCIOD_SD_SPEED_MAX)
		return sd3_bus_mode;
	keep = (1u << (sd_bus_speed + 1u)) - 1u;
	return sd3_bus_mode & keep;
}

#ifdef __KERNEL__
/* notifier 回调（lciod_sd.c 定义；供内核内部引用） */
int lciod_sd_notifier(struct notifier_block *nb, unsigned long action,
		      void *data);
#endif /* __KERNEL__ */

#endif /* LCIOD_SD_H */
