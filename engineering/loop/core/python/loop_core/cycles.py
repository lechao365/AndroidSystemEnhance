"""通用 cycle 切分工具。

泛化自 boot_cycles.py：按 marker 序列切分观察窗口。
不同 workflow 赋予 cycle 不同含义（如 boot_cycle / restart_cycle）。
"""
from __future__ import annotations

from loop_core.models import ObservedLine


def assign_cycles(
    lines: list[ObservedLine],
    cycle_markers: list[str],
    field_name: str = "cycle_id",
) -> list[ObservedLine]:
    """给每行分配 cycle_id。

    规则：
    - 首行 cycle_id = 1
    - 遇到 cycle_marker 时，该行归当前 cycle
    - cycle_marker 之后的下一行开始新 cycle（cycle_id + 1）

    Args:
        lines: 原始 ObservedLine 列表（cycle_id 被忽略，重新分配）
        cycle_markers: 触发新 cycle 的标记文本列表
        field_name: 保留参数，当前固定写入 ``cycle_id`` 字段

    Returns:
        新的 ObservedLine 列表，cycle_id 已分配
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
            ObservedLine(t=line.t, text=line.text, cycle_id=cycle)
        )

        # 首行不触发分裂——首行就是 marker 视为冷启动
        if len(result) > 1 and any(
            marker in line.text for marker in cycle_markers
        ):
            next_line_is_new_cycle = True

    return result


def count_cycles(lines: list[ObservedLine], field_name: str = "cycle_id") -> int:
    """返回已分配 cycle_id 的行列表中的 cycle 总数。"""
    if not lines:
        return 0
    return max(line.cycle_id for line in lines)
