from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import (
    CheckpointRecord,
    LoopSession,
    RuntimeState,
    RuntimeTerminalState,
)


def test_runtime_state_defaults():
    state = RuntimeState(current_node="INIT_SESSION")
    assert state.previous_node == ""
    assert state.node_status == "PENDING"
    assert state.pending_human_gate is False
    assert state.terminal_state == RuntimeTerminalState.NONE


def test_loop_session_tracks_attempts_and_failure_code():
    session = LoopSession(
        session_id="sess-001",
        workflow_id="runtime",
        target="lciod",
        suite="engineering/loop/cases/features/lciod/hal.yaml",
        max_attempts=5,
    )
    assert session.current_attempt == 0
    assert session.latest_failure_code == FailureCode.NONE
    assert session.attempts == []


def test_checkpoint_record_serializable():
    cp = CheckpointRecord(
        checkpoint_id="cp-001",
        session_id="sess-001",
        attempt_index=1,
        current_node="RUN_VERIFY",
        input_summary={"suite": "hal.yaml"},
        output_summary={"verify_result": "FAIL"},
        failure_code=FailureCode.RUN_FAILED,
        matched_guards=["attempts_below_limit"],
        next_node="BUILD_ANALYSIS_REQUEST",
        timestamp="2026-06-26T12:00:00+08:00",
    )
    data = cp.to_dict()
    assert data["failure_code"] == "RUN_FAILED"
    assert data["next_node"] == "BUILD_ANALYSIS_REQUEST"
