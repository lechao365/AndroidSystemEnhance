"""rp5_serial provider transport 适配层。

包装 AutomationClient，实现 loop_core.BaseTransport 接口。
属于 connection 域，不依赖任何 workflow。
"""
from __future__ import annotations

import time

from loop_core.models import ObservedLine
from loop_core.transport import BaseTransport


class Rp5SerialTransport(BaseTransport):
    """包装 AutomationClient 的 live transport。

    通过 TCP 连接 rp5-serial Host，调用 capture_recent_lines / read_until_timeout 采集输出。
    """

    def __init__(self, client) -> None:
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
        """在等待窗口内轮询 recent buffer 与 stream 推送，命中即返回。

        覆盖两类 prompt 出现场景：
        - 已存在于 recent buffer（含未换行的 pending prompt）
        - 在等待期间新推送到 stream.data
        """
        deadline = time.monotonic() + timeout_sec
        poll_interval_sec = 0.2

        while True:
            # 先查 recent buffer（host pending prompt 只会在这里）
            recent_raw = self.client.capture_recent_lines(recent_limit)
            recent_t = time.monotonic()
            for text in recent_raw:
                for pattern in patterns:
                    if pattern in text:
                        return ObservedLine(t=recent_t, text=text)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None

            # 等待新推送
            pushed_raw = self.client.read_until_timeout(min(poll_interval_sec, remaining))
            base_t = time.monotonic()
            for i, text in enumerate(pushed_raw):
                for pattern in patterns:
                    if pattern in text:
                        return ObservedLine(t=base_t + i * 0.01, text=text)

            # 再次查 recent buffer（等待期间可能出现新 pending prompt）
            recent_raw = self.client.capture_recent_lines(recent_limit)
            recent_t = time.monotonic()
            for text in recent_raw:
                for pattern in patterns:
                    if pattern in text:
                        return ObservedLine(t=recent_t, text=text)
