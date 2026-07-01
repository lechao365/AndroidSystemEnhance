from dataclasses import fields

from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import (
    AttemptState,
    CheckpointRecord,
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
    """SessionMetrics 必须含 14 个字段（G9 11 + G2 3）。"""
    names = {f.name for f in fields(SessionMetrics)}
    expected = {
        "success", "terminal_state", "attempt_count",
        "wall_clock_used_ms", "wall_clock_budget_ms",
        "analyzer_layer_hits", "analyzer_first_hit_layer",
        "failure_code_distribution", "human_gate_triggered",
        "human_gate_count", "kb_hit",
        "candidates_per_attempt_avg", "candidate_compile_pass_rate",
        "candidate_selected_layer_dist",
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


def test_loop_session_has_candidates_per_attempt() -> None:
    """G2: LoopSession 必须有 candidates_per_attempt 字段，默认 1。"""
    s = LoopSession(
        session_id="s1", workflow_id="w", target="t", suite="s", max_attempts=5,
    )
    assert s.candidates_per_attempt == 1
    s2 = LoopSession(
        session_id="s2", workflow_id="w", target="t", suite="s", max_attempts=5,
        candidates_per_attempt=3,
    )
    assert s2.candidates_per_attempt == 3


def test_checkpoint_record_has_candidate_id() -> None:
    """G2: CheckpointRecord 必须有 candidate_id 字段，默认空串。"""
    cp = CheckpointRecord(
        checkpoint_id="cp-1", session_id="s1", attempt_index=0,
        current_node="APPLY_PATCH", input_summary={}, output_summary={},
        failure_code=FailureCode.NONE, matched_guards=[],
        next_node="COMPILE_PATCH", timestamp="2026-07-01T00:00:00+08:00",
    )
    assert cp.candidate_id == ""
    cp2 = CheckpointRecord(
        checkpoint_id="cp-2", session_id="s1", attempt_index=0,
        current_node="SELECT_BEST_CANDIDATE", input_summary={}, output_summary={},
        failure_code=FailureCode.NONE, matched_guards=[],
        next_node="APPLY_PATCH", timestamp="2026-07-01T00:00:00+08:00",
        candidate_id="c1",
    )
    assert cp2.candidate_id == "c1"


def test_session_metrics_has_g2_fields() -> None:
    """G2: SessionMetrics 必须有 3 个 G2 指标字段。"""
    m = SessionMetrics()
    assert m.candidates_per_attempt_avg == 0.0
    assert m.candidate_compile_pass_rate == 0.0
    assert m.candidate_selected_layer_dist == {}

    m2 = SessionMetrics(
        candidates_per_attempt_avg=2.5,
        candidate_compile_pass_rate=0.67,
        candidate_selected_layer_dist={"KnowledgeBaseAnalyzer": 2, "OpencodeAnalyzer": 1},
    )
    assert m2.candidates_per_attempt_avg == 2.5
    assert m2.candidate_compile_pass_rate == 0.67
    assert m2.candidate_selected_layer_dist["KnowledgeBaseAnalyzer"] == 2
