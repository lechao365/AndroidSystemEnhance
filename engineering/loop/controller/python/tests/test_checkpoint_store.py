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


def test_checkpoint_store_filters_by_session_id(tmp_path: Path):
    """latest()/all() must only return checkpoints for the constructed session."""
    from loop_contracts.models import CheckpointRecord

    # Write checkpoints for two different sessions into the same artifacts dir
    store_a = CheckpointStore(str(tmp_path), "sess-A")
    store_a.save(CheckpointRecord(
        checkpoint_id="a-1", session_id="sess-A", attempt_index=1,
        current_node="RUN_VERIFY", input_summary={}, output_summary={},
        failure_code=FailureCode.NONE, matched_guards=[],
        next_node="DECIDE_NEXT", timestamp="2026-06-26T10:00:00+08:00",
    ))
    store_b = CheckpointStore(str(tmp_path), "sess-B")
    store_b.save(CheckpointRecord(
        checkpoint_id="b-1", session_id="sess-B", attempt_index=1,
        current_node="INIT_SESSION", input_summary={}, output_summary={},
        failure_code=FailureCode.NONE, matched_guards=[],
        next_node="RUN_VERIFY", timestamp="2026-06-26T11:00:00+08:00",
    ))

    # store_a should only see sess-A checkpoint despite sess-B being newer
    latest_a = store_a.latest()
    assert latest_a is not None
    assert latest_a.session_id == "sess-A"
    assert latest_a.checkpoint_id == "a-1"

    # store_b should only see sess-B checkpoint
    latest_b = store_b.latest()
    assert latest_b is not None
    assert latest_b.session_id == "sess-B"

    # all() also filters
    assert len(store_a.all()) == 1
    assert len(store_b.all()) == 1
