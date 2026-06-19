"""rules.py 单元测试。

覆盖 6 条 V1 规则：
1. no_output_after_attach
2. kernel_panic_detected
3. kernel_boot_hang
4. login_prompt_not_reached
5. shell_prompt_available
6. reboot_loop_detected

以及 classify（分类优先级）。
"""
from pathlib import Path

import pytest

from boot_failure_debug.boot_cycles import assign_boot_cycles
from boot_failure_debug.config import load_profiles
from boot_failure_debug.models import ObservedLine
from boot_failure_debug.observer import ObservationSnapshot
from boot_failure_debug.rules import (
    classify,
    evaluate_rules,
    RULE_PRIORITY,
)
from boot_failure_debug.transport import FixtureTransport
from boot_failure_debug.observer import capture_snapshot

REPO = Path(__file__).resolve().parents[6]
DEVICE_PROFILE = REPO / "engineering/loop/connection/profiles/devices/rp5/default.json"
WORKFLOW_PROFILE = REPO / "engineering/loop/profiles/boot-failure-debug/default.json"
FIXTURES = REPO / "engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures"


def _cfg():
    return load_profiles(str(DEVICE_PROFILE), str(WORKFLOW_PROFILE))


def _snapshot_from_fixture(name: str, timeout: float = 20) -> ObservationSnapshot:
    cfg = _cfg()
    transport = FixtureTransport.from_jsonl(str(FIXTURES / f"{name}.jsonl"))
    return capture_snapshot(transport, cfg, timeout_sec=timeout)


# ============================================================================
# 规则匹配
# ============================================================================

class TestNoOutputAfterAttach:
    def test_matches_no_output_fixture(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("no_output", timeout=15)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "no_output_after_attach")
        assert m.matched is True
        assert m.severity == "high"

    def test_does_not_match_normal_boot(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("normal_boot", timeout=15)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "no_output_after_attach")
        assert m.matched is False


class TestKernelPanicDetected:
    def test_matches_panic_fixture(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("kernel_panic", timeout=5)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "kernel_panic_detected")
        assert m.matched is True
        assert m.severity == "high"
        assert any("Kernel panic" in e for e in m.evidence)

    def test_does_not_match_normal_boot(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("normal_boot", timeout=15)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "kernel_panic_detected")
        assert m.matched is False


class TestKernelBootHang:
    def test_matches_hang_fixture(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("boot_hang", timeout=90)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "kernel_boot_hang")
        assert m.matched is True
        assert m.severity == "medium"

    def test_does_not_match_normal_boot(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("normal_boot", timeout=15)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "kernel_boot_hang")
        assert m.matched is False


class TestLoginPromptNotReached:
    def test_matches_panic_no_prompt(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("kernel_panic", timeout=5)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "login_prompt_not_reached")
        assert m.matched is True

    def test_does_not_match_normal_boot(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("normal_boot", timeout=15)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "login_prompt_not_reached")
        assert m.matched is False


class TestShellPromptAvailable:
    def test_matches_normal_boot(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("normal_boot", timeout=15)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "shell_prompt_available")
        assert m.matched is True
        assert m.severity == "low"

    def test_does_not_match_panic(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("kernel_panic", timeout=5)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "shell_prompt_available")
        assert m.matched is False


class TestRebootLoopDetected:
    def test_matches_reboot_loop_fixture(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("reboot_loop", timeout=15)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "reboot_loop_detected")
        assert m.matched is True
        assert m.severity == "high"

    def test_does_not_match_normal_boot(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("normal_boot", timeout=15)
        matches = evaluate_rules(snap, cfg)
        m = next(m for m in matches if m.rule_id == "reboot_loop_detected")
        assert m.matched is False


# ============================================================================
# 分类优先级
# ============================================================================

class TestClassify:
    def test_panic_takes_priority_over_prompt_absence(self):
        """kernel_panic 优先级高于 login_prompt_not_reached。"""
        cfg = _cfg()
        snap = _snapshot_from_fixture("kernel_panic", timeout=5)
        matches = evaluate_rules(snap, cfg)
        result = classify(matches)
        assert result == "kernel_panic_detected"

    def test_reboot_loop_takes_priority_over_hang(self):
        """reboot_loop 优先级高于 boot_hang。"""
        cfg = _cfg()
        snap = _snapshot_from_fixture("reboot_loop", timeout=15)
        matches = evaluate_rules(snap, cfg)
        result = classify(matches)
        assert result == "reboot_loop_detected"

    def test_shell_prompt_returns_success_classification(self):
        cfg = _cfg()
        snap = _snapshot_from_fixture("normal_boot", timeout=15)
        matches = evaluate_rules(snap, cfg)
        result = classify(matches)
        assert result == "shell_prompt_available"

    def test_no_match_returns_unknown(self):
        """无规则命中时返回 unknown。"""
        matches = [
            type("M", (), {
                "rule_id": "no_output_after_attach",
                "matched": False,
            })()
        ]
        result = classify(matches)
        assert result == "unknown"

    def test_priority_order_is_correct(self):
        """RULE_PRIORITY 列表顺序正确。"""
        assert RULE_PRIORITY[0] == "kernel_panic_detected"
        assert RULE_PRIORITY[1] == "reboot_loop_detected"
        assert "shell_prompt_available" in RULE_PRIORITY


# ============================================================================
# evaluate_rules 返回全部 6 条
# ============================================================================

def test_evaluate_rules_returns_exactly_six_matches():
    cfg = _cfg()
    snap = _snapshot_from_fixture("normal_boot", timeout=15)
    matches = evaluate_rules(snap, cfg)
    assert len(matches) == 6
    rule_ids = {m.rule_id for m in matches}
    expected = {
        "no_output_after_attach",
        "kernel_panic_detected",
        "kernel_boot_hang",
        "login_prompt_not_reached",
        "shell_prompt_available",
        "reboot_loop_detected",
    }
    assert rule_ids == expected
