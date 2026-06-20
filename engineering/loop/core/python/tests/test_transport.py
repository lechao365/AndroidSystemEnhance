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

    def test_capture_since_recent_limit_keeps_tail(self):
        """capture_since 的 recent_limit 应保留末尾 N 行，与 live transport 一致。

        场景：fixture 有 6 行（无 prompt），boundary 从 0 开始，
        recent_limit=3 应返回最后 3 行（tail），而非前 3 行（head）。
        head 语义会丢弃命令末尾输出，live/fixture 行为必须一致。
        """
        rows = [{"t": float(i), "text": f"line{i}"} for i in range(6)]
        transport = FixtureTransport(rows)
        capture = transport.capture_since(
            transport.mark_output_boundary(),
            timeout_sec=999,
            recent_limit=3,
        )
        texts = [line.text for line in capture.lines]
        assert texts == ["line3", "line4", "line5"]

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


def test_base_transport_has_reboot_and_wait_abstract():
    """BaseTransport 声明 reboot_and_wait 抽象方法，子类必须实现。"""
    import inspect
    from loop_core.transport import BaseTransport

    assert hasattr(BaseTransport, "reboot_and_wait")
    sig = inspect.signature(BaseTransport.reboot_and_wait)
    param_names = list(sig.parameters.keys())
    assert "boot_markers" in param_names
    assert "panic_markers" in param_names


def test_fixture_transport_reboot_and_wait_pass_with_reboot_marker():
    """fixture 含 boot marker 时，reboot_and_wait 返回 pass。"""
    from loop_core.transport import FixtureTransport

    rows = [
        {"t": 0.0, "text": "reboot: Restarting system"},
        {"t": 1.0, "text": "Booting Linux on physical CPU 0x0"},
        {"t": 2.0, "text": "Linux version 6.6.116"},
        {"t": 18.0, "text": "init: ... started service 'zygote' has pid 636"},
        {"t": 19.0, "text": "1"},
    ]
    transport = FixtureTransport(rows)
    result = transport.reboot_and_wait(
        boot_markers=["Booting Linux on physical CPU", "init: ... started service 'zygote' has pid"],
        panic_markers=["Kernel panic"],
        prompt_markers=["console:/ $"],
    )
    assert result.status == "pass"
    assert result.stage_reached == "l3_verified"


def test_fixture_transport_reboot_and_wait_fail_no_reboot_marker():
    """fixture 不含任何 boot marker 时，返回 fail。"""
    from loop_core.transport import FixtureTransport

    rows = [
        {"t": 0.0, "text": "unrelated line"},
        {"t": 1.0, "text": "another line"},
    ]
    transport = FixtureTransport(rows)
    result = transport.reboot_and_wait(
        boot_markers=["Booting Linux on physical CPU", "init: ... started service 'zygote' has pid"],
        panic_markers=["Kernel panic"],
    )
    assert result.status == "fail"
    assert "fixture_no_reboot" in result.failure_reason or result.stage_reached == "none"


def test_fixture_transport_reboot_and_wait_detects_panic():
    """fixture 含 panic marker 时立即返回 fail。"""
    from loop_core.transport import FixtureTransport

    rows = [
        {"t": 0.0, "text": "reboot: Restarting system"},
        {"t": 1.0, "text": "Booting Linux on physical CPU"},
        {"t": 2.0, "text": "Kernel panic - not syncing"},
    ]
    transport = FixtureTransport(rows)
    result = transport.reboot_and_wait(
        boot_markers=["Booting Linux on physical CPU", "init: ... started service 'zygote' has pid"],
        panic_markers=["Kernel panic"],
    )
    assert result.status == "fail"
    assert "panic_detected" in result.failure_reason
