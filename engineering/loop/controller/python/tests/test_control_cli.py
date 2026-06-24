import io
import json
import subprocess
import sys
from pathlib import Path


def _make_session(artifacts_dir: Path, **overrides) -> dict:
    session = {
        "session_id": "test-session",
        "artifacts_dir": str(artifacts_dir),
        "current_attempt": 0,
        "max_attempts": 5,
        "status": "PENDING",
        "attempts": [],
    }
    session.update(overrides)
    return session


def _run_verify_args(session_path: Path) -> "argparse.Namespace":
    import argparse
    return argparse.Namespace(
        session=str(session_path),
        suite="test.yaml",
        adb_endpoint="",
    )


def _decide_args(session_path: Path) -> "argparse.Namespace":
    import argparse
    return argparse.Namespace(session=str(session_path))


def _capture_stdout(fn, *args, **kwargs):
    captured = io.StringIO()
    old = sys.stdout
    sys.stdout = captured
    try:
        rc = fn(*args, **kwargs)
    finally:
        sys.stdout = old
    return rc, captured.getvalue()


def test_g1_evidence_path_uses_bundle(tmp_path: Path, monkeypatch):
    """run-verify 后 session 记录的 evidence_path 应指向 evidence_bundle.json。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(artifacts)
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    bundle_data = {
        "bundle_id": "test",
        "summary": {"overall": "PASS", "total": 1, "passed": 1, "failed": 0, "skipped": 0},
        "cases": [{"id": "test.case", "status": "pass", "command": "echo ok"}],
    }

    def fake_run(cmd, **kwargs):
        for i, arg in enumerate(cmd):
            if arg == "--artifacts-dir" and i + 1 < len(cmd):
                Path(cmd[i + 1], "evidence_bundle.json").write_text(
                    json.dumps(bundle_data), encoding="utf-8"
                )
                break
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    from loop_controller.control_cli import _handle_control_run_verify
    rc, _ = _capture_stdout(_handle_control_run_verify, _run_verify_args(artifacts / "session.json"))
    assert rc == 0

    loaded = json.loads((artifacts / "session.json").read_text(encoding="utf-8"))
    last_attempt = loaded["attempts"][-1]
    assert "evidence_bundle.json" in last_attempt["evidence_path"]


def test_g2_extract_failed_cases(tmp_path: Path, monkeypatch):
    """run-verify 后 session 应含 failed_cases（从 evidence_bundle 提取）。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(artifacts)
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    bundle_data = {
        "summary": {"overall": "FAIL", "total": 2, "passed": 1, "failed": 1, "skipped": 0},
        "cases": [
            {"id": "case.ok", "status": "pass", "command": "echo ok"},
            {"id": "case.fail", "status": "fail", "command": "echo bad",
             "failure_reason": "output mismatch"},
        ],
    }

    def fake_run(cmd, **kwargs):
        for i, arg in enumerate(cmd):
            if arg == "--artifacts-dir" and i + 1 < len(cmd):
                Path(cmd[i + 1], "evidence_bundle.json").write_text(
                    json.dumps(bundle_data), encoding="utf-8"
                )
                break
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(subprocess, "run", fake_run)

    from loop_controller.control_cli import _handle_control_run_verify
    _capture_stdout(_handle_control_run_verify, _run_verify_args(artifacts / "session.json"))

    loaded = json.loads((artifacts / "session.json").read_text(encoding="utf-8"))
    last = loaded["attempts"][-1]
    assert len(last["failed_cases"]) == 1
    assert last["failed_cases"][0]["id"] == "case.fail"
    assert last["failure_code"] == "RUN_FAILED"


def test_g2_analyze_request_from_session(tmp_path: Path):
    """analyze-request 从 session 的 failed_cases 构造请求。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(
        artifacts,
        current_attempt=1,
        status="FAIL",
        attempts=[{
            "attempt_index": 1,
            "verify_result": "FAIL",
            "evidence_path": str(artifacts / "evidence_bundle.json"),
            "failed_cases": [{"id": "case.fail", "status": "fail",
                              "failure_reason": "x", "command": "y"}],
            "failure_code": "RUN_FAILED",
        }],
    )
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")
    bundle = {"cases": [], "evidence": {"dmesg": {"commands": ["dmesg"], "hints": "ok"}}}
    (artifacts / "evidence_bundle.json").write_text(json.dumps(bundle), encoding="utf-8")

    from loop_controller.control_cli import _handle_control_analyze_request
    rc, _ = _capture_stdout(_handle_control_analyze_request, _decide_args(artifacts / "session.json"))
    assert rc == 0

    req = json.loads((artifacts / "analysis_request.json").read_text(encoding="utf-8"))
    assert len(req["failed_cases"]) == 1
    assert req["failed_cases"][0]["id"] == "case.fail"
    assert req["evidence_bundle_path"].endswith("evidence_bundle.json")
    assert "dmesg" in req["collectors_output"]


def test_g5_decide_pass(tmp_path: Path):
    """decide: PASS -> STOP verification_passed。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(
        artifacts, current_attempt=1, status="PASS",
        attempts=[{"attempt_index": 1, "verify_result": "PASS",
                   "evidence_path": "", "failed_cases": [], "failure_code": ""}],
    )
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    from loop_controller.control_cli import _handle_control_decide
    _, out = _capture_stdout(_handle_control_decide, _decide_args(artifacts / "session.json"))
    assert "decision=STOP" in out
    assert "verification_passed" in out


def test_g5_decide_retry_on_fail(tmp_path: Path):
    """decide: FAIL attempt 1/5 -> RETRY。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(
        artifacts, current_attempt=1, status="FAIL",
        attempts=[{"attempt_index": 1, "verify_result": "FAIL",
                   "evidence_path": "", "failed_cases": [{"id": "x"}],
                   "failure_code": "RUN_FAILED"}],
    )
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    from loop_controller.control_cli import _handle_control_decide
    _, out = _capture_stdout(_handle_control_decide, _decide_args(artifacts / "session.json"))
    assert "decision=RETRY" in out


def test_g5_decide_stop_on_max_attempts(tmp_path: Path):
    """decide: attempt 6/5 -> STOP escalate。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(
        artifacts, current_attempt=6, status="FAIL",
        attempts=[{"attempt_index": 6, "verify_result": "FAIL",
                   "evidence_path": "", "failed_cases": [], "failure_code": "RUN_FAILED"}],
    )
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    from loop_controller.control_cli import _handle_control_decide
    _, out = _capture_stdout(_handle_control_decide, _decide_args(artifacts / "session.json"))
    assert "decision=STOP" in out
    assert "escalate=true" in out


def test_g5_decide_stop_on_repeated_failure(tmp_path: Path):
    """decide: 连续两次同 failure_code -> STOP escalate。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(
        artifacts, current_attempt=2, status="FAIL",
        attempts=[
            {"attempt_index": 1, "verify_result": "FAIL",
             "evidence_path": "", "failed_cases": [], "failure_code": "RUN_FAILED"},
            {"attempt_index": 2, "verify_result": "FAIL",
             "evidence_path": "", "failed_cases": [], "failure_code": "RUN_FAILED"},
        ],
    )
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    from loop_controller.control_cli import _handle_control_decide
    _, out = _capture_stdout(_handle_control_decide, _decide_args(artifacts / "session.json"))
    assert "decision=STOP" in out
    assert "same_failure_repeated" in out


def test_g5_decide_stop_on_duplicate_patch(tmp_path: Path):
    """decide: 当前 patch_hash 与之前相同 → STOP escalate。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(
        artifacts, current_attempt=2, status="FAIL",
        attempts=[
            {"attempt_index": 1, "verify_result": "FAIL",
             "evidence_path": "", "failed_cases": [], "failure_code": "COMPILE_FAILED",
             "patch_applied": {"patch_hash": "abc123def456"}},
            {"attempt_index": 2, "verify_result": "FAIL",
             "evidence_path": "", "failed_cases": [], "failure_code": "RUN_FAILED",
             "patch_applied": {"patch_hash": "abc123def456"}},
        ],
    )
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    from loop_controller.control_cli import _handle_control_decide
    _, out = _capture_stdout(_handle_control_decide, _decide_args(artifacts / "session.json"))
    assert "decision=STOP" in out
    assert "duplicate_patch" in out
    assert "escalate=true" in out


def test_g5_decide_retry_different_patch(tmp_path: Path):
    """decide: 不同 patch_hash 且不同 failure_code → RETRY（不误杀）。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(
        artifacts, current_attempt=2, status="FAIL",
        attempts=[
            {"attempt_index": 1, "verify_result": "FAIL",
             "evidence_path": "", "failed_cases": [], "failure_code": "COMPILE_FAILED",
             "patch_applied": {"patch_hash": "aaa111"}},
            {"attempt_index": 2, "verify_result": "FAIL",
             "evidence_path": "", "failed_cases": [], "failure_code": "RUN_FAILED",
             "patch_applied": {"patch_hash": "bbb222"}},
        ],
    )
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    from loop_controller.control_cli import _handle_control_decide
    _, out = _capture_stdout(_handle_control_decide, _decide_args(artifacts / "session.json"))
    assert "decision=RETRY" in out


def test_apply_patch_rejects_outside_whitelist(tmp_path: Path):
    """apply-patch 拒绝白名单外的文件。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = {
        "session_id": "ap-test", "artifacts_dir": str(artifacts),
        "target": "lciod", "current_attempt": 1, "max_attempts": 5,
        "status": "FAIL", "attempts": [],
    }
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    patch_data = [{"workspace_path": "vendor/other/foo.cpp", "change_type": "edit",
                   "old_marker": "x", "new_content": "y"}]
    patch_file = artifacts / "patch.json"
    patch_file.write_text(json.dumps(patch_data), encoding="utf-8")

    import argparse
    from loop_controller.control_cli import _handle_control_apply_patch, _load_target_paths
    allowed_prefixes = _load_target_paths("lciod")
    assert "vendor/lechao/services/lechao_lciod/" in allowed_prefixes

    args = argparse.Namespace(
        session=str(artifacts / "session.json"),
        patch=str(patch_file),
        workspace_root="",
    )
    rc = _handle_control_apply_patch(args)
    assert rc == 1


def test_apply_patch_success(tmp_path: Path, monkeypatch):
    """apply-patch 成功应用白名单内补丁。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = {
        "session_id": "ap-ok", "artifacts_dir": str(artifacts),
        "target": "lciod", "current_attempt": 1, "max_attempts": 5,
        "status": "FAIL", "attempts": [],
    }
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    target_file = tmp_path / "test.cpp"
    target_file.write_text("int x = 1;\n", encoding="utf-8")

    patch_data = [{"workspace_path": "test.cpp", "change_type": "edit",
                   "old_marker": "int x = 1;", "new_content": "int x = 42;"}]
    patch_file = artifacts / "patch.json"
    patch_file.write_text(json.dumps(patch_data), encoding="utf-8")

    monkeypatch.setattr("loop_controller.control_cli._load_target_paths", lambda target: [""])

    import argparse
    from loop_controller.control_cli import _handle_control_apply_patch
    args = argparse.Namespace(
        session=str(artifacts / "session.json"),
        patch=str(patch_file),
        workspace_root=str(tmp_path),
    )
    rc = _handle_control_apply_patch(args)
    assert rc == 0

    assert "int x = 42;" in target_file.read_text()

    loaded = json.loads((artifacts / "session.json").read_text(encoding="utf-8"))
    last = loaded["attempts"][-1]
    assert "patch_applied" in last
    assert "test.cpp" in last["patch_applied"]["files"]
    assert "patch_hash" in last["patch_applied"]


def test_apply_patch_patch_file_not_found(tmp_path: Path, monkeypatch):
    """apply-patch 补丁文件不存在时返回 1。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = {"session_id": "ap-nf", "artifacts_dir": str(artifacts),
               "target": "lciod", "current_attempt": 1, "max_attempts": 5, "attempts": []}
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    monkeypatch.setattr("loop_controller.control_cli._load_target_paths", lambda target: [""])

    import argparse
    from loop_controller.control_cli import _handle_control_apply_patch
    args = argparse.Namespace(
        session=str(artifacts / "session.json"),
        patch=str(artifacts / "no_such_patch.json"),
        workspace_root=str(tmp_path),
    )
    rc = _handle_control_apply_patch(args)
    assert rc == 1


def test_apply_patch_invalid_json(tmp_path: Path, monkeypatch):
    """apply-patch 补丁文件 JSON 非法时返回 1。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = {"session_id": "ap-bad", "artifacts_dir": str(artifacts),
               "target": "lciod", "current_attempt": 1, "max_attempts": 5, "attempts": []}
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")
    (artifacts / "patch.json").write_text("{not valid json", encoding="utf-8")

    monkeypatch.setattr("loop_controller.control_cli._load_target_paths", lambda target: [""])

    import argparse
    from loop_controller.control_cli import _handle_control_apply_patch
    args = argparse.Namespace(
        session=str(artifacts / "session.json"),
        patch=str(artifacts / "patch.json"),
        workspace_root=str(tmp_path),
    )
    rc = _handle_control_apply_patch(args)
    assert rc == 1


def test_revert_no_attempts(tmp_path: Path):
    """revert 无 attempt 时返回 1。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = {"session_id": "rv-test", "artifacts_dir": str(artifacts),
               "current_attempt": 0, "max_attempts": 5, "attempts": []}
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    import argparse
    from loop_controller.control_cli import _handle_control_revert
    rc = _handle_control_revert(argparse.Namespace(session=str(artifacts / "session.json")))
    assert rc == 1


def test_revert_no_stash_ref(tmp_path: Path):
    """revert 无 stash_ref 时返回 1。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = {"session_id": "rv-test2", "artifacts_dir": str(artifacts),
               "current_attempt": 1, "max_attempts": 5,
               "attempts": [{"attempt_index": 1, "patch_applied": {"stash_ref": ""}}]}
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    import argparse
    from loop_controller.control_cli import _handle_control_revert
    rc = _handle_control_revert(argparse.Namespace(session=str(artifacts / "session.json")))
    assert rc == 1


# ---------------------------------------------------------------------------
# D2: compile SKIP 降级修正
# ---------------------------------------------------------------------------

def _compile_args(session_path: Path) -> "argparse.Namespace":
    import argparse
    return argparse.Namespace(session=str(session_path), workspace_root="")


def test_compile_skip_stays_skip_for_docs_only(tmp_path: Path, monkeypatch):
    """compile: diff 全是 .md 文件时保持 SKIP，不误触发编译。"""
    import loop_deploy.models as dm
    from loop_deploy.models import CompileResult

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(artifacts, current_attempt=1, status="FAIL")
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    monkeypatch.setattr("loop_deploy.decider.get_diff_files", lambda rev="HEAD": ["docs/foo.md"])
    monkeypatch.setattr(
        "loop_deploy.compiler.compile_plan",
        lambda plan, ws="": CompileResult(success=True, artifacts=[]),
    )
    monkeypatch.setattr("os.environ.get", lambda *a, **kw: "/tmp")

    from loop_controller.control_cli import _handle_control_compile
    rc, _ = _capture_stdout(_handle_control_compile, _compile_args(artifacts / "session.json"))
    assert rc == 0

    loaded = json.loads((artifacts / "session.json").read_text(encoding="utf-8"))
    last = loaded["attempts"][-1]
    assert last["compile_result"] == "SUCCESS"


def test_compile_skip_downgrades_for_code(tmp_path: Path, monkeypatch):
    """compile: diff 含 .cpp 文件时 SKIP 降级为 PUSH_SINGLE。"""
    import loop_deploy.models as dm
    from loop_deploy.models import CompileResult

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(artifacts, current_attempt=1, status="FAIL")
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    monkeypatch.setattr(
        "loop_deploy.decider.get_diff_files",
        lambda rev="HEAD": ["docs/foo.md", "vendor/bar/hello.cpp"],
    )
    monkeypatch.setattr(
        "loop_deploy.compiler.compile_plan",
        lambda plan, ws="": CompileResult(success=True, artifacts=[]),
    )
    monkeypatch.setattr("os.environ.get", lambda *a, **kw: "/tmp")

    from loop_controller.control_cli import _handle_control_compile
    rc, _ = _capture_stdout(_handle_control_compile, _compile_args(artifacts / "session.json"))
    assert rc == 0


# ---------------------------------------------------------------------------
# A1: deploy 子命令闭环（写 session + 异常处理）
# ---------------------------------------------------------------------------

def _deploy_args(session_path: Path, adb_endpoint: str = "") -> "argparse.Namespace":
    import argparse
    return argparse.Namespace(
        session=str(session_path),
        adb_endpoint=adb_endpoint,
    )


def test_deploy_writes_session_on_success(tmp_path: Path, monkeypatch):
    """deploy 成功时 session 应追加 deploy_result=SUCCESS 的 attempt。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(artifacts, current_attempt=1, status="FAIL")
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0] if a else [], 0, stdout="DEPLOY OK", stderr=""))

    from loop_controller.control_cli import _handle_control_deploy
    rc, _ = _capture_stdout(_handle_control_deploy, _deploy_args(artifacts / "session.json"))
    assert rc == 0

    loaded = json.loads((artifacts / "session.json").read_text(encoding="utf-8"))
    last = loaded["attempts"][-1]
    assert last["deploy_result"] == "SUCCESS"
    assert last["failure_code"] == ""
    assert last["verify_result"] == "DEPLOYED"


def test_deploy_writes_session_on_failure(tmp_path: Path, monkeypatch):
    """deploy 失败时 session 应追加 failure_code=DEPLOY_FATAL。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(artifacts, current_attempt=1, status="FAIL")
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
        a[0] if a else [], 1, stdout="", stderr="DEPLOY FAILED: push error"))

    from loop_controller.control_cli import _handle_control_deploy
    rc, _ = _capture_stdout(_handle_control_deploy, _deploy_args(artifacts / "session.json"))
    assert rc == 1

    loaded = json.loads((artifacts / "session.json").read_text(encoding="utf-8"))
    last = loaded["attempts"][-1]
    assert last["deploy_result"] == "FAILED"
    assert last["failure_code"] == "DEPLOY_FATAL"
    assert "push error" in last["deploy_error"]


def test_deploy_handles_timeout(tmp_path: Path, monkeypatch):
    """deploy 超时时 session 应记录 deploy_error 并返回 1。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = _make_session(artifacts, current_attempt=1, status="FAIL")
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    def raise_timeout(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="deploy", timeout=3600)

    monkeypatch.setattr(subprocess, "run", raise_timeout)

    from loop_controller.control_cli import _handle_control_deploy
    rc, _ = _capture_stdout(_handle_control_deploy, _deploy_args(artifacts / "session.json"))
    assert rc == 1

    loaded = json.loads((artifacts / "session.json").read_text(encoding="utf-8"))
    last = loaded["attempts"][-1]
    assert last["deploy_result"] == "FAILED"
    assert "timed out" in last["deploy_error"]


# ---------------------------------------------------------------------------
# A2: apply-patch 回滚失败时打印 WARNING
# ---------------------------------------------------------------------------

def test_apply_patch_rollback_warns_on_failure(tmp_path: Path, monkeypatch):
    """apply-patch 失败且回滚 stash apply 也失败时，stderr 应含 WARNING。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    session = {
        "session_id": "ap-rb", "artifacts_dir": str(artifacts),
        "target": "lciod", "current_attempt": 1, "max_attempts": 5,
        "status": "FAIL", "attempts": [],
    }
    (artifacts / "session.json").write_text(json.dumps(session), encoding="utf-8")

    patch_data = [{"workspace_path": "test.cpp", "change_type": "edit",
                   "old_marker": "old", "new_content": "new"}]
    patch_file = artifacts / "patch.json"
    patch_file.write_text(json.dumps(patch_data), encoding="utf-8")

    monkeypatch.setattr("loop_controller.control_cli._load_target_paths", lambda target: [""])

    call_count = {"git_stash_apply": 0}

    def fake_git_run(cmd, **kwargs):
        if "stash" in cmd and "create" in cmd:
            return subprocess.CompletedProcess(cmd, 0, stdout="fake_stash_ref_123", stderr="")
        if "stash" in cmd and "apply" in cmd:
            call_count["git_stash_apply"] += 1
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="merge conflict")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", fake_git_run)
    monkeypatch.setattr(
        "loop_controller.patch_applier.apply_file_changes",
        lambda changes, ws_root: __import__("loop_controller.patch_applier",
                                            fromlist=["ApplyResult"]).ApplyResult(
            success=False, applied_files=[], error="old_marker not found"),
    )

    import argparse
    import io
    captured_err = io.StringIO()
    old_err = sys.stderr
    sys.stderr = captured_err
    try:
        from loop_controller.control_cli import _handle_control_apply_patch
        args = argparse.Namespace(
            session=str(artifacts / "session.json"),
            patch=str(patch_file),
            workspace_root=str(tmp_path),
        )
        rc = _handle_control_apply_patch(args)
    finally:
        sys.stderr = old_err

    assert rc == 1
    assert call_count["git_stash_apply"] == 1
    assert "WARNING" in captured_err.getvalue()
    assert "rollback failed" in captured_err.getvalue()
