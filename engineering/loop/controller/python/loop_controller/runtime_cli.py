"""runtime_cli：新 runtime 主入口——le runtime {init,run,resume,status,explain}。

替代旧 le control 主闭环模式，由 runtime engine 自动驱动状态机。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import LoopSession, RuntimeTerminalState
from loop_controller.runtime.engine import LoopRuntime


# ---------------------------------------------------------------------------
# 路径解析（与 stages._resolve_loop_paths 一致：优先 harness_path_util，回退相对路径）
# ---------------------------------------------------------------------------
def _resolve_paths() -> tuple[str, str]:
    try:
        from harness_path_util import path as _hp

        return (
            str(_hp("LOOP_CASES_DIR")),
            str(
                _hp("LOOP_DIR")
                / "connection"
                / "profiles"
                / "devices"
                / "rp5"
                / "adb.json"
            ),
        )
    except Exception:
        loop_dir = Path(__file__).resolve().parent.parent.parent.parent
        return (
            str(loop_dir / "cases"),
            str(
                loop_dir
                / "connection"
                / "profiles"
                / "devices"
                / "rp5"
                / "adb.json"
            ),
        )


_CASES_DIR, _DEVICE_PROFILE = _resolve_paths()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Loop Runtime CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    init_p = sub.add_parser("init", help="initialize loop session")
    init_p.add_argument("--target", required=True)
    init_p.add_argument("--suite", required=True)
    init_p.add_argument("--max-attempts", type=int, default=5)
    init_p.add_argument("--artifacts-dir", required=True)
    init_p.set_defaults(func=_handle_init)

    run_p = sub.add_parser("run", help="execute full auto-loop")
    run_p.add_argument("--session", required=True)
    run_p.add_argument("--adb-endpoint", default="")
    run_p.set_defaults(func=_handle_run)

    resume_p = sub.add_parser("resume", help="resume from last checkpoint")
    resume_p.add_argument("--session", required=True)
    resume_p.add_argument("--adb-endpoint", default="")
    resume_p.set_defaults(func=_handle_resume)

    status_p = sub.add_parser("status", help="show session state")
    status_p.add_argument("--session", required=True)
    status_p.set_defaults(func=_handle_status)

    explain_p = sub.add_parser("explain", help="explain runtime behavior")
    explain_p.set_defaults(func=_handle_explain)

    args = parser.parse_args(argv)
    return args.func(args)


# ---------------------------------------------------------------------------
# 子命令 handlers
# ---------------------------------------------------------------------------
def _handle_init(args: argparse.Namespace) -> int:
    sid = f"{args.target}-{time.strftime('%Y%m%d%H%M%S')}"
    session = LoopSession(
        session_id=sid,
        workflow_id="runtime",
        target=args.target,
        suite=args.suite,
        max_attempts=args.max_attempts,
        artifacts_dir=args.artifacts_dir,
    )
    out_path = Path(args.artifacts_dir) / f"{sid}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(_session_to_dict(session), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    latest = Path(args.artifacts_dir) / "session.json"
    latest.write_text(
        json.dumps(_session_to_dict(session), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"session_id={sid}")
    print(f"artifacts_dir={args.artifacts_dir}")
    print(f"session_path={out_path}")
    return 0


def _resolve_serial_shell() -> callable | None:
    """尝试加载 rp5-serial helper 作为 serial_shell_provider。

    若加载成功返回 callable(remote_cmd: str) -> str | None；
    若 rp5-serial 不可用返回 None。
    """
    try:
        from rp5_serial_helper import SerialHelper
        helper = SerialHelper()
        return helper.execute
    except (ImportError, Exception):
        return None


def _handle_run(args: argparse.Namespace) -> int:
    try:
        session, ts = _load_session(args.session)
        # 幂等：已终态的 session 不重复执行
        if ts != RuntimeTerminalState.NONE:
            print(f"terminal_state={ts.value}")
            return 0 if ts == RuntimeTerminalState.DONE_SUCCESS else 1
        serial_sh = _resolve_serial_shell()
        rt = LoopRuntime(session, _CASES_DIR, _DEVICE_PROFILE, adb_endpoint=args.adb_endpoint, initial_terminal_state=ts, serial_shell_provider=serial_sh)
        state = rt.run()
        print(f"terminal_state={state.terminal_state.value}")
        if state.terminal_state == RuntimeTerminalState.DONE_SUCCESS:
            return 0
        return 1
    except Exception as e:
        print(f"RUNTIME_FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        _persist_failure(args.session, e)
        return 2


def _handle_resume(args: argparse.Namespace) -> int:
    try:
        session, ts = _load_session(args.session)
        # 幂等：已终态的 session 不续跑
        if ts != RuntimeTerminalState.NONE:
            print(f"terminal_state={ts.value}")
            return 0 if ts == RuntimeTerminalState.DONE_SUCCESS else 1
        serial_sh = _resolve_serial_shell()
        rt = LoopRuntime(session, _CASES_DIR, _DEVICE_PROFILE, adb_endpoint=args.adb_endpoint, initial_terminal_state=ts, serial_shell_provider=serial_sh)
        rt.resume()
        state = rt.run()
        print(f"terminal_state={state.terminal_state.value}")
        if state.terminal_state == RuntimeTerminalState.DONE_SUCCESS:
            return 0
        return 1
    except Exception as e:
        print(f"RUNTIME_FATAL: {type(e).__name__}: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        _persist_failure(args.session, e)
        return 2


def _handle_status(args: argparse.Namespace) -> int:
    session, ts = _load_session(args.session)
    data = _session_to_dict(session)
    data["terminal_state"] = ts.value
    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _handle_explain(args: argparse.Namespace) -> int:
    print("Runtime state machine:")
    print("  INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT")
    print("  DECIDE_NEXT -> DONE_SUCCESS (on PASS)")
    print(
        "  DECIDE_NEXT -> BUILD_ANALYSIS_REQUEST -> WAIT_ANALYZER_PATCH (on RETRY)"
    )
    print(
        "  DECIDE_NEXT -> ESCALATE_HUMAN (on max attempts / repeated failure)"
    )
    print("")
    print(
        "Guards: all_cases_passed, attempt_limit_reached, repeated_failure_code,"
    )
    print(
        "        duplicate_patch_hash, kernel_dead_no_shell, patch_rejected, ..."
    )
    print("")
    print("Terminal states: DONE_SUCCESS, ESCALATE_HUMAN, DONE_FAILURE")
    return 0


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _load_session(path_str: str) -> tuple[LoopSession, RuntimeTerminalState]:
    data = json.loads(Path(path_str).read_text(encoding="utf-8"))
    try:
        fc = FailureCode(data.get("latest_failure_code", "NONE"))
    except ValueError:
        fc = FailureCode.NONE
    session = LoopSession(
        session_id=data.get("session_id", ""),
        workflow_id=data.get("workflow_id", "runtime"),
        target=data.get("target", ""),
        suite=data.get("suite", ""),
        max_attempts=data.get("max_attempts", 5),
        current_attempt=data.get("current_attempt", 0),
        status=data.get("status", "PENDING"),
        latest_failure_code=fc,
        attempts=data.get("attempts", []),
        artifacts_dir=data.get("artifacts_dir", ""),
    )
    ts_str = data.get("terminal_state", "NONE")
    try:
        ts = RuntimeTerminalState(ts_str)
    except ValueError:
        ts = RuntimeTerminalState.NONE
    return session, ts


def _session_to_dict(session: LoopSession) -> dict:
    return {
        "session_id": session.session_id,
        "workflow_id": session.workflow_id,
        "target": session.target,
        "suite": session.suite,
        "max_attempts": session.max_attempts,
        "current_attempt": session.current_attempt,
        "status": session.status,
        "latest_failure_code": session.latest_failure_code.value,
        "attempts": session.attempts,
        "artifacts_dir": session.artifacts_dir,
    }


def _persist_failure(session_path_str: str, e: Exception) -> None:
    """异常时同时写回原始 session 文件和 session.json，标记 DONE_FAILURE。"""
    try:
        p = Path(session_path_str)
        if not p.exists():
            return
        data = json.loads(p.read_text(encoding="utf-8"))
        data["terminal_state"] = RuntimeTerminalState.DONE_FAILURE.value
        data["transition_reason"] = f"RUNTIME_FATAL: {type(e).__name__}: {e}"
        updated = json.dumps(data, indent=2, ensure_ascii=False)
        p.write_text(updated, encoding="utf-8")
        # 同步到 session.json
        artifacts = data.get("artifacts_dir", "")
        if artifacts:
            sp = Path(artifacts) / "session.json"
            if sp != p:
                sp.write_text(updated, encoding="utf-8")
    except Exception:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
