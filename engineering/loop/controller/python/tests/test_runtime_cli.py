import io
import json
import sys

from loop_controller.runtime_cli import main as runtime_main


def _capture(argv):
    captured = io.StringIO()
    old = sys.stdout
    sys.stdout = captured
    try:
        rc = runtime_main(argv)
    finally:
        sys.stdout = old
    return rc, captured.getvalue()


def _extract_sid(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("session_id="):
            return line.split("=", 1)[1].strip()
    return ""


def test_runtime_init_creates_session(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    rc, out = _capture([
        "init", "--target", "lciod",
        "--suite", "engineering/loop/cases/features/lciod/hal.yaml",
        "--max-attempts", "3",
        "--artifacts-dir", str(artifacts),
    ])
    assert rc == 0
    assert "session_id=" in out
    sid = _extract_sid(out)
    assert sid
    session_file = artifacts / f"{sid}.json"
    assert session_file.exists()
    data = json.loads(session_file.read_text())
    assert data["target"] == "lciod"
    assert data["max_attempts"] == 3


def test_runtime_run_pass_path(tmp_path, monkeypatch):
    """Runtime run with PASS verify -> DONE_SUCCESS."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    rc, out = _capture([
        "init", "--target", "test", "--suite", "test.yaml",
        "--max-attempts", "3", "--artifacts-dir", str(artifacts),
    ])
    sid = _extract_sid(out)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    (artifacts / "evidence_bundle.json").write_text(json.dumps({
        "summary": {
            "overall": "PASS", "total": 1, "passed": 1, "failed": 0, "skipped": 0,
        },
        "cases": [],
    }), encoding="utf-8")

    rc, out = _capture(["run", "--session", str(artifacts / f"{sid}.json")])
    assert rc == 0
    assert "DONE_SUCCESS" in out


def test_runtime_run_escalate_on_max(tmp_path, monkeypatch):
    """Runtime run with FAIL and max_attempts=1 -> ESCALATE_HUMAN."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    rc, out = _capture([
        "init", "--target", "test", "--suite", "test.yaml",
        "--max-attempts", "1", "--artifacts-dir", str(artifacts),
    ])
    sid = _extract_sid(out)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 1
            stdout = ""
            stderr = ""

        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    (artifacts / "evidence_bundle.json").write_text(json.dumps({
        "summary": {
            "overall": "FAIL", "total": 1, "passed": 0, "failed": 1, "skipped": 0,
        },
        "cases": [
            {
                "id": "case.fail", "status": "fail",
                "failure_reason": "boom", "command": "echo boom",
            }
        ],
    }), encoding="utf-8")

    rc, out = _capture(["run", "--session", str(artifacts / f"{sid}.json")])
    assert rc == 1
    assert "ESCALATE_HUMAN" in out


def test_runtime_resume(tmp_path):
    """resume loads from checkpoint."""
    from loop_controller.runtime.checkpoint_store import CheckpointStore
    from loop_contracts.models import CheckpointRecord
    from loop_contracts.failure_codes import FailureCode

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    rc, out = _capture([
        "init", "--target", "test", "--suite", "test.yaml",
        "--max-attempts", "3", "--artifacts-dir", str(artifacts),
    ])
    sid = _extract_sid(out)

    store = CheckpointStore(str(artifacts), sid)
    store.save(CheckpointRecord(
        checkpoint_id="cp-1", session_id=sid, attempt_index=1,
        current_node="RUN_VERIFY", input_summary={}, output_summary={},
        failure_code=FailureCode.NONE, matched_guards=[],
        next_node="DECIDE_NEXT", timestamp="2026-06-26T12:00:00+08:00",
    ))

    rc, out = _capture(["resume", "--session", str(artifacts / f"{sid}.json")])
    assert rc == 0
    assert "DECIDE_NEXT" in out


def test_runtime_status(tmp_path):
    """status shows session JSON."""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    rc, out = _capture([
        "init", "--target", "test", "--suite", "test.yaml",
        "--max-attempts", "3", "--artifacts-dir", str(artifacts),
    ])
    sid = _extract_sid(out)

    rc, out = _capture(["status", "--session", str(artifacts / f"{sid}.json")])
    assert rc == 0
    data = json.loads(out)
    assert data["session_id"] == sid


def test_runtime_explain():
    """explain outputs state machine description."""
    rc, out = _capture(["explain"])
    assert rc == 0
    assert "INIT_SESSION" in out
    assert "DONE_SUCCESS" in out
