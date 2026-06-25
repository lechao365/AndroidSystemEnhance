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
    def boom(rev):
        raise RuntimeError("git error")
    monkeypatch.setattr("loop_deploy.decider.get_diff_files", boom)

    result = node_compile({"target": "test"}, "/fake/workspace")
    assert result["status"] == "COMPILE_FAILED"
    assert "git error" in result["error"]


def test_compile_skip_for_empty_diff(monkeypatch):
    from loop_deploy.models import DeployMode, DeployPlan
    monkeypatch.setattr("loop_deploy.decider.get_diff_files", lambda rev: [])
    monkeypatch.setattr("loop_deploy.decider.decide", lambda files: DeployPlan.skip("no changes"))
    result = node_compile({"target": "test"}, "/fake/workspace")
    # SKIP with no diff files → compiler returns success (nothing to build)
    assert result["status"] == "COMPILED"


# ---------------------------------------------------------------------------
# node_deploy
# ---------------------------------------------------------------------------

def test_deploy_success(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "OK", "")
    monkeypatch.setattr("loop_controller.runtime.nodes.subprocess.run", fake_run)
    monkeypatch.setattr("loop_controller.runtime.nodes._build_env", lambda: {})

    result = node_deploy({"target": "test"}, adb_endpoint="192.168.1.55:5555")
    assert result["status"] == "DEPLOYED"
    assert result["failure_code"].value == "NONE"


def test_deploy_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, "", "deploy error")
    monkeypatch.setattr("loop_controller.runtime.nodes.subprocess.run", fake_run)
    monkeypatch.setattr("loop_controller.runtime.nodes._build_env", lambda: {})

    result = node_deploy({"target": "test"})
    assert result["status"] == "DEPLOY_FAILED"
    assert "deploy error" in result["error"]


def test_deploy_timeout(monkeypatch):
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, 3600)
    monkeypatch.setattr("loop_controller.runtime.nodes.subprocess.run", fake_run)
    monkeypatch.setattr("loop_controller.runtime.nodes._build_env", lambda: {})

    result = node_deploy({"target": "test"})
    assert result["status"] == "DEPLOY_FAILED"
    assert "timed out" in result["error"]


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
