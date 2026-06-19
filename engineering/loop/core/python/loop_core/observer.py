"""loop_core 观察器：在 transport 之上封装采样快照。

capture_snapshot 参数化接收配置值，不绑定具体 Config 类型，
避免 core 反向依赖业务层。
"""
from __future__ import annotations

from dataclasses import dataclass

from loop_core.models import ObservedLine


@dataclass
class ObservationSnapshot:
    """一次观察窗口的快照。"""

    lines: list[ObservedLine]
    quiet_for_sec: float
    prompt_line: ObservedLine | None


def capture_snapshot(
    transport,
    timeout_sec: float,
    prompt_markers: list[str],
    recent_limit: int,
    quiet_window_sec: float = 0.0,
    cycle_markers: list[str] | None = None,
) -> ObservationSnapshot:
    """采集一次观察窗口并构建快照。

    Args:
        transport: 实现 BaseTransport 接口的实例
        timeout_sec: 观察时长上限
        prompt_markers: prompt 标记列表
        recent_limit: recent buffer 行数上限
        quiet_window_sec: 静默窗口阈值（用于计算 quiet_for_sec 的参考）
        cycle_markers: cycle 切分标记；提供时会为行分配 cycle_id

    Returns:
        ObservationSnapshot
    """
    from loop_core.cycles import assign_cycles

    raw_lines = transport.capture_window(
        timeout_sec=timeout_sec, recent_limit=recent_limit
    )

    # 分配 cycle（如果提供了 cycle_markers）
    if cycle_markers:
        lines = assign_cycles(raw_lines, cycle_markers)
    else:
        lines = list(raw_lines)

    # 计算静默时长
    if lines:
        last_t = max(line.t for line in lines)
        quiet_for_sec = max(0.0, timeout_sec - last_t)
    else:
        quiet_for_sec = timeout_sec

    # 检测 prompt
    prompt_line: ObservedLine | None = None
    for line in lines:
        if any(marker in line.text for marker in prompt_markers):
            prompt_line = line
            break

    return ObservationSnapshot(
        lines=lines,
        quiet_for_sec=quiet_for_sec,
        prompt_line=prompt_line,
    )


def detect_prompt(texts: list[str], markers: list[str]) -> str | None:
    """从文本行列表中检测是否包含 prompt marker。

    Args:
        texts: 文本行列表
        markers: prompt 标记列表

    Returns:
        第一个匹配 prompt marker 的文本行；无匹配返回 None
    """
    for text in texts:
        for marker in markers:
            if marker in text:
                return text
    return None
