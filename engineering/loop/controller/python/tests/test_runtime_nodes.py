"""nodes.py 单元测试——验证 4 个节点 handler 的边界行为。

这些 handler 目前尚未接入 engine（engine 仍走 placeholder），
但必须有独立测试覆盖其核心逻辑，确保未来接线时行为正确。
"""
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from loop_controller.runtime.nodes import (
    node_apply_patch,
    node_compile,
    node_deploy,
    node_revert,
    node_revert_workspace,
)
from loop_controller.workspace_isolation import (
    WorktreeHandle,
    create_patch_worktree,
)


# ---------------------------------------------------------------------------
# node_apply_patch
# ---------------------------------------------------------------------------

def test_apply_patch_rejects_invalid_json(tmp_path: Path):
    bad_patch = tmp_path / "bad.json"
    bad_patch.write_text("not json", encoding="utf-8")
    result = node_apply_patch(str(bad_patch), {"target": "test"}, str(tmp_path))
    assert result["status"] == "PATCH_INVALID"
    assert result["failure_code"].value == "PATCH_REJECTED"


def test_apply_patch_rejects_outside_whitelist(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "loop_controller.runtime.nodes._load_target_paths",
        lambda target: ["vendor/allowed/"],
    )
    patch_data = [{"workspace_path": "vendor/other/foo.cpp", "change_type": "edit",
                   "old_marker": "x", "new_content": "y"}]
    patch_file = tmp_path / "patch.json"
    patch_file.write_text(json.dumps(patch_data), encoding="utf-8")

    result = node_apply_patch(str(patch_file), {"target": "test"}, str(tmp_path))
    assert result["status"] == "PATCH_REJECTED"
    assert "vendor/other/foo.cpp" in result["error"]


def test_apply_patch_success(tmp_path: Path, monkeypatch):
    target_file = tmp_path / "test.cpp"
    target_file.write_text("int x = 1;\n", encoding="utf-8")

    monkeypatch.setattr(
        "loop_controller.runtime.nodes._load_target_paths",
        lambda target: [""],
    )
    # mock git stash create to return empty
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "", ""),
    )

    patch_data = [{"workspace_path": "test.cpp", "change_type": "edit",
                   "old_marker": "int x = 1;", "new_content": "int x = 42;"}]
    patch_file = tmp_path / "patch.json"
    patch_file.write_text(json.dumps(patch_data), encoding="utf-8")

    result = node_apply_patch(str(patch_file), {"target": "test"}, str(tmp_path))
    assert result["status"] == "APPLIED"
    assert result["failure_code"].value == "NONE"
    assert "test.cpp" in result["files"]
    assert len(result["patch_hash"]) > 0


# ---------------------------------------------------------------------------
# node_compile
# ---------------------------------------------------------------------------

def test_compile_returns_failed_on_diff_error(monkeypatch):
    def boom(rev, cwd=None):
        raise RuntimeError("git error")
    monkeypatch.setattr("loop_deploy.decider.get_diff_files", boom)

    result = node_compile({"target": "test", "attempts": []}, "/fake/workspace")
    assert result["status"] == "COMPILE_FAILED"
    assert "no changed files" in result["error"]


def test_compile_skip_for_empty_diff(monkeypatch):
    from loop_deploy.models import DeployMode, DeployPlan
    monkeypatch.setattr("loop_deploy.decider.get_diff_files", lambda rev, cwd=None: [])
    monkeypatch.setattr("loop_deploy.decider.decide", lambda files: DeployPlan.skip("no changes"))
    result = node_compile({"target": "test", "attempts": []}, "/fake/workspace")
    # SKIP with no diff files → COMPILE_FAILED (no changed files)
    assert result["status"] == "COMPILE_FAILED"


# ---------------------------------------------------------------------------
# node_deploy
# ---------------------------------------------------------------------------

def test_deploy_success(monkeypatch):
    from loop_deploy.models import DeployResult, DeployMode

    def fake_deployer_deploy(self, plan, artifacts):
        return DeployResult(success=True, mode=DeployMode.PUSH_SINGLE,
                            deployed_files=["/system/bin/foo"])

    monkeypatch.setattr("loop_deploy.deployer.Deployer.deploy", fake_deployer_deploy)

    session = {
        "target": "lcview",
        "attempts": [{"compile_result": {"artifacts": ["/tmp/lechao_lcview"]},
                       "patch_applied": {"files": ["vendor/lechao/services/lechao_lcview/daemon/lechao_lcview.cpp"]}}],
    }
    result = node_deploy(session, adb_endpoint="192.168.1.55:5555")
    assert result["status"] == "DEPLOYED"
    assert result["failure_code"].value == "NONE"


def test_deploy_failure(monkeypatch):
    from loop_deploy.models import DeployResult, DeployMode, DeployErrorCode

    def fake_deployer_deploy(self, plan, artifacts):
        return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                            error="push failed",
                            error_code=DeployErrorCode.ADB_PUSH_FAILED)

    monkeypatch.setattr("loop_deploy.deployer.Deployer.deploy", fake_deployer_deploy)

    session = {
        "target": "lcview",
        "attempts": [{"compile_result": {"artifacts": ["/tmp/lechao_lcview"]},
                       "patch_applied": {"files": ["vendor/lechao/services/lechao_lcview/daemon/lechao_lcview.cpp"]}}],
    }
    result = node_deploy(session, adb_endpoint="192.168.1.55:5555")
    assert result["status"] == "DEPLOY_FAILED"
    assert "push failed" in result.get("error", "")


def test_deploy_no_endpoint():
    """无 adb endpoint 时应返回 DEPLOY_FAILED。"""
    session = {
        "target": "lcview",
        "attempts": [{"compile_result": {"artifacts": ["/tmp/lechao_lcview"]},
                       "patch_applied": {"files": ["vendor/lechao/services/lechao_lcview/daemon/lechao_lcview.cpp"]}}],
    }
    result = node_deploy(session, adb_endpoint="")
    assert result["status"] == "DEPLOY_FAILED"
    assert "no adb endpoint" in result["error"]


# ---------------------------------------------------------------------------
# node_revert
# ---------------------------------------------------------------------------

def test_revert_no_attempts():
    result = node_revert({"attempts": []})
    assert result["status"] == "NO_STASH_REF"
    assert result["failure_code"].value == "ROLLBACK_FAILED"


def test_revert_no_stash_in_attempts():
    session = {"attempts": [{"patch_applied": {}}]}
    result = node_revert(session)
    assert result["status"] == "NO_STASH_REF"


def test_revert_success(monkeypatch):
    session = {"attempts": [{"patch_applied": {"stash_ref": "abc123", "workspace_root": "/ws"}}]}
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "", ""),
    )
    result = node_revert(session)
    assert result["status"] == "REVERTED"
    assert result["failure_code"].value == "NONE"


def test_revert_stash_apply_failure(monkeypatch):
    session = {"attempts": [{"patch_applied": {"stash_ref": "abc123", "workspace_root": "/ws"}}]}
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 1, "", "conflict"),
    )
    result = node_revert(session)
    assert result["status"] == "REVERT_FAILED"
    assert "conflict" in result["error"]


# ---------------------------------------------------------------------------
# worktree 集成（ISSUE-2）：patch 应用到独立 worktree，revert 移除 worktree
# ---------------------------------------------------------------------------

@pytest.fixture
def git_workspace(tmp_path: Path) -> Path:
    """构造真实 git 仓库作为 workspace_root。"""
    repo = tmp_path / "workspace"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=repo, check=True)
    (repo / "target.cpp").write_text("int x = 1;\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


def test_apply_patch_uses_worktree_when_handle_provided(git_workspace: Path, monkeypatch):
    """传入 worktree_handle 时，patch 应应用到 worktree 路径而非原 workspace。"""
    monkeypatch.setattr(
        "loop_controller.runtime.nodes._load_target_paths",
        lambda target: [""],
    )
    handle = create_patch_worktree(str(git_workspace), "sess-wt", 0)

    patch_data = [{"workspace_path": "target.cpp", "change_type": "edit",
                   "old_marker": "int x = 1;", "new_content": "int x = 42;"}]
    patch_file = git_workspace / "patch.json"
    patch_file.write_text(json.dumps(patch_data), encoding="utf-8")

    result = node_apply_patch(
        str(patch_file), {"target": "test"}, str(git_workspace),
        worktree_handle=handle,
    )
    assert result["status"] == "APPLIED"
    assert result.get("worktree_handle") is not None
    assert "worktree_path" in result["worktree_handle"]
    # worktree 内文件已改，原 workspace 文件未改（隔离生效）
    wt_path = Path(handle.worktree_path)
    assert (wt_path / "target.cpp").read_text() == "int x = 42;\n"
    assert (git_workspace / "target.cpp").read_text() == "int x = 1;\n"


def test_apply_patch_falls_back_to_stash_when_no_handle(tmp_path: Path, monkeypatch):
    """无 worktree_handle 时降级到原 stash 路径（break-glass 兼容）。"""
    target_file = tmp_path / "test.cpp"
    target_file.write_text("int x = 1;\n", encoding="utf-8")

    monkeypatch.setattr(
        "loop_controller.runtime.nodes._load_target_paths",
        lambda target: [""],
    )
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "", ""),
    )

    patch_data = [{"workspace_path": "test.cpp", "change_type": "edit",
                   "old_marker": "int x = 1;", "new_content": "int x = 42;"}]
    patch_file = tmp_path / "patch.json"
    patch_file.write_text(json.dumps(patch_data), encoding="utf-8")

    result = node_apply_patch(str(patch_file), {"target": "test"}, str(tmp_path))
    assert result["status"] == "APPLIED"
    assert "stash_ref" in result
    assert result.get("worktree_handle") is None


def test_revert_workspace_removes_worktree_when_handle_present(git_workspace: Path):
    """session attempt 含 worktree_handle 时，revert 应移除该 worktree。"""
    handle = create_patch_worktree(str(git_workspace), "sess-rv", 0)
    session = {
        "attempts": [{
            "patch_applied": {
                "worktree_handle": {
                    "worktree_path": handle.worktree_path,
                    "branch": handle.branch,
                    "workspace_root": handle.workspace_root,
                    "created": True,
                },
                "workspace_root": str(git_workspace),
            },
        }],
    }
    # worktree 存在
    assert Path(handle.worktree_path).exists()
    result = node_revert_workspace(session)
    assert result["status"] == "REVERTED"
    assert result["failure_code"].value == "NONE"
    # worktree 已清理
    assert not Path(handle.worktree_path).exists()


def test_revert_workspace_falls_back_to_stash_without_handle(monkeypatch):
    """session attempt 无 worktree_handle 时降级到 stash 回滚（向后兼容）。"""
    session = {"attempts": [{"patch_applied": {"stash_ref": "abc", "workspace_root": "/ws"}}]}
    monkeypatch.setattr(
        "loop_controller.runtime.nodes.subprocess.run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0], 0, "", ""),
    )
    result = node_revert_workspace(session)
    assert result["status"] == "REVERTED"
