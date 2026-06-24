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
