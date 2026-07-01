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


def test_status_output_includes_trace_summary(tmp_path, capsys):
    """G5: le runtime status 输出含 trace_summary 段。"""
    from loop_controller.runtime_cli import _handle_status

    session_data = {
        "session_id": "s1",
        "workflow_id": "runtime",
        "target": "lciod",
        "suite": "hal",
        "max_attempts": 5,
        "current_attempt": 0,
        "status": "PENDING",
        "latest_failure_code": "NONE",
        "attempts": [],
        "artifacts_dir": str(tmp_path),
        "wall_clock_limit": 0,
    }
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps(session_data), encoding="utf-8")

    args = MagicMock()
    args.session = str(session_path)
    _handle_status(args)

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert "trace_summary" in output
    assert output["trace_summary"]["node_count"] == 0
    # wall_clock_limit 也应在输出中
    assert output["wall_clock_limit"] == 0


def test_status_outputs_metrics(tmp_path, capsys):
    """G9: le runtime status 输出含 metrics 段。"""
    from loop_controller.runtime_cli import _handle_status

    metrics_dict = {
        "success": True, "terminal_state": "DONE_SUCCESS",
        "attempt_count": 2, "wall_clock_used_ms": 5000,
        "wall_clock_budget_ms": 3600000,
        "analyzer_layer_hits": {"KnowledgeBaseAnalyzer": 1},
        "analyzer_first_hit_layer": "KnowledgeBaseAnalyzer",
        "failure_code_distribution": {"RUN_FAILED": 1, "NONE": 2},
        "human_gate_triggered": False, "human_gate_count": 0,
        "kb_hit": True,
    }
    session_data = {
        "session_id": "s1", "workflow_id": "runtime",
        "target": "lciod", "suite": "hal",
        "max_attempts": 5, "current_attempt": 2,
        "status": "DONE", "latest_failure_code": "NONE",
        "attempts": [], "artifacts_dir": str(tmp_path),
        "wall_clock_limit": 3600,
        "terminal_state": "DONE_SUCCESS",
        "metrics": metrics_dict,
    }
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps(session_data), encoding="utf-8")

    args = MagicMock()
    args.session = str(session_path)
    _handle_status(args)

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert "metrics" in output
    assert output["metrics"]["success"] is True
    assert output["metrics"]["attempt_count"] == 2


def test_load_session_handles_missing_metrics(tmp_path):
    """G9: 旧 session.json 无 metrics 段时 _load_session 不报错。"""
    from loop_controller.runtime_cli import _load_session

    session_data = {
        "session_id": "s1", "workflow_id": "runtime",
        "target": "lciod", "suite": "hal",
        "max_attempts": 5, "current_attempt": 0,
        "status": "PENDING", "latest_failure_code": "NONE",
        "attempts": [], "artifacts_dir": str(tmp_path),
        "wall_clock_limit": 0,
    }
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps(session_data), encoding="utf-8")

    session, ts = _load_session(str(session_path))
    assert session.metrics is None


def _make_session_json(path, metrics=None, target="lciod", suite="hal",
                      terminal="DONE_SUCCESS", attempt_count=1,
                      wall_used=10000):
    """辅助：构造一个 session.json 文件。"""
    data = {
        "session_id": path.stem, "workflow_id": "runtime",
        "target": target, "suite": suite,
        "max_attempts": 5, "current_attempt": attempt_count,
        "status": terminal, "latest_failure_code": "NONE",
        "attempts": [], "artifacts_dir": str(path.parent),
        "wall_clock_limit": 3600,
        "terminal_state": terminal,
    }
    if metrics is not None:
        data["metrics"] = metrics
    path.write_text(json.dumps(data), encoding="utf-8")


def test_stats_command_no_sessions(tmp_path, capsys):
    """G9: 空目录输出 total=0。"""
    from loop_controller.runtime_cli import _handle_stats
    args = MagicMock()
    args.artifacts_dir = str(tmp_path)
    rc = _handle_stats(args)
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["total"] == 0
    assert rc == 0


def test_stats_command_aggregates(tmp_path, capsys):
    """G9: 聚合 3 个 session（2 成功 1 失败），验证 success_rate。"""
    from loop_controller.runtime_cli import _handle_stats
    for i, (success, target) in enumerate([
        (True, "lciod"), (True, "lcview"), (False, "kernel"),
    ]):
        sd = tmp_path / f"session-{i}"
        sd.mkdir()
        terminal = "DONE_SUCCESS" if success else "DONE_FAILURE"
        metrics = {
            "success": success, "terminal_state": terminal,
            "attempt_count": 1 if success else 3,
            "wall_clock_used_ms": 10000 + i * 1000,
            "wall_clock_budget_ms": 3600000,
            "analyzer_layer_hits": {"KnowledgeBaseAnalyzer": 1} if success else {},
            "analyzer_first_hit_layer": "KnowledgeBaseAnalyzer" if success else "",
            "failure_code_distribution": {"RUN_FAILED": 2},
            "human_gate_triggered": False, "human_gate_count": 0,
            "kb_hit": success,
        }
        _make_session_json(sd / "session.json", metrics=metrics,
                           target=target, terminal=terminal,
                           attempt_count=1 if success else 3)
    args = MagicMock()
    args.artifacts_dir = str(tmp_path)
    _handle_stats(args)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["total_sessions"] == 3
    assert out["success_count"] == 2
    assert abs(out["success_rate"] - 0.67) < 0.01
    assert "lciod" in out["by_target"]
    assert "kernel" in out["by_target"]
    assert out["by_target"]["kernel"]["success"] == 0


def test_stats_command_skips_no_metrics(tmp_path, capsys):
    """G9: 无 metrics 段的 session 被跳过。"""
    from loop_controller.runtime_cli import _handle_stats
    sd = tmp_path / "session-1"
    sd.mkdir()
    _make_session_json(sd / "session.json", metrics=None)
    args = MagicMock()
    args.artifacts_dir = str(tmp_path)
    _handle_stats(args)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out.get("total_sessions", 0) == 0 or out.get("total", 0) == 0


def test_stats_command_skips_corrupted(tmp_path, capsys):
    """G9: 损坏 json 被跳过，不崩溃。"""
    from loop_controller.runtime_cli import _handle_stats
    sd = tmp_path / "session-broken"
    sd.mkdir()
    (sd / "session.json").write_text("{ not valid json", encoding="utf-8")
    args = MagicMock()
    args.artifacts_dir = str(tmp_path)
    rc = _handle_stats(args)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["total"] == 0
    assert rc == 0


def test_stats_command_median_wall_clock(tmp_path, capsys):
    """G9: 偶数 session 取中位数（中间两数均值）。"""
    from loop_controller.runtime_cli import _handle_stats
    for i, ms in enumerate([10000, 20000, 30000, 40000]):
        sd = tmp_path / f"session-{i}"
        sd.mkdir()
        metrics = {
            "success": True, "terminal_state": "DONE_SUCCESS",
            "attempt_count": 1, "wall_clock_used_ms": ms,
            "wall_clock_budget_ms": 3600000,
            "analyzer_layer_hits": {}, "analyzer_first_hit_layer": "",
            "failure_code_distribution": {},
            "human_gate_triggered": False, "human_gate_count": 0,
            "kb_hit": False,
        }
        _make_session_json(sd / "session.json", metrics=metrics, terminal="DONE_SUCCESS")
    args = MagicMock()
    args.artifacts_dir = str(tmp_path)
    _handle_stats(args)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["median_wall_clock_ms"] == 25000


def test_init_with_candidates_flag(tmp_path):
    """G2: le runtime init --candidates N 存入 session.candidates_per_attempt。"""
    import json
    from loop_controller.runtime_cli import main

    artifacts = tmp_path / "artifacts"
    ret = main([
        "init", "--target", "test", "--suite", "s.yaml",
        "--max-attempts", "5", "--artifacts-dir", str(artifacts),
        "--candidates", "3",
    ])
    assert ret == 0
    session_data = json.loads((artifacts / "session.json").read_text())
    assert session_data["candidates_per_attempt"] == 3


def test_init_default_candidates_is_1(tmp_path):
    """G2: 不传 --candidates 时默认 1（单线性）。"""
    import json
    from loop_controller.runtime_cli import main

    artifacts = tmp_path / "artifacts"
    ret = main([
        "init", "--target", "test", "--suite", "s.yaml",
        "--max-attempts", "5", "--artifacts-dir", str(artifacts),
    ])
    assert ret == 0
    session_data = json.loads((artifacts / "session.json").read_text())
    assert session_data["candidates_per_attempt"] == 1
