"""深度证据采集执行器。

collector 在用例 fail 时触发，执行预定义的命令列表采集诊断证据。
同 suite 内同 collector 去重（只执行一次）。
"""
from __future__ import annotations

import time

from loop_core.models import CollectorResult


class Collector:
    """深度证据采集器。

    通过 transport 执行命令列表，采集输出作为 AI 分析证据。
    """

    def __init__(self, transport) -> None:
        self.transport = transport

    def run(self, name: str, spec: dict, capture_timeout: float = 5.0,
            recent_limit: int = 400) -> CollectorResult:
        """执行一个 collector 的全部命令。

        Args:
            name: collector 名称
            spec: collector 规格 {commands: [...], hints: "..."}
            capture_timeout: 每条命令的采集超时
            recent_limit: 每条命令的行数上限

        Returns:
            CollectorResult
        """
        commands = spec.get("commands", [])
        hints = spec.get("hints", "")
        outputs: list[dict] = []

        for cmd in commands:
            start = time.monotonic()
            self.transport.send_line(cmd)
            lines = self.transport.capture_window(
                timeout_sec=capture_timeout, recent_limit=recent_limit
            )
            outputs.append({
                "command": cmd,
                "lines": [line.text for line in lines],
                "duration_sec": round(time.monotonic() - start, 3),
            })

        return CollectorResult(
            name=name,
            commands=commands,
            outputs=outputs,
            hints=hints,
        )
