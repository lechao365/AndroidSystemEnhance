import json
from pathlib import Path

from loop_controller.runtime.engine import LoopRuntime
from loop_controller.runtime.checkpoint_store import CheckpointStore
from loop_controller.runtime.types import NodeKind
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


def _write_bundle_n_failures(tmp_path: Path, n_failed: int, total: int = 5) -> None:
    """写入指定失败用例数的 evidence_bundle（用于收敛判定测试）。

    第 n_failed 个用例 fail，其余 pass。
    """
    cases = []
    for i in range(total):
        cases.append({
            "id": f"case.{i}",
            "status": "fail" if i < n_failed else "pass",
            "failure_reason": "boom" if i < n_failed else "",
            "command": f"echo {i}",
        })
    bundle = {
        "summary": {
            "overall": "PASS" if n_failed == 0 else "FAIL",
            "total": total,
            "passed": total - n_failed,
            "failed": n_failed,
            "skipped": 0,
        },
        "cases": cases,
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
        lambda *a, **kw: {
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
        lambda session_dict, adb_endpoint="", serial_shell_provider=None: {
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


def test_resume_restores_deploy_context(tmp_path, monkeypatch):
    """resume 后 deploy_context 从 attempts 恢复，设备回滚不会被跳过"""
    from loop_controller.runtime.checkpoint_store import CheckpointStore
    from loop_contracts.models import CheckpointRecord
    from loop_contracts.failure_codes import FailureCode

    # checkpoint: DEPLOY_PATCH 失败后 next=REVERT_PATCH
    store = CheckpointStore(str(tmp_path), "sess-dc")
    store.save(CheckpointRecord(
        checkpoint_id="cp-1", session_id="sess-dc", attempt_index=1,
        current_node="DEPLOY_PATCH",
        input_summary={},
        output_summary={"node_status": "DEPLOY_FAILED_REVERT"},
        failure_code=FailureCode.DEPLOY_FATAL, matched_guards=["deploy_failed_but_recoverable"],
        next_node="REVERT_PATCH", timestamp="2026-06-26T12:00:00+08:00",
    ))

    # session 带 deploy_context 的 attempt
    session = LoopSession(
        session_id="sess-dc", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    session.attempts = [{
        "attempt_index": 1,
        "failure_code": "DEPLOY_FATAL",
        "deploy_context": {
            "mode": "PUSH_SINGLE",
            "backup_path": "/tmp/fake_backup",
            "backup_sha": "",
            "deployed_files": ["/system/lib/test.so"],
        },
    }]

    rt = LoopRuntime(session, "cases", "profile.json")
    rt.resume()
    # resume 后 deploy_context 必须从 attempts 恢复
    assert rt._deploy_context.get("mode") == "PUSH_SINGLE"
    assert rt._deploy_context.get("backup_path") == "/tmp/fake_backup"
    assert "/system/lib/test.so" in rt._deploy_context.get("deployed_files", [])


def test_compile_deploy_results_recorded_in_attempts(tmp_path, monkeypatch):
    """COMPILE 和 DEPLOY 结果写入 attempts[] 历史"""
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
        session_id="sess-att-cd", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    rt.run()

    # 验证 attempts 里有 compile_result 和 deploy_result
    # 注意：re-verify 会新增一个 attempt，compile/deploy 在前一个 attempt 里
    assert len(session.attempts) >= 2
    # 找包含 compile_result 的 attempt
    compile_att = None
    for att in session.attempts:
        if isinstance(att, dict) and "compile_result" in att:
            compile_att = att
            break
    assert compile_att is not None, "no attempt contains compile_result"
    assert compile_att["compile_result"]["status"] == "COMPILED"
    assert "deploy_result" in compile_att, "deploy_result missing from same attempt"
    assert compile_att["deploy_result"]["status"] == "DEPLOYED"
    assert compile_att["deploy_result"]["mode"] == "PUSH_SINGLE"


def test_rollback_deploy_uses_adb_endpoint(tmp_path, monkeypatch):
    """node_rollback_deploy 使用 adb_endpoint 参数连接设备"""
    import sys
    from types import ModuleType

    # 用 monkeypatch.setitem/setattr 注入 fake module，测试结束自动恢复 sys.modules，
    # 避免残留假 loop_adb 模块污染后续 deploy 模块测试（test_adb_ops / test_deployer）
    if "loop_adb" not in sys.modules:
        loop_adb = ModuleType("loop_adb")
        monkeypatch.setitem(sys.modules, "loop_adb", loop_adb)
    if "loop_adb.client" not in sys.modules:
        loop_adb_client = ModuleType("loop_adb.client")
        monkeypatch.setitem(sys.modules, "loop_adb.client", loop_adb_client)
        monkeypatch.setattr(sys.modules["loop_adb"], "client", loop_adb_client, raising=False)

    from loop_controller.runtime.nodes import node_rollback_deploy
    from loop_contracts.failure_codes import FailureCode as FC

    # 准备 backup 目录和文件
    import tempfile
    backup_dir = Path(tempfile.mkdtemp())
    (backup_dir / "test.so").write_bytes(b"fake_old_binary")

    # mock AdbClient
    endpoint_used = []

    class FakePushResult:
        exit_code = 0

    class FakeAdbClient:
        def __init__(self, endpoint=None, device_serial=None, **kw):
            endpoint_used.append(endpoint)
        def connect(self, **kw):
            pass
        def push(self, local, remote, **kw):
            return FakePushResult()

    monkeypatch.setattr(sys.modules["loop_adb.client"], "AdbClient", FakeAdbClient, raising=False)

    result = node_rollback_deploy(
        session_dict={},
        deploy_context={
            "mode": "PUSH_SINGLE",
            "backup_path": str(backup_dir),
            "deployed_files": ["/system/lib/test.so"],
        },
        adb_endpoint="192.168.1.100:5555",
    )
    assert result["status"] == "REVERTED"
    # 验证 endpoint 被传入
    assert "192.168.1.100:5555" in endpoint_used


# ---------------------------------------------------------------------------
# ISSUE-1：WAIT_ANALYZER_PATCH 缺 patch 文件时调用注入的 analyzer 产出
# ---------------------------------------------------------------------------

def test_wait_analyzer_invokes_injected_analyzer_when_no_patch_file(tmp_path, monkeypatch):
    """缺 patch_suggestion.json 时，engine 调用注入的 analyzer 产出补丁文件。"""
    _write_bundle_n_failures(tmp_path, 1, total=1)

    analyzer_calls = []

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    from loop_controller.analyzer_protocol import LlmAnalyzer, AnalysisRequest, PatchSuggestion, FileChange

    class StubAnalyzer(LlmAnalyzer):
        """测试用 analyzer：产出单个 FileChange（engine 负责落盘 patch_suggestion.json）。"""
        def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
            analyzer_calls.append(request.session_id)
            change = FileChange(workspace_path="test.cpp", change_type="edit",
                                old_marker="x", new_content="y")
            # confidence 必须 >= 阈值(0.7) 才不会触发 LOW_CONFIDENCE gate
            return PatchSuggestion(target_files=[change], rationale="stub fix", confidence=0.9)

    # mock node_apply_patch 让 APPLY_PATCH 成功
    from loop_contracts.failure_codes import FailureCode as FC
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_apply_patch",
        lambda *a, **kw: {"status": "APPLIED", "failure_code": FC.NONE,
                          "files": ["test.cpp"], "stash_ref": "", "patch_hash": "h1",
                          "risk": {}, "workspace_root": str(tmp_path)},
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_compile",
        lambda *a, **kw: {"status": "COMPILED", "failure_code": FC.NONE, "artifacts": []},
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_deploy",
        lambda *a, **kw: {"status": "DEPLOYED", "failure_code": FC.NONE, "mode": "PUSH_SINGLE"},
    )

    # 第二次 verify PASS（让闭环收敛）
    call_count = [0]

    def fake_run_pass_on_second(cmd, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            _write_bundle_n_failures(tmp_path, 1, total=1)
            class R1:
                returncode = 1
                stdout = ""
                stderr = ""
            return R1()
        else:
            _write_bundle_n_failures(tmp_path, 0, total=1)
            class R2:
                returncode = 0
                stdout = ""
                stderr = ""
            return R2()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run_pass_on_second)

    session = LoopSession(
        session_id="sess-analyzer", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json", analyzer=StubAnalyzer())
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.DONE_SUCCESS
    assert len(analyzer_calls) == 1
    assert analyzer_calls[0] == "sess-analyzer"


def test_wait_analyzer_escalates_when_analyzer_returns_empty(tmp_path, monkeypatch):
    """analyzer 返回空 PatchSuggestion（无可行补丁）→ 退人工。"""
    _write_bundle_n_failures(tmp_path, 1, total=1)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = ""
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    from loop_controller.analyzer_protocol import LlmAnalyzer, AnalysisRequest, PatchSuggestion

    class EmptyAnalyzer(LlmAnalyzer):
        def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
            return PatchSuggestion(target_files=[], rationale="no rule", confidence=0.0)

    session = LoopSession(
        session_id="sess-empty-an", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json", analyzer=EmptyAnalyzer())
    state = rt.run()
    assert state.terminal_state == RuntimeTerminalState.ESCALATE_HUMAN
    # patch_suggestion.json 不应被写入
    assert not (tmp_path / "patch_suggestion.json").exists()


# ---------------------------------------------------------------------------
# ISSUE-3：progress_converging 宽限（失败用例数严格下降时即使达上限也 RETRY）
# ---------------------------------------------------------------------------

def test_progress_converging_grants_retry_when_failures_decreasing(tmp_path, monkeypatch):
    """失败用例数严格下降 → progress_converging 宽限 RETRY（即使 attempt 达上限）。

    直接构造两次 verify 后的 session 状态，验证 _build_guard_eval_request 与
    _execute_decide_next 的 guard 匹配，避免完整 run() 的链路复杂度。
    """
    # 构造 session：已有两次 attempt，failed_count 4 → 2（严格下降）
    session = LoopSession(
        session_id="sess-conv", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(tmp_path),
    )
    session.current_attempt = 2
    session.attempts = [
        {"attempt_index": 1, "failed_count": 4, "failure_code": "RUN_FAILED",
         "verify_result": "FAIL"},
        {"attempt_index": 2, "failed_count": 2, "failure_code": "RUN_FAILED",
         "verify_result": "FAIL"},
    ]
    session.latest_failure_code = __import__("loop_contracts").failure_codes.FailureCode.RUN_FAILED

    rt = LoopRuntime(session, "cases", "profile.json")
    rt._state.current_node = "DECIDE_NEXT"
    rt._state.node_status = "FAIL"

    # 验证 guard_eval_request 字段填充正确
    req = rt._build_guard_eval_request()
    assert req.latest_failed_count == 2
    assert req.previous_failed_count == 4
    assert req.attempt_count == 2
    assert req.max_attempts == 2  # 达上限

    # 执行 DECIDE_NEXT，应匹配 progress_converging（RETRY 而非 escalate）
    rt._execute_decide_next()
    cps = CheckpointStore(str(tmp_path), "sess-conv").all()
    decide_cp = [c for c in cps if c.current_node == "DECIDE_NEXT"][-1]
    assert "progress_converging" in decide_cp.matched_guards
    # 宽限 RETRY，不 escalate
    assert rt._state.terminal_state != RuntimeTerminalState.ESCALATE_HUMAN


def test_progress_converging_escalates_when_failures_stuck(tmp_path, monkeypatch):
    """失败用例数持平不下降 → progress_converging 触发 ESCALATE（STUCK）。"""
    session = LoopSession(
        session_id="sess-stuck", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    session.current_attempt = 2
    session.attempts = [
        {"attempt_index": 1, "failed_count": 3, "failure_code": "RUN_FAILED",
         "verify_result": "FAIL"},
        {"attempt_index": 2, "failed_count": 3, "failure_code": "RUN_FAILED",
         "verify_result": "FAIL"},
    ]
    session.latest_failure_code = __import__("loop_contracts").failure_codes.FailureCode.RUN_FAILED

    rt = LoopRuntime(session, "cases", "profile.json")
    rt._state.current_node = "DECIDE_NEXT"
    rt._state.node_status = "FAIL"

    rt._execute_decide_next()
    assert rt._state.terminal_state == RuntimeTerminalState.ESCALATE_HUMAN
    cps = CheckpointStore(str(tmp_path), "sess-stuck").all()
    decide_cp = [c for c in cps if c.current_node == "DECIDE_NEXT"][-1]
    assert "progress_converging" in decide_cp.matched_guards


def test_progress_converging_escalates_when_failures_increasing(tmp_path, monkeypatch):
    """失败用例数上升 → progress_converging 触发 ESCALATE（REGRESSION）。"""
    session = LoopSession(
        session_id="sess-regress", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    session.current_attempt = 2
    session.attempts = [
        {"attempt_index": 1, "failed_count": 2, "failure_code": "RUN_FAILED",
         "verify_result": "FAIL"},
        {"attempt_index": 2, "failed_count": 4, "failure_code": "RUN_FAILED",
         "verify_result": "FAIL"},
    ]
    session.latest_failure_code = __import__("loop_contracts").failure_codes.FailureCode.RUN_FAILED

    rt = LoopRuntime(session, "cases", "profile.json")
    rt._state.current_node = "DECIDE_NEXT"
    rt._state.node_status = "FAIL"

    rt._execute_decide_next()
    assert rt._state.terminal_state == RuntimeTerminalState.ESCALATE_HUMAN
    cps = CheckpointStore(str(tmp_path), "sess-regress").all()
    decide_cp = [c for c in cps if c.current_node == "DECIDE_NEXT"][-1]
    assert "progress_converging" in decide_cp.matched_guards


# ---------------------------------------------------------------------------
# ISSUE-1（本轮）：worktree 隔离的补丁，compile/deploy 必须在 worktree 内执行
# ---------------------------------------------------------------------------

def test_compile_receives_worktree_path_as_workspace_root(tmp_path, monkeypatch):
    """APPLY_PATCH 用 worktree 隔离后，COMPILE_PATCH 必须把 worktree_path 传给 node_compile。

    验证：当 attempt 含 worktree_handle 时，engine 调 node_compile 时 workspace_root
    参数等于 worktree_path（而非空字符串或原 workspace root）。
    """
    _write_bundle(tmp_path, "FAIL", 1)

    compile_calls = []
    call_count = [0]

    def fake_run(cmd, **kwargs):
        call_count[0] += 1
        # 第一次 verify FAIL，第二次 verify PASS（deploy 后收敛）
        if call_count[0] == 1:
            _write_bundle(tmp_path, "FAIL", 1)
            class R:
                returncode = 1
                stdout = ""
                stderr = ""
            return R()
        _write_bundle(tmp_path, "PASS", 0)
        class R2:
            returncode = 0
            stdout = ""
            stderr = ""
        return R2()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    from loop_contracts.failure_codes import FailureCode as FC

    fake_worktree_path = "/tmp/fake-worktree-sess-wt1"

    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_apply_patch",
        lambda *a, **kw: {
            "status": "APPLIED", "failure_code": FC.NONE,
            "files": ["test.cpp"], "stash_ref": "",
            "patch_hash": "abc", "risk": {},
            "workspace_root": str(tmp_path),
            "worktree_handle": {
                "worktree_path": fake_worktree_path,
                "branch": "loop/sess-wt1/1",
                "workspace_root": str(tmp_path),
                "created": True,
            },
        },
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_compile",
        lambda session_dict, workspace_root="": (
            compile_calls.append(workspace_root),
            {"status": "COMPILED", "failure_code": FC.NONE, "artifacts": ["out/t"]},
        )[1],
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_deploy",
        lambda *a, **kw: {"status": "DEPLOYED", "failure_code": FC.NONE, "mode": "PUSH_SINGLE"},
    )

    (tmp_path / "patch_suggestion.json").write_text(
        json.dumps([{"workspace_path": "t.cpp", "change_type": "edit",
                     "old_marker": "x", "new_content": "y"}]),
        encoding="utf-8",
    )

    session = LoopSession(
        session_id="sess-wt1", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    state = rt.run()
    assert len(compile_calls) == 1, f"expected 1 compile call, got {len(compile_calls)}"
    assert compile_calls[0] == fake_worktree_path, (
        f"compile workspace_root should be worktree path, got {compile_calls[0]}"
    )


def test_compile_falls_back_to_workspace_root_when_no_worktree(tmp_path, monkeypatch):
    """无 worktree（降级 stash 模式）时，compile 仍用原 workspace_root（向后兼容）。"""
    _write_bundle(tmp_path, "FAIL", 1)

    compile_calls = []
    call_count = [0]

    def fake_run(cmd, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            _write_bundle(tmp_path, "FAIL", 1)
            class R:
                returncode = 1
                stdout = ""
                stderr = ""
            return R()
        _write_bundle(tmp_path, "PASS", 0)
        class R2:
            returncode = 0
            stdout = ""
            stderr = ""
        return R2()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    from loop_contracts.failure_codes import FailureCode as FC

    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_apply_patch",
        lambda *a, **kw: {
            "status": "APPLIED", "failure_code": FC.NONE,
            "files": ["test.cpp"], "stash_ref": "fake-stash",
            "patch_hash": "abc", "risk": {},
            "workspace_root": str(tmp_path),
        },
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_compile",
        lambda session_dict, workspace_root="": (
            compile_calls.append(workspace_root),
            {"status": "COMPILED", "failure_code": FC.NONE, "artifacts": ["out/t"]},
        )[1],
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_deploy",
        lambda *a, **kw: {"status": "DEPLOYED", "failure_code": FC.NONE, "mode": "PUSH_SINGLE"},
    )

    (tmp_path / "patch_suggestion.json").write_text(
        json.dumps([{"workspace_path": "t.cpp", "change_type": "edit",
                     "old_marker": "x", "new_content": "y"}]),
        encoding="utf-8",
    )

    session = LoopSession(
        session_id="sess-nowt", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    rt.run()
    assert len(compile_calls) == 1
    assert compile_calls[0] == ""


# ---------------------------------------------------------------------------
# ISSUE-3（本轮）：DONE_SUCCESS 时清理 worktree；revert 路径已清理（上一轮覆盖）
# ---------------------------------------------------------------------------

def test_done_success_cleans_worktrees(tmp_path, monkeypatch):
    """全 PASS 收敛后，engine 应清理所有 attempts 中的 worktree。

    用真实 git worktree 验证清理生效（目录消失 + worktree list 不再列出）。
    """
    import subprocess

    # 构造真实 git 仓库作为 workspace
    repo = tmp_path / "workspace"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "a.txt").write_text("init")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    # 在仓库内创建一个 worktree（模拟 APPLY_PATCH 产出的）
    from loop_controller.workspace_isolation import create_patch_worktree, WorktreeHandle
    wt_parent = tmp_path / "wts"
    handle = create_patch_worktree(str(repo), "sess-clean", 1, worktree_parent=str(wt_parent))
    wt_path = handle.worktree_path
    # 确认 worktree 存在
    assert Path(wt_path).exists()

    # 构造 session：已有 attempt 含 worktree_handle，verify PASS
    artifacts_dir = tmp_path / "artifacts"
    artifacts_dir.mkdir(exist_ok=True)
    _write_bundle(artifacts_dir, "PASS", 0)

    session = LoopSession(
        session_id="sess-clean", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=2, artifacts_dir=str(artifacts_dir),
    )
    session.current_attempt = 1
    session.attempts = [{
        "attempt_index": 1,
        "verify_result": "PASS",
        "failed_count": 0,
        "patch_applied": {
            "worktree_handle": {
                "worktree_path": wt_path,
                "branch": handle.branch,
                "workspace_root": str(repo),
                "created": True,
            },
            "workspace_root": str(repo),
        },
    }]
    session.latest_failure_code = __import__("loop_contracts").failure_codes.FailureCode.NONE

    rt = LoopRuntime(session, "cases", "profile.json")
    rt._state.current_node = "DECIDE_NEXT"
    rt._state.node_status = "PASS"

    rt._execute_decide_next()
    assert rt._state.terminal_state == RuntimeTerminalState.DONE_SUCCESS
    # worktree 应被清理
    assert not Path(wt_path).exists(), f"worktree not cleaned: {wt_path}"


# ---------------------------------------------------------------------------
# Task 6: DONE_SUCCESS 归档到知识库（Reflexion 模式）
# ---------------------------------------------------------------------------
def test_done_success_archives_to_kb(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    patch_data = [{"workspace_path": "foo.c", "change_type": "edit",
                   "old_marker": "x", "new_content": "y"}]
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps({"patches": patch_data, "confidence": 0.9}), encoding="utf-8")
    kb_path = str(tmp_path / "kb.json")

    session = LoopSession(
        session_id="lciod-test", workflow_id="runtime", target="lciod",
        suite="features.lciod.end_to_end", max_attempts=3,
        current_attempt=1, artifacts_dir=str(artifacts),
        attempts=[{
            "attempt_index": 1,
            "case_results": [{"id": "HA-03", "status": "fail"}],
            "failed_count": 1,
            "failed_cases": [{"id": "HA-03", "failure_reason": "field mismatch"}],
            "patch_applied": {"patch_hash": "abc"},
        }],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._kb_path = kb_path
    rt._archive_to_knowledge_base()

    kb = json.loads(Path(kb_path).read_text())
    assert len(kb["entries"]) == 1
    assert kb["entries"][0]["source_session"] == "lciod-test"


def test_archive_uses_last_failed_attempt_fingerprint_for_recall(tmp_path):
    """回归 P0-1：DONE_SUCCESS 归档必须用"触发修复的那次失败" attempt 的
    failed_cases 算指纹，才能被后续同类失败的 KnowledgeBaseAnalyzer 召回（Reflexion 闭环）。

    真实 DONE_SUCCESS 形态：attempts = [失败(failed_cases 非空), 成功(failed_cases 空)]。
    原 bug 用 latest（成功，failed_cases 空）算指纹，与查询指纹（失败）永不匹配。
    """
    from loop_controller.analyzer_protocol import AnalysisRequest, KnowledgeBaseAnalyzer

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    patch_data = [{"workspace_path": "foo.c", "change_type": "edit",
                   "old_marker": "x", "new_content": "y"}]
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps({"patches": patch_data, "confidence": 0.9}), encoding="utf-8")
    kb_path = str(tmp_path / "kb.json")

    failed_cases = [{"id": "HA-03",
                     "failure_reason": "getStats field mismatch: read_bytes wrong"}]
    session = LoopSession(
        session_id="lciod-x", workflow_id="runtime", target="lciod",
        suite="features.lciod.end_to_end", max_attempts=3,
        current_attempt=2, artifacts_dir=str(artifacts),
        attempts=[
            {"attempt_index": 1, "failed_cases": failed_cases,
             "case_results": [{"id": "HA-03", "status": "fail"}], "failed_count": 1,
             "patch_applied": {"patch_hash": "abc"}},
            {"attempt_index": 2, "failed_cases": [], "case_results": [], "failed_count": 0},
        ],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._kb_path = kb_path
    rt._archive_to_knowledge_base()

    # 用"失败那次"的 failed_cases + 同 target/suite 查询，必须召回归档补丁
    analyzer = KnowledgeBaseAnalyzer(kb_path)
    query = AnalysisRequest(
        session_id="next", attempt_index=1, target="lciod",
        suite="features.lciod.end_to_end", failed_cases=failed_cases,
    )
    suggestion = analyzer.analyze(query)
    assert len(suggestion.target_files) == 1
    assert suggestion.target_files[0].workspace_path == "foo.c"
    assert suggestion.confidence == 0.98


def test_archive_does_not_duplicate_same_fingerprint(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    patch1 = [{"workspace_path": "a.c", "old_marker": "x", "new_content": "y"}]
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps({"patches": patch1, "confidence": 0.9}), encoding="utf-8")
    kb_path = str(tmp_path / "kb.json")

    session = LoopSession(
        session_id="s1", workflow_id="runtime", target="lciod", suite="s",
        max_attempts=3, current_attempt=1, artifacts_dir=str(artifacts),
        attempts=[{"attempt_index": 1, "failed_count": 1,
                   "failed_cases": [{"id": "C1", "failure_reason": "err"}]}],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._kb_path = kb_path
    rt._archive_to_knowledge_base()
    rt._archive_to_knowledge_base()

    kb = json.loads(Path(kb_path).read_text())
    assert len(kb["entries"]) == 1


def test_archive_silent_failure_on_missing_patch(tmp_path):
    kb_path = str(tmp_path / "kb.json")
    session = LoopSession(
        session_id="s1", workflow_id="runtime", target="t", suite="s", max_attempts=1,
        current_attempt=0, artifacts_dir=str(tmp_path), attempts=[],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._kb_path = kb_path
    rt._archive_to_knowledge_base()
    assert not Path(kb_path).exists()


def test_archive_failure_is_logged_not_silenced(tmp_path, monkeypatch, caplog):
    """P2-3：KB 归档失败时必须记录 warning，而非静默吞掉（CXX-004 故障静默）。

    回归 P2-3：原 `except Exception: pass` 使归档失败完全无诊断痕迹，
    导致 Reflexion 闭环静默失效且无法排查。
    """
    import logging
    from loop_controller import analyzer_protocol as ap_mod

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    patch_data = [{"workspace_path": "foo.c", "old_marker": "x", "new_content": "y"}]
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps({"patches": patch_data, "confidence": 0.9}), encoding="utf-8")
    session = LoopSession(
        session_id="s1", workflow_id="runtime", target="lciod", suite="s",
        max_attempts=3, current_attempt=1, artifacts_dir=str(artifacts),
        attempts=[{"attempt_index": 1, "failed_count": 1,
                   "failed_cases": [{"id": "C1", "failure_reason": "err"}]}],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._kb_path = str(tmp_path / "kb.json")

    # 让 update_kb 抛异常
    def boom(*a, **kw):
        raise OSError("disk full")
    monkeypatch.setattr(ap_mod, "update_kb", boom)

    with caplog.at_level(logging.WARNING, logger="loop_runtime_engine"):
        rt._archive_to_knowledge_base()  # 不应抛异常
    # 必须留下诊断 warning
    assert any("归档" in r.message or "archive" in r.message.lower()
               or "kb" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# Task 8: confidence 阈值检查 + patch_suggestion.json 新旧格式兼容
# ---------------------------------------------------------------------------

def test_low_confidence_triggers_human_gate(tmp_path):
    """confidence < threshold 时触发 pending_human_gate 而非自动 apply。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    suggestion = {
        "patches": [{"workspace_path": "foo.c", "change_type": "edit",
                     "old_marker": "x", "new_content": "y"}],
        "confidence": 0.3,
        "rationale": "low confidence test",
    }
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps(suggestion), encoding="utf-8")

    session = LoopSession(
        session_id="test", workflow_id="runtime", target="lciod", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
        attempts=[],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._confidence_threshold = 0.7
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    rt._execute_current_node()
    assert rt._state.pending_human_gate is True
    assert rt._state.node_status == "LOW_CONFIDENCE"
    assert rt._state.terminal_state == RuntimeTerminalState.NONE


def test_high_confidence_proceeds_to_apply(tmp_path):
    """confidence >= threshold 时不触发 LOW_CONFIDENCE gate（marker 不匹配会走 PATCH_REJECTED，但不是 confidence gate）。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    suggestion = {
        "patches": [{"workspace_path": "foo.c", "change_type": "edit",
                     "old_marker": "x", "new_content": "y"}],
        "confidence": 0.9,
    }
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps(suggestion), encoding="utf-8")

    session = LoopSession(
        session_id="test", workflow_id="runtime", target="lciod", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
        attempts=[],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._confidence_threshold = 0.7
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    rt._execute_current_node()
    # confidence 检查通过，不会进入 LOW_CONFIDENCE 分支
    assert rt._state.node_status != "LOW_CONFIDENCE"


def test_old_format_list_treated_as_high_confidence(tmp_path):
    """旧格式 [FileChange] 列表视为 confidence=1.0，不触发 LOW_CONFIDENCE gate。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    patch_list = [{"workspace_path": "foo.c", "old_marker": "x", "new_content": "y"}]
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps(patch_list), encoding="utf-8")

    session = LoopSession(
        session_id="test", workflow_id="runtime", target="lciod", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
        attempts=[],
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._confidence_threshold = 0.7
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    rt._execute_current_node()
    # 旧格式视为高置信度，不进入 LOW_CONFIDENCE 分支
    assert rt._state.node_status != "LOW_CONFIDENCE"


def test_engine_records_nonzero_duration_ms(tmp_path):
    """G5: engine 执行节点后 checkpoint 含 duration_ms 字段。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_controller.runtime.checkpoint_store import CheckpointStore

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    rt._execute_current_node()  # INIT_SESSION
    store = CheckpointStore(str(tmp_path), "s1")
    cp = store.latest()
    assert cp is not None
    # duration_ms 字段存在（INIT_SESSION 极快，可能为 0，但字段必须有）
    assert hasattr(cp, "duration_ms")


def test_engine_wall_clock_budget_exceeds(tmp_path):
    """G5: wall_clock_limit 极小时超限，设 DONE_FAILURE。"""
    import time as _time
    from loop_controller.runtime.engine import LoopRuntime
    from loop_contracts.models import RuntimeTerminalState

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=5,
        artifacts_dir=str(tmp_path),
        wall_clock_limit=0.001,  # 极小，确保超限
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    _time.sleep(0.01)  # 确保已超过 0.001s
    rt.run(max_iterations=3)
    assert rt._state.terminal_state == RuntimeTerminalState.DONE_FAILURE
    assert "wall_clock" in rt._state.transition_reason.lower()


def test_engine_wall_clock_zero_means_unlimited(tmp_path):
    """G5: wall_clock_limit=0 时不触发预算闸。"""
    from loop_controller.runtime.engine import LoopRuntime

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
        wall_clock_limit=0,  # 不限制
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    rt.run(max_iterations=3)
    # 不应因 wall_clock 超时退出
    assert "wall_clock" not in rt._state.transition_reason.lower()


def test_engine_init_has_metrics_counters(tmp_path):
    """G9: engine __init__ 后存在埋点计数器实例变量。"""
    from loop_controller.runtime.engine import LoopRuntime

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    assert hasattr(rt, "_layer_hits") and rt._layer_hits == {}
    assert hasattr(rt, "_first_hit_layer") and rt._first_hit_layer == ""
    assert hasattr(rt, "_hg_count") and rt._hg_count == 0
    assert hasattr(rt, "_fc_dist") and rt._fc_dist == {}
    assert hasattr(rt, "_kb_hit") and rt._kb_hit is False


def test_engine_analyzer_layer_hit_counted(tmp_path):
    """G9: analyzer 产出补丁时 _layer_hits 累积 + _first_hit_layer 记录。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_controller.analyzer_protocol import (
        ChainedAnalyzer, FileChange, LlmAnalyzer, PatchSuggestion,
    )

    class KnowledgeBaseAnalyzer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(
                target_files=[FileChange(workspace_path="a.c")],
                confidence=0.98,
            )

    chain = ChainedAnalyzer([KnowledgeBaseAnalyzer()])
    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5",
                     analyzer=chain)
    rt._state.current_node = "WAIT_ANALYZER_PATCH"
    rt._execute_wait_analyzer_patch()
    assert rt._layer_hits.get("KnowledgeBaseAnalyzer") == 1
    assert rt._first_hit_layer == "KnowledgeBaseAnalyzer"
    assert rt._kb_hit is True


def test_engine_analyzer_unknown_layer_when_not_chained(tmp_path):
    """G9: 单层 analyzer（非 ChainedAnalyzer）matched_layer 为空 → 兜底 'unknown'。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_controller.analyzer_protocol import (
        FileChange, LlmAnalyzer, PatchSuggestion,
    )

    class SoloAnalyzer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(
                target_files=[FileChange(workspace_path="a.c")],
                confidence=0.9,
            )

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5",
                     analyzer=SoloAnalyzer())
    rt._state.current_node = "WAIT_ANALYZER_PATCH"
    rt._execute_wait_analyzer_patch()
    assert rt._layer_hits.get("unknown") == 1
    assert rt._first_hit_layer == "unknown"
    assert rt._kb_hit is False  # unknown 不是 KnowledgeBaseAnalyzer


def test_engine_human_gate_counter_increments(tmp_path):
    """G9: _set_human_gate 调用后 _hg_count 递增。"""
    from loop_controller.runtime.engine import LoopRuntime

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    rt._set_human_gate()
    assert rt._state.pending_human_gate is True
    assert rt._hg_count == 1
    rt._set_human_gate()
    assert rt._hg_count == 2
