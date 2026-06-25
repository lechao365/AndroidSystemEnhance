"""control_cli：le control 子命令——session 管理 + 分阶段编排。

注：run-verify / analyze-request / decide 三个阶段的业务逻辑已下沉到
``loop_controller.stages``（纯函数，供新 runtime 直接调用）；本模块仅负责
CLI argparse 解析并委托执行。通用 helpers 与路径常量亦从 stages 复用。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

# 通用 helpers 与路径常量统一从 stages 复用，避免重复实现
from loop_controller.stages import (  # noqa: F401  (re-exported for tests/monkeypatch)
    _CASES_DIR,
    _DEVICE_PROFILE,
    _TARGET_PATHS_YAML,
    _extract_failed_cases,
    _get_workspace_diff,
    _load_session,
    _load_target_paths,
    _resolve_loop_paths,
    _save_session,
)


def add_control_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("control", help="AI 闭环控制——session 管理 + 分阶段编排")
    sub_c = p.add_subparsers(dest="control_cmd", required=True)

    i = sub_c.add_parser("init", help="初始化 session")
    i.add_argument("--target", default="lciod")
    i.add_argument("--max-attempts", type=int, default=5)
    i.add_argument("--artifacts-dir", required=True)
    i.set_defaults(func=_handle_control_init)

    rv = sub_c.add_parser("run-verify", help="执行一次验证")
    rv.add_argument("--session", required=True)
    rv.add_argument("--suite", required=True)
    rv.add_argument("--adb-endpoint", default="")
    rv.set_defaults(func=_handle_control_run_verify)

    ar = sub_c.add_parser("analyze-request", help="生成 analysis_request.json")
    ar.add_argument("--session", required=True)
    ar.set_defaults(func=_handle_control_analyze_request)

    dp = sub_c.add_parser("deploy", help="部署当前改动")
    dp.add_argument("--session", required=True)
    dp.add_argument("--adb-endpoint", default="")
    dp.set_defaults(func=_handle_control_deploy)

    dc = sub_c.add_parser("decide", help="判定下一步")
    dc.add_argument("--session", required=True)
    dc.set_defaults(func=_handle_control_decide)

    st = sub_c.add_parser("status", help="查看 session 状态")
    st.add_argument("--session", required=True)
    st.set_defaults(func=_handle_control_status)

    ap = sub_c.add_parser("apply-patch", help="应用 AI 生成的补丁（含白名单+语法校验+stash 备份）")
    ap.add_argument("--session", required=True)
    ap.add_argument("--patch", required=True, help="patch.json 路径（FileChange[] 序列化）")
    ap.add_argument("--workspace-root", default="", help="workspace 根路径，缺省从 AOSP_ROOT 获取")
    ap.set_defaults(func=_handle_control_apply_patch)

    cp = sub_c.add_parser("compile", help="编译当前 workspace 改动（不部署）")
    cp.add_argument("--session", required=True)
    cp.add_argument("--workspace-root", default="")
    cp.set_defaults(func=_handle_control_compile)

    rv = sub_c.add_parser("revert", help="回滚最近一次 apply-patch")
    rv.add_argument("--session", required=True)
    rv.set_defaults(func=_handle_control_revert)


def _handle_control_init(args: argparse.Namespace) -> int:
    sid = f"{args.target}-{time.strftime('%Y%m%d%H%M%S')}"
    session = {
        "session_id": sid,
        "workflow_id": f"{args.target}-verify",
        "target": args.target,
        "max_attempts": args.max_attempts,
        "current_attempt": 0,
        "status": "PENDING",
        "attempts": [],
        "artifacts_dir": args.artifacts_dir,
    }
    _save_session(session, args.artifacts_dir)
    print(f"session_id={sid}")
    print(f"artifacts_dir={args.artifacts_dir}")
    return 0


def _handle_control_run_verify(args: argparse.Namespace) -> int:
    from loop_controller.stages import run_verify_stage
    session_data, stage = run_verify_stage(args.session, args.suite, args.adb_endpoint)
    artifacts_dir = session_data.get(
        "artifacts_dir",
        os.path.dirname(args.session) if os.path.isfile(args.session) else args.session,
    )
    _save_session(session_data, artifacts_dir)
    print(f"verify={stage.status} attempt={session_data['current_attempt']}")
    return 0 if stage.status == "PASS" else 1


def _handle_control_analyze_request(args: argparse.Namespace) -> int:
    from loop_controller.stages import analyze_request_stage
    session_data = _load_session(args.session)
    req_path = analyze_request_stage(session_data)
    print(f"analysis_request={req_path}")
    return 0


def _handle_control_deploy(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    artifacts_dir = session_data.get("artifacts_dir", os.path.dirname(args.session))

    cmd = [sys.executable, "-m", "loop_core.cli", "deploy", "--diff-rev", "HEAD"]
    if args.adb_endpoint:
        cmd += ["--adb-endpoint", args.adb_endpoint]
    env = os.environ.copy()
    extra_path = ":".join(p for p in sys.path if "loop" in p or "engineering" in p)
    if extra_path:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{extra_path}:{existing}" if existing else extra_path

    deploy_success = False
    deploy_error = ""
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600, env=env)
        rc = result.returncode
        deploy_success = (rc == 0)
        if not deploy_success:
            deploy_error = (result.stderr or result.stdout or "")[-500:]
    except subprocess.TimeoutExpired:
        rc = 1
        deploy_error = "deploy timed out (60min)"
    except (OSError, ValueError) as e:
        rc = 1
        deploy_error = f"deploy subprocess error: {e}"

    session_data.setdefault("attempts", []).append({
        "attempt_index": session_data.get("current_attempt", 0),
        "verify_result": "DEPLOYED" if deploy_success else "DEPLOY_FAILED",
        "evidence_path": "",
        "failed_cases": [],
        "failure_code": "" if deploy_success else "DEPLOY_FATAL",
        "deploy_result": "SUCCESS" if deploy_success else "FAILED",
        "deploy_error": deploy_error,
    })
    _save_session(session_data, artifacts_dir)

    if deploy_success:
        print(f"deploy=OK")
        return 0
    else:
        print(f"deploy=FAILED error={deploy_error}", file=sys.stderr)
        return 1


def _handle_control_decide(args: argparse.Namespace) -> int:
    from loop_controller.stages import decide_stage
    session_data = _load_session(args.session)
    d = decide_stage(session_data)
    print(f"decision={d['decision']} reason={d['reason']} code={d['failure_code']} escalate={str(d['should_escalate']).lower()}")
    return 0


def _handle_control_status(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    print(json.dumps(session_data, indent=2, ensure_ascii=False))
    return 0


def _handle_control_apply_patch(args: argparse.Namespace) -> int:
    import hashlib
    from loop_controller.analyzer_protocol import FileChange
    from loop_controller.patch_guard import check_white_list, detect_risk, check_syntax
    from loop_controller.patch_applier import apply_file_changes

    session_data = _load_session(args.session)
    artifacts_dir = session_data.get("artifacts_dir", os.path.dirname(args.session))
    target = session_data.get("target", "")

    patch_path = Path(args.patch)
    if not patch_path.exists():
        print(f"patch file not found: {args.patch}", file=sys.stderr)
        return 1
    try:
        raw_changes = json.loads(patch_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        print(f"invalid patch file: {e}", file=sys.stderr)
        return 1

    changes = [FileChange(**c) for c in raw_changes]

    allowed = _load_target_paths(target)
    guard_result = check_white_list(changes, allowed)
    if not guard_result.allowed:
        print(f"PATCH_REJECTED: files outside white list: {guard_result.rejected_files}", file=sys.stderr)
        return 1

    ws_root = args.workspace_root or os.environ.get("AOSP_ROOT", os.path.expanduser("~/workspace/aosp"))
    syntax_errors = check_syntax(changes, ws_root)
    if syntax_errors:
        for err in syntax_errors:
            print(f"SYNTAX_ERROR: {err}", file=sys.stderr)
        return 1

    stash_ref = ""
    try:
        stash_result = subprocess.run(
            ["git", "stash", "create", "-u"],
            capture_output=True, text=True, timeout=10, cwd=ws_root,
        )
        stash_ref = stash_result.stdout.strip() or ""
    except (subprocess.SubprocessError, OSError):
        stash_ref = ""

    result = apply_file_changes(changes, ws_root)
    if not result.success:
        if stash_ref:
            rollback_r = subprocess.run(
                ["git", "stash", "apply", stash_ref],
                capture_output=True, text=True, timeout=10, cwd=ws_root,
            )
            if rollback_r.returncode != 0:
                print(f"WARNING: rollback failed (stash apply rc={rollback_r.returncode}): "
                      f"{(rollback_r.stderr or '')[:200]}", file=sys.stderr)
        print(f"apply failed: {result.error}", file=sys.stderr)
        return 1

    risk = detect_risk(changes)
    patch_hash = hashlib.sha256(json.dumps(raw_changes, sort_keys=True).encode()).hexdigest()

    current_attempt = session_data.get("current_attempt", 0)
    session_data.setdefault("attempts", []).append({
        "attempt_index": current_attempt,
        "verify_result": "APPLIED",
        "evidence_path": "",
        "failed_cases": [],
        "failure_code": "",
        "patch_applied": {
            "files": result.applied_files,
            "stash_ref": stash_ref,
            "patch_hash": patch_hash,
            "risk": risk,
            "workspace_root": ws_root,
        },
    })
    _save_session(session_data, artifacts_dir)
    print(f"apply=OK files={result.applied_files} risk={risk}")
    return 0


def _handle_control_compile(args: argparse.Namespace) -> int:
    from loop_deploy.compiler import compile_plan
    from loop_deploy.decider import get_diff_files, decide
    from loop_deploy.models import DeployPlan, DeployMode

    session_data = _load_session(args.session)
    artifacts_dir = session_data.get("artifacts_dir", os.path.dirname(args.session))
    ws_root = args.workspace_root or os.environ.get("AOSP_ROOT", os.path.expanduser("~/workspace/aosp"))

    try:
        diff_files = get_diff_files("HEAD")
    except RuntimeError as e:
        print(f"compile failed: cannot get diff: {e}", file=sys.stderr)
        return 1
    plan = decide(diff_files)

    if plan.mode == DeployMode.SKIP and diff_files:
        _COMPILABLE_SUFFIXES = {".cpp", ".c", ".cc", ".h", ".hpp", ".bp", ".java", ".kt"}
        has_code = any(Path(f).suffix.lower() in _COMPILABLE_SUFFIXES for f in diff_files)
        if has_code:
            plan = DeployPlan(
                mode=DeployMode.PUSH_SINGLE,
                changed_files=diff_files,
                reason="manual compile (code files detected despite SKIP decision)",
                build_targets=[],
                deploy_targets=[],
                requires_reboot=False,
                estimated_seconds=600,
            )

    result = compile_plan(plan, ws_root)

    session_data.setdefault("attempts", []).append({
        "attempt_index": session_data.get("current_attempt", 0),
        "verify_result": "COMPILED" if result.success else "COMPILE_FAILED",
        "evidence_path": "",
        "failed_cases": [],
        "failure_code": "" if result.success else "COMPILE_FAILED",
        "compile_result": "SUCCESS" if result.success else "FAILED",
        "compile_artifacts": result.artifacts,
        "compile_error": result.error,
    })
    _save_session(session_data, artifacts_dir)

    if result.success:
        print(f"compile=OK artifacts={result.artifacts}")
        return 0
    else:
        print(f"compile=FAILED error={result.error}", file=sys.stderr)
        return 1


def _handle_control_revert(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    artifacts_dir = session_data.get("artifacts_dir", os.path.dirname(args.session))
    attempts = session_data.get("attempts", [])
    if not attempts:
        print("no attempts to revert", file=sys.stderr)
        return 1

    for att in reversed(attempts):
        patch_applied = att.get("patch_applied", {})
        stash_ref = patch_applied.get("stash_ref", "")
        if stash_ref:
            ws_root = patch_applied.get("workspace_root", "")
            try:
                result = subprocess.run(
                    ["git", "stash", "apply", stash_ref],
                    capture_output=True, text=True, timeout=30,
                    cwd=ws_root or None,
                )
            except (subprocess.SubprocessError, OSError) as e:
                print(f"revert error: {e}", file=sys.stderr)
                return 1
            if result.returncode != 0:
                print(f"revert failed: {result.stderr}", file=sys.stderr)
                return 1
            att["reverted"] = True
            _save_session(session_data, artifacts_dir)
            print(f"revert=OK stash_ref={stash_ref}")
            return 0

    print("no stash ref found in any attempt", file=sys.stderr)
    return 1
