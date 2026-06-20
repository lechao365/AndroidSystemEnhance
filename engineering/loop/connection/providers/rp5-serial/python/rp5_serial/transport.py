"""rp5_serial provider transport 适配层。

包装 AutomationClient，实现 loop_core.BaseTransport 接口。
属于 connection 域，不依赖任何 workflow。
"""
from __future__ import annotations

import time

from loop_core.models import ObservedLine
from loop_core.transport import BaseTransport, CommandCapture


class Rp5SerialTransport(BaseTransport):
    """包装 AutomationClient 的 live transport。

    通过 TCP 连接 rp5-serial Host，调用 capture_recent_lines / read_until_timeout 采集输出。

    两套采集 API 共享同一条采集管线：
    - 合并 recent buffer 与 stream 推送
    - 仅裁剪 recent 末尾与 pushed 开头的"边界重叠"，不做全局文本去重
      （全局去重会吞掉内核日志中合法的重复 crash 行）
    - 使用 0-based 相对时间戳（i * 0.01），避免绝对时间戳破坏 observer 的
      quiet window 计算（quiet_for_sec = timeout_sec - last_t）
    """

    def __init__(self, client) -> None:
        self.client = client
        # 新 API 边界游标：每次 mark_output_boundary 自增，仅作为不透明 token
        # 传给 capture_since；当前 live 实现依赖 host 环形缓冲 + stream 推送
        # 的自然时序隔离，generation 本身不参与采集逻辑。
        self._capture_generation = 0

    # ------------------------------------------------------------------
    # writer / send
    # ------------------------------------------------------------------

    def acquire_writer(self) -> bool:
        return self.client.acquire_writer()

    def release(self) -> None:
        self.client.release()

    def send_line(self, text: str) -> None:
        self.client.send_line(text)

    # ------------------------------------------------------------------
    # 合并辅助：仅裁剪边界重叠，不做全局去重
    # ------------------------------------------------------------------

    @staticmethod
    def _merge_boundary_overlap(
        recent_raw: list[str], pushed_raw: list[str]
    ) -> list[str]:
        """合并 recent 与 pushed，仅裁剪两者边界重叠部分，不做全局去重。

        recent 的末尾若干行可能与 pushed 的开头相同（host 环形缓冲尚未
        flush 的旧数据再次被 stream 推送一遍）。用最长后缀匹配定位重叠，
        跳过 pushed 开头的重复段。重复行（如内核 crash 重复日志）会被
        完整保留。
        """
        max_overlap = min(len(recent_raw), len(pushed_raw))
        overlap = 0
        for size in range(max_overlap, 0, -1):
            if recent_raw[-size:] == pushed_raw[:size]:
                overlap = size
                break
        return recent_raw + pushed_raw[overlap:]

    @staticmethod
    def _apply_recent_limit(
        lines: list[ObservedLine], recent_limit: int
    ) -> list[ObservedLine]:
        """recent_limit > 0 时保留末尾 N 行（与 fixture 行为一致）。"""
        if recent_limit > 0 and len(lines) > recent_limit:
            return lines[-recent_limit:]
        return lines

    @staticmethod
    def _detect_prompt_visible(
        lines: list[ObservedLine], prompt_markers: list[str]
    ) -> bool:
        """判断输出中是否可见任意 prompt marker。"""
        return any(
            any(marker in line.text for marker in prompt_markers)
            for line in lines
        )

    # ------------------------------------------------------------------
    # 旧 API（兼容期）
    # ------------------------------------------------------------------

    def capture_window(
        self, timeout_sec: float, recent_limit: int
    ) -> list[ObservedLine]:
        """采集输出窗口：先拉 recent buffer，再等待新推送。

        live 场景下板子可能已启动完成、串口输出稀疏，仅靠 read_until_timeout
        可能拿不到数据。因此先通过 capture_recent_lines 拉 host 环形缓冲中的
        历史行，再等待 timeout_sec 内的新输出，合并裁剪边界重叠。

        时间戳使用 0-based 相对值（i * 0.01），便于 observer 计算 quiet window。
        """
        recent_raw = self.client.capture_recent_lines(recent_limit)
        pushed_raw = self.client.read_until_timeout(timeout_sec)
        merged = self._merge_boundary_overlap(recent_raw, pushed_raw)
        lines = [
            ObservedLine(t=i * 0.01, text=text) for i, text in enumerate(merged)
        ]
        return self._apply_recent_limit(lines, recent_limit)

    def wait_for_pattern(
        self, patterns: list[str], timeout_sec: float, recent_limit: int
    ) -> ObservedLine | None:
        """在等待窗口内轮询 recent buffer 与 stream 推送，命中即返回。

        覆盖两类 prompt 出现场景：
        - 已存在于 recent buffer（含未换行的 pending prompt）
        - 在等待期间新推送到 stream.data

        返回的 ObservedLine.t 使用相对轮询序号时间戳，仅供调用方顺序参考。
        """
        deadline = time.monotonic() + timeout_sec
        poll_interval_sec = 0.2

        while True:
            # 先查 recent buffer（host pending prompt 只会在这里）
            recent_raw = self.client.capture_recent_lines(recent_limit)
            for i, text in enumerate(recent_raw):
                for pattern in patterns:
                    if pattern in text:
                        return ObservedLine(t=i * 0.01, text=text)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None

            # 等待新推送
            pushed_raw = self.client.read_until_timeout(
                min(poll_interval_sec, remaining)
            )
            for i, text in enumerate(pushed_raw):
                for pattern in patterns:
                    if pattern in text:
                        return ObservedLine(t=i * 0.01, text=text)

            # 再次查 recent buffer（等待期间可能出现新 pending prompt）
            recent_raw = self.client.capture_recent_lines(recent_limit)
            for i, text in enumerate(recent_raw):
                for pattern in patterns:
                    if pattern in text:
                        return ObservedLine(t=i * 0.01, text=text)

    # ------------------------------------------------------------------
    # 新 API（边界游标化）
    # ------------------------------------------------------------------

    def mark_output_boundary(self) -> int:
        """返回当前输出位置的边界游标。

        live 实现靠 host 环形缓冲 + stream 推送的时序自然隔离：每次采集
        都会重新拉 recent buffer + 等待新推送，最近一轮输出会被下一轮
        的边界重叠裁剪逻辑处理掉。generation 作为不透明 token 供调用方
        持有，每次调用单调递增。
        """
        self._capture_generation += 1
        return self._capture_generation

    def capture_since(
        self,
        boundary: object,
        timeout_sec: float,
        recent_limit: int,
        prompt_markers: list[str] | None = None,
    ) -> CommandCapture:
        """采集 ``boundary`` 之后的输出。

        复用 capture_window 的合并管线（recent + pushed，仅裁剪边界重叠），
        额外计算 prompt 可见性并封装为 CommandCapture。

        Args:
            boundary: mark_output_boundary 返回的不透明游标（live 实现未使用，
                保留参数以对齐接口契约）
            timeout_sec: stream 推送等待时长上限
            recent_limit: 行数上限（0 表示不限），取末尾 N 行
            prompt_markers: prompt 标记列表，用于判断 prompt 可见性
        """
        del boundary  # live 实现靠时序隔离，未消费游标本身

        recent_raw = self.client.capture_recent_lines(recent_limit)
        pushed_raw = self.client.read_until_timeout(timeout_sec)
        merged = self._merge_boundary_overlap(recent_raw, pushed_raw)
        lines = [
            ObservedLine(t=i * 0.01, text=text) for i, text in enumerate(merged)
        ]
        lines = self._apply_recent_limit(lines, recent_limit)

        markers = prompt_markers or []
        prompt_visible = self._detect_prompt_visible(lines, markers)
        return CommandCapture(lines=lines, prompt_visible=prompt_visible)
