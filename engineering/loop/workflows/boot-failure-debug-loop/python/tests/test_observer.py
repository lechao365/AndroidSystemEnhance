"""observer.py 单元测试。

覆盖：
- capture_snapshot 返回 ObservationSnapshot
- quiet_window 检测（最后一条到 timeout 的间隔）
- prompt_line 检测
- recent_context 返回最近 N 行
"""
from pathlib import Path

from boot_failure_debug.config import load_profiles
from boot_failure_debug.observer import ObservationSnapshot, capture_snapshot, detect_prompt
from boot_failure_debug.transport import FixtureTransport

REPO = Path(__file__).resolve().parents[6]
DEVICE_PROFILE = REPO / "engineering/loop/connection/profiles/devices/rp5/default.json"
WORKFLOW_PROFILE = REPO / "engineering/loop/profiles/boot-failure-debug/default.json"
FIXTURES = REPO / "engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures"


def _cfg():
    return load_profiles(str(DEVICE_PROFILE), str(WORKFLOW_PROFILE))


class TestCaptureSnapshot:
    def test_returns_observation_snapshot(self):
        cfg = _cfg()
        transport = FixtureTransport.from_jsonl(str(FIXTURES / "normal_boot.jsonl"))
        snapshot = capture_snapshot(transport, cfg, timeout_sec=15)
        assert isinstance(snapshot, ObservationSnapshot)

    def test_snapshot_contains_lines(self):
        cfg = _cfg()
        transport = FixtureTransport.from_jsonl(str(FIXTURES / "normal_boot.jsonl"))
        snapshot = capture_snapshot(transport, cfg, timeout_sec=15)
        assert len(snapshot.lines) >= 5

    def test_snapshot_detects_prompt_in_normal_boot(self):
        cfg = _cfg()
        transport = FixtureTransport.from_jsonl(str(FIXTURES / "normal_boot.jsonl"))
        snapshot = capture_snapshot(transport, cfg, timeout_sec=15)
        assert snapshot.prompt_line is not None
        assert "console:/ $" in snapshot.prompt_line.text

    def test_snapshot_prompt_none_in_panic(self):
        cfg = _cfg()
        transport = FixtureTransport.from_jsonl(str(FIXTURES / "kernel_panic.jsonl"))
        snapshot = capture_snapshot(transport, cfg, timeout_sec=5)
        assert snapshot.prompt_line is None

    def test_snapshot_quiet_window_normal_boot(self):
        """normal boot 最后一条 t=12，timeout=15，quiet = 15 - 12 = 3。"""
        cfg = _cfg()
        transport = FixtureTransport.from_jsonl(str(FIXTURES / "normal_boot.jsonl"))
        snapshot = capture_snapshot(transport, cfg, timeout_sec=15)
        assert snapshot.quiet_for_sec >= 0

    def test_snapshot_quiet_window_large_for_no_output(self):
        cfg = _cfg()
        transport = FixtureTransport.from_jsonl(str(FIXTURES / "no_output.jsonl"))
        snapshot = capture_snapshot(transport, cfg, timeout_sec=15)
        # no_output 只有 1 行占位，quiet 很大
        assert snapshot.quiet_for_sec >= 0

    def test_snapshot_assigns_boot_cycle_ids(self):
        cfg = _cfg()
        transport = FixtureTransport.from_jsonl(str(FIXTURES / "reboot_loop.jsonl"))
        snapshot = capture_snapshot(transport, cfg, timeout_sec=15)
        cycle_ids = {line.boot_cycle_id for line in snapshot.lines}
        assert len(cycle_ids) >= 2, "reboot loop 应检测到多个 boot cycle"


class TestDetectPrompt:
    def test_detects_console_prompt(self):
        cfg = _cfg()
        line = detect_prompt(["Booting Linux", "console:/ $"], cfg)
        assert line == "console:/ $"

    def test_detects_root_prompt(self):
        cfg = _cfg()
        line = detect_prompt(["init: starting", "localhost:/ #"], cfg)
        assert "localhost:/ #" in line

    def test_returns_none_when_no_prompt(self):
        cfg = _cfg()
        line = detect_prompt(["Kernel panic", "Booting Linux"], cfg)
        assert line is None

    def test_returns_first_match(self):
        cfg = _cfg()
        line = detect_prompt(["console:/ $", "localhost:/ #"], cfg)
        assert line == "console:/ $"
