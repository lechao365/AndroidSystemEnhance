"""runtime_cli：新 runtime 主入口——le runtime {init,run,resume,status,explain}。

替代旧 le control 主闭环模式，由 runtime engine 自动驱动状态机。
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import LoopSession, RuntimeTerminalState
from loop_controller.runtime.engine import LoopRuntime

_logger = logging.getLogger("loop_runtime_cli")


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

    pending_p = sub.add_parser("pending", help="show pending human gate info")
    pending_p.add_argument("--session", required=True)
    pending_p.set_defaults(func=_handle_pending)

    approve_p = sub.add_parser("approve", help="approve pending patch and resume")
    approve_p.add_argument("--session", required=True)
    approve_p.add_argument("--adb-endpoint", default="")
    approve_p.set_defaults(func=_handle_approve)

    reject_p = sub.add_parser("reject", help="reject and escalate to human")
    reject_p.add_argument("--session", required=True)
    reject_p.set_defaults(func=_handle_reject)

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


def _load_analyzer_config() -> dict:
    """读取 engineering/loop/config/analyzer.yaml；缺失或 PyYAML 不可用返回空 dict。"""
    config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "analyzer.yaml"
    if not config_path.is_file():
        return {}
    try:
        import yaml
        return yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _build_analyzer() -> tuple["object", str, float, list[str]]:
    """构建三层降级 ChainedAnalyzer。

    返回 (analyzer, kb_path, confidence_threshold, human_gate_triggers)。
    顺序：KnowledgeBaseAnalyzer → ScriptedAnalyzer → OpencodeAnalyzer。
    """
    from loop_controller.analyzer_protocol import (
        ChainedAnalyzer,
        KnowledgeBaseAnalyzer,
        OpencodeAnalyzer,
        ScriptedAnalyzer,
    )
    cfg = _load_analyzer_config()
    kb_cfg = cfg.get("knowledge_base", {})
    oai_cfg = cfg.get("opencode", {})
    conf_cfg = cfg.get("confidence", {})
    gate_cfg = cfg.get("human_gate", {})
    loop_config_dir = Path(__file__).resolve().parent.parent.parent.parent / "config"
    kb_rel = kb_cfg.get("path", "patch_knowledge_base.json")
    # 兼容配置中带 "config/" 前缀或裸文件名：统一取 basename 后拼到 loop/config 目录
    kb_path = str(loop_config_dir / Path(kb_rel).name)
    ws_root = os.environ.get("AOSP_ROOT", os.path.expanduser("~/workspace/aosp"))
    layers = [
        KnowledgeBaseAnalyzer(
            kb_path,
            hit_confidence=conf_cfg.get("kb_match", 0.98),
        ),
        ScriptedAnalyzer(),
        OpencodeAnalyzer(
            workspace_root=ws_root,
            model=oai_cfg.get("model", ""),
            timeout=oai_cfg.get("timeout", 300),
            binary=oai_cfg.get("binary", "opencode"),
        ),
    ]
    threshold = conf_cfg.get("threshold", 0.7)
    triggers = gate_cfg.get("triggers", ["low_confidence", "kernel_patch", "dd_boot_reboot"]) if gate_cfg.get("enabled", True) else []
    return ChainedAnalyzer(layers), kb_path, threshold, triggers


def _handle_run(args: argparse.Namespace) -> int:
    try:
        session, ts = _load_session(args.session)
        # 幂等：已终态的 session 不重复执行
        if ts != RuntimeTerminalState.NONE:
            print(f"terminal_state={ts.value}")
            return 0 if ts == RuntimeTerminalState.DONE_SUCCESS else 1
        serial_sh = _resolve_serial_shell()
        analyzer, kb_path, conf_threshold, gate_triggers = _build_analyzer()
        rt = LoopRuntime(
            session, _CASES_DIR, _DEVICE_PROFILE,
            adb_endpoint=getattr(args, "adb_endpoint", ""),
            initial_terminal_state=ts,
            serial_shell_provider=serial_sh,
            analyzer=analyzer,
        )
        rt._kb_path = kb_path
        rt._confidence_threshold = conf_threshold
        rt._human_gate_triggers = gate_triggers
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
        analyzer, kb_path, conf_threshold, gate_triggers = _build_analyzer()
        rt = LoopRuntime(
            session, _CASES_DIR, _DEVICE_PROFILE,
            adb_endpoint=getattr(args, "adb_endpoint", ""),
            initial_terminal_state=ts,
            serial_shell_provider=serial_sh,
            analyzer=analyzer,
        )
        rt._kb_path = kb_path
        rt._confidence_threshold = conf_threshold
        rt._human_gate_triggers = gate_triggers
        rt.resume()
        # 传入人工 approve 标记：approve 后 resume 回到 APPLY_PATCH，需跳过 confidence gate 真正 apply
        _raw = json.loads(Path(args.session).read_text(encoding="utf-8"))
        if _raw.get("human_gate_approved"):
            rt._state.human_gate_approved = True
            # 清除 session 中的一次性 approve 标记，避免后续轮次误跳过 gate
            _raw["human_gate_approved"] = False
            Path(args.session).write_text(
                json.dumps(_raw, indent=2, ensure_ascii=False), encoding="utf-8")
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


def _handle_pending(args: argparse.Namespace) -> int:
    """显示 pending human gate 的待确认信息（node/status/gate/patch 路径）。"""
    data = json.loads(Path(args.session).read_text(encoding="utf-8"))
    node = data.get("current_node", "?")
    status = data.get("node_status", "?")
    gate = data.get("pending_human_gate", False)
    print(f"node={node} status={status} pending_human_gate={gate}")
    if gate:
        artifacts = data.get("artifacts_dir", "")
        patch_path = Path(artifacts) / "patch_suggestion.json" if artifacts else None
        if patch_path and patch_path.is_file():
            print(f"patch: {patch_path}")
    return 0


def _handle_approve(args: argparse.Namespace) -> int:
    """批准待确认补丁：清 pending_human_gate、标记 APPROVED，然后 resume 续跑。

    P0-2：必须同时把 terminal_state 复位为 NONE。human gate 暂停时 session.json
    可能同时落盘了 terminal_state=ESCALATE_HUMAN，若不清除，_handle_resume 的
    幂等检查（ts != NONE → return）会让 approve 无法续跑（死锁）。
    """
    p = Path(args.session)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["pending_human_gate"] = False
    data["node_status"] = "APPROVED"
    data["human_gate_approved"] = True
    data["terminal_state"] = RuntimeTerminalState.NONE.value
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return _handle_resume(args)


def _handle_reject(args: argparse.Namespace) -> int:
    """拒绝待确认补丁：设终态 ESCALATE_HUMAN 并写回 session 文件。"""
    p = Path(args.session)
    data = json.loads(p.read_text(encoding="utf-8"))
    data["pending_human_gate"] = False
    data["terminal_state"] = RuntimeTerminalState.ESCALATE_HUMAN.value
    data["transition_reason"] = "human rejected patch"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    print("terminal_state=ESCALATE_HUMAN")
    return 1


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
    except Exception as e:
        # P2-3：session.json 同步失败不阻断 CLI，但需记录诊断
        _logger.warning("session.json 同步失败: %s", e)


if __name__ == "__main__":
    raise SystemExit(main())
