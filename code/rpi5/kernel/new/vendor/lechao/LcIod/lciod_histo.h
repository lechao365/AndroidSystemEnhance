/* ============================================================
 * lciod_histo.h — LcIod 传输延迟/块大小二维直方图（host test 可覆盖）
 *
 * 设计目的：把"块大小档 x 延迟档"的二维累计直方图分桶逻辑抽为无内核依赖的
 *   纯函数，内核（lciod_usbd transport_end / lciod_sd COMPLETE）与 host 单测
 *   共用同一实现防漂移（KRN-004 同构：内核调用点只做参数采样与入桶）。
 *
 * 维度（编译期固定）：2 方向 x 8 块大小档 x 12 延迟档
 *   - 方向索引：0=读（LCIOD_HISTO_DIR_READ）/ 1=写（LCIOD_HISTO_DIR_WRITE）；
 *     内核侧由 VENDOR_LECHAO_USBD_DIR_READ(=1)/DIR_WRITE(=2) 或
 *     LECHAO_SD_DIR_READ(=1)/DIR_WRITE(=2) 映射。
 *   - 档位上界见 lciod_histo_size_bounds / lciod_histo_latency_bounds，
 *     末档为饱和桶（上界全 1）。
 * ============================================================ */

#ifndef LCIOD_HISTO_H
#define LCIOD_HISTO_H

/* 双环境（仿 lciod_read_logic.h）：内核用 linux/types.h，host 用 stdint.h */
#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdint.h>
#endif

/* 直方图维度（编译期固定，与 ABI 矩阵 counts[dir][size][lat] 一致） */
#define LCIOD_HISTO_DIRS      2u  /* 方向数：读/写 */
#define LCIOD_HISTO_SIZE_BINS 8u  /* 块大小档数 */
#define LCIOD_HISTO_LAT_BINS  12u /* 延迟档数 */

/* 方向索引（ABI 矩阵第一维；内核方向码经调用方映射到此索引） */
#define LCIOD_HISTO_DIR_READ  0u
#define LCIOD_HISTO_DIR_WRITE 1u

/*
 * struct lciod_histo — 累计直方图矩阵
 *
 * counts[dir][size][lat] 为单调递增累计计数（reset 时清零），仅在持有设备/
 * 模块自旋锁（vendor_lechao_usbd_device.lock 或 lciod_sd_state.lock）时自增，
 * 保证并发一致（无跨锁读写）。
 */
struct lciod_histo {
	uint32_t counts[LCIOD_HISTO_DIRS][LCIOD_HISTO_SIZE_BINS][LCIOD_HISTO_LAT_BINS];
};

/* 档位上界（末档饱和）：块大小字节 / 延迟纳秒。编译期固定，内核与 host 同源 */
extern const uint64_t lciod_histo_size_bounds[LCIOD_HISTO_SIZE_BINS];
extern const uint64_t lciod_histo_latency_bounds[LCIOD_HISTO_LAT_BINS];

/*
 * lciod_histo_size_bucket — 块大小档索引（纯函数）
 * @bytes: 本次有效传输字节
 * @return 0..LCIOD_HISTO_SIZE_BINS-1（<= 上界的最小档；超出落末饱和桶）
 */
unsigned int lciod_histo_size_bucket(uint64_t bytes);

/*
 * lciod_histo_latency_bucket — 延迟档索引（纯函数）
 * @elapsed_ns: 本次传输耗时（纳秒）
 * @return 0..LCIOD_HISTO_LAT_BINS-1（<= 上界的最小档；超出落末饱和桶）
 */
unsigned int lciod_histo_latency_bucket(uint64_t elapsed_ns);

/*
 * lciod_histo_add — 入桶自增（纯函数）
 * @h:         目标直方图（NULL 时静默返回）
 * @dir:       方向索引（越界钳制到末方向）
 * @bytes:     本次有效传输字节
 * @elapsed_ns: 本次传输耗时（纳秒）
 *
 * 调用上下文：须由调用方在设备/模块锁内调用，本函数不加锁。
 */
void lciod_histo_add(struct lciod_histo *h, unsigned int dir,
		     uint64_t bytes, uint64_t elapsed_ns);

#endif /* LCIOD_HISTO_H */
