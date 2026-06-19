"""config.py 与 models.py 的单元测试。

覆盖：
- profile 合并优先级（provider 默认 < device < workflow < override）
- WorkflowConfig 字段可访问性
- 数据模型 to_dict 序列化
- ObservedLine / RuleMatch / ActionRecord / LoopAttempt 边界
"""
from pathlib import Path

import pytest

from boot_failure_debug.config import BootFailureConfig, load_profiles
from loop_core.models import (
    ActionRecord,
    LoopAttempt,
    ObservedLine,
    RuleMatch,
)

REPO = Path(__file__).resolve().parents[6]
DEVICE_PROFILE = REPO / "engineering/loop/connection/profiles/devices/rp5/default.json"
WORKFLOW_PROFILE = REPO / "engineering/loop/profiles/boot-failure-debug/default.json"


# ----------------------------------------------------------------------------
# config
# ----------------------------------------------------------------------------

def test_load_profiles_returns_workflow_config():
    cfg = load_profiles(str(DEVICE_PROFILE), str(WORKFLOW_PROFILE))
    assert isinstance(cfg, BootFailureConfig)


def test_load_profiles_merges_device_fields():
    cfg = load_profiles(str(DEVICE_PROFILE), str(WORKFLOW_PROFILE))
    assert cfg.device_id == "rp5"
    assert "console:/ $" in cfg.prompt_markers
    assert "Kernel panic" in cfg.panic_markers


def test_load_profiles_merges_workflow_fields():
    cfg = load_profiles(str(DEVICE_PROFILE), str(WORKFLOW_PROFILE))
    assert cfg.observe_timeout_sec == 90
    assert cfg.reboot_loop_threshold == 2
    assert "dmesg" in cfg.l1_commands


def test_load_profiles_override_takes_priority():
    cfg = load_profiles(
        str(DEVICE_PROFILE),
        str(WORKFLOW_PROFILE),
        override={"observe_timeout_sec": 30, "reboot_loop_threshold": 3},
    )
    assert cfg.observe_timeout_sec == 30
    assert cfg.reboot_loop_threshold == 3


def test_load_profiles_override_does_not_touch_device_fields():
    cfg = load_profiles(
        str(DEVICE_PROFILE),
        str(WORKFLOW_PROFILE),
        override={"observe_timeout_sec": 30},
    )
    # device 字段不受 override 影响
    assert cfg.device_id == "rp5"
    assert "console:/ $" in cfg.prompt_markers


# ----------------------------------------------------------------------------
# models
# ----------------------------------------------------------------------------

def test_observed_line_defaults_boot_cycle_zero():
    line = ObservedLine(t=1.0, text="hello")
    assert line.cycle_id == 0


def test_rule_match_to_dict_roundtrip():
    rm = RuleMatch(
        rule_id="kernel_panic_detected",
        matched=True,
        confidence=0.95,
        severity="high",
        evidence=["Kernel panic - not syncing"],
        phase="CLASSIFY_FAILURE",
        suggested_actions=["capture_recent_context"],
    )
    d = rm.to_dict()
    assert d["rule_id"] == "kernel_panic_detected"
    assert d["matched"] is True
    assert d["confidence"] == 0.95
    assert d["evidence"] == ["Kernel panic - not syncing"]


def test_action_record_serializes_output_lines_and_metadata():
    ar = ActionRecord(
        action_id="a-serialized",
        level="L1",
        command="dmesg",
        reason="prompt available",
        result="OK",
        output_lines=["[ 1.0 ] init"],
        metadata={"captured_line_count": 1, "pattern_matched": True},
    )
    d = ar.to_dict()
    assert d["output_lines"] == ["[ 1.0 ] init"]
    assert d["metadata"]["captured_line_count"] == 1
    assert d["metadata"]["pattern_matched"] is True


def test_action_record_to_dict():
    ar = ActionRecord(
        action_id="a-1",
        level="L1",
        command="dmesg",
        reason="prompt available",
        result="OK",
        evidence_ref="artifacts/a-1/dmesg.txt",
    )
    d = ar.to_dict()
    assert d["command"] == "dmesg"
    assert d["level"] == "L1"
    assert d["evidence_ref"] == "artifacts/a-1/dmesg.txt"


def test_action_record_evidence_ref_optional():
    ar = ActionRecord(
        action_id="a-2",
        level="L2",
        command="send_enter",
        reason="prompt not visible",
        result="OK",
    )
    assert ar.evidence_ref is None


def test_loop_attempt_to_dict_contains_core_fields():
    rm = RuleMatch(
        rule_id="shell_prompt_available",
        matched=True,
        confidence=0.9,
        severity="low",
        evidence=["console:/ $"],
        phase="CLASSIFY_FAILURE",
        suggested_actions=[],
    )
    ar = ActionRecord(
        action_id="a-1",
        level="L1",
        command="dmesg",
        reason="prompt available",
        result="OK",
    )
    attempt = LoopAttempt(
        attempt_id="att-1",
        device_id="rp5",
        outcome="EXIT_SUCCESS",
        final_classification="shell_prompt_available",
        boot_cycle_count=1,
        matched_rules=[rm],
        actions=[ar],
        artifacts_dir="artifacts/att-1",
    )
    d = attempt.to_dict()
    assert d["attempt_id"] == "att-1"
    assert d["outcome"] == "EXIT_SUCCESS"
    assert d["final_classification"] == "shell_prompt_available"
    assert d["boot_cycle_count"] == 1
    assert d["artifacts_dir"] == "artifacts/att-1"
    assert len(d["matched_rules"]) == 1
    assert d["matched_rules"][0]["rule_id"] == "shell_prompt_available"
    assert len(d["actions"]) == 1


def test_loop_attempt_empty_lists_default():
    attempt = LoopAttempt(
        attempt_id="att-2",
        device_id="rp5",
        outcome="EXIT_FAILURE",
        final_classification="no_output_after_attach",
        boot_cycle_count=0,
    )
    assert attempt.matched_rules == []
    assert attempt.actions == []
    assert attempt.artifacts_dir == ""
