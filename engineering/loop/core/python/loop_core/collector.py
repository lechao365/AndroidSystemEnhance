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
            recent_limit: int = 400,
            prompt_markers: list[str] | None = None) -> CollectorResult:
        """执行一个 collector 的全部命令。

        Args:
            name: collector 名称
            spec: collector 规格 {commands: [...], hints: "..."}
            capture_timeout: 每条命令的采集超时
            recent_limit: 每条命令的行数上限
            prompt_markers: prompt 标记列表；传给 transport 用于在 fixture
                回放下按命令/prompt 自然分段，避免首条命令消费全部 fixture 行

        Returns:
            CollectorResult
        """
        commands = spec.get("commands", [])
        hints = spec.get("hints", "")
        prompt_markers = prompt_markers or []

        mode = spec.get("mode", "commands")
        if mode == "serial_context":
            describe = getattr(self.transport, "describe_runtime_context", None)
            context = describe() if callable(describe) else {}
            outputs = [{
                "command": "serial_context",
                "lines": context.get("serial_snippet", []),
                "duration_sec": 0.0,
                "metadata": {
                    "reboot_cycles": context.get("reboot_cycles", 0),
                    "recent_line_count": context.get("recent_line_count", 0),
                },
            }]
            transc_path = context.get("transcript_path", "")
            return CollectorResult(
                name=name,
                commands=[],
                outputs=outputs,
                hints=hints,
                status="ok",
                partial=False,
                artifact_paths=[transc_path] if transc_path else [],
            )

        outputs: list[dict] = []
        error_msg = ""

        for cmd in commands:
            boundary = self.transport.mark_output_boundary()
            start = time.monotonic()
            try:
                self.transport.send_line(cmd)
                capture = self.transport.capture_since(
                    boundary, capture_timeout, recent_limit, prompt_markers
                )
                # 剥离 shell prompt 行：prompt 是 shell 提示符，不属于命令输出
                lines = [
                    line.text for line in capture.lines
                    if not any(m in line.text for m in prompt_markers)
                ]
                outputs.append({
                    "command": cmd,
                    "lines": lines,
                    "duration_sec": round(time.monotonic() - start, 3),
                })
            except OSError as exc:
                outputs.append({
                    "command": cmd,
                    "lines": [],
                    "duration_sec": round(time.monotonic() - start, 3),
                    "error": str(exc),
                })
                if not error_msg:
                    error_msg = str(exc)

        # 循环后统一判定最终状态：全成功=ok，部分失败=degraded，全失败=error
        failed_count = sum(1 for out in outputs if "error" in out)
        succeeded_count = len(outputs) - failed_count
        if failed_count == 0:
            status = "ok"
            partial = False
        elif succeeded_count > 0:
            status = "degraded"
            partial = True
        else:
            status = "error"
            partial = False

        return CollectorResult(
            name=name,
            commands=commands,
            outputs=outputs,
            hints=hints,
            status=status,
            partial=partial,
            error=error_msg,
        )
