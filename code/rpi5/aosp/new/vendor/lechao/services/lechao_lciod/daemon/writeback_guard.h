// ============================================================
// writeback_guard.h — zram writeback 拔盘韧性守护（事件 id=21）
// 所属模块：LcIod IO 健康监控 — Daemon 层
// 设计目的：两层结构（对齐 block_collector.h / trim_manager.h）——
//   纯函数层（本头声明，单测直接调用）：解析 zram0/backing_dev 文本、
//   判定后备盘供给是否健康、格式化事件 JSON——无 I/O、无状态，可脱离
//   系统独立测试；
//   采样层（QueryWritebackSupply）：读 /sys/block/zram0/backing_dev，
//   检查其指向的 /sys/block/<dev> 是否存在。
// 语义：后备盘断供（U 盘拔出/驱动 unbind/backing_dev 残留 deleted）时
//   上报 lost，供给恢复后上报 recovered。守护**不自动重绑**——换盘后
//   zram 绑定新设备会丢弃已 writeback 页，恢复统一留给开机 setup。
// 安全边界：仅只读 sysfs（backing_dev 与 /sys/block 存在性），无块设备
//   写、无重绑动作。
// ============================================================

#pragma once

#include <cstddef>
#include <cstdint>
#include <string>

namespace lechao {
namespace lciod {

// ---- 事件 id（与 config/lcview_events.json id=21 writeback_guard 对应） ----
inline constexpr uint16_t kEventWritebackGuardId = 21;

// ---- 采样周期（秒） ----
// 拔盘韧性守护为低频巡检：60s 一轮足以在分钟级发现断供，避免高频读
// sysfs 与日志刷屏；断供/恢复各上报一次（边沿触发）。
inline constexpr uint32_t kWritebackGuardIntervalSec = 60;

// ---- 路径 ----
// zram0 写回后备设备节点（内容形如 /dev/block/sdb；设备移除后内核在
// 名称后追加 " (deleted)"）。
inline constexpr const char* kWritebackBackingDevPath =
    "/sys/block/zram0/backing_dev";
// 块设备 sysfs 根（检查后备设备是否仍存在）。
inline constexpr const char* kWritebackBlockDir = "/sys/block";

// 一次供给采样结果（仅只读 sysfs 推导）。
struct WritebackSupplySample {
    std::string backing_dev;   // backing_dev 原始文本（已 strip）
    std::string target_name;   // 解析出的设备名（如 sdb；无则空）
    bool target_present = false;  // /sys/block/<target_name> 是否存在
    bool healthy = false;      // 供给健康（非 deleted 且目标存在）
};

// 解析 backing_dev 文本 → 块设备名（如 "/dev/block/sdb" → "sdb"）。
// 去除尾部 " (deleted)"、前后空白；空串/"none"/无设备名部分返回空串。
std::string ParseBackingTarget(const std::string& raw);

// 供给健康判定（纯函数）：
//   raw 含 "(deleted)"（内核标记设备已移除）→ false；
//   解析不出设备名（空/none）→ false；
//   目标 /sys/block/<name> 不存在 → false；
//   否则 true。
bool EvaluateWritebackHealthy(const std::string& raw, bool target_present);

// 读取 sysfs 采样一次。out 空指针返回 false（参数错误）；sysfs 不可读
// 按"无供给"处理（backing_dev 空、healthy=false）仍返回 true——守护的
// 职责是暴露断供，而非把读失败当正常（CXX-002 不吞错）。
bool QueryWritebackSupply(WritebackSupplySample* out);

// 格式化事件 JSON 行（rule=writeback_guard，state=lost/recovered）。
// 返回 snprintf 结果；out 空/cap 0 返回 -1。供 service.cpp 经
// EVENT_ALOG 落 logcat，并供单测校验字段契约。
int FormatWritebackGuardJson(bool lost, const std::string& backing_dev,
                             bool target_present, char* out, size_t cap);

}  // namespace lciod
}  // namespace lechao
