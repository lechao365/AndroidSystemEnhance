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
from loop_controller.workspace_isolation import WorktreeHandle, remove_patch_worktree
from loop_controller.stages import _load_target_paths, _build_env


def _workspace_root(workspace_root: str = "") -> str:
    """解析 workspace 根路径，缺省回退到 AOSP_ROOT 环境变量或默认路径。"""
    return workspace_root or os.environ.get(
        "AOSP_ROOT", os.path.expanduser("~/workspace/aosp")
    )


def _parse_deploy_ctx(stdout: str) -> dict | None:
    """从 deploy CLI stdout 解析 DEPLOY_CTX JSON，返回 dict 或 None。"""
    for line in stdout.split("\n"):
        if line.startswith("DEPLOY_CTX:"):
            try:
                return json.loads(line[len("DEPLOY_CTX:"):].strip())
            except json.JSONDecodeError:
                pass
    return None


# DeployErrorCode → (status, FailureCode, needs_rollback) 映射
# needs_rollback: 只有实际写入设备的错误才需要设备回滚，未写入的跳过
_DEPLOY_ERROR_MAP: dict[str, tuple[str, FailureCode, bool]] = {
    # --- 实际写入设备，需要设备回滚 ---
    "KERNEL_PANIC": ("KERNEL_DEAD", FailureCode.KERNEL_DEAD_NO_SHELL, True),
    "BOOT_COMPLETED_NOT_REACHED": ("BOOT_TIMEOUT", FailureCode.BOOT_TIMEOUT_ROLLBACK, True),
    "DD_WRITE_FAILED": ("BOOT_TIMEOUT", FailureCode.BOOT_TIMEOUT_ROLLBACK, True),
    "ADB_PUSH_FAILED": ("DEPLOY_FAILED", FailureCode.DEPLOY_FATAL, True),
    "SERVICE_NOT_STARTED": ("DEPLOY_FAILED", FailureCode.DEPLOY_FATAL, True),
    # --- 未写入设备，无需设备回滚 ---
    "ADB_ROOT_FAILED": ("DEPLOY_FAILED", FailureCode.DEPLOY_FATAL, False),
    "ADB_REMOUNT_FAILED": ("DEPLOY_FAILED", FailureCode.DEPLOY_FATAL, False),
    "SHA256_MISMATCH": ("DEPLOY_FAILED", FailureCode.DEPLOY_FATAL, False),
    "IMAGE_VERIFY_FAILED": ("DEPLOY_FAILED", FailureCode.DEPLOY_FATAL, False),
    "DEVICE_NOT_HEALTHY": ("DEPLOY_FAILED", FailureCode.DEPLOY_FATAL, False),
    "ARTIFACT_NOT_FOUND": ("DEPLOY_FAILED", FailureCode.DEPLOY_FATAL, False),
    "HEALTH_CHECK_FAILED": ("DEPLOY_FAILED", FailureCode.DEPLOY_FATAL, False),
}


def _classify_deploy_failure(ctx: dict) -> tuple[str, FailureCode, bool]:
    """根据 DeployErrorCode 结构化错误码判定 failure_code 和是否需要设备回滚。"""
    error_code = ctx.get("error_code", "")
    if error_code in _DEPLOY_ERROR_MAP:
        return _DEPLOY_ERROR_MAP[error_code]
    if ctx.get("error"):
        return ("DEPLOY_FAILED", FailureCode.DEPLOY_FATAL, False)
    return ("DEPLOY_FAILED", FailureCode.DEPLOY_FATAL, False)


def node_apply_patch(
    patch_path: str, session_dict: dict, workspace_root: str = "",
    worktree_handle: WorktreeHandle | None = None,
) -> dict:
    """应用补丁。

    流程：白名单校验 → 语法预检 → 备份 → 落盘 → 计算风险/hash。
    备份策略：优先用独立 worktree（worktree_handle 非空时应用至 worktree 路径，
              实现补丁隔离）；无 handle 时降级到 git stash（break-glass 兼容）。
    返回 {status, failure_code, files, stash_ref, patch_hash, risk, workspace_root, worktree_handle, error}。
    """
    target = session_dict.get("target", "")
    ws_root = _workspace_root(workspace_root)

    # 读取补丁文件（兼容新旧格式）
    try:
        raw = json.loads(Path(patch_path).read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return {
            "status": "PATCH_INVALID",
            "failure_code": FailureCode.PATCH_REJECTED,
            "error": f"invalid patch: {e}",
        }

    if isinstance(raw, dict) and "patches" in raw:
        raw_changes = raw["patches"]
    elif isinstance(raw, list):
        raw_changes = raw
    else:
        return {
            "status": "PATCH_INVALID",
            "failure_code": FailureCode.PATCH_REJECTED,
            "error": "patch_suggestion.json format unknown",
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

    # 应用目标路径：worktree 模式用 worktree_path，stash 模式用 workspace_root
    apply_root = worktree_handle.worktree_path if worktree_handle else ws_root
    stash_ref = ""

    # stash 备份仅在 break-glass 模式下使用（worktree 模式隔离已天然提供回滚能力）
    if not worktree_handle:
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
    result = apply_file_changes(changes, apply_root)
    if not result.success:
        # 失败回滚：stash 模式恢复快照；worktree 模式移除 worktree
        if worktree_handle:
            remove_patch_worktree(worktree_handle)
        elif stash_ref:
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

    resp = {
        "status": "APPLIED",
        "failure_code": FailureCode.NONE,
        "files": result.applied_files,
        "stash_ref": stash_ref,
        "patch_hash": patch_hash,
        "risk": risk,
        "workspace_root": ws_root,
    }
    if worktree_handle:
        resp["worktree_handle"] = {
            "worktree_path": worktree_handle.worktree_path,
            "branch": worktree_handle.branch,
            "workspace_root": worktree_handle.workspace_root,
            "created": worktree_handle.created,
        }
    return resp


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
    """部署编译产物到设备。

    从 session 的 compile_result.artifacts 取编译产物，通过 --artifact 传给 deploy CLI。
    deploy CLI 收到 --artifact 后跳过内置编译，避免重复。
    通过 DEPLOY_CTX 结构化输出传递 backup 元数据和错误信息。
    返回 {status, failure_code, mode, backup_path, backup_sha, deployed_files, error}。
    """
    # 从上一次编译结果获取 artifacts，跳过 deploy CLI 内置编译
    artifacts = _get_compile_artifacts(session_dict)
    cmd = [
        sys.executable, "-m", "loop_core.cli", "deploy",
        "--diff-rev", "HEAD",
        "--skip-compile",
    ]
    for art in artifacts:
        cmd += ["--artifact", art]
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

    ctx = _parse_deploy_ctx(result.stdout or "")

    if result.returncode == 0:
        return _build_deploy_result("DEPLOYED", FailureCode.NONE, ctx)

    # 失败路径：从 DEPLOY_CTX 提取结构化错误和回滚元数据
    if ctx:
        status, fc, needs_rollback = _classify_deploy_failure(ctx)
        r = _build_deploy_result(status, fc, ctx)
        r["needs_rollback"] = needs_rollback
        return r
    # 无 DEPLOY_CTX（deploy CLI 在到达 Deployer 之前就退出了）
    error = (result.stderr or result.stdout or "")[:500]
    return {
        "status": "DEPLOY_FAILED",
        "failure_code": FailureCode.DEPLOY_FATAL,
        "error": error,
    }


def _get_compile_artifacts(session_dict: dict) -> list[str]:
    """从 session attempts 最近一次 compile_result 提取编译产物路径。"""
    attempts = session_dict.get("attempts", [])
    if not attempts:
        return []
    latest = attempts[-1]
    if not isinstance(latest, dict):
        return []
    compile_result = latest.get("compile_result", {})
    if isinstance(compile_result, dict):
        return compile_result.get("artifacts", [])
    return []


def _build_deploy_result(status: str, fc: FailureCode, ctx: dict | None) -> dict:
    """从 DEPLOY_CTX 构造统一的 deploy 返回 dict。"""
    if not ctx:
        return {
            "status": status,
            "failure_code": fc,
            "mode": "",
        }
    return {
        "status": status,
        "failure_code": fc,
        "mode": ctx.get("mode", ""),
        "backup_path": ctx.get("backup_path", ""),
        "backup_sha": ctx.get("backup_sha", ""),
        "deployed_files": ctx.get("deployed_files", []),
        "block_device": ctx.get("block_device", ""),
        "error": ctx.get("error", ""),
    }


def node_revert_workspace(session_dict: dict) -> dict:
    """回滚最近一次 apply-patch 的源码改动。

    优先策略：若 attempt 含 worktree_handle，移除独立 worktree（隔离式回滚，干净彻底）。
    降级策略：若 attempt 含 stash_ref，git stash apply 恢复（break-glass 兼容旧路径）。
    返回 {status, failure_code, error}。
    """
    attempts = session_dict.get("attempts", [])
    ws_root = _workspace_root()

    for att in reversed(attempts):
        patch_applied = att.get("patch_applied", {}) if isinstance(att, dict) else {}

        # 策略 1：worktree 隔离回滚（优先）
        handle_dict = patch_applied.get("worktree_handle")
        if handle_dict and isinstance(handle_dict, dict) and handle_dict.get("worktree_path"):
            handle = WorktreeHandle(
                worktree_path=handle_dict["worktree_path"],
                branch=handle_dict.get("branch", ""),
                workspace_root=handle_dict.get("workspace_root", ws_root),
                created=handle_dict.get("created", False),
            )
            ok = remove_patch_worktree(handle)
            if ok:
                return {
                    "status": "REVERTED",
                    "failure_code": FailureCode.NONE,
                }
            return {
                "status": "REVERT_FAILED",
                "failure_code": FailureCode.ROLLBACK_FAILED,
                "error": f"failed to remove worktree: {handle.worktree_path}",
            }

        # 策略 2：stash 降级回滚（向后兼容）
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
        from loop_deploy.rollback import serial_rollback_dd
        # block_device 从 deploy_context 读取（由 decider → deployer → DEPLOY_CTX 全链路传递）
        block_device = deploy_context.get("block_device", "") or "/dev/block/mmcblk0p1"
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
