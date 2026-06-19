"""transport 抽象层。

为 workflow runner 提供统一的观察与操作接口，屏蔽 fixture 回放与 live provider 的差异。

两类实现：
- FixtureTransport：基于 JSONL transcript 的离线回放
- 具体 provider transport（如 Rp5SerialTransport）留在 connection 域

两者都实现 BaseTransport 接口：
    acquire_writer / release / send_line / capture_window / wait_for_pattern
"""
from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path

from loop_core.models import ObservedLine


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

    @abstractmethod
    def capture_window(self, timeout_sec: float, recent_limit: int) -> list[ObservedLine]:
        """在 timeout_sec 时长内采集输出，返回 ObservedLine 列表。"""

    @abstractmethod
    def wait_for_pattern(
        self, patterns: list[str], timeout_sec: float, recent_limit: int
    ) -> ObservedLine | None:
        """等待 patterns 中任一模式出现。命中返回 ObservedLine，超时返回 None。"""


class FixtureTransport(BaseTransport):
    """基于 JSONL transcript 的离线回放 transport。

    每行格式：``{"t": <float>, "text": "<str>"}``
    capture_window 按 t <= timeout_sec 过滤行。
    wait_for_pattern 在 t <= timeout_sec 范围内扫描。
    """

    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
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
