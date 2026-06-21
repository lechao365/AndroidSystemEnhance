from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import StageResult
from loop_controller.policy import decide_termination


def test_pass_result_stops_session():
    decision = decide_termination(
        max_attempts=5,
        current_attempt=1,
        latest_stage=StageResult(stage_name="verify", status="PASS"),
        previous_failure_codes=[],
    )
    assert decision.decision == "STOP"
    assert decision.reason_code == FailureCode.NONE


def test_exceed_max_attempts_stops_session():
    decision = decide_termination(
        max_attempts=2,
        current_attempt=3,
        latest_stage=StageResult(stage_name="verify", status="FAIL", failure_code=FailureCode.RUN_FAILED),
        previous_failure_codes=[FailureCode.RUN_FAILED],
    )
    assert decision.decision == "STOP"
    assert decision.reason_code == FailureCode.REPEATED_FAILURE


def test_same_failure_twice_stops_session():
    decision = decide_termination(
        max_attempts=5,
        current_attempt=2,
        latest_stage=StageResult(stage_name="verify", status="FAIL", failure_code=FailureCode.RUN_FAILED),
        previous_failure_codes=[FailureCode.RUN_FAILED],
    )
    assert decision.decision == "STOP"
    assert decision.reason_code == FailureCode.REPEATED_FAILURE


def test_first_failure_allows_retry():
    decision = decide_termination(
        max_attempts=5,
        current_attempt=1,
        latest_stage=StageResult(stage_name="verify", status="FAIL", failure_code=FailureCode.RUN_FAILED),
        previous_failure_codes=[],
    )
    assert decision.decision == "RETRY"
    assert decision.can_retry is True
