"""观察器：在 transport 之上封装采样快照。

提供：
- :func:`capture_snapshot`：采集一次观察窗口，返回 :class:`ObservationSnapshot`
- :func:`detect_prompt`：从文本行列表中检测 prompt

ObservationSnapshot 包含：
- lines: 带时间戳的观察行（已分配 boot_cycle_id）
- quiet_for_sec: 最后一条输出距 timeout 的静默时长
- prompt_line: 检测到的 prompt 行（ObservedLine 或 None）
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from boot_failure_debug.models import ObservedLine

if TYPE_CHECKING:
    from boot_failure_debug.config import WorkflowConfig
    from boot_failure_debug.transport import BaseTransport


@dataclass
class ObservationSnapshot:
    """一次观察窗口的快照。"""

    lines: list[ObservedLine]
    quiet_for_sec: float
    prompt_line: ObservedLine | None


def capture_snapshot(
    transport: "BaseTransport",
    cfg: "WorkflowConfig",
    timeout_sec: float,
) -> ObservationSnapshot:
    """采集一次观察窗口并构建快照。

    Args:
        transport: transport 实例（FixtureTransport 或 Rp5SerialTransport）
        cfg: workflow 配置
        timeout_sec: 观察时长上限

    Returns:
        :class:`ObservationSnapshot`
    """
    # 延迟导入避免循环依赖
    from boot_failure_debug.boot_cycles import assign_boot_cycles

    raw_lines = transport.capture_window(
        timeout_sec=timeout_sec, recent_limit=cfg.recent_lines_limit
    )

    # 分配 boot cycle
    lines = assign_boot_cycles(raw_lines, cfg)

    # 计算静默时长
    if lines:
        last_t = max(line.t for line in lines)
        # fixture: last_t 是相对时间，timeout 是上界
        # live: line.t 是 monotonic 基准，取近似
        quiet_for_sec = max(0.0, timeout_sec - last_t)
    else:
        quiet_for_sec = timeout_sec

    # 检测 prompt
    prompt_line: ObservedLine | None = None
    for line in lines:
        if any(marker in line.text for marker in cfg.prompt_markers):
            prompt_line = line
            break

    return ObservationSnapshot(
        lines=lines,
        quiet_for_sec=quiet_for_sec,
        prompt_line=prompt_line,
    )


def detect_prompt(
    texts: list[str], cfg: "WorkflowConfig"
) -> str | None:
    """从文本行列表中检测是否包含 prompt marker。

    Args:
        texts: 文本行列表
        cfg: workflow 配置（取 prompt_markers）

    Returns:
        第一个匹配 prompt marker 的文本行；无匹配返回 None
    """
    for text in texts:
        for marker in cfg.prompt_markers:
            if marker in text:
                return text
    return None
