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

from loop_core.models import ObservedLine


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
    # 旧采集 API（兼容期保留，Task 3 迁移后移除）
    # ------------------------------------------------------------------

    @abstractmethod
    def capture_window(self, timeout_sec: float, recent_limit: int) -> list[ObservedLine]:
        """在 timeout_sec 时长内采集输出，返回 ObservedLine 列表。"""

    @abstractmethod
    def wait_for_pattern(
        self, patterns: list[str], timeout_sec: float, recent_limit: int
    ) -> ObservedLine | None:
        """等待 patterns 中任一模式出现。命中返回 ObservedLine，超时返回 None。"""

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
            recent_limit: 行数上限；0 表示不限
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
            lines = lines[:recent_limit]

        prompt_visible = any(
            any(marker in line.text for marker in prompt_markers)
            for line in lines
        )
        return CommandCapture(lines=lines, prompt_visible=prompt_visible)
