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

/*
 * lciod_event_tail_rollback_ok — copy_to_user 失败回滚 event_tail 的守卫
 *
 * 事件环读取：读一条后 event_tail 推进到 (consumed_pos + 1) % buf_size；
 * 期间写者驱逐（head 追上 tail，见 lciod_usbd-stats.c event_push）会继续
 * 推进 event_tail。此时若无守卫直接回滚 tail-1：
 *   - 单读者：回滚后 tail 可能 == head 判空，已读事件被"清零"丢失；
 *   - 多读者（LCD-008 未限制单打开）：回滚到已被新事件覆盖的槽位，
 *     造成重复消费。
 * 守卫：仅当 tail 仍等于读后推进位置（期间未被驱逐）才允许回滚——
 * 与 lcview_ring.c KRN-003 读指针回滚守卫同构。
 *
 * @tail_after   当前 event_tail（读取推进后、回滚前）
 * @consumed_pos 本次读取的事件槽位（读取前 tail）
 * @buf_size     事件缓冲区大小（VENDOR_LECHAO_USBD_EVENT_BUF_SIZE）
 * @return 1 未被驱逐，可安全回滚 / 0 已被驱逐推进，禁止回滚
 */
int lciod_event_tail_rollback_ok(uint32_t tail_after, uint32_t consumed_pos,
                                 uint32_t buf_size);

#endif /* LCIOD_READ_LOGIC_H */
