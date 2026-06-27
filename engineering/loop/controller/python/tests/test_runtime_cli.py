import io
import json
import sys
from unittest.mock import MagicMock, patch

from loop_controller.runtime_cli import main as runtime_main
from loop_controller.runtime_cli import _handle_run


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


def test_runtime_resume(tmp_path, monkeypatch):
    """resume loads from checkpoint and continues to terminal state."""
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

    # checkpoint: RUN_VERIFY 已 PASS，resume 后 DECIDE_NEXT 应判 DONE_SUCCESS
    store = CheckpointStore(str(artifacts), sid)
    store.save(CheckpointRecord(
        checkpoint_id="cp-1", session_id=sid, attempt_index=1,
        current_node="RUN_VERIFY", input_summary={},
        output_summary={"node_status": "PASS"},
        failure_code=FailureCode.NONE, matched_guards=[],
        next_node="DECIDE_NEXT", timestamp="2026-06-26T12:00:00+08:00",
    ))

    rc, out = _capture(["resume", "--session", str(artifacts / f"{sid}.json")])
    # resume 续跑到终态：DECIDE_NEXT guard 匹配 all_cases_passed → DONE_SUCCESS
    assert rc == 0
    assert "DONE_SUCCESS" in out


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


def test_runtime_run_writes_session_json(tmp_path, monkeypatch):
    """run subcommand writes session.json after completion"""
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
        "summary": {"overall": "PASS", "total": 1, "passed": 1, "failed": 0, "skipped": 0},
        "cases": [],
    }), encoding="utf-8")

    rc, out = _capture(["run", "--session", str(artifacts / f"{sid}.json")])
    assert rc == 0
    session_path = artifacts / "session.json"
    assert session_path.exists()
    data = json.loads(session_path.read_text())
    assert data["session_id"] == sid
    assert "terminal_state" in data


def test_cli_resume_then_run_reaches_terminal(tmp_path):
    """CLI resume 子命令从 checkpoint 恢复后续跑到终态"""
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

    # checkpoint: RUN_VERIFY(PASS) 已完成，resume 后 DECIDE_NEXT → DONE_SUCCESS
    store = CheckpointStore(str(artifacts), sid)
    store.save(CheckpointRecord(
        checkpoint_id="cp-1", session_id=sid, attempt_index=1,
        current_node="RUN_VERIFY", input_summary={},
        output_summary={"node_status": "PASS"},
        failure_code=FailureCode.NONE, matched_guards=[],
        next_node="DECIDE_NEXT", timestamp="2026-06-26T12:00:00+08:00",
    ))

    rc, out = _capture(["resume", "--session", str(artifacts / f"{sid}.json")])
    assert rc == 0
    assert "DONE_SUCCESS" in out


def test_cli_resume_on_already_terminal_is_idempotent(tmp_path):
    """对已终态的 session 调 resume 幂等返回"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    rc, out = _capture([
        "init", "--target", "test", "--suite", "test.yaml",
        "--max-attempts", "3", "--artifacts-dir", str(artifacts),
    ])
    sid = _extract_sid(out)

    # 手动标记 session 为 DONE_SUCCESS
    session_file = artifacts / f"{sid}.json"
    data = json.loads(session_file.read_text())
    data["terminal_state"] = "DONE_SUCCESS"
    session_file.write_text(json.dumps(data), encoding="utf-8")

    rc, out = _capture(["resume", "--session", str(session_file)])
    # 幂等：直接返回，不续跑
    assert rc == 0
    assert "DONE_SUCCESS" in out


def test_cli_run_on_already_terminal_is_idempotent(tmp_path):
    """对已终态的 session 调 run 幂等返回"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    rc, out = _capture([
        "init", "--target", "test", "--suite", "test.yaml",
        "--max-attempts", "3", "--artifacts-dir", str(artifacts),
    ])
    sid = _extract_sid(out)

    # 手动标记 session 为 ESCALATE_HUMAN
    session_file = artifacts / f"{sid}.json"
    data = json.loads(session_file.read_text())
    data["terminal_state"] = "ESCALATE_HUMAN"
    session_file.write_text(json.dumps(data), encoding="utf-8")

    rc, out = _capture(["run", "--session", str(session_file)])
    # 幂等：非 SUCCESS 终态返回 rc=1
    assert rc == 1
    assert "ESCALATE_HUMAN" in out


# ---------------------------------------------------------------------------
# Task 5: ChainedAnalyzer 注入
# ---------------------------------------------------------------------------
def test_run_injects_chained_analyzer(tmp_path):
    session = tmp_path / "session.json"
    session_data = {
        "session_id": "test-s1", "workflow_id": "runtime", "target": "lciod",
        "suite": "features.lciod.common", "max_attempts": 1, "current_attempt": 0,
        "status": "PENDING", "latest_failure_code": "NONE", "attempts": [],
        "artifacts_dir": str(tmp_path),
    }
    session.write_text(json.dumps(session_data), encoding="utf-8")

    captured = {}

    def fake_init(self, *args, **kwargs):
        captured["analyzer"] = kwargs.get("analyzer")
        from loop_contracts.models import RuntimeTerminalState
        self._state = MagicMock(terminal_state=RuntimeTerminalState.DONE_SUCCESS)
        self.run = lambda max_iterations=100: self._state

    with patch("loop_controller.runtime.engine.LoopRuntime.__init__", fake_init):
        args = MagicMock(session=str(session), adb_endpoint="")
        _handle_run(args)
    assert captured["analyzer"] is not None
