from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import SessionState, StageResult
from loop_controller.engine import apply_stage_result


def test_apply_stage_result_appends_attempt_and_updates_status():
    session = SessionState(
        session_id="sess-001",
        workflow_id="single_run_verify",
        target="system.boot",
        max_attempts=5,
    )

    updated = apply_stage_result(
        session,
        attempt_index=1,
        stage_result=StageResult(stage_name="verify", status="FAIL", failure_code=FailureCode.RUN_FAILED),
        decision="RETRY",
    )

    assert updated.current_attempt == 1
    assert updated.status == "RETRY"
    assert updated.attempts[-1].stage_results[-1].failure_code == FailureCode.RUN_FAILED
