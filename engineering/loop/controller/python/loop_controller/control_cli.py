"""control_cli：le control 子命令——session 管理 + 分阶段编排。"""
from __future__ import annotations

import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from loop_controller.cycle_orchestrator import build_analysis_request
from loop_controller.analyzer_protocol import AnalysisRequest


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


def _load_session(session_ref: str) -> dict:
    if os.path.isfile(session_ref):
        return json.loads(Path(session_ref).read_text(encoding="utf-8"))
    d = Path(session_ref)
    if d.is_dir():
        latest = d / "session.json"
        if latest.exists():
            return json.loads(latest.read_text(encoding="utf-8"))
        files = sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
        if files:
            return json.loads(files[0].read_text(encoding="utf-8"))
    parent = d.parent
    name = d.name
    if parent.is_dir():
        for f in parent.glob("*.json"):
            if name in f.name:
                return json.loads(f.read_text(encoding="utf-8"))
    return {}


def _save_session(session: dict, artifacts_dir: str):
    sid = session.get("session_id", "unknown")
    p = Path(artifacts_dir) / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
    latest = Path(artifacts_dir) / "session.json"
    latest.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")


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
    session_data = _load_session(args.session)
    if not session_data:
        session_data = {"artifacts_dir": os.path.dirname(args.session) if os.path.isfile(args.session) else args.session,
                        "session_id": os.path.basename(args.session), "current_attempt": 0, "max_attempts": 5}
    artifacts_dir = session_data.get("artifacts_dir", args.session if os.path.isdir(args.session) else os.path.dirname(args.session))
    sid = session_data.get("session_id", os.path.basename(args.session))
    attempt = session_data.get("current_attempt", 0) + 1

    cmd = [
        sys.executable, "-m", "loop_core.cli", "run",
        "--suite", args.suite,
        "--case-dirs", "engineering/loop/cases",
        "--device-profile", "engineering/loop/connection/profiles/devices/rp5/adb.json",
        "--artifacts-dir", artifacts_dir,
    ]
    if args.adb_endpoint:
        cmd += ["--adb-endpoint", args.adb_endpoint]

    env = os.environ.copy()
    extra_path = ":".join(p for p in sys.path if "loop" in p or "engineering" in p)
    if extra_path:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{extra_path}:{existing}" if existing else extra_path

    try:
        result = subprocess.run(cmd, capture_output=False, timeout=600, env=env)
        rc = result.returncode
    except subprocess.TimeoutExpired:
        rc = 1

    status = "PASS" if rc == 0 else "FAIL"
    session_data["current_attempt"] = attempt
    session_data["status"] = status

    bundle_path = os.path.join(artifacts_dir, "evidence_bundle.json")
    if os.path.isfile(bundle_path):
        evidence_path = bundle_path
        try:
            with open(bundle_path, encoding="utf-8") as f:
                bundle_data = json.load(f)
        except (json.JSONDecodeError, OSError):
            bundle_data = {}
    else:
        bundle_data = {}
        evidence_path = ""

    session_data.setdefault("attempts", []).append({
        "attempt_index": attempt,
        "verify_result": status,
        "evidence_path": evidence_path,
        "failed_cases": _extract_failed_cases(bundle_data),
        "failure_code": "RUN_FAILED" if status == "FAIL" else "",
    })
    _save_session(session_data, artifacts_dir)
    print(f"verify={status} attempt={attempt}")
    return rc


def _handle_control_analyze_request(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    artifacts_dir = session_data.get("artifacts_dir", os.path.dirname(args.session))
    attempts = session_data.get("attempts", [])
    last = attempts[-1] if attempts else {}

    failed_cases = last.get("failed_cases", [])

    evidence_path = last.get("evidence_path", "")
    collectors_output = {}
    if evidence_path and os.path.isfile(evidence_path):
        try:
            with open(evidence_path, encoding="utf-8") as f:
                bundle = json.load(f)
            collectors_output = bundle.get("evidence", {})
        except (json.JSONDecodeError, OSError):
            pass

    request = AnalysisRequest(
        session_id=session_data.get("session_id", ""),
        attempt_index=session_data.get("current_attempt", 0),
        failed_cases=failed_cases,
        evidence_bundle_path=evidence_path,
        collectors_output=collectors_output,
        workspace_diff_so_far=_get_workspace_diff(),
    )
    req_path = os.path.join(artifacts_dir, "analysis_request.json")
    Path(req_path).write_text(json.dumps(dataclasses.asdict(request), indent=2, ensure_ascii=False),
                              encoding="utf-8")
    print(f"analysis_request={req_path}")
    return 0


def _handle_control_deploy(args: argparse.Namespace) -> int:
    cmd = [sys.executable, "-m", "loop_core.cli", "deploy", "--diff-rev", "HEAD"]
    if args.adb_endpoint:
        cmd += ["--adb-endpoint", args.adb_endpoint]
    env = os.environ.copy()
    extra_path = ":".join(p for p in sys.path if "loop" in p or "engineering" in p)
    if extra_path:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{extra_path}:{existing}" if existing else extra_path
    result = subprocess.run(cmd, timeout=3600, env=env)
    return result.returncode


def _handle_control_decide(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    from loop_contracts.models import StageResult
    from loop_contracts.failure_codes import FailureCode
    from loop_controller.policy import decide_termination

    status = session_data.get("status", "PENDING")
    current = session_data.get("current_attempt", 0)
    max_att = session_data.get("max_attempts", 5)
    attempts = session_data.get("attempts", [])

    last = attempts[-1] if attempts else {}

    if len(attempts) >= 2:
        prev_fc = attempts[-2].get("failure_code", "")
        curr_fc = last.get("failure_code", "")
        if prev_fc and curr_fc and prev_fc == curr_fc:
            print(f"decision=STOP reason=same_failure_repeated failure_code={curr_fc} escalate=true")
            return 0

    try:
        fc = FailureCode(last.get("failure_code", "RUN_FAILED") or "RUN_FAILED")
    except ValueError:
        fc = FailureCode.RUN_FAILED

    latest_stage = StageResult(
        stage_name="verify",
        status="PASS" if status == "PASS" else "FAIL",
        failure_code=fc,
    )

    prev_codes: list[FailureCode] = []
    for att in attempts[:-1]:
        fc_str = att.get("failure_code", "")
        if fc_str:
            try:
                prev_codes.append(FailureCode(fc_str))
            except ValueError:
                pass

    decision = decide_termination(
        max_attempts=max_att,
        current_attempt=current,
        latest_stage=latest_stage,
        previous_failure_codes=prev_codes,
    )

    reason_slug = decision.reason_summary.replace(" ", "_")
    print(f"decision={decision.decision} reason={reason_slug} code={decision.reason_code.value} escalate={str(decision.should_escalate).lower()}")
    return 0


def _handle_control_status(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    print(json.dumps(session_data, indent=2, ensure_ascii=False))
    return 0


def _extract_failed_cases(bundle_data: dict) -> list[dict]:
    cases = bundle_data.get("cases", [])
    failed = []
    for c in cases:
        if c.get("status") in ("fail", "error"):
            failed.append({
                "id": c.get("id", ""),
                "status": c.get("status", ""),
                "failure_reason": c.get("failure_reason", ""),
                "command": c.get("command", ""),
            })
    return failed


def _get_workspace_diff() -> str:
    try:
        result = subprocess.run(["git", "diff", "HEAD"], capture_output=True, text=True, timeout=10)
        return result.stdout[:2000]
    except (subprocess.SubprocessError, OSError):
        return ""


def _load_target_paths(target: str) -> list[str]:
    import yaml
    config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "target-paths.yaml"
    if not config_path.exists():
        return []
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return []
    entries = data.get(target, []) if isinstance(data, dict) else []
    return list(entries)


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
            subprocess.run(["git", "stash", "apply", stash_ref], capture_output=True, timeout=10, cwd=ws_root)
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
        plan = DeployPlan(
            mode=DeployMode.PUSH_SINGLE,
            changed_files=diff_files,
            reason="manual compile",
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
