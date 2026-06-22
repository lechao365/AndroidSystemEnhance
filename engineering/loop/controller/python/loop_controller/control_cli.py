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


def _load_session(session_ref: str) -> dict:
    if os.path.isfile(session_ref):
        return json.loads(Path(session_ref).read_text(encoding="utf-8"))
    artifacts_dir = os.path.dirname(session_ref) if os.path.isdir(session_ref) else session_ref
    sid_base = os.path.basename(session_ref) if not os.path.isdir(session_ref) else ""
    for f in Path(artifacts_dir).glob("*.json"):
        if sid_base and sid_base in f.name:
            return json.loads(f.read_text(encoding="utf-8"))
    return {}


def _save_session(session: dict, artifacts_dir: str):
    sid = session.get("session_id", "unknown")
    p = Path(artifacts_dir) / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")


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
    artifacts_dir = session_data.get("artifacts_dir", os.path.dirname(args.session))
    sid = session_data.get("session_id", os.path.basename(args.session))
    attempt = session_data.get("current_attempt", 0) + 1

    evidence_path = os.path.join(artifacts_dir, f"evidence_{attempt}.json")
    cmd = [
        sys.executable, "-m", "loop_core.cli", "run",
        "--suite", args.suite,
        "--case-dirs", "engineering/loop/cases",
        "--device-profile", "engineering/loop/connection/profiles/devices/rp5/adb.json",
        "--artifacts-dir", artifacts_dir,
    ]
    if args.adb_endpoint:
        cmd += ["--adb-endpoint", args.adb_endpoint]

    try:
        result = subprocess.run(cmd, capture_output=False, timeout=600)
        rc = result.returncode
    except subprocess.TimeoutExpired:
        rc = 1

    status = "PASS" if rc == 0 else "FAIL"
    session_data["current_attempt"] = attempt
    session_data["status"] = status
    session_data.setdefault("attempts", []).append({
        "attempt_index": attempt,
        "verify_result": status,
        "evidence_path": evidence_path,
    })
    _save_session(session_data, artifacts_dir)
    print(f"verify={status} attempt={attempt}")
    return rc


def _handle_control_analyze_request(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    artifacts_dir = session_data.get("artifacts_dir", os.path.dirname(args.session))
    attempts = session_data.get("attempts", [{}])
    last = attempts[-1] if attempts else {}
    evidence_path = last.get("evidence_path", "")
    raw = _load_session(os.path.join(artifacts_dir, f"evidence_{session_data.get('current_attempt', 0)}.json"))

    request = AnalysisRequest(
        session_id=session_data.get("session_id", ""),
        attempt_index=session_data.get("current_attempt", 0),
        failed_cases=last.get("failed_cases", raw.get("failed_cases", [])),
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
    result = subprocess.run(cmd, timeout=3600)
    return result.returncode


def _handle_control_decide(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    status = session_data.get("status", "PENDING")
    current = session_data.get("current_attempt", 0)
    max_att = session_data.get("max_attempts", 5)
    if status == "PASS":
        print("decision=STOP reason=verification_passed")
    elif current >= max_att:
        print("decision=STOP reason=max_attempts_exceeded should_escalate=true")
    else:
        print(f"decision=RETRY attempt={current}/{max_att}")
    return 0


def _handle_control_status(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    print(json.dumps(session_data, indent=2, ensure_ascii=False))
    return 0
