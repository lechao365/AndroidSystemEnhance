from dataclasses import fields

from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import (
    AttemptState,
    LoopSession,
    SessionMetrics,
    SessionState,
    StageResult,
    TerminationDecision,
)


def test_stage_result_defaults():
    result = StageResult(stage_name="run", status="PASS")
    assert result.failure_code == FailureCode.NONE
    assert result.summary == ""
    assert result.artifacts == []
    assert result.next_action_hint == ""


def test_attempt_state_holds_stage_results():
    result = StageResult(stage_name="run", status="FAIL", failure_code=FailureCode.RUN_FAILED)
    attempt = AttemptState(attempt_index=2, stage_results=[result], attempt_decision="retry")
    assert attempt.attempt_index == 2
    assert attempt.stage_results[0].failure_code == FailureCode.RUN_FAILED
    assert attempt.attempt_decision == "retry"


def test_session_state_tracks_attempts():
    session = SessionState(
        session_id="sess-001",
        workflow_id="single_run_verify",
        target="system.boot",
        suite="test",
        max_attempts=5,
    )
    assert session.current_attempt == 0
    assert session.status == "PENDING"
    assert session.attempts == []


def test_termination_decision_flags_retry_and_escalation():
    decision = TerminationDecision(
        decision="STOP",
        reason_code=FailureCode.REGRESSION_DETECTED,
        reason_summary="new severe failure",
        can_retry=False,
        should_escalate=True,
    )
    assert decision.should_escalate is True
    assert decision.can_retry is False


def test_loop_session_wall_clock_limit_default_zero():
    """G5: LoopSession 新增 wall_clock_limit 字段，默认 0（不限制）。"""
    session = LoopSession(
        session_id="s1",
        workflow_id="runtime",
        target="lciod",
        suite="hal",
        max_attempts=5,
    )
    assert session.wall_clock_limit == 0


def test_session_metrics_fields():
    """SessionMetrics 必须含 11 个字段。"""
    names = {f.name for f in fields(SessionMetrics)}
    expected = {
        "success", "terminal_state", "attempt_count",
        "wall_clock_used_ms", "wall_clock_budget_ms",
        "analyzer_layer_hits", "analyzer_first_hit_layer",
        "failure_code_distribution", "human_gate_triggered",
        "human_gate_count", "kb_hit",
    }
    assert names == expected, f"SessionMetrics 字段不匹配: {names ^ expected}"


def test_session_metrics_defaults():
    """SessionMetrics 默认值。"""
    m = SessionMetrics()
    assert m.success is False
    assert m.terminal_state == "NONE"
    assert m.attempt_count == 0
    assert m.wall_clock_used_ms == 0
    assert m.wall_clock_budget_ms == 0
    assert m.analyzer_layer_hits == {}
    assert m.analyzer_first_hit_layer == ""
    assert m.failure_code_distribution == {}
    assert m.human_gate_triggered is False
    assert m.human_gate_count == 0
    assert m.kb_hit is False


def test_loop_session_metrics_defaults_none():
    """LoopSession.metrics 默认 None（未终态）。"""
    s = LoopSession(
        session_id="s1", workflow_id="w", target="t", suite="su", max_attempts=5,
    )
    assert s.metrics is None


def test_session_metrics_importable_from_package():
    """SessionMetrics 必须能从 loop_contracts 顶层导入。"""
    import loop_contracts
    assert hasattr(loop_contracts, "SessionMetrics")
    assert "SessionMetrics" in loop_contracts.__all__
