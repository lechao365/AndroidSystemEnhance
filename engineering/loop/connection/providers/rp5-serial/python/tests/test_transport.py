"""Rp5SerialTransport 合同测试（从 boot_failure_debug/tests 迁入）。

验证 provider transport 实现 loop_core.BaseTransport 接口。
"""
from unittest.mock import MagicMock

from loop_core.models import ObservedLine
from loop_core.transport import BaseTransport
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
        """recent 和 pushed 不同行时应合并，相同时应去重。"""
        client = MagicMock()
        client.acquire_writer.return_value = True
        client.capture_recent_lines.return_value = ["line_a", "line_b"]
        client.read_until_timeout.return_value = ["line_b", "line_c"]
        transport = Rp5SerialTransport(client)
        lines = transport.capture_window(timeout_sec=5, recent_limit=100)
        texts = [l.text for l in lines]
        assert texts == ["line_a", "line_b", "line_c"]

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
        import time as time_module

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
