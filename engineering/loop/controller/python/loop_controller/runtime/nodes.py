"""runtime/nodes：APPLY_PATCH / COMPILE_PATCH / DEPLOY_PATCH / REVERT_PATCH 节点 handlers。

这些是纯函数，供 runtime engine 调用。每个函数接收 session_dict 和必要的参数，
返回结构化结果 dict。
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from loop_contracts.failure_codes import FailureCode
from loop_controller.analyzer_protocol import FileChange
from loop_controller.patch_guard import check_white_list, detect_risk, check_syntax
from loop_controller.patch_applier import apply_file_changes
from loop_controller.stages import _load_target_paths, _build_env


def _workspace_root(workspace_root: str = "") -> str:
    """解析 workspace 根路径，缺省回退到 AOSP_ROOT 环境变量或默认路径。"""
    return workspace_root or os.environ.get(
        "AOSP_ROOT", os.path.expanduser("~/workspace/aosp")
    )


def node_apply_patch(
    patch_path: str, session_dict: dict, workspace_root: str = ""
) -> dict:
    """应用补丁。

    流程：白名单校验 → 语法预检 → stash 备份 → 落盘 → 计算风险/hash。
    失败时若已 stash 则尝试回滚。
    返回 {status, failure_code, files, stash_ref, patch_hash, risk, workspace_root, error}。
    """
    target = session_dict.get("target", "")
    ws_root = _workspace_root(workspace_root)

    # 读取补丁文件
    try:
        raw_changes = json.loads(Path(patch_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {
            "status": "PATCH_INVALID",
            "failure_code": FailureCode.PATCH_REJECTED,
            "error": f"invalid patch: {e}",
        }

    changes = [FileChange(**c) for c in raw_changes]

    # 白名单校验
    allowed = _load_target_paths(target)
    guard = check_white_list(changes, allowed)
    if not guard.allowed:
        return {
            "status": "PATCH_REJECTED",
            "failure_code": FailureCode.PATCH_REJECTED,
            "error": f"rejected: {guard.rejected_files}",
        }

    # 语法预检
    syntax_errors = check_syntax(changes, ws_root)
    if syntax_errors:
        return {
            "status": "SYNTAX_ERROR",
            "failure_code": FailureCode.PATCH_REJECTED,
            "error": syntax_errors[0][:300],
        }

    # stash 备份（用于失败回滚 / 显式 revert）
    try:
        stash_result = subprocess.run(
            ["git", "stash", "create", "-u"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=ws_root,
        )
        stash_ref = stash_result.stdout.strip() or ""
    except (subprocess.SubprocessError, OSError):
        stash_ref = ""

    # 落盘
    result = apply_file_changes(changes, ws_root)
    if not result.success:
        # 失败回滚：恢复到 stash 快照
        if stash_ref:
            try:
                subprocess.run(
                    ["git", "stash", "apply", stash_ref],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    cwd=ws_root,
                )
            except (subprocess.SubprocessError, OSError):
                pass
        return {
            "status": "APPLY_FAILED",
            "failure_code": FailureCode.PATCH_REJECTED,
            "error": result.error,
        }

    risk = detect_risk(changes)
    patch_hash = hashlib.sha256(
        json.dumps(raw_changes, sort_keys=True).encode()
    ).hexdigest()

    return {
        "status": "APPLIED",
        "failure_code": FailureCode.NONE,
        "files": result.applied_files,
        "stash_ref": stash_ref,
        "patch_hash": patch_hash,
        "risk": risk,
        "workspace_root": ws_root,
    }


def node_compile(session_dict: dict, workspace_root: str = "") -> dict:
    """编译当前 workspace 改动。

    流程：git diff → deploy decider → 若 SKIP 但存在代码文件则强制编译 → compile_plan。
    返回 {status, failure_code, artifacts, error}。
    """
    from loop_deploy.compiler import compile_plan
    from loop_deploy.decider import get_diff_files, decide
    from loop_deploy.models import DeployMode, DeployPlan

    ws_root = _workspace_root(workspace_root)

    try:
        diff_files = get_diff_files("HEAD")
    except RuntimeError as e:
        return {
            "status": "COMPILE_FAILED",
            "failure_code": FailureCode.COMPILE_FAILED,
            "error": str(e),
        }

    plan = decide(diff_files)

    # SKIP 但存在代码文件 → 强制按 PUSH_SINGLE 编译
    if plan.mode == DeployMode.SKIP and diff_files:
        _COMPILABLE_SUFFIXES = {
            ".cpp", ".c", ".cc", ".h", ".hpp", ".bp", ".java", ".kt"
        }
        has_code = any(
            Path(f).suffix.lower() in _COMPILABLE_SUFFIXES for f in diff_files
        )
        if has_code:
            plan = DeployPlan(
                mode=DeployMode.PUSH_SINGLE,
                changed_files=diff_files,
                reason="manual compile (code files detected)",
                build_targets=[],
                deploy_targets=[],
                requires_reboot=False,
                estimated_seconds=600,
            )

    result = compile_plan(plan, ws_root)
    if result.success:
        return {
            "status": "COMPILED",
            "failure_code": FailureCode.NONE,
            "artifacts": result.artifacts,
        }
    return {
        "status": "COMPILE_FAILED",
        "failure_code": FailureCode.COMPILE_FAILED,
        "error": result.error,
    }


def node_deploy(
    session_dict: dict, artifacts: list[str] | None = None, adb_endpoint: str = ""
) -> dict:
    """部署到设备。

    当前实现委托给 loop_core.cli deploy subprocess（与旧 control_cli 一致）。
    返回 {status, failure_code, mode, error}。
    """
    cmd = [sys.executable, "-m", "loop_core.cli", "deploy", "--diff-rev", "HEAD"]
    if adb_endpoint:
        cmd += ["--adb-endpoint", adb_endpoint]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=3600,
            env=_build_env(),
        )
        if result.returncode == 0:
            return {
                "status": "DEPLOYED",
                "failure_code": FailureCode.NONE,
                "mode": "OK",
            }
        error = (result.stderr or result.stdout or "")[-500:]
        return {
            "status": "DEPLOY_FAILED",
            "failure_code": FailureCode.DEPLOY_FATAL,
            "error": error,
        }
    except subprocess.TimeoutExpired:
        return {
            "status": "DEPLOY_FAILED",
            "failure_code": FailureCode.DEPLOY_FATAL,
            "error": "deploy timed out",
        }
    except (OSError, ValueError) as e:
        return {
            "status": "DEPLOY_FAILED",
            "failure_code": FailureCode.DEPLOY_FATAL,
            "error": str(e),
        }


def node_revert(session_dict: dict) -> dict:
    """回滚最近一次 apply-patch。

    从 attempts 倒序查找 stash_ref，git stash apply 恢复。
    返回 {status, failure_code, error}。
    """
    attempts = session_dict.get("attempts", [])
    ws_root = _workspace_root()

    for att in reversed(attempts):
        patch_applied = att.get("patch_applied", {})
        stash_ref = patch_applied.get("stash_ref", "")
        if stash_ref:
            ws = patch_applied.get("workspace_root", ws_root)
            try:
                r = subprocess.run(
                    ["git", "stash", "apply", stash_ref],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=ws,
                )
            except (subprocess.SubprocessError, OSError) as e:
                return {
                    "status": "REVERT_FAILED",
                    "failure_code": FailureCode.ROLLBACK_FAILED,
                    "error": str(e),
                }
            if r.returncode == 0:
                return {
                    "status": "REVERTED",
                    "failure_code": FailureCode.NONE,
                }
            return {
                "status": "REVERT_FAILED",
                "failure_code": FailureCode.ROLLBACK_FAILED,
                "error": (r.stderr or "")[:300],
            }

    return {
        "status": "NO_STASH_REF",
        "failure_code": FailureCode.ROLLBACK_FAILED,
        "error": "no stash ref found in any attempt",
    }
