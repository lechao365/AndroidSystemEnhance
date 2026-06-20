"""rp5_serial provider transport 适配层。

包装 AutomationClient，实现 loop_core.BaseTransport 接口。
属于 connection 域，不依赖任何 workflow。
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta

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
        # reboot cycle 边界标记（Task 5 计算 reboot_cycles 用），本 Task 仅占位。
        self._cycle_markers: list[str] = []

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

    # ------------------------------------------------------------------
    # host 时间戳采集辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_iso_ts(ts_str: str) -> float:
        """解析 host 侧 ISO8601 时间戳（含时区偏移）为 epoch 秒。"""
        return datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%S%z").timestamp()

    def _build_lines_from_entries(
        self, entries: list[dict]
    ) -> list[ObservedLine]:
        """基于 host 结构化条目构建 0-based 相对时间戳的 ObservedLine 列表。

        以首条目的时间戳为基准，后续条目相对其求差，保留 host 真实时序，
        供 observer 计算 quiet window。
        """
        if not entries:
            return []
        base = self._parse_iso_ts(entries[0]["ts"])
        lines: list[ObservedLine] = []
        for entry in entries:
            current = self._parse_iso_ts(entry["ts"])
            lines.append(
                ObservedLine(t=round(current - base, 3), text=entry["text"])
            )
        return lines

    def _safe_capture_entries(self, recent_limit: int) -> list[dict] | None:
        """尝试获取 host 结构化条目，校验通过返回 list，否则返回 None。

        返回 None 表示 host/client 不支持结构化条目（如旧 client、Mock 未配置
        或缺少合法 ts/text 字段），调用方据此降级到旧的伪时间戳采集管线。
        """
        try:
            entries = self.client.capture_recent_entries(recent_limit)
        except (OSError, AttributeError):
            return None
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if not isinstance(entry, dict):
                return None
            if not isinstance(entry.get("ts"), str) or not isinstance(
                entry.get("text"), str
            ):
                return None
        return entries

    def _append_pushed_entries(
        self, entries: list[dict], pushed_raw: list[str]
    ) -> list[dict]:
        """将 stream 推送行（无 host 时间戳）追加到结构化条目尾部。

        推送行本身不带 host ISO 时间戳，这里以"最后一条条目时间戳 + 递增
        毫秒偏移"合成时间戳，保持相对时序单调；条目为空时退化到固定基准。
        """
        if not pushed_raw:
            return entries
        result = list(entries)
        if result:
            base_dt = datetime.strptime(
                result[-1]["ts"], "%Y-%m-%dT%H:%M:%S%z"
            )
            offset_start = 1
        else:
            base_dt = datetime.strptime(
                "2026-01-01T00:00:00+0800", "%Y-%m-%dT%H:%M:%S%z"
            )
            offset_start = 0
        for index, text in enumerate(pushed_raw, offset_start):
            new_dt = base_dt + timedelta(milliseconds=index * 100)
            result.append(
                {
                    "text": text,
                    "ts": new_dt.strftime("%Y-%m-%dT%H:%M:%S%z"),
                    "pending": False,
                }
            )
        return result

    def describe_runtime_context(self) -> dict:
        """汇总 host 运行时上下文（供 AI 分析 / 调试快照使用）。

        组合 ``session.status`` 与最近串口条目，输出 transcript 路径、缓冲
        统计与末尾串口片段；fetch_status 失败时返回带 warnings 的降级上下文。

        Returns:
            含 transcript_path / recent_line_count / recent_buffer_limit /
            serial_snippet / reboot_cycles 的 dict
        """
        try:
            status = self.client.fetch_status()
        except OSError:
            return {"warnings": ["fetch_status failed"]}
        data = status.get("data", {}) if isinstance(status, dict) else {}

        entries = self._safe_capture_entries(200) or []
        snippet = [entry["text"] for entry in entries[-40:]]
        return {
            "transcript_path": data.get("transcript_path", ""),
            "recent_line_count": data.get("recent_line_count", 0),
            "recent_buffer_limit": data.get("recent_buffer_limit", 0),
            "serial_snippet": snippet,
            "reboot_cycles": 0,
        }

    def capture_since(
        self,
        boundary: object,
        timeout_sec: float,
        recent_limit: int,
        prompt_markers: list[str] | None = None,
    ) -> CommandCapture:
        """采集 ``boundary`` 之后的输出。

        优先走 host 时间戳管线：当 ``capture_recent_entries`` 返回合法结构化
        条目时，基于 host ISO 时间戳构建相对时间戳；否则降级到旧的伪时间戳
        合并管线（``capture_recent_lines`` + ``read_until_timeout``，仅裁剪边界
        重叠），保证未升级 host / 旧调用方行为不变。

        Args:
            boundary: mark_output_boundary 返回的不透明游标（live 实现未使用，
                保留参数以对齐接口契约）
            timeout_sec: stream 推送等待时长上限
            recent_limit: 行数上限（0 表示不限），取末尾 N 行
            prompt_markers: prompt 标记列表，用于判断 prompt 可见性
        """
        entries = self._safe_capture_entries(recent_limit)
        if entries is not None:
            pushed_raw = self.client.read_until_timeout(timeout_sec)
            entries = self._append_pushed_entries(entries, pushed_raw)
            lines = self._build_lines_from_entries(entries)
            lines = self._apply_recent_limit(lines, recent_limit)
        else:
            del boundary  # live 实现靠时序隔离，未消费游标本身
            recent_raw = self.client.capture_recent_lines(recent_limit)
            pushed_raw = self.client.read_until_timeout(timeout_sec)
            merged = self._merge_boundary_overlap(recent_raw, pushed_raw)
            lines = [
                ObservedLine(t=i * 0.01, text=text)
                for i, text in enumerate(merged)
            ]
            lines = self._apply_recent_limit(lines, recent_limit)

        markers = prompt_markers or []
        prompt_visible = self._detect_prompt_visible(lines, markers)
        return CommandCapture(lines=lines, prompt_visible=prompt_visible)
