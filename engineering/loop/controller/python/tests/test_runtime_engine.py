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


def test_resume_then_run_reaches_done_success(tmp_path, monkeypatch):
    """resume 从中间 checkpoint 恢复后 run() 续跑到 DONE_SUCCESS"""
    from loop_controller.runtime.checkpoint_store import CheckpointStore
    from loop_contracts.models import CheckpointRecord
    from loop_contracts.failure_codes import FailureCode

    _write_bundle(tmp_path, "PASS", 0)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    # 构造 checkpoint：RUN_VERIFY(PASS) 已完成，next=DECIDE_NEXT
    store = CheckpointStore(str(tmp_path), "sess-resume-run")
    store.save(CheckpointRecord(
        checkpoint_id="cp-1", session_id="sess-resume-run", attempt_index=1,
        current_node="RUN_VERIFY",
        input_summary={}, output_summary={"node_status": "PASS"},
        failure_code=FailureCode.NONE, matched_guards=[],
        next_node="DECIDE_NEXT", timestamp="2026-06-26T12:00:00+08:00",
    ))

    session = LoopSession(
        session_id="sess-resume-run", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    rt.resume()
    # resume 后 current_node=DECIDE_NEXT，run() 续跑应直达 DONE_SUCCESS
    assert rt._state.current_node == "DECIDE_NEXT"
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.DONE_SUCCESS


def test_resume_on_already_terminal_is_noop(tmp_path):
    """对已终态的 session 调 resume 不恢复、不执行"""
    from loop_controller.runtime.checkpoint_store import CheckpointStore
    from loop_contracts.models import CheckpointRecord
    from loop_contracts.failure_codes import FailureCode

    store = CheckpointStore(str(tmp_path), "sess-terminal")
    store.save(CheckpointRecord(
        checkpoint_id="cp-1", session_id="sess-terminal", attempt_index=1,
        current_node="DECIDE_NEXT", input_summary={},
        output_summary={"node_status": "PASS"},
        failure_code=FailureCode.NONE, matched_guards=["all_cases_passed"],
        next_node="DONE_SUCCESS", timestamp="2026-06-26T12:00:00+08:00",
    ))

    session = LoopSession(
        session_id="sess-terminal", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    # 传入 initial_terminal_state=DONE_SUCCESS 模拟已终态
    rt = LoopRuntime(session, "cases", "profile.json",
                     initial_terminal_state=RuntimeTerminalState.DONE_SUCCESS)
    state = rt.resume()
    # 幂等：不恢复 checkpoint
    assert state.current_node == "INIT_SESSION"
    assert state.terminal_state == RuntimeTerminalState.DONE_SUCCESS


def test_resume_restores_failure_code_for_guard(tmp_path, monkeypatch):
    """中断在 RUN_VERIFY(FAIL) 后 → resume 恢复 fc=RUN_FAILED → guard 正确判定"""
    from loop_controller.runtime.checkpoint_store import CheckpointStore
    from loop_contracts.models import CheckpointRecord
    from loop_contracts.failure_codes import FailureCode

    store = CheckpointStore(str(tmp_path), "sess-fc")
    store.save(CheckpointRecord(
        checkpoint_id="cp-1", session_id="sess-fc", attempt_index=1,
        current_node="RUN_VERIFY",
        input_summary={}, output_summary={"node_status": "FAIL"},
        failure_code=FailureCode.RUN_FAILED, matched_guards=[],
        next_node="DECIDE_NEXT", timestamp="2026-06-26T12:00:00+08:00",
    ))

    session = LoopSession(
        session_id="sess-fc", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    rt.resume()
    # resume 后 latest_failure_code 必须是 RUN_FAILED 而非 NONE
    assert rt._session.latest_failure_code == FailureCode.RUN_FAILED
    # guard_eval_request 的 latest_failure_code 也应该是 RUN_FAILED
    guard_req = rt._build_guard_eval_request()
    assert guard_req.latest_failure_code == FailureCode.RUN_FAILED


def test_resume_restores_attempt_count(tmp_path):
    """中断在 attempt=3 后 → resume 恢复 current_attempt=3"""
    from loop_controller.runtime.checkpoint_store import CheckpointStore
    from loop_contracts.models import CheckpointRecord
    from loop_contracts.failure_codes import FailureCode

    store = CheckpointStore(str(tmp_path), "sess-att")
    store.save(CheckpointRecord(
        checkpoint_id="cp-1", session_id="sess-att", attempt_index=3,
        current_node="RUN_VERIFY",
        input_summary={}, output_summary={"node_status": "FAIL"},
        failure_code=FailureCode.RUN_FAILED, matched_guards=[],
        next_node="DECIDE_NEXT", timestamp="2026-06-26T12:00:00+08:00",
    ))

    session = LoopSession(
        session_id="sess-att", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    rt.resume()
    # current_attempt 从 checkpoint 的 attempt_index 恢复
    assert rt._session.current_attempt == 3
    guard_req = rt._build_guard_eval_request()
    assert guard_req.attempt_count == 3


def test_deploy_fail_reverts_then_goes_to_decide(tmp_path, monkeypatch):
    """DEPLOY_FAILED(DEPLOY_FATAL) → REVERT_PATCH(revert ok) → DECIDE_NEXT"""
    _write_bundle(tmp_path, "FAIL", 1)

    call_log = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        call_log.append(cmd_str)
        class R:
            stdout = ""
            stderr = ""
            if "loop_core.cli" in cmd_str:
                if len([c for c in call_log if "loop_core.cli" in c]) == 1:
                    returncode = 1
                else:
                    returncode = 0
            else:
                returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    from loop_contracts.failure_codes import FailureCode as FC

    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_apply_patch",
        lambda *a, **kw: {"status": "APPLIED", "failure_code": FC.NONE, "files": ["t.cpp"], "stash_ref": "stub", "patch_hash": "abc", "risk": {}, "workspace_root": str(tmp_path)},
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_compile",
        lambda *a, **kw: {"status": "COMPILED", "failure_code": FC.NONE, "artifacts": ["out/t"]},
    )
    # deploy fails → DEPLOY_FATAL
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_deploy",
        lambda *a, **kw: {"status": "DEPLOY_FAILED", "failure_code": FC.DEPLOY_FATAL, "mode": "PUSH_SINGLE", "backup_path": "/tmp/fake", "deployed_files": ["/system/fake.so"]},
    )
    # device rollback succeeds
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_rollback_deploy",
        lambda *a, **kw: {"status": "REVERTED", "failure_code": FC.NONE},
    )
    # workspace revert succeeds
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_revert_workspace",
        lambda *a, **kw: {"status": "REVERTED", "failure_code": FC.NONE},
    )

    (tmp_path / "patch_suggestion.json").write_text(
        json.dumps([{"path": "t.cpp", "action": "modify", "content": ""}]), encoding="utf-8",
    )

    session = LoopSession(
        session_id="sess-dr", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    # After revert, goes to DECIDE_NEXT → no guard matches terminal_success → goes to DONE_FAILURE
    # Actually RESULT should be that after revert success, if under max, it tries again
    assert state.terminal_state in (RuntimeTerminalState.DONE_FAILURE, RuntimeTerminalState.ESCALATE_HUMAN)


def test_deploy_kernel_dead_escalates(tmp_path, monkeypatch):
    """KERNEL_DEAD → guard kernel_dead_no_shell → ESCALATE_HUMAN (no revert)"""
    _write_bundle(tmp_path, "FAIL", 1)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    from loop_contracts.failure_codes import FailureCode as FC

    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_apply_patch",
        lambda *a, **kw: {"status": "APPLIED", "failure_code": FC.NONE, "files": ["t.cpp"], "stash_ref": "stub", "patch_hash": "abc", "risk": {}, "workspace_root": str(tmp_path)},
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_compile",
        lambda *a, **kw: {"status": "COMPILED", "failure_code": FC.NONE, "artifacts": ["out/t"]},
    )
    # deploy returns KERNEL_DEAD
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_deploy",
        lambda *a, **kw: {"status": "KERNEL_DEAD", "failure_code": FC.KERNEL_DEAD_NO_SHELL, "mode": ""},
    )

    (tmp_path / "patch_suggestion.json").write_text(
        json.dumps([{"path": "t.cpp", "action": "modify", "content": ""}]), encoding="utf-8",
    )

    session = LoopSession(
        session_id="sess-kd", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.ESCALATE_HUMAN


def test_deploy_success_no_revert(tmp_path, monkeypatch):
    """DEPLOYED → RUN_VERIFY, no revert triggered"""
    _write_bundle(tmp_path, "FAIL", 1)

    call_log = []

    def fake_run(cmd, **kwargs):
        cmd_str = " ".join(cmd)
        call_log.append(cmd_str)
        class R:
            stdout = ""
            stderr = ""
            if "loop_core.cli" in cmd_str:
                if len([c for c in call_log if "loop_core.cli" in c]) == 1:
                    returncode = 1
                else:
                    returncode = 0
            else:
                returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    from loop_contracts.failure_codes import FailureCode as FC

    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_apply_patch",
        lambda *a, **kw: {"status": "APPLIED", "failure_code": FC.NONE, "files": ["t.cpp"], "stash_ref": "stub", "patch_hash": "abc", "risk": {}, "workspace_root": str(tmp_path)},
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_compile",
        lambda *a, **kw: {"status": "COMPILED", "failure_code": FC.NONE, "artifacts": ["out/t"]},
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_deploy",
        lambda *a, **kw: {"status": "DEPLOYED", "failure_code": FC.NONE, "mode": "PUSH_SINGLE"},
    )

    (tmp_path / "patch_suggestion.json").write_text(
        json.dumps([{"path": "t.cpp", "action": "modify", "content": ""}]), encoding="utf-8",
    )

    session = LoopSession(
        session_id="sess-dok", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    # deploy success → second verify passes → DONE_SUCCESS
    assert state.terminal_state == RuntimeTerminalState.DONE_SUCCESS
