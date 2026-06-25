import json
from pathlib import Path

from loop_controller.runtime.engine import LoopRuntime
from loop_controller.runtime.checkpoint_store import CheckpointStore
from loop_contracts.models import LoopSession, RuntimeTerminalState


def _write_bundle(tmp_path: Path, overall: str, failed: int) -> None:
    bundle = {
        "summary": {
            "overall": overall, "total": 1, "passed": 1 - failed, "failed": failed, "skipped": 0,
        },
        "cases": [],
    } if overall == "PASS" else {
        "summary": {
            "overall": overall, "total": 1, "passed": 0, "failed": failed, "skipped": 0,
        },
        "cases": [{"id": "case.fail", "status": "fail", "failure_reason": "boom", "command": "echo boom"}],
    }
    (tmp_path / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")


def test_runtime_pass_path_done_success(tmp_path: Path, monkeypatch):
    """verify PASS -> DECIDE_NEXT -> DONE_SUCCESS"""
    _write_bundle(tmp_path, "PASS", 0)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    session = LoopSession(
        session_id="sess-001", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )

    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.DONE_SUCCESS


def test_runtime_escalate_on_max_attempts(tmp_path: Path, monkeypatch):
    """verify FAIL x1, max_attempts=1 -> DECIDE_NEXT(RETRY) -> BUILD -> WAIT -> ESCALATE_HUMAN"""
    _write_bundle(tmp_path, "FAIL", 1)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = ""
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    session = LoopSession(
        session_id="sess-002", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=1, artifacts_dir=str(tmp_path),
    )

    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.ESCALATE_HUMAN


def test_runtime_resume_from_checkpoint(tmp_path: Path, monkeypatch):
    """resume() loads next_node from latest checkpoint"""
    from loop_controller.runtime.checkpoint_store import CheckpointStore
    from loop_contracts.models import CheckpointRecord
    from loop_contracts.failure_codes import FailureCode

    store = CheckpointStore(str(tmp_path), "sess-003")
    store.save(CheckpointRecord(
        checkpoint_id="cp-1", session_id="sess-003", attempt_index=1,
        current_node="RUN_VERIFY", input_summary={}, output_summary={},
        failure_code=FailureCode.NONE, matched_guards=[],
        next_node="DECIDE_NEXT", timestamp="2026-06-26T12:00:00+08:00",
    ))

    session = LoopSession(
        session_id="sess-003", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.resume()
    assert state.current_node == "DECIDE_NEXT"
    assert state.previous_node == "RUN_VERIFY"


def test_runtime_guard_chain_done_success(tmp_path: Path, monkeypatch):
    """verify PASS -> guard_chain returns DONE_SUCCESS by all_cases_passed"""
    _write_bundle(tmp_path, "PASS", 0)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    session = LoopSession(
        session_id="sess-004", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.DONE_SUCCESS
    cps = CheckpointStore(str(tmp_path), "sess-004").all()
    decide_cps = [cp for cp in cps if cp.current_node == "DECIDE_NEXT"]
    assert len(decide_cps) >= 1
    assert "all_cases_passed" in decide_cps[0].matched_guards


def test_runtime_guard_chain_escalate_on_limit(tmp_path: Path, monkeypatch):
    """verify FAIL+max=1 -> guard_chain returns ESCALATE_HUMAN by attempt_limit_reached"""
    _write_bundle(tmp_path, "FAIL", 1)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = ""
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    session = LoopSession(
        session_id="sess-005", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=1, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.ESCALATE_HUMAN
    cps = CheckpointStore(str(tmp_path), "sess-005").all()
    decide_cps = [cp for cp in cps if cp.current_node == "DECIDE_NEXT"]
    assert len(decide_cps) >= 1
    assert "attempt_limit_reached" in decide_cps[0].matched_guards
