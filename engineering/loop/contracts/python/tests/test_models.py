from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import AttemptState, SessionState, StageResult, TerminationDecision


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
