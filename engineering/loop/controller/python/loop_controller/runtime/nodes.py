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

    try:
        result = compile_plan(plan, ws_root)
    except Exception as e:
        return {
            "status": "COMPILE_FAILED",
            "failure_code": FailureCode.COMPILE_FAILED,
            "error": f"compile_plan exception: {type(e).__name__}: {e}",
        }
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


def node_deploy(session_dict: dict, adb_endpoint: str = "") -> dict:
    """部署到设备。

    当前实现委托给 loop_core.cli deploy subprocess（与旧 control_cli 一致）。
    部署由 git diff HEAD 驱动，编译产物由 workspace 落盘后自动发现。
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
        error_lower = error.lower()

        # 真正内核死透：kernel panic / not syncing / 串口完全无响应
        _KERNEL_DEAD_PATTERNS = (
            "kernel panic", "not syncing", "no response from serial",
        )
        if any(p in error_lower for p in _KERNEL_DEAD_PATTERNS):
            return {
                "status": "KERNEL_DEAD",
                "failure_code": FailureCode.KERNEL_DEAD_NO_SHELL,
                "error": error,
            }

        # 可恢复的启动超时：设备可能仍在启动中，串口可达，可尝试 DD 回滚
        _BOOT_TIMEOUT_PATTERNS = (
            "boot timeout", "device offline", "connection refused",
        )
        if any(p in error_lower for p in _BOOT_TIMEOUT_PATTERNS):
            return {
                "status": "BOOT_TIMEOUT",
                "failure_code": FailureCode.BOOT_TIMEOUT_ROLLBACK,
                "error": error,
            }
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


def node_revert_workspace(session_dict: dict) -> dict:
    """回滚最近一次 apply-patch 的源码改动（git stash apply）。

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


def node_rollback_deploy(
    session_dict: dict,
    deploy_context: dict,
    serial_shell: callable | None = None,
    adb_endpoint: str = "",
) -> dict:
    """部署失败后的设备回滚，根据 deploy mode 选择策略。

    PUSH_SINGLE: 从 deploy_context 取 backup_path（adb pull 备份目录），
                 adb push 回旧文件 + restart service。
    DD_BOOT:     从 deploy_context 取 backup_path（设备端路径）+ backup_sha，
                 通过 serial_shell 调 serial_rollback_dd；
                 若 serial_shell 不可用，fallback 返回 REVERT_FAILED。
    其他/无 context: 返回 NO_DEPLOY_CONTEXT。
    返回 {status, failure_code, error}。
    """
    mode = deploy_context.get("mode", "")
    backup_path = deploy_context.get("backup_path", "")
    backup_sha = deploy_context.get("backup_sha", "")
    deployed_files: list[str] = deploy_context.get("deployed_files", [])

    # -- PUSH_SINGLE 回滚 --
    if mode in ("PUSH_SINGLE", "push_single"):
        if not backup_path or not deployed_files:
            return {
                "status": "NO_BACKUP",
                "failure_code": FailureCode.ROLLBACK_FAILED,
                "error": "no backup available for push_single rollback",
            }
        from pathlib import Path
        backup_dir = Path(backup_path)
        try:
            from loop_adb.client import AdbClient
            client = AdbClient(endpoint=adb_endpoint) if adb_endpoint else AdbClient()
        except Exception as e:
            return {
                "status": "REVERT_FAILED",
                "failure_code": FailureCode.ROLLBACK_FAILED,
                "error": f"adb client unavailable: {e}",
            }
        try:
            client.connect(timeout_sec=10.0)
        except Exception:
            pass
        errors: list[str] = []
        for remote_path in deployed_files:
            local_backup = backup_dir / Path(remote_path).name
            if not local_backup.exists():
                errors.append(f"backup not found: {local_backup}")
                continue
            try:
                push_r = client.push(str(local_backup), remote_path, timeout_sec=30.0)
                if push_r.exit_code != 0:
                    errors.append(f"push failed: {remote_path}")
            except Exception as e:
                errors.append(f"push exception {remote_path}: {e}")
        if errors:
            return {
                "status": "REVERT_FAILED",
                "failure_code": FailureCode.ROLLBACK_FAILED,
                "error": "; ".join(errors)[:300],
            }
        return {
            "status": "REVERTED",
            "failure_code": FailureCode.NONE,
        }

    # -- DD_BOOT 回滚 --
    if mode in ("DD_BOOT_REBOOT", "dd_boot_reboot", "DD_BOOT"):
        if not backup_path:
            return {
                "status": "NO_BACKUP",
                "failure_code": FailureCode.ROLLBACK_FAILED,
                "error": "no backup_path for dd rollback",
            }
        if serial_shell is None:
            return {
                "status": "REVERT_FAILED",
                "failure_code": FailureCode.ROLLBACK_FAILED,
                "error": "serial_shell unavailable, cannot perform dd rollback",
            }
        from pathlib import Path as _P
        from loop_deploy.rollback import serial_rollback_dd
        block_device = "/dev/block/mmcblk0p1"
        # backup_path 是设备端绝对路径（如 /data/local/tmp/boot_backup.img），
        # 直接传给 serial_rollback_dd，不硬编码拼接。
        result = serial_rollback_dd(
            serial_shell=serial_shell,
            backup_path=backup_path,
            block_device=block_device,
        )
        if result.success:
            return {
                "status": "REVERTED",
                "failure_code": FailureCode.NONE,
            }
        return {
            "status": "REVERT_FAILED",
            "failure_code": FailureCode.ROLLBACK_FAILED,
            "error": result.reason,
        }

    return {
        "status": "NO_DEPLOY_CONTEXT",
        "failure_code": FailureCode.ROLLBACK_FAILED,
        "error": f"unknown deploy mode or no context: mode={mode}",
    }


def node_revert(session_dict: dict) -> dict:
    """兼容旧调用：委托给 node_revert_workspace。"""
    return node_revert_workspace(session_dict)
