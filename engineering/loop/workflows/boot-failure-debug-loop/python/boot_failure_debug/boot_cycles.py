"""boot cycle 识别。

为支持"反复重启"分析，引入 boot_cycle_id：
- 识别 boot 起点
- 标记 reboot 边界
- 按 cycle 归档关键错误片段

reboot marker 行本身归到当前 cycle，下一行开始新 cycle。
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from boot_failure_debug.models import ObservedLine

if TYPE_CHECKING:
    from boot_failure_debug.config import WorkflowConfig


def assign_boot_cycles(
    lines: list[ObservedLine], cfg: "WorkflowConfig"
) -> list[ObservedLine]:
    """给每行分配 boot_cycle_id。

    规则：
    - 首行 cycle_id = 1
    - 遇到 reboot_marker 时，该行归当前 cycle
    - reboot_marker 之后的下一行开始新 cycle（cycle_id + 1）

    Args:
        lines: 原始 ObservedLine 列表（boot_cycle_id 被忽略，重新分配）
        cfg: workflow 配置（取 reboot_markers）

    Returns:
        新的 ObservedLine 列表，boot_cycle_id 已分配
    """
    if not lines:
        return []

    result: list[ObservedLine] = []
    cycle = 1
    next_line_is_new_cycle = False

    for line in lines:
        if next_line_is_new_cycle:
            cycle += 1
            next_line_is_new_cycle = False

        result.append(
            ObservedLine(t=line.t, text=line.text, boot_cycle_id=cycle)
        )

        # 检测 reboot marker，标记下一行进入新 cycle
        # 首行（result 长度为 1）不触发分裂——首行就是 reboot marker 视为冷启动
        if len(result) > 1 and any(
            marker in line.text for marker in cfg.reboot_markers
        ):
            next_line_is_new_cycle = True

    return result


def count_boot_cycles(lines: list[ObservedLine]) -> int:
    """返回已分配 boot_cycle_id 的行列表中的 cycle 总数。"""
    if not lines:
        return 0
    return max(line.boot_cycle_id for line in lines)
