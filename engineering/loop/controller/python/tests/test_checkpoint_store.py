from pathlib import Path

from loop_controller.runtime.checkpoint_store import CheckpointStore
from loop_contracts.models import CheckpointRecord
from loop_contracts.failure_codes import FailureCode


def _make_cp(cp_id="cp-001", node="RUN_VERIFY", next_node="DECIDE_NEXT", attempt=1,
             failure_code=FailureCode.RUN_FAILED):
    return CheckpointRecord(
        checkpoint_id=cp_id, session_id="sess-001", attempt_index=attempt,
        current_node=node,
        input_summary={"suite": "t.yaml"},
        output_summary={"verify_result": "FAIL"},
        failure_code=failure_code,
        matched_guards=["attempts_below_limit"],
        next_node=next_node,
        timestamp="2026-06-26T12:00:00+08:00",
    )


def test_checkpoint_store_save_and_load_latest(tmp_path: Path):
    store = CheckpointStore(str(tmp_path), "sess-001")
    store.save(_make_cp("cp-001"))
    store.save(_make_cp("cp-002", node="DECIDE_NEXT", next_node="BUILD_ANALYSIS_REQUEST", attempt=2))
    loaded = store.latest()
    assert loaded is not None
    assert loaded.checkpoint_id == "cp-002"
    assert loaded.current_node == "DECIDE_NEXT"


def test_checkpoint_store_returns_none_when_empty(tmp_path: Path):
    store = CheckpointStore(str(tmp_path), "sess-none")
    assert store.latest() is None


def test_checkpoint_store_all_returns_in_order(tmp_path: Path):
    store = CheckpointStore(str(tmp_path), "sess-001")
    store.save(_make_cp("cp-001", attempt=1))
    store.save(_make_cp("cp-002", attempt=2))
    all_cps = store.all()
    assert len(all_cps) == 2
    assert all_cps[0].attempt_index == 1
    assert all_cps[1].attempt_index == 2


def test_checkpoint_store_round_trip_failure_code(tmp_path: Path):
    store = CheckpointStore(str(tmp_path), "sess-001")
    store.save(_make_cp(failure_code=FailureCode.COMPILE_FAILED))
    loaded = store.latest()
    assert loaded is not None
    assert loaded.failure_code == FailureCode.COMPILE_FAILED
