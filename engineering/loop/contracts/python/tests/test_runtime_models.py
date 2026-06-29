from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import (
    CheckpointRecord,
    LoopSession,
    RuntimeState,
    RuntimeTerminalState,
    SessionState,
)


def test_runtime_state_defaults():
    state = RuntimeState(current_node="INIT_SESSION")
    assert state.previous_node == ""
    assert state.node_status == "PENDING"
    assert state.transition_reason == ""
    assert state.pending_human_gate is False
    assert state.interrupted is False
    assert state.resume_token == ""
    assert state.last_checkpoint_at == ""
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


def test_session_state_alias_is_loop_session():
    assert SessionState is LoopSession


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


def test_checkpoint_record_round_trip():
    cp = CheckpointRecord(
        checkpoint_id="cp-002",
        session_id="sess-001",
        attempt_index=2,
        current_node="DECIDE_NEXT",
        input_summary={"suite": "hal.yaml"},
        output_summary={"decision": "RETRY"},
        failure_code=FailureCode.COMPILE_FAILED,
        matched_guards=["attempts_below_limit"],
        next_node="REVERT_PATCH",
        timestamp="2026-06-26T12:05:00+08:00",
    )
    import json
    blob = json.dumps(cp.to_dict(), ensure_ascii=False)
    restored = json.loads(blob)
    assert restored["checkpoint_id"] == "cp-002"
    assert restored["failure_code"] == "COMPILE_FAILED"
    assert restored["current_node"] == "DECIDE_NEXT"
    assert restored["next_node"] == "REVERT_PATCH"
    assert restored["matched_guards"] == ["attempts_below_limit"]
    assert restored["input_summary"] == {"suite": "hal.yaml"}
    assert restored["output_summary"] == {"decision": "RETRY"}


def test_checkpoint_record_duration_ms_default_zero():
    """G5: CheckpointRecord 新增 duration_ms 字段，默认 0。"""
    cp = CheckpointRecord(
        checkpoint_id="cp-test",
        session_id="s1",
        attempt_index=0,
        current_node="INIT_SESSION",
        input_summary={},
        output_summary={},
        failure_code=FailureCode.NONE,
        matched_guards=[],
        next_node="RUN_VERIFY",
        timestamp="2026-01-01T00:00:00+08:00",
    )
    assert cp.duration_ms == 0


def test_checkpoint_record_to_dict_includes_duration_ms():
    """G5: to_dict() 输出含 duration_ms。"""
    cp = CheckpointRecord(
        checkpoint_id="cp-test",
        session_id="s1",
        attempt_index=0,
        current_node="INIT_SESSION",
        input_summary={},
        output_summary={},
        failure_code=FailureCode.NONE,
        matched_guards=[],
        next_node="RUN_VERIFY",
        timestamp="2026-01-01T00:00:00+08:00",
        duration_ms=500,
    )
    d = cp.to_dict()
    assert d["duration_ms"] == 500
