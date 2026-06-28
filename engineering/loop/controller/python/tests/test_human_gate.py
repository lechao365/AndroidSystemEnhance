"""Task 9：human-in-loop 门（pending/approve/reject CLI + run 退出逻辑）。"""
import json
from pathlib import Path

from loop_controller.runtime.engine import LoopRuntime
from loop_controller.runtime.types import NodeKind
from loop_contracts.models import LoopSession, RuntimeTerminalState


def test_pending_human_gate_stops_run_loop(tmp_path):
    """pending_human_gate=True 时 run() 退出且不设终态。"""
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    suggestion = {
        "patches": [{"workspace_path": "f.c", "old_marker": "x", "new_content": "y"}],
        "confidence": 0.3,
    }
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps(suggestion), encoding="utf-8")
    session = LoopSession(
        session_id="test", workflow_id="runtime", target="lciod", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="dummy")
    rt._confidence_threshold = 0.7
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    rt.run(max_iterations=1)
    assert rt._state.pending_human_gate is True
    assert rt._state.terminal_state == RuntimeTerminalState.NONE


def test_pending_command_shows_gate_info(tmp_path):
    """le runtime pending 显示待确认信息。"""
    from loop_controller.runtime_cli import _handle_pending
    session_data = {
        "session_id": "s1", "current_node": "APPLY_PATCH",
        "node_status": "LOW_CONFIDENCE", "pending_human_gate": True,
        "artifacts_dir": str(tmp_path),
    }
    sp = tmp_path / "session.json"
    sp.write_text(json.dumps(session_data), encoding="utf-8")
    args = type("A", (), {"session": str(sp)})()
    rc = _handle_pending(args)
    assert rc == 0


def test_compute_next_node_human_gate_returns_apply_patch(tmp_path):
    """human gate 暂停（APPLY_PATCH + LOW_CONFIDENCE）时 next_node 应为 APPLY_PATCH 自身，
    使 approve 后 resume 回到 APPLY_PATCH 重新执行真正 apply（修复：原先返回 COMPILE_PATCH 跳过 apply）。"""
    artifacts = tmp_path / "a"
    artifacts.mkdir()
    session = LoopSession(
        session_id="t", workflow_id="runtime", target="x", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="d")
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    rt._state.node_status = "LOW_CONFIDENCE"
    assert rt._compute_next_node() == NodeKind.APPLY_PATCH.value


def test_apply_patch_skips_gate_when_approved(tmp_path):
    """human_gate_approved=True 时，低置信补丁不再触发 gate（人工已确认，继续 apply）。"""
    artifacts = tmp_path / "a"
    artifacts.mkdir()
    suggestion = {
        "patches": [{"workspace_path": "f.c", "old_marker": "x", "new_content": "y"}],
        "confidence": 0.3,
    }
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps(suggestion), encoding="utf-8")
    session = LoopSession(
        session_id="t2", workflow_id="runtime", target="lcview", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="d")
    rt._confidence_threshold = 0.7
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    rt._state.human_gate_approved = True
    rt.run(max_iterations=1)
    # 核心：approve 后不再走 LOW_CONFIDENCE gate，而是继续执行 apply 逻辑
    assert rt._state.node_status != "LOW_CONFIDENCE"
    # 一次性 approve 标记已被消费
    assert rt._state.human_gate_approved is False


def test_reject_sets_escalate_terminal(tmp_path):
    """le runtime reject 设终态 ESCALATE_HUMAN。"""
    from loop_controller.runtime_cli import _handle_reject
    session_data = {
        "session_id": "s1", "pending_human_gate": True,
        "artifacts_dir": str(tmp_path),
    }
    sp = tmp_path / "session.json"
    sp.write_text(json.dumps(session_data), encoding="utf-8")
    args = type("A", (), {"session": str(sp)})()
    rc = _handle_reject(args)
    assert rc == 1
    data = json.loads(sp.read_text())
    assert data["terminal_state"] == "ESCALATE_HUMAN"
