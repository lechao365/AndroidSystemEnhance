/* ============================================================
 * lciod_read_logic.h — LcIod 读路径纯逻辑决策（host test 可覆盖）
 *
 * 设计目的：把 vendor_lechao_usbd_read 的 O_NONBLOCK 分支判定
 * 抽为无内核依赖的纯函数，host 单测直接覆盖三态语义，
 * 内核调用点只做参数采样与结果映射（KRN-004/S8）。
 * ============================================================ */

#ifndef LCIOD_READ_LOGIC_H
#define LCIOD_READ_LOGIC_H

/* 双环境（仿 lcview_ring_logic.h）：内核用 linux/types.h，host 用 stdint.h */
#ifdef __KERNEL__
#include <linux/types.h>
#else
#include <stdint.h>
#endif

/*
 * lciod_nonblock_read_decision — 非阻塞 read 的三态判定
 *
 * @ring_empty ring 是否为空（head == tail）
 * @shutdown   设备是否已 shutdown（断连/卸载）
 * @return 1  返回 -EAGAIN（空环且未断连，非阻塞重试）
 *         0  返回 0（EOF：空环且已断连）
 *         -1 落到阻塞循环取事件（ring 非空；已断连时也先 drain，
 *            与阻塞路径语义一致——KRN-004：shutdown 不得越过非空判定）
 */
int lciod_nonblock_read_decision(int ring_empty, int shutdown);

#endif /* LCIOD_READ_LOGIC_H */
