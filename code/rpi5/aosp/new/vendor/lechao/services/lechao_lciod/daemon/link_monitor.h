/*
 * ============================================================
 * link_monitor.h — 全局链路事件监控（R2 方向 5+6）
 * 所属模块: lechao_lciod (system 分区 daemon)
 * 设计目的: 独立于 per-device 分片监控的全局链路线程。经 epoll 消费
 *           内核全局链路节点 /dev/vendor_lechao_usbd_link 的事件流
 *           （连接/掉线/过流，lciod_link 订阅 hub notifier 转出），
 *           按 busnum/port 聚合输出 JSON 行；周期采集 RPi get_throttled
 *           供电降频值，规则一：掉线/过流事件 5s 窗口内 throttled 非零
 *           → 该事件标 power_suspect（供电不足归因）。
 *
 * 线程模型: LinkMonitorRun() 作为 detach 线程由 IoServiceImpl::start()
 *           启动，与既有 4 分片 per-device 监控并行，互不影响。
 * ============================================================
 */
#ifndef _LECHAO_LCIOD_LINK_MONITOR_H
#define _LECHAO_LCIOD_LINK_MONITOR_H

#include <cstdint>

namespace lechao {
namespace lciod {

/* 规则一窗口：掉线/过流事件后该时间窗（ns）内 throttled 非零即标
 * power_suspect（契约：now-event<=5s） */
constexpr uint64_t kPowerSuspectWindowNs = 5000000000ULL;  /* 5s */

/*
 * ApplyPowerSuspectRule — 供电不足归因规则一（纯函数，供单测）
 * 语义: 事件类型为链路掉线(LINK_DISCONNECT)或过流(LINK_OVERCURRENT)，
 *       且 now - event <= 5s 窗口内，最近一次 throttled 采样非零
 *       → 返回 true（该事件标 power_suspect）。
 * 输入防御（CXX-002）: 事件时间戳晚于当前（时钟回拨/异常）时按窗口外
 *       处理返回 false，先判大小再相减防无符号回绕。
 * @event_mono_ns: 事件 mono 时间戳（event.timestamp_ns，内核 ktime_get_ns()
 *                 = CLOCK_MONOTONIC，与用户态 clock_gettime(CLOCK_MONOTONIC)
 *                 同一时间基，可直接比较）
 * @now_mono_ns:   当前 mono 时间戳（clock_gettime(CLOCK_MONOTONIC) 换算 ns）
 * @throttled:     最近一次 RPi get_throttled 采样值（bit0 欠压、bit1 频率
 *                 受限等；非零即视为供电异常在位）
 * @event_type:    事件类型（仅 LINK_DISCONNECT/LINK_OVERCURRENT 参与判定）
 */
bool ApplyPowerSuspectRule(uint64_t event_mono_ns, uint64_t now_mono_ns,
                           uint64_t throttled, uint32_t event_type);

/*
 * LinkMonitorRun — 全局链路监控线程入口（detach 线程）
 * 独立于 4 分片 per-device 监控，生命周期随进程（非 oneshot，init 自动
 * 重启）；致命错误（epoll_wait 非 EINTR）按 CXX-004 4 步退出交 init 重启。
 */
void LinkMonitorRun();

}  // namespace lciod
}  // namespace lechao

#endif  // _LECHAO_LCIOD_LINK_MONITOR_H
