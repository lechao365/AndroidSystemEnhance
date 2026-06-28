"""Task 9：human-in-loop 门（pending/approve/reject CLI + run 退出逻辑）。"""
import json
from pathlib import Path

from loop_controller.runtime.engine import LoopRuntime
from loop_controller.runtime.types import NodeKind
from loop_contracts.models import LoopSession, RuntimeTerminalState
from loop_contracts.failure_codes import FailureCode as FC


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


def test_approve_clears_terminal_state_before_resume(tmp_path, monkeypatch):
    """approve 必须清除 terminal_state，否则 _handle_resume 的幂等检查
    （ts != NONE → 直接 return）会让 approve 无法续跑（死锁）。回归 P0-2。

    场景：human gate 暂停时 session.json 同时落盘了 terminal_state=ESCALATE_HUMAN
    与 pending_human_gate=True。approve 后必须把 terminal_state 复位为 NONE，
    resume 才能真正构造 runtime 并续跑。
    """
    import argparse
    from loop_controller import runtime_cli

    session_file = tmp_path / "session.json"
    session_file.write_text(json.dumps({
        "session_id": "s1", "workflow_id": "runtime", "target": "lciod", "suite": "s",
        "max_attempts": 3, "current_attempt": 1, "artifacts_dir": str(tmp_path),
        "attempts": [],
        "terminal_state": "ESCALATE_HUMAN",
        "pending_human_gate": True,
        "node_status": "LOW_CONFIDENCE",
    }), encoding="utf-8")

    captured = {}

    def fake_resume(args):
        data = json.loads(Path(args.session).read_text(encoding="utf-8"))
        captured["terminal_state"] = data.get("terminal_state")
        captured["human_gate_approved"] = data.get("human_gate_approved")
        return 0

    monkeypatch.setattr(runtime_cli, "_handle_resume", fake_resume)

    args = argparse.Namespace(session=str(session_file), adb_endpoint="")
    rc = runtime_cli._handle_approve(args)
    assert rc == 0
    # 关键：approve 写回后 terminal_state 必须为 NONE，否则 resume 幂等检查挡住
    assert captured["terminal_state"] == "NONE"
    assert captured["human_gate_approved"] is True


# ---------------------------------------------------------------------------
# Phase C 新增：kernel_patch / dd_boot_reboot 触发场景
# ---------------------------------------------------------------------------

def test_kernel_patch_triggers_gate(tmp_path, monkeypatch):
    """补丁涉及内核文件（risk.level=KERNEL）→ 触发 human gate。"""
    artifacts = tmp_path / "a"
    artifacts.mkdir()
    suggestion = {
        "patches": [{"workspace_path": "kernel/foo.c", "old_marker": "x", "new_content": "y"}],
        "confidence": 0.95,  # 高置信，不触发 low_confidence gate
    }
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps(suggestion), encoding="utf-8")
    session = LoopSession(
        session_id="t-kernel", workflow_id="runtime", target="lciod", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="d")
    rt._confidence_threshold = 0.7
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    # mock node_apply_patch 返回 KERNEL 风险
    monkeypatch.setattr(
        "loop_controller.runtime.engine._runtime_nodes.node_apply_patch",
        lambda *a, **kw: {
            "status": "APPLIED",
            "failure_code": FC.NONE,
            "patch_hash": "abc",
            "stash_ref": "",
            "workspace_root": "",
            "risk": {"level": "KERNEL", "files": ["kernel/foo.c"]},
            "files": ["kernel/foo.c"],
        },
    )
    rt.run(max_iterations=1)
    assert rt._state.pending_human_gate is True
    assert rt._state.node_status == "KERNEL_PATCH_REVIEW"
    assert rt._state.terminal_state == RuntimeTerminalState.NONE


def test_dd_boot_reboot_triggers_gate(tmp_path, monkeypatch):
    """deploy mode=dd_boot_reboot 成功 → 触发 human gate。"""
    artifacts = tmp_path / "a"
    artifacts.mkdir()
    session = LoopSession(
        session_id="t-dd", workflow_id="runtime", target="lciod", suite="s",
        max_attempts=3, current_attempt=1, artifacts_dir=str(artifacts),
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="d")
    rt._confidence_threshold = 0.7
    rt._state.current_node = NodeKind.DEPLOY_PATCH.value
    # 先构造一个 attempt（DEPLOY 前需要 attempts 非空）
    rt._session.attempts = [{"patch_applied": {"files": ["kernel/foo.c"]}}]
    # mock node_deploy 返回 dd_boot_reboot 成功
    monkeypatch.setattr(
        "loop_controller.runtime.engine._runtime_nodes.node_deploy",
        lambda *a, **kw: {
            "status": "DEPLOYED",
            "failure_code": FC.NONE,
            "mode": "dd_boot_reboot",
            "backup_path": "/tmp/backup.img",
            "backup_sha": "sha",
            "deployed_files": [],
            "error": "",
            "block_device": "/dev/block/mmcblk0p1",
            "warnings": [],
        },
    )
    rt.run(max_iterations=1)
    assert rt._state.pending_human_gate is True
    assert rt._state.node_status == "DD_BOOT_REVIEW"
    assert rt._state.terminal_state == RuntimeTerminalState.NONE


def test_kernel_patch_disabled_when_not_in_triggers(tmp_path, monkeypatch):
    """human_gate.triggers 不含 kernel_patch 时，内核补丁不触发 gate。"""
    artifacts = tmp_path / "a"
    artifacts.mkdir()
    suggestion = {
        "patches": [{"workspace_path": "kernel/foo.c", "old_marker": "x", "new_content": "y"}],
        "confidence": 0.95,
    }
    (artifacts / "patch_suggestion.json").write_text(
        json.dumps(suggestion), encoding="utf-8")
    session = LoopSession(
        session_id="t-kernel-off", workflow_id="runtime", target="lciod", suite="s",
        max_attempts=3, current_attempt=0, artifacts_dir=str(artifacts),
    )
    rt = LoopRuntime(session, cases_dir="/tmp", device_profile="d")
    rt._confidence_threshold = 0.7
    rt._human_gate_triggers = ["low_confidence"]  # 关闭 kernel_patch 触发
    rt._state.current_node = NodeKind.APPLY_PATCH.value
    monkeypatch.setattr(
        "loop_controller.runtime.engine._runtime_nodes.node_apply_patch",
        lambda *a, **kw: {
            "status": "APPLIED",
            "failure_code": FC.NONE,
            "patch_hash": "abc",
            "stash_ref": "",
            "workspace_root": "",
            "risk": {"level": "KERNEL", "files": ["kernel/foo.c"]},
            "files": ["kernel/foo.c"],
        },
    )
    rt.run(max_iterations=1)
    # kernel_patch 关闭 → 不触发 gate，继续走到 COMPILE
    assert rt._state.pending_human_gate is False
    assert rt._state.node_status == "APPLIED"
