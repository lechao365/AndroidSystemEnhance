"""transport 层合同测试。

FixtureTransport：基于 JSONL transcript 的离线回放 transport，供 AI 自验证。
Rp5SerialTransport：包装 AutomationClient 的 live transport（通过 mock 验证合同）。

两者必须实现相同接口：
    acquire_writer / release / send_line / capture_window / wait_for_pattern
"""
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from boot_failure_debug.models import ObservedLine
from boot_failure_debug.transport import BaseTransport, FixtureTransport, Rp5SerialTransport

REPO = Path(__file__).resolve().parents[6]
FIXTURES = REPO / "engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures"
NORMAL_BOOT = str(FIXTURES / "normal_boot.jsonl")
KERNEL_PANIC = str(FIXTURES / "kernel_panic.jsonl")
NO_OUTPUT = str(FIXTURES / "no_output.jsonl")


# ============================================================================
# FixtureTransport
# ============================================================================

class TestFixtureTransport:
    def test_from_jsonl_loads_rows(self):
        transport = FixtureTransport.from_jsonl(NORMAL_BOOT)
        assert len(transport._rows) > 0

    def test_inherits_base_transport(self):
        transport = FixtureTransport.from_jsonl(NORMAL_BOOT)
        assert isinstance(transport, BaseTransport)

    def test_acquire_writer_always_succeeds(self):
        transport = FixtureTransport.from_jsonl(NORMAL_BOOT)
        assert transport.acquire_writer() is True

    def test_release_is_noop(self):
        transport = FixtureTransport.from_jsonl(NORMAL_BOOT)
        transport.release()  # 不应抛异常

    def test_send_line_records_input(self):
        transport = FixtureTransport.from_jsonl(NORMAL_BOOT)
        transport.acquire_writer()
        transport.send_line("dmesg")
        assert any("dmesg" in line.text for line in transport._sent_lines)

    def test_capture_window_returns_observed_lines_in_order(self):
        transport = FixtureTransport.from_jsonl(NORMAL_BOOT)
        lines = transport.capture_window(timeout_sec=15, recent_limit=100)
        assert len(lines) >= 5
        assert all(isinstance(line, ObservedLine) for line in lines)
        # 按 t 排序
        ts = [line.t for line in lines]
        assert ts == sorted(ts)

    def test_capture_window_respects_timeout(self):
        transport = FixtureTransport.from_jsonl(NORMAL_BOOT)
        # timeout=2 只拿到 t <= 2 的行
        lines = transport.capture_window(timeout_sec=2, recent_limit=100)
        assert all(line.t <= 2.0 for line in lines)

    def test_capture_window_empty_fixture(self):
        transport = FixtureTransport.from_jsonl(NO_OUTPUT)
        lines = transport.capture_window(timeout_sec=15, recent_limit=100)
        # no_output fixture 含占位行，但文本是 __NO_OUTPUT__
        assert len(lines) == 1
        assert "__NO_OUTPUT__" in lines[0].text

    def test_wait_for_pattern_matches_prompt(self):
        transport = FixtureTransport.from_jsonl(NORMAL_BOOT)
        matched = transport.wait_for_pattern(
            ["console:/ $"], timeout_sec=15, recent_limit=100
        )
        assert matched is not None
        assert matched.text == "console:/ $"

    def test_wait_for_pattern_returns_none_on_timeout(self):
        transport = FixtureTransport.from_jsonl(KERNEL_PANIC)
        # panic fixture 没有 prompt
        matched = transport.wait_for_pattern(
            ["console:/ $"], timeout_sec=5, recent_limit=100
        )
        assert matched is None

    def test_wait_for_pattern_multiple_patterns(self):
        transport = FixtureTransport.from_jsonl(NORMAL_BOOT)
        matched = transport.wait_for_pattern(
            ["localhost:/ #", "console:/ $"], timeout_sec=15, recent_limit=100
        )
        assert matched is not None
        assert "console:/ $" in matched.text


# ============================================================================
# Rp5SerialTransport（通过 mock AutomationClient 验证合同）
# ============================================================================

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

    def test_wait_for_pattern_found(self):
        client = self._make_mock_client()
        client.read_until_timeout.return_value = ["Linux version", "console:/ $"]
        transport = Rp5SerialTransport(client)
        matched = transport.wait_for_pattern(
            ["console:/ $"], timeout_sec=5, recent_limit=100
        )
        assert matched is not None
        assert matched.text == "console:/ $"

    def test_wait_for_pattern_not_found(self):
        client = self._make_mock_client()
        client.read_until_timeout.return_value = ["no prompt here"]
        transport = Rp5SerialTransport(client)
        matched = transport.wait_for_pattern(
            ["console:/ $"], timeout_sec=5, recent_limit=100
        )
        assert matched is None
