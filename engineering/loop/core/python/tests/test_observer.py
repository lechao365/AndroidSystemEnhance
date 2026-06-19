"""loop_core/observer.py 单元测试。"""
from unittest.mock import MagicMock

from loop_core.models import ObservedLine
from loop_core.observer import ObservationSnapshot, capture_snapshot, detect_prompt


def _make_transport(lines: list[ObservedLine]):
    transport = MagicMock()
    transport.capture_window.return_value = lines
    return transport


class TestCaptureSnapshot:
    def test_returns_observation_snapshot(self):
        transport = _make_transport([ObservedLine(1.0, "hello")])
        snap = capture_snapshot(
            transport, timeout_sec=10, prompt_markers=[], recent_limit=100
        )
        assert isinstance(snap, ObservationSnapshot)

    def test_lines_from_transport(self):
        lines = [ObservedLine(1.0, "hello"), ObservedLine(2.0, "world")]
        transport = _make_transport(lines)
        snap = capture_snapshot(
            transport, timeout_sec=10, prompt_markers=[], recent_limit=100
        )
        assert len(snap.lines) == 2

    def test_prompt_detection(self):
        lines = [ObservedLine(1.0, "init"), ObservedLine(2.0, "console:/ $")]
        transport = _make_transport(lines)
        snap = capture_snapshot(
            transport,
            timeout_sec=10,
            prompt_markers=["console:/ $"],
            recent_limit=100,
        )
        assert snap.prompt_line is not None
        assert snap.prompt_line.text == "console:/ $"

    def test_no_prompt(self):
        lines = [ObservedLine(1.0, "init")]
        transport = _make_transport(lines)
        snap = capture_snapshot(
            transport,
            timeout_sec=10,
            prompt_markers=["console:/ $"],
            recent_limit=100,
        )
        assert snap.prompt_line is None

    def test_quiet_for_sec_with_lines(self):
        lines = [ObservedLine(3.0, "last output")]
        transport = _make_transport(lines)
        snap = capture_snapshot(
            transport, timeout_sec=10, prompt_markers=[], recent_limit=100
        )
        assert snap.quiet_for_sec == 7.0

    def test_quiet_for_sec_empty_lines(self):
        transport = _make_transport([])
        snap = capture_snapshot(
            transport, timeout_sec=10, prompt_markers=[], recent_limit=100
        )
        assert snap.quiet_for_sec == 10.0

    def test_cycle_markers_triggers_assign(self):
        from loop_core.cycles import assign_cycles

        lines = [
            ObservedLine(1.0, "boot1"),
            ObservedLine(2.0, "reboot"),
            ObservedLine(3.0, "boot2"),
        ]
        transport = _make_transport(lines)
        snap = capture_snapshot(
            transport,
            timeout_sec=10,
            prompt_markers=[],
            recent_limit=100,
            cycle_markers=["reboot"],
        )
        assert snap.lines[0].cycle_id == 1
        assert snap.lines[2].cycle_id == 2

    def test_no_cycle_markers_keeps_default_zero(self):
        lines = [ObservedLine(1.0, "boot1")]
        transport = _make_transport(lines)
        snap = capture_snapshot(
            transport,
            timeout_sec=10,
            prompt_markers=[],
            recent_limit=100,
        )
        assert snap.lines[0].cycle_id == 0


class TestDetectPrompt:
    def test_found(self):
        assert detect_prompt(["init", "console:/ $"], ["console:/ $"]) == "console:/ $"

    def test_not_found(self):
        assert detect_prompt(["init"], ["console:/ $"]) is None

    def test_multiple_markers(self):
        result = detect_prompt(["root@device"], ["console:/ $", "root@"])
        assert result == "root@device"

    def test_empty_texts(self):
        assert detect_prompt([], ["marker"]) is None
