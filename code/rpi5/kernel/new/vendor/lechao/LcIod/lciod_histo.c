/* ============================================================
 * lciod_histo.c — 传输延迟/块大小二维直方图纯函数实现
 *
 * 无内核 API 依赖（仅用类型与纯标量计算），与 host 单测共用同一源码
 * （tests/lciod_histo_host_test.c 的桶边界用例直接编译本文件）。
 * 档位上界为编译期固定常量，末档为饱和桶。
 * ============================================================ */

#include "lciod_histo.h"

/*
 * 块大小档上界（字节，末档饱和）：
 *   0: <=512B  1: <=4KiB  2: <=16KiB 3: <=64KiB
 *   4: <=256KiB 5: <=1MiB 6: <=8MiB  7: 其余（饱和）
 */
const uint64_t lciod_histo_size_bounds[LCIOD_HISTO_SIZE_BINS] = {
	512ull, 4096ull, 16384ull, 65536ull, 262144ull, 1048576ull,
	8388608ull, ~0ull
};

/*
 * 延迟档上界（纳秒，末档饱和）：
 *   0: <=1us 1: <=5us 2: <=10us 3: <=50us 4: <=100us 5: <=500us
 *   6: <=1ms 7: <=5ms 8: <=10ms 9: <=50ms 10: <=100ms 11: 其余（饱和）
 */
const uint64_t lciod_histo_latency_bounds[LCIOD_HISTO_LAT_BINS] = {
	1000ull, 5000ull, 10000ull, 50000ull, 100000ull, 500000ull,
	1000000ull, 5000000ull, 10000000ull, 50000000ull, 100000000ull,
	~0ull
};

unsigned int lciod_histo_size_bucket(uint64_t bytes)
{
	unsigned int i;

	for (i = 0; i < LCIOD_HISTO_SIZE_BINS; i++)
		if (bytes <= lciod_histo_size_bounds[i])
			return i;
	return LCIOD_HISTO_SIZE_BINS - 1;
}

unsigned int lciod_histo_latency_bucket(uint64_t elapsed_ns)
{
	unsigned int i;

	for (i = 0; i < LCIOD_HISTO_LAT_BINS; i++)
		if (elapsed_ns <= lciod_histo_latency_bounds[i])
			return i;
	return LCIOD_HISTO_LAT_BINS - 1;
}

void lciod_histo_add(struct lciod_histo *h, unsigned int dir,
		     uint64_t bytes, uint64_t elapsed_ns)
{
	unsigned int size_idx, lat_idx;

	if (!h)
		return;
	if (dir >= LCIOD_HISTO_DIRS)
		dir = LCIOD_HISTO_DIRS - 1;
	size_idx = lciod_histo_size_bucket(bytes);
	lat_idx = lciod_histo_latency_bucket(elapsed_ns);
	h->counts[dir][size_idx][lat_idx]++;
}
