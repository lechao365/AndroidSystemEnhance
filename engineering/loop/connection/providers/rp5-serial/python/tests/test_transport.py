"""Rp5SerialTransport 合同测试（从 boot_failure_debug/tests 迁入）。

验证 provider transport 实现 loop_core.BaseTransport 接口。
"""
from unittest.mock import MagicMock

from loop_core.models import ObservedLine
from loop_core.transport import BaseTransport, CommandCapture
from rp5_serial.transport import Rp5SerialTransport


class TestRp5SerialTransport:
    def _make_mock_client(self):
        client = MagicMock()
        client.acquire_writer.return_value = True
        client.capture_recent_lines.return_value = ["line1", "line2"]
        client.read_until_timeout.return_value = ["line1", "line2"]
        return client

    def test_inherits_base_transport(self):
        transport = Rp5SerialTransport(self._make_mock_client())
        assert isinstance(transport, BaseTransport)

    def test_acquire_writer_delegates_to_client(self):
        client = self._make_mock_client()
        transport = Rp5SerialTransport(client)
        assert transport.acquire_writer() is True
        client.acquire_writer.assert_called_once()

    def test_release_delegates_to_client(self):
        client = self._make_mock_client()
        transport = Rp5SerialTransport(client)
        transport.release()
        client.release.assert_called_once()

    def test_send_line_delegates_to_client(self):
        client = self._make_mock_client()
        transport = Rp5SerialTransport(client)
        transport.acquire_writer()
        transport.send_line("uname -a")
        client.send_line.assert_called_once_with("uname -a")

    def test_capture_window_returns_observed_lines(self):
        client = self._make_mock_client()
        transport = Rp5SerialTransport(client)
        lines = transport.capture_window(timeout_sec=5, recent_limit=100)
        assert len(lines) == 2
        assert all(isinstance(line, ObservedLine) for line in lines)
        assert lines[0].text == "line1"

    def test_capture_window_merges_recent_and_pushed(self):
        """recent 和 pushed 不同行时应合并，边界重叠部分裁剪。"""
        client = MagicMock()
        client.acquire_writer.return_value = True
        client.capture_recent_lines.return_value = ["line_a", "line_b"]
        client.read_until_timeout.return_value = ["line_b", "line_c"]
        transport = Rp5SerialTransport(client)
        lines = transport.capture_window(timeout_sec=5, recent_limit=100)
        texts = [l.text for l in lines]
        assert texts == ["line_a", "line_b", "line_c"]

    def test_capture_window_preserves_duplicate_lines(self):
        """capture_window 保留合法重复行，仅裁剪边界重叠。"""
        client = MagicMock()
        client.capture_recent_lines.return_value = ["line_a", "repeat"]
        client.read_until_timeout.return_value = ["repeat", "repeat", "line_c"]
        transport = Rp5SerialTransport(client)
        lines = transport.capture_window(timeout_sec=5, recent_limit=100)
        texts = [l.text for l in lines]
        assert texts == ["line_a", "repeat", "repeat", "line_c"]

    def test_capture_window_uses_relative_timestamps(self):
        """capture_window 使用相对时间戳（0-based），不用 time.monotonic 绝对值。"""
        client = MagicMock()
        client.capture_recent_lines.return_value = []
        client.read_until_timeout.return_value = ["one", "two", "three"]
        transport = Rp5SerialTransport(client)
        lines = transport.capture_window(timeout_sec=5, recent_limit=100)
        assert lines[0].t == 0.0
        assert lines[1].t > lines[0].t
        assert lines[2].t > lines[1].t
        assert all(l.t < 100.0 for l in lines)  # 不应是 8473.x 这种绝对时间戳

    def test_wait_for_pattern_found_in_pushed(self):
        """pattern 在 pushed 中找到。"""
        client = self._make_mock_client()
        client.capture_recent_lines.return_value = ["line1", "line2"]
        client.read_until_timeout.return_value = ["Linux version", "console:/ $"]
        transport = Rp5SerialTransport(client)
        matched = transport.wait_for_pattern(
            ["console:/ $"], timeout_sec=5, recent_limit=100
        )
        assert matched is not None
        assert matched.text == "console:/ $"

    def test_wait_for_pattern_found_in_recent(self):
        """pattern 在 recent buffer 中找到。"""
        client = MagicMock()
        client.acquire_writer.return_value = True
        client.capture_recent_lines.return_value = ["Booting Linux", "console:/ $"]
        client.read_until_timeout.return_value = ["line_after"]
        transport = Rp5SerialTransport(client)
        matched = transport.wait_for_pattern(
            ["console:/ $"], timeout_sec=5, recent_limit=100
        )
        assert matched is not None
        assert matched.text == "console:/ $"

    def test_wait_for_pattern_delayed_pending_prompt(self):
        """delayed pending prompt：首次 recent 没有，后续 recent 出现。"""
        client = MagicMock()
        client.acquire_writer.return_value = True
        call_count = {"recent": 0, "read": 0}

        def mock_capture_recent_lines(limit):
            call_count["recent"] += 1
            # 前几次返回无 prompt，第 3 次返回有 prompt
            if call_count["recent"] >= 3:
                return ["some log", "console:/ $"]
            return ["some log"]

        def mock_read_until_timeout(timeout):
            call_count["read"] += 1
            return []

        client.capture_recent_lines.side_effect = mock_capture_recent_lines
        client.read_until_timeout.side_effect = mock_read_until_timeout

        transport = Rp5SerialTransport(client)
        matched = transport.wait_for_pattern(
            ["console:/ $"], timeout_sec=1.0, recent_limit=100
        )
        assert matched is not None
        assert matched.text == "console:/ $"


class TestRp5SerialTransportBoundaryApi:
    """新 API（mark_output_boundary / capture_since）边界契约测试。"""

    def test_mark_output_boundary_returns_increasing_generation(self):
        """mark_output_boundary 返回单调递增的边界游标。"""
        client = MagicMock()
        transport = Rp5SerialTransport(client)
        b1 = transport.mark_output_boundary()
        b2 = transport.mark_output_boundary()
        assert isinstance(b1, int)
        assert b2 > b1

    def test_capture_since_preserves_duplicate_lines(self):
        """capture_since 不做全局去重，保留合法重复日志。"""
        client = MagicMock()
        client.capture_recent_lines.return_value = ["line_a", "repeat"]
        client.read_until_timeout.return_value = ["repeat", "repeat", "line_c"]
        transport = Rp5SerialTransport(client)
        boundary = transport.mark_output_boundary()
        capture = transport.capture_since(boundary, timeout_sec=5, recent_limit=100)
        assert isinstance(capture, CommandCapture)
        texts = [line.text for line in capture.lines]
        assert texts == ["line_a", "repeat", "repeat", "line_c"]

    def test_capture_since_uses_relative_timestamps(self):
        """capture_since 使用相对时间戳（0-based），不用 time.monotonic 绝对值。"""
        client = MagicMock()
        client.capture_recent_lines.return_value = []
        client.read_until_timeout.return_value = ["one", "two", "three"]
        transport = Rp5SerialTransport(client)
        capture = transport.capture_since(
            transport.mark_output_boundary(), 5, 100
        )
        assert capture.lines[0].t == 0.0
        assert capture.lines[1].t > capture.lines[0].t
        assert capture.lines[2].t > capture.lines[1].t
        assert all(l.t < 100.0 for l in capture.lines)

    def test_capture_since_detects_prompt(self):
        """capture_since 正确检测 prompt 可见性。"""
        client = MagicMock()
        client.capture_recent_lines.return_value = []
        client.read_until_timeout.return_value = ["some output", "console:/ $"]
        transport = Rp5SerialTransport(client)
        capture = transport.capture_since(
            transport.mark_output_boundary(),
            5,
            100,
            prompt_markers=["console:/ $"],
        )
        assert capture.prompt_visible is True

    def test_capture_since_no_prompt(self):
        """capture_since 在无 prompt 时返回 prompt_visible=False。"""
        client = MagicMock()
        client.capture_recent_lines.return_value = []
        client.read_until_timeout.return_value = ["some output", "more output"]
        transport = Rp5SerialTransport(client)
        capture = transport.capture_since(
            transport.mark_output_boundary(),
            5,
            100,
            prompt_markers=["console:/ $"],
        )
        assert capture.prompt_visible is False

    def test_capture_since_respects_recent_limit(self):
        """capture_since 在 recent_limit > 0 时截断末尾行。"""
        client = MagicMock()
        client.capture_recent_lines.return_value = []
        client.read_until_timeout.return_value = [
            "l1", "l2", "l3", "l4", "l5"
        ]
        transport = Rp5SerialTransport(client)
        capture = transport.capture_since(
            transport.mark_output_boundary(), 5, recent_limit=2
        )
        texts = [line.text for line in capture.lines]
        assert texts == ["l4", "l5"]


def test_transport_describe_runtime_context():
    """transport 从 client.fetch_status/capture_recent_entries 构建运行时上下文"""
    client = MagicMock()
    client.capture_recent_entries.return_value = [
        {"text": "Booting Linux", "ts": "2026-06-20T12:00:00+0800", "pending": False},
        {"text": "console:/ $", "ts": "2026-06-20T12:00:02+0800", "pending": False},
    ]
    client.read_until_timeout.return_value = []
    client.fetch_status.return_value = {
        "data": {
            "transcript_path": "/tmp/rp5-serial-transcript.log",
            "recent_line_count": 2,
            "recent_buffer_limit": 2000,
        }
    }
    transport = Rp5SerialTransport(client)

    ctx = transport.describe_runtime_context()

    assert ctx["transcript_path"] == "/tmp/rp5-serial-transcript.log"
    assert ctx["recent_line_count"] == 2
    assert ctx["recent_buffer_limit"] == 2000
    assert len(ctx["serial_snippet"]) == 2
    assert ctx["serial_snippet"][0] == "Booting Linux"


def test_transport_capture_since_uses_host_timestamps():
    """capture_since 基于 host ISO 时间戳计算相对时间"""
    client = MagicMock()
    client.capture_recent_entries.return_value = [
        {"text": "Booting Linux", "ts": "2026-06-20T12:00:00+0800", "pending": False},
        {"text": "console:/ $", "ts": "2026-06-20T12:00:02+0800", "pending": False},
    ]
    client.read_until_timeout.return_value = []
    transport = Rp5SerialTransport(client)

    capture = transport.capture_since(transport.mark_output_boundary(), 5, 50, ["console:/ $"])

    assert capture.lines[0].text == "Booting Linux"
    assert capture.lines[0].t == 0.0
    assert abs(capture.lines[1].t - 2.0) < 0.01
    assert capture.warnings == []


def test_transport_set_cycle_markers_and_count_cycles():
    """transport.set_cycle_markers 后 describe_runtime_context 输出 reboot_cycles"""
    client = MagicMock()
    client.capture_recent_entries.return_value = [
        {"text": "Booting Linux", "ts": "2026-06-20T12:00:00+0800", "pending": False},
        {"text": "reboot: Restarting system", "ts": "2026-06-20T12:00:03+0800", "pending": False},
        {"text": "Booting Linux", "ts": "2026-06-20T12:00:05+0800", "pending": False},
    ]
    client.read_until_timeout.return_value = []
    client.fetch_status.return_value = {
        "data": {
            "transcript_path": "/tmp/serial.log",
            "recent_line_count": 3,
            "recent_buffer_limit": 2000,
        }
    }
    transport = Rp5SerialTransport(client)
    transport.set_cycle_markers(["reboot: Restarting system", "U-Boot"])

    ctx = transport.describe_runtime_context()

    assert ctx["reboot_cycles"] == 2
    assert ctx["serial_snippet"][1] == "reboot: Restarting system"


def test_transport_reboot_cycles_zero_without_markers():
    """无 cycle_markers 时 reboot_cycles 为 0"""
    client = MagicMock()
    client.capture_recent_entries.return_value = [
        {"text": "Booting Linux", "ts": "2026-06-20T12:00:00+0800", "pending": False},
    ]
    client.read_until_timeout.return_value = []
    client.fetch_status.return_value = {"data": {}}
    transport = Rp5SerialTransport(client)

    ctx = transport.describe_runtime_context()

    assert ctx["reboot_cycles"] == 0
