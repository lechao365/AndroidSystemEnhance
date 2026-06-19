"""loop_core/models.py 单元测试。"""
from loop_core.models import ActionRecord, LoopAttempt, ObservedLine, RuleMatch


def test_observed_line_defaults_cycle_id_zero():
    line = ObservedLine(t=1.0, text="hello")
    assert line.cycle_id == 0


def test_observed_line_uses_cycle_id_not_boot_cycle_id():
    """确认字段名是 cycle_id，不是 boot_cycle_id。"""
    line = ObservedLine(t=1.0, text="hello", cycle_id=3)
    assert line.cycle_id == 3
    assert not hasattr(line, "boot_cycle_id")


def test_observed_line_to_dict():
    line = ObservedLine(t=1.0, text="hello", cycle_id=2)
    d = line.to_dict()
    assert d == {"t": 1.0, "text": "hello", "cycle_id": 2}


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
    record = ActionRecord(
        action_id="a-1",
        level="L1",
        command="dmesg",
        reason="prompt available",
        result="OK",
        output_lines=["[ 1.0 ] init"],
        metadata={"captured_line_count": 1, "pattern_matched": True},
    )
    d = record.to_dict()
    assert d["output_lines"] == ["[ 1.0 ] init"]
    assert d["metadata"]["captured_line_count"] == 1


def test_action_record_defaults_empty_output_lines_and_metadata():
    record = ActionRecord(
        action_id="a-1",
        level="L1",
        command="dmesg",
        reason="prompt available",
        result="OK",
    )
    assert record.output_lines == []
    assert record.metadata == {}


def test_loop_attempt_has_extra_summary_lines():
    attempt = LoopAttempt(
        attempt_id="att-1",
        device_id="rp5",
        outcome="EXIT_FAILURE",
        final_classification="no_output_after_attach",
        boot_cycle_count=0,
    )
    assert attempt.extra_summary_lines == []


def test_loop_attempt_extra_summary_lines_serialized():
    attempt = LoopAttempt(
        attempt_id="att-1",
        device_id="rp5",
        outcome="EXIT_SUCCESS",
        final_classification="shell_prompt_available",
        boot_cycle_count=1,
        extra_summary_lines=["boot_cycle: 1", "device_model: rpi5"],
    )
    d = attempt.to_dict()
    assert d["extra_summary_lines"] == ["boot_cycle: 1", "device_model: rpi5"]


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
    assert attempt.extra_summary_lines == []
