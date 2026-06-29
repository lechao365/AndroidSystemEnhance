"""transport 抽象层。

为 workflow runner 提供统一的观察与操作接口，屏蔽 fixture 回放与 live provider 的差异。

两类实现：
- FixtureTransport：基于 JSONL transcript 的离线回放
- 具体 provider transport（如 Rp5SerialTransport）留在 connection 域

BaseTransport 同时暴露两套采集 API：
- 旧 API（兼容期）：acquire_writer / release / send_line / capture_window / wait_for_pattern
- 新 API（边界游标化）：mark_output_boundary / capture_since

新 API 通过边界游标保证每次命令只读到自身发送之后的输出，
消除历史缓冲污染。Task 3 将迁移 Rp5SerialTransport 到新 API，
迁移完成后旧 API 将被移除。
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from loop_core.models import ObservedLine, RebootResult


@dataclass
class CommandCapture:
    """单次命令的采集结果。

    Attributes:
        lines: 本次命令之后新增的 ObservedLine 列表
        prompt_visible: 本次输出中是否可见任意 prompt marker
        exit_code: 命令退出码（不可获取时为 None）
        warnings: 采集过程中产生的告警信息
    """

    lines: list[ObservedLine]
    prompt_visible: bool = False
    exit_code: int | None = None
    warnings: list[str] = field(default_factory=list)


class BaseTransport(ABC):
    """transport 统一接口。"""

    @abstractmethod
    def acquire_writer(self) -> bool:
        """申请写入权。成功返回 True，失败返回 False。"""

    @abstractmethod
    def release(self) -> None:
        """释放写入权与连接资源。"""

    @abstractmethod
    def send_line(self, text: str) -> None:
        """发送一行文本。必须先 acquire_writer。"""

    # ------------------------------------------------------------------
    # 旧采集 API（兼容期保留，P2-8：降级为默认实现，不再强制子类实现）
    # ------------------------------------------------------------------
    # executor 实际使用新 API（mark_output_boundary / capture_since）。
    # 旧 API 仅 FixtureTransport / Rp5SerialTransport 等已实现者保留；
    # 新 provider 无需实现无用的旧 API 即可实例化。调用未实现的旧 API 时
    # 抛 NotImplementedError（与新 API 的默认行为一致）。

    def capture_window(self, timeout_sec: float, recent_limit: int) -> list[ObservedLine]:
        """在 timeout_sec 时长内采集输出，返回 ObservedLine 列表。"""
        raise NotImplementedError(
            "capture_window not implemented; provider uses new API (capture_since)"
        )

    def wait_for_pattern(
        self, patterns: list[str], timeout_sec: float, recent_limit: int
    ) -> ObservedLine | None:
        """等待 patterns 中任一模式出现。命中返回 ObservedLine，超时返回 None。"""
        raise NotImplementedError(
            "wait_for_pattern not implemented; provider uses new API (capture_since)"
        )

    # ------------------------------------------------------------------
    # 新采集 API（边界游标化）
    # ------------------------------------------------------------------
    # 说明：此处提供默认 NotImplementedError 实现而非 abstractmethod，
    # 以便尚未迁移的 provider（如 Rp5SerialTransport）继续使用旧 API，
    # 避免 ABC 实例化失败。Task 3 将为各 provider 落地真实实现。

    def mark_output_boundary(self) -> object:
        """返回当前输出位置的边界游标。

        Returns:
            不透明游标对象，后续传给 ``capture_since`` 圈定采集起点。
        """
        raise NotImplementedError(
            "mark_output_boundary not implemented; provider needs new API migration"
        )

    def capture_since(
        self,
        boundary: object,
        timeout_sec: float,
        recent_limit: int,
        prompt_markers: list[str] | None = None,
    ) -> CommandCapture:
        """仅返回 ``boundary`` 之后的输出。

        Args:
            boundary: ``mark_output_boundary`` 返回的游标
            timeout_sec: 采集时长上限
            recent_limit: 行数上限（0 表示不限）
            prompt_markers: prompt 标记列表，用于判断 prompt 可见性

        Returns:
            CommandCapture
        """
        raise NotImplementedError(
            "capture_since not implemented; provider needs new API migration"
        )

    # ------------------------------------------------------------------
    # reboot API（跨重启）
    # ------------------------------------------------------------------

    def reboot_and_wait(
        self,
        boot_markers: list[str],
        panic_markers: list[str],
        boot_complete_timeout: float = 180.0,
        l1_timeout: float = 30.0,
        l2_timeout: float = 90.0,
        l3_timeout: float = 60.0,
        prompt_markers: list[str] | None = None,
    ) -> RebootResult:
        """发 reboot 并等待设备回来。

        子类必须实现。live 实现走 stream + marker 检测；
        fixture 实现走 fixture 数据消费。

        Args:
            boot_markers: [L1_early, L2_init_ready] 两级 boot 标记
            panic_markers: kernel panic 标记（命中即 fail）
            boot_complete_timeout: 总超时（兜底，默认 180s）
            l1_timeout: 等 boot_markers[0] 的上限
            l2_timeout: 等 boot_markers[1] 的上限
            l3_timeout: 等 getprop sys.boot_completed 返回 1 的上限
            prompt_markers: prompt 标记列表（L3 getprop 响应判定用）

        Returns:
            RebootResult
        """
        raise NotImplementedError(
            "reboot_and_wait not implemented; provider needs reboot support"
        )


class FixtureTransport(BaseTransport):
    """基于 JSONL transcript 的离线回放 transport。

    每行格式：``{"t": <float>, "text": "<str>"}``

    旧 API（capture_window / wait_for_pattern）按 ``t <= timeout_sec`` 过滤行，
    不会推进游标；新 API（mark_output_boundary / capture_since）基于内部
    ``_cursor`` 只返回游标之后的行，并推进游标，避免跨命令历史污染。
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self._cursor = 0
        self._sent_lines: list[ObservedLine] = []
        self._sim_time = 0.0
        self._writer_held = False

    @classmethod
    def from_jsonl(cls, path: str) -> "FixtureTransport":
        """从 JSONL 文件加载 fixture。"""
        rows = [
            json.loads(line)
            for line in Path(path).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        return cls(rows)

    def acquire_writer(self) -> bool:
        self._writer_held = True
        return True

    def release(self) -> None:
        self._writer_held = False

    def send_line(self, text: str) -> None:
        if not self._writer_held:
            raise RuntimeError("writer not acquired")
        self._sim_time += 0.1
        self._sent_lines.append(ObservedLine(t=self._sim_time, text=text))

    # ------------------------------------------------------------------
    # 旧 API
    # ------------------------------------------------------------------

    def capture_window(self, timeout_sec: float, recent_limit: int) -> list[ObservedLine]:
        window = [r for r in self._rows if r["t"] <= timeout_sec]
        lines = [ObservedLine(t=r["t"], text=r["text"]) for r in window]
        if recent_limit > 0 and len(lines) > recent_limit:
            lines = lines[-recent_limit:]
        return lines

    def wait_for_pattern(
        self, patterns: list[str], timeout_sec: float, recent_limit: int
    ) -> ObservedLine | None:
        for r in self._rows:
            if r["t"] > timeout_sec:
                break
            for pattern in patterns:
                if pattern in r["text"]:
                    return ObservedLine(t=r["t"], text=r["text"])
        return None

    # ------------------------------------------------------------------
    # 新 API（边界游标化）
    # ------------------------------------------------------------------

    def mark_output_boundary(self) -> int:
        """返回当前游标位置作为边界。"""
        return self._cursor

    def capture_since(
        self,
        boundary: object,
        timeout_sec: float,
        recent_limit: int,
        prompt_markers: list[str] | None = None,
    ) -> CommandCapture:
        """只采集 ``boundary`` 之后的 fixture 行，并推进游标。

        采集边界规则（模拟真实设备"命令输出在 shell prompt 回来时结束"）：
        - 若提供 ``prompt_markers``：从 ``boundary`` 开始读行，命中第一个
          prompt 行（含）后停止，游标推进到 prompt 行的下一行；
        - 若未提供 ``prompt_markers`` 或未命中任何 prompt：消费从
          ``boundary`` 到列表末尾的全部行。

        该规则既保证跨命令历史隔离，又能让多 case 的静态 fixture 按
        "命令/prompt" 自然分段回放。

        Args:
            boundary: int 类型的行索引（由 mark_output_boundary 返回）
            timeout_sec: 仅为与 live transport API 对齐而保留；fixture 回放
                不真实等待，本实现忽略该参数
            recent_limit: 行数上限（保留末尾 N 行）；0 表示不限
            prompt_markers: prompt 标记列表；提供时用于在首个 prompt 处截断
        """
        prompt_markers = prompt_markers or []
        start = int(boundary)
        selected: list[dict] = []
        next_cursor = len(self._rows)

        for idx in range(start, len(self._rows)):
            row = self._rows[idx]
            selected.append(row)
            if prompt_markers and any(
                marker in row["text"] for marker in prompt_markers
            ):
                # 命令输出在首个 prompt 行结束，游标推进到 prompt 之后
                next_cursor = idx + 1
                break

        self._cursor = next_cursor

        lines = [ObservedLine(t=r["t"], text=r["text"]) for r in selected]
        if recent_limit > 0 and len(lines) > recent_limit:
            lines = lines[-recent_limit:]

        prompt_visible = any(
            any(marker in line.text for marker in prompt_markers)
            for line in lines
        )
        return CommandCapture(lines=lines, prompt_visible=prompt_visible)

    # ------------------------------------------------------------------
    # reboot API（fixture 兼容实现）
    # ------------------------------------------------------------------

    def reboot_and_wait(
        self,
        boot_markers: list[str],
        panic_markers: list[str],
        boot_complete_timeout: float = 180.0,
        l1_timeout: float = 30.0,
        l2_timeout: float = 90.0,
        l3_timeout: float = 60.0,
        prompt_markers: list[str] | None = None,
    ) -> RebootResult:
        """fixture 模式：在 fixture 数据里检测 boot marker。

        fixture 回放不真实发 reboot，而是扫描 fixture 行：
        - 命中 panic_markers → 立即返回 fail(panic_detected)
        - 命中 boot_markers[0] → L1 达到
        - 命中 boot_markers[1] → L2 达到
        - L2 后模拟发 getprop，扫剩余行找 "1" → L3 达成 pass
        - 无任何 boot marker → fail(fixture_no_reboot)

        timeout 参数在 fixture 模式下忽略（不真实等待）。
        """
        del boot_complete_timeout, l1_timeout, l2_timeout, l3_timeout

        all_lines = [r["text"] for r in self._rows]
        l1_marker = boot_markers[0] if len(boot_markers) > 0 else ""
        l2_marker = boot_markers[1] if len(boot_markers) > 1 else ""

        stage = "none"
        l2_end_idx = len(all_lines)

        for idx, line in enumerate(all_lines):
            for p in panic_markers:
                if p in line:
                    return RebootResult(
                        status="fail",
                        transcript_lines=all_lines,
                        failure_reason=f"panic_detected: {line}",
                        stage_reached=stage,
                        boot_duration_sec=0.0,
                    )
            if stage == "none" and l1_marker and l1_marker in line:
                stage = "l1_boot_start"
                continue
            if stage == "l1_boot_start" and l2_marker and l2_marker in line:
                stage = "l2_init_ready"
                l2_end_idx = idx
                continue

        if stage == "none":
            return RebootResult(
                status="fail",
                transcript_lines=all_lines,
                failure_reason="fixture_no_reboot: no boot marker found in fixture",
                stage_reached="none",
                boot_duration_sec=0.0,
            )

        remaining = all_lines[l2_end_idx + 1:]
        markers = prompt_markers or []
        boot_completed_hit = any(
            line.strip() == "1" or any(m in line for m in markers)
            for line in remaining
        )
        if stage == "l2_init_ready" and boot_completed_hit:
            return RebootResult(
                status="pass",
                transcript_lines=all_lines,
                failure_reason="",
                stage_reached="l3_verified",
                boot_duration_sec=0.0,
            )

        return RebootResult(
            status="fail",
            transcript_lines=all_lines,
            failure_reason=f"timeout at stage {stage}: boot_completed not found in fixture",
            stage_reached=stage,
            boot_duration_sec=0.0,
        )
