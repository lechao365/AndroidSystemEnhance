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


def test_checkpoint_store_all_performance(tmp_path: Path):
    """all() works correctly for many checkpoints"""
    store = CheckpointStore(str(tmp_path), "sess-001")
    for i in range(100):
        store.save(_make_cp(f"cp-{i:03d}", attempt=i + 1))
    results = store.all()
    assert len(results) == 100


def test_checkpoint_store_all_single_parse_per_line(tmp_path: Path):
    """_from_line is called exactly once per line during all()"""
    store = CheckpointStore(str(tmp_path), "sess-001")
    store.save(_make_cp("cp-001"))
    store.save(_make_cp("cp-002"))
    call_count = [0]
    orig = store._from_line

    def counting_from_line(line):
        call_count[0] += 1
        return orig(line)

    store._from_line = counting_from_line
    results = store.all()
    assert len(results) == 2
    assert call_count[0] == 2


# ---------------------------------------------------------------------------
# P2-1：JSONL 坏行容错
# ---------------------------------------------------------------------------


def test_latest_skips_corrupt_lines(tmp_path: Path):
    """坏行（非法 JSON）不阻断 latest()，跳过并返回最近的有效 checkpoint。

    回归 P2-1：原 _from_line 对坏行抛 JSONDecodeError，单条坏行使
    latest()/all() 全部不可用。
    """
    store = CheckpointStore(str(tmp_path), "sess-001")
    store.save(_make_cp("cp-001", node="RUN_VERIFY", attempt=1))
    # 注入坏行
    cp_file = tmp_path / "runtime_checkpoints.jsonl"
    with cp_file.open("a", encoding="utf-8") as f:
        f.write("this is not valid json\n")
    store.save(_make_cp("cp-002", node="DECIDE_NEXT", attempt=2))
    latest = store.latest()
    assert latest is not None
    assert latest.checkpoint_id == "cp-002"


def test_all_skips_corrupt_lines(tmp_path: Path):
    """all() 跳过坏行，返回所有有效 checkpoint。"""
    store = CheckpointStore(str(tmp_path), "sess-001")
    store.save(_make_cp("cp-001", attempt=1))
    cp_file = tmp_path / "runtime_checkpoints.jsonl"
    with cp_file.open("a", encoding="utf-8") as f:
        f.write("{broken json\n")
        f.write("another bad line\n")
    store.save(_make_cp("cp-002", attempt=2))
    results = store.all()
    assert len(results) == 2
    assert results[0].checkpoint_id == "cp-001"
    assert results[1].checkpoint_id == "cp-002"


def test_latest_returns_none_when_all_lines_corrupt(tmp_path: Path):
    """所有行都坏时 latest() 返回 None（不抛异常）。"""
    cp_file = tmp_path / "runtime_checkpoints.jsonl"
    cp_file.write_text("bad1\nbad2\n", encoding="utf-8")
    store = CheckpointStore(str(tmp_path), "sess-001")
    assert store.latest() is None
    assert store.all() == []


def test_from_line_handles_missing_duration_ms(tmp_path):
    """G5: 旧 JSONL 行（无 duration_ms）反序列化时填默认 0。"""
    import json
    from loop_controller.runtime.checkpoint_store import CheckpointStore

    store = CheckpointStore(str(tmp_path), "s1")
    old_record = {
        "checkpoint_id": "cp-old",
        "session_id": "s1",
        "attempt_index": 0,
        "current_node": "INIT_SESSION",
        "input_summary": {},
        "output_summary": {"reason": "init"},
        "failure_code": "NONE",
        "matched_guards": [],
        "next_node": "RUN_VERIFY",
        "timestamp": "2026-01-01T00:00:00+08:00",
    }
    (tmp_path / "runtime_checkpoints.jsonl").write_text(
        json.dumps(old_record) + "\n", encoding="utf-8"
    )
    records = store.all()
    assert len(records) == 1
    assert records[0].duration_ms == 0


def test_from_line_reads_duration_ms(tmp_path):
    """G5: 新 JSONL 行（含 duration_ms）反序列化正确读取。"""
    import json
    from loop_controller.runtime.checkpoint_store import CheckpointStore

    store = CheckpointStore(str(tmp_path), "s1")
    new_record = {
        "checkpoint_id": "cp-new",
        "session_id": "s1",
        "attempt_index": 0,
        "current_node": "RUN_VERIFY",
        "input_summary": {},
        "output_summary": {"reason": "verify"},
        "failure_code": "NONE",
        "matched_guards": [],
        "next_node": "DECIDE_NEXT",
        "timestamp": "2026-01-01T00:00:01+08:00",
        "duration_ms": 1234,
    }
    (tmp_path / "runtime_checkpoints.jsonl").write_text(
        json.dumps(new_record) + "\n", encoding="utf-8"
    )
    records = store.all()
    assert len(records) == 1
    assert records[0].duration_ms == 1234
