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

/*
 * SD 故障演练闭环（自愈演练）：合成 STUCK 事件的起点档位/模式与事件构造
 * 契约。演练入口（lciod_sd.c sysfs inject_stuck，root-only 写一次触发一次）
 * 依据本契约在内存构造一张合成 SD 卡并经 notifier 处理路径走完整降档链
 * （stuck_count 递增 → 剥 caps 降档 → SD_STUCK 上报），**不触碰真实 mmc
 * 卡/硬件操作**。纯函数，host 单测直接覆盖，防演练口径漂移。
 */
#define LCIOD_SD_DRILL_START_SPEED  LCIOD_SD_SPEED_MAX
/* 起点模式位全集：SDR12|SDR25|SDR50|SDR104|DDR50 = 0x1F */
#define LCIOD_SD_DRILL_START_MODE   0x1Fu
#define LCIOD_SD_DRILL_ERR          (-5) /* -EIO，模拟卡死错误码 */

/* 一次合成 STUCK 降档事件（降档前/后档位与模式 + 错误码） */
struct lciod_sd_stuck_evt {
	unsigned int old_speed;
	unsigned int new_speed;
	unsigned int old_mode;
	unsigned int new_mode;
	int err;
};

/*
 * lciod_sd_build_stuck_evt — 由演练起点档位/模式构造一次降档事件
 * @speed/mode：合成卡起始速度档位（LCIOD_SD_SPEED_*）与模式位掩码
 * @err：模拟 STUCK 错误码
 * @out：事件输出（NULL 安全，直接返回）
 *
 * 语义：new_speed = next_lower_speed(speed)，new_mode = strip_modes_above(
 * mode, new_speed)——与 notifier 回调内真实降档同源纯函数，保证演练与
 * 生产降档口径一致。无内核 API 依赖，host 与内核共用。
 */
static inline void lciod_sd_build_stuck_evt(unsigned int speed,
					    unsigned int mode, int err,
					    struct lciod_sd_stuck_evt *out)
{
	if (!out)
		return;
	out->old_speed = speed;
	out->new_speed = lciod_sd_next_lower_speed(speed);
	out->old_mode = mode;
	out->new_mode = lciod_sd_strip_modes_above(mode, out->new_speed);
	out->err = err;
}

/*
 * SD 延迟感知自动分级（QoS 延迟反馈）——窗口延迟只读导出与延迟演练契约
 *
 * COMPLETE 事件在 10s 窗内累计请求历时：窗口平均延迟 window_lat_avg_us 与
 * 长尾计数 window_lat_tail（历时 >= LCIOD_SD_LAT_TAIL_NS 的请求数）为 daemon
 * IoQosManager 自动档的反馈源——超阈收紧 bg 限速、回落放松（口径见
 * docs/02-IO增强/02.08-IO-QoS延迟感知分级.md）。窗口到期若长尾计数 > 0，
 * 经 LcView 上报 QOS_AUTO_ADJUST（id=18）。纯函数 host 单测直接覆盖。
 */
#define LCIOD_SD_LAT_TAIL_NS 50000000ULL /* 50ms 长尾阈值（纳秒） */

/* 延迟演练入口（inject_latency）合成一次慢 COMPLETE 的历时与块大小 */
#define LCIOD_SD_DRILL_LAT_NS    500000000ULL /* 500ms 慢 COMPLETE 历时 */
#define LCIOD_SD_DRILL_LAT_BYTES 131072u      /* 128KiB（入块大小直方图档） */

/* 长尾判据宏：请求历时是否达到长尾阈值 */
#define LCIOD_SD_LAT_IS_TAIL(duration_ns) \
	((uint64_t)(duration_ns) >= LCIOD_SD_LAT_TAIL_NS)

/*
 * lciod_sd_lat_avg_us — 窗口平均延迟（微秒，纯函数）
 * @sum_ns：窗口内累计请求历时（纳秒）
 * @count：窗口内计入历时的请求数
 * 语义：count == 0 时返回 0（CXX-002 除零防护）；否则 sum_ns/count/1000。
 * 无内核 API 依赖（仅整数运算），host 与内核共用同一实现。
 */
static inline uint64_t lciod_sd_lat_avg_us(uint64_t sum_ns, uint64_t count)
{
	if (count == 0)
		return 0;
	return sum_ns / count / 1000ULL;
}

/* SD Status Register 字数（512bit = 16 x u32） */
#define LCIOD_SD_SSR_WORDS 16

/* SSR 速度档解码结果（A 批二方向 1） */
struct lciod_sd_ssr {
	unsigned int speed_class;     /* SD Status SPEED_CLASS (bits 447:440) */
	unsigned int uhs_speed_grade; /* SD Status UHS_SPEED_GRADE (bits 399:396) */
};

/*
 * lciod_sd_decode_ssr — 从已转主机序的 SD Status Register 解码速度档
 * @raw_ssr：u32[16]，已由内核 be32_to_cpu 逐字转换（MSB-first 数组）
 * @out：解码结果输出
 *
 * 语义：口径与 mmc core sd.c 的 UNSTUFF_BITS 一致——SD Status 规格按
 * LSB-first 编号；AU_SIZE(bits 431:428)=raw_ssr[2]>>12 是该口径的既有锚点。
 * SPEED_CLASS 落 raw_ssr[2] 高字节，UHS_SPEED_GRADE 落 raw_ssr[3][15:12]。
 * 无内核 API 依赖，host 与内核共用同一实现（禁内核依赖）。
 */
static inline void lciod_sd_decode_ssr(const unsigned int raw_ssr[LCIOD_SD_SSR_WORDS],
					struct lciod_sd_ssr *out)
{
	if (!raw_ssr || !out) {
		if (out) {
			out->speed_class = 0;
			out->uhs_speed_grade = 0;
		}
		return;
	}
	out->speed_class = (raw_ssr[2] >> 24) & 0xFFu;
	out->uhs_speed_grade = (raw_ssr[3] >> 12) & 0x0Fu;
}

#ifdef __KERNEL__
/* notifier 回调（lciod_sd.c 定义；供内核内部引用） */
int lciod_sd_notifier(struct notifier_block *nb, unsigned long action,
		      void *data);
#endif /* __KERNEL__ */

#endif /* LCIOD_SD_H */
