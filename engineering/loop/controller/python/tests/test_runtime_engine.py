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


def test_runtime_wires_apply_compile_deploy(tmp_path, monkeypatch):
    """patched FAIL cycle routes APPLY->COMPILE->DEPLOY->RUN_VERIFY->DONE_SUCCESS"""
    _write_bundle(tmp_path, "FAIL", 1)

    call_log = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        call_log.append(cmd_str)
        class R:
            stdout = ""
            stderr = ""
            # first verify call returns fail (1), subsequent verify returns pass (0)
            if "loop_core.cli" in cmd_str:
                if len([c for c in call_log if "loop_core.cli" in c]) == 1:
                    returncode = 1
                else:
                    returncode = 0
            else:
                returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    # mock nodes.py handlers to return success
    from loop_contracts.failure_codes import FailureCode as FC
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_apply_patch",
        lambda patch_path, session_dict, workspace_root: {
            "status": "APPLIED", "failure_code": FC.NONE,
            "files": ["test.cpp"], "stash_ref": "fake-stash",
            "patch_hash": "abc123", "risk": {},
            "workspace_root": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_compile",
        lambda session_dict, workspace_root: {
            "status": "COMPILED", "failure_code": FC.NONE,
            "artifacts": ["out/test"],
        },
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_deploy",
        lambda session_dict, adb_endpoint: {
            "status": "DEPLOYED", "failure_code": FC.NONE,
            "mode": "PUSH_SINGLE",
        },
    )

    # create patch_suggestion.json so WAIT_ANALYZER_PATCH proceeds to APPLY_PATCH
    (tmp_path / "patch_suggestion.json").write_text(
        json.dumps([{"path": "test.cpp", "action": "modify", "content": "// test"}]),
        encoding="utf-8",
    )

    session = LoopSession(
        session_id="sess-wire", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    # Should end at DONE_SUCCESS since deploy mocked ok + re-verify passes
    assert state.terminal_state == RuntimeTerminalState.DONE_SUCCESS


def test_run_writes_back_session_json(tmp_path, monkeypatch):
    """run() writes session.json after completion"""
    _write_bundle(tmp_path, "PASS", 0)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    session = LoopSession(
        session_id="sess-wb", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.DONE_SUCCESS

    session_path = tmp_path / "session.json"
    assert session_path.exists()
    saved = json.loads(session_path.read_text())
    assert saved["session_id"] == "sess-wb"
    assert "terminal_state" in saved
    assert saved["terminal_state"] == "DONE_SUCCESS"


def test_resume_restores_full_state(tmp_path, monkeypatch):
    """resume() restores node_status, last_checkpoint_at, and previous_node"""
    from loop_controller.runtime.checkpoint_store import CheckpointStore
    from loop_contracts.models import CheckpointRecord
    from loop_contracts.failure_codes import FailureCode

    store = CheckpointStore(str(tmp_path), "sess-full")
    store.save(CheckpointRecord(
        checkpoint_id="cp-1", session_id="sess-full", attempt_index=2,
        current_node="RUN_VERIFY",
        input_summary={}, output_summary={"node_status": "FAIL", "reason": "verify FAIL"},
        failure_code=FailureCode.RUN_FAILED, matched_guards=["attempts_below_limit"],
        next_node="DECIDE_NEXT", timestamp="2026-06-26T12:30:00+08:00",
    ))

    session = LoopSession(
        session_id="sess-full", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.resume()
    assert state.current_node == "DECIDE_NEXT"
    assert state.previous_node == "RUN_VERIFY"
    assert state.node_status == "FAIL"
    assert state.last_checkpoint_at == "2026-06-26T12:30:00+08:00"
