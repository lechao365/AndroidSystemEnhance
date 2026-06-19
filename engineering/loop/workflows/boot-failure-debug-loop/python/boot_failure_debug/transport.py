"""transport 抽象层。

为 workflow runner 提供统一的观察与操作接口，屏蔽 fixture 回放与 live rp5-serial 的差异。

两类实现：
- :class:`FixtureTransport`：基于 JSONL transcript 的离线回放，供 AI 自验证
- :class:`Rp5SerialTransport`：包装 :class:`AutomationClient` 的 live transport

两者都实现 :class:`BaseTransport` 接口：
    acquire_writer / release / send_line / capture_window / wait_for_pattern
"""
from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING

from boot_failure_debug.models import ObservedLine

if TYPE_CHECKING:
    from rp5_serial.client.automation import AutomationClient


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
        # fixture 时间线是相对的，timeout_sec 直接当作 t 上界
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


class Rp5SerialTransport(BaseTransport):
    """包装 :class:`AutomationClient` 的 live transport。

    通过 TCP 连接 rp5-serial Host，调用 capture_recent_lines / read_until_timeout 采集输出。
    """

    def __init__(self, client: "AutomationClient") -> None:
        self.client = client

    def acquire_writer(self) -> bool:
        return self.client.acquire_writer()

    def release(self) -> None:
        self.client.release()

    def send_line(self, text: str) -> None:
        self.client.send_line(text)

    def capture_window(self, timeout_sec: float, recent_limit: int) -> list[ObservedLine]:
        """采集输出窗口：先拉 recent buffer，再等待新推送。

        live 场景下板子可能已启动完成、串口输出稀疏，仅靠 read_until_timeout
        可能拿不到数据。因此先通过 capture_recent_lines 拉 host 环形缓冲中的
        历史行，再等待 timeout_sec 内的新输出，合并去重。
        """
        base_t = time.monotonic()

        # 1) 先拉 recent buffer（host 侧环形缓冲，最多 recent_limit 行）
        recent_raw = self.client.capture_recent_lines(recent_limit)

        # 2) 等待新推送
        pushed_raw = self.client.read_until_timeout(timeout_sec)

        # 3) 合并去重：recent 的末尾可能与 pushed 的开头重叠
        seen: set[str] = set()
        merged: list[str] = []
        for text in recent_raw + pushed_raw:
            if text not in seen:
                seen.add(text)
                merged.append(text)

        lines = [
            ObservedLine(t=base_t + i * 0.01, text=text)
            for i, text in enumerate(merged)
        ]
        if recent_limit > 0 and len(lines) > recent_limit:
            lines = lines[-recent_limit:]
        return lines

    def wait_for_pattern(
        self, patterns: list[str], timeout_sec: float, recent_limit: int
    ) -> ObservedLine | None:
        """在 recent buffer + timeout_sec 内持续读取输出，命中任一 pattern 即返回。"""
        # 先在 recent buffer 中找
        recent_raw = self.client.capture_recent_lines(recent_limit)
        for text in recent_raw:
            for pattern in patterns:
                if pattern in text:
                    return ObservedLine(t=0.0, text=text)

        # 再等新推送
        pushed_raw = self.client.read_until_timeout(timeout_sec)
        base_t = time.monotonic()
        for i, text in enumerate(pushed_raw):
            for pattern in patterns:
                if pattern in text:
                    return ObservedLine(t=base_t + i * 0.01, text=text)
        return None
