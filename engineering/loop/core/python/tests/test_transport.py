"""loop_core/transport.py 单元测试。"""
import json
from pathlib import Path

import pytest

from loop_core.models import ObservedLine
from loop_core.transport import BaseTransport, FixtureTransport


# ============================================================================
# FixtureTransport
# ============================================================================

class TestFixtureTransport:
    def _make_rows(self):
        return [
            {"t": 1.0, "text": "Booting Linux"},
            {"t": 2.0, "text": "init: starting"},
            {"t": 3.0, "text": "console:/ $"},
        ]

    def test_inherits_base_transport(self):
        transport = FixtureTransport(self._make_rows())
        assert isinstance(transport, BaseTransport)

    def test_acquire_writer_always_succeeds(self):
        transport = FixtureTransport(self._make_rows())
        assert transport.acquire_writer() is True

    def test_release_is_noop(self):
        transport = FixtureTransport(self._make_rows())
        transport.release()  # 不应抛异常

    def test_send_line_requires_writer(self):
        transport = FixtureTransport(self._make_rows())
        with pytest.raises(RuntimeError):
            transport.send_line("dmesg")

    def test_send_line_records_input(self):
        transport = FixtureTransport(self._make_rows())
        transport.acquire_writer()
        transport.send_line("dmesg")
        assert any("dmesg" in line.text for line in transport._sent_lines)

    def test_capture_window_returns_observed_lines_in_order(self):
        transport = FixtureTransport(self._make_rows())
        lines = transport.capture_window(timeout_sec=15, recent_limit=100)
        assert len(lines) == 3
        assert all(isinstance(line, ObservedLine) for line in lines)
        ts = [line.t for line in lines]
        assert ts == sorted(ts)

    def test_capture_window_respects_timeout(self):
        transport = FixtureTransport(self._make_rows())
        lines = transport.capture_window(timeout_sec=2, recent_limit=100)
        assert all(line.t <= 2.0 for line in lines)

    def test_capture_window_respects_recent_limit(self):
        rows = [{"t": float(i), "text": f"line{i}"} for i in range(10)]
        transport = FixtureTransport(rows)
        lines = transport.capture_window(timeout_sec=20, recent_limit=3)
        assert len(lines) == 3
        assert lines[-1].text == "line9"

    def test_wait_for_pattern_matches_prompt(self):
        transport = FixtureTransport(self._make_rows())
        matched = transport.wait_for_pattern(
            ["console:/ $"], timeout_sec=15, recent_limit=100
        )
        assert matched is not None
        assert matched.text == "console:/ $"

    def test_wait_for_pattern_returns_none_on_timeout(self):
        transport = FixtureTransport(self._make_rows())
        matched = transport.wait_for_pattern(
            ["not_found"], timeout_sec=1, recent_limit=100
        )
        assert matched is None

    def test_wait_for_pattern_multiple_patterns(self):
        transport = FixtureTransport(self._make_rows())
        matched = transport.wait_for_pattern(
            ["localhost:/ #", "console:/ $"], timeout_sec=15, recent_limit=100
        )
        assert matched is not None
        assert "console:/ $" in matched.text

    def test_from_jsonl_loads_rows(self, tmp_path):
        fixture = tmp_path / "test.jsonl"
        fixture.write_text(
            "\n".join(json.dumps(r) for r in self._make_rows())
        )
        transport = FixtureTransport.from_jsonl(str(fixture))
        assert len(transport._rows) == 3


# ============================================================================
# BaseTransport 抽象性
# ============================================================================

class TestBaseTransport:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseTransport()
