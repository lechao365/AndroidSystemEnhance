"""transport 层合同测试。

FixtureTransport：基于 JSONL transcript 的离线回放 transport，供 AI 自验证。

Rp5SerialTransport 的测试已迁移到 provider 侧：
    engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py

FixtureTransport 必须实现接口：
    acquire_writer / release / send_line / capture_window / wait_for_pattern
"""
from pathlib import Path

import pytest

from loop_core.models import ObservedLine
from loop_core.transport import BaseTransport, FixtureTransport

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
