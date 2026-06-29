"""stages：从旧 control_cli 提取的可复用阶段 handlers（纯函数，供 runtime 直接调用）。"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import StageResult, TerminationDecision
from loop_controller.analyzer_protocol import AnalysisRequest


# ---------------------------------------------------------------------------
# StageContext：每会话 stage 执行上下文，消除模块级全局状态
# ---------------------------------------------------------------------------
@dataclass
class StageContext:
    """Per-session stage 执行上下文，消除模块级全局状态。

    通过显式参数注入，替代对模块级 _CASES_DIR / _DEVICE_PROFILE 的隐式依赖。
    所有字段缺省空串，保证向后兼容（未传入时回退到模块级全局变量）。
    """
    cases_dir: str = ""
    device_profile: str = ""
    artifacts_dir: str = ""
    session_id: str = ""


# ---------------------------------------------------------------------------
# 路径解析（延迟调用，不再缓存到模块级全局变量，消除多 session 并发污染）
# ---------------------------------------------------------------------------
def _resolve_loop_paths() -> tuple[str, str, str]:
    """返回 (cases_dir, device_profile, target_paths_yaml) 绝对路径。"""
    try:
        from harness_path_util import path as _hp
        loop_dir = _hp("LOOP_DIR")
        return (
            str(_hp("LOOP_CASES_DIR")),
            str(loop_dir / "connection" / "profiles" / "devices" / "rp5" / "adb.json"),
            str(loop_dir / "config" / "target-paths.yaml"),
        )
    except (ImportError, RuntimeError, KeyError):
        loop_dir = Path(__file__).resolve().parent.parent.parent.parent
        return (
            str(loop_dir / "cases"),
            str(loop_dir / "connection" / "profiles" / "devices" / "rp5" / "adb.json"),
            str(loop_dir / "config" / "target-paths.yaml"),
        )


def _get_default_paths() -> tuple[str, str, str]:
    """延迟解析路径（仅在 ctx 和显式参数均未提供时调用）。"""
    return _resolve_loop_paths()


# ---------------------------------------------------------------------------
# 通用 helpers（从 control_cli 迁移）
# ---------------------------------------------------------------------------
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


def _save_session(session: dict, artifacts_dir: str) -> None:
    sid = session.get("session_id", "unknown")
    p = Path(artifacts_dir) / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")
    latest = Path(artifacts_dir) / "session.json"
    latest.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")


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


def _extract_case_results(bundle_data: dict) -> tuple[list[dict], int]:
    """提取逐用例结构化结果与失败用例数。

    返回 (case_results, failed_count)：
    - case_results：每个用例的 {id, status}（pass/fail/error/skip）
    - failed_count：status 为 fail 或 error 的用例数
    供收敛判定（progress_converging）按用例粒度比较进度。
    """
    cases = bundle_data.get("cases", [])
    case_results = [
        {"id": c.get("id", ""), "status": c.get("status", "")}
        for c in cases
    ]
    failed_count = sum(1 for c in cases if c.get("status") in ("fail", "error"))
    return case_results, failed_count


def _get_workspace_diff() -> str:
    try:
        result = subprocess.run(["git", "diff", "HEAD"], capture_output=True, text=True, timeout=10)
        return result.stdout[:2000]
    except (subprocess.SubprocessError, OSError):
        return ""


def _load_target_paths(target: str, target_paths_yaml: str = "") -> list[str]:
    import yaml
    if not target_paths_yaml:
        _, _, target_paths_yaml = _get_default_paths()
    config_path = Path(target_paths_yaml)
    if not config_path.exists():
        return []
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (yaml.YAMLError, OSError):
        return []
    entries = data.get(target, []) if isinstance(data, dict) else []
    return list(entries)


def _build_env() -> dict:
    env = os.environ.copy()
    extra_path = ":".join(p for p in sys.path if "loop" in p or "engineering" in p)
    if extra_path:
        existing = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{extra_path}:{existing}" if existing else extra_path
    return env


# ---------------------------------------------------------------------------
# 阶段 handlers（纯函数）
# ---------------------------------------------------------------------------
def run_verify_stage(session_path: str, suite: str, adb_endpoint: str,
                     cases_dir: str = "", device_profile: str = "",
                     *,
                     ctx: StageContext | None = None) -> tuple[dict, StageResult]:
    """执行一次验证，返回 (updated_session_dict, StageResult)。

    参数优先级：显式 cases_dir/device_profile > ctx（若传入）> 延迟解析默认路径。
    """
    if ctx is not None:
        cases_dir = cases_dir or ctx.cases_dir
        device_profile = device_profile or ctx.device_profile
    if not cases_dir or not device_profile:
        default_cases, default_profile, _ = _get_default_paths()
        cases_dir = cases_dir or default_cases
        device_profile = device_profile or default_profile
    session_data = _load_session(session_path)
    # 与旧 control_cli 保持一致的空 session 回退语义
    if not session_data:
        session_data = {
            "session_id": os.path.basename(session_path) if not os.path.isdir(session_path) else "unknown",
            "current_attempt": 0,
            "max_attempts": 5,
        }
    artifacts_dir = session_data.get(
        "artifacts_dir",
        os.path.dirname(session_path) if os.path.isfile(session_path) else session_path,
    )
    attempt = session_data.get("current_attempt", 0) + 1

    cmd = [
        sys.executable, "-m", "loop_core.cli", "run",
        "--suite", suite,
        "--case-dirs", cases_dir,
        "--device-profile", device_profile,
        "--artifacts-dir", artifacts_dir,
    ]
    if adb_endpoint:
        cmd += ["--adb-endpoint", adb_endpoint]

    try:
        result = subprocess.run(cmd, capture_output=False, timeout=600, env=_build_env())
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

    case_results, failed_count = _extract_case_results(bundle_data)

    session_data.setdefault("attempts", []).append({
        "attempt_index": attempt,
        "verify_result": status,
        "evidence_path": evidence_path,
        "failed_cases": _extract_failed_cases(bundle_data),
        "case_results": case_results,
        "failed_count": failed_count,
        "failure_code": "RUN_FAILED" if status == "FAIL" else "",
    })

    fc = FailureCode.NONE if status == "PASS" else FailureCode.RUN_FAILED
    return session_data, StageResult(stage_name="RUN_VERIFY", status=status, failure_code=fc)


def _summarize_failure(attempt: dict) -> str:
    """从 attempt 生成一行失败摘要（优先 compile_error 首行，其次 failed_case id，最后 failure_code）。"""
    compile_error = (attempt.get("compile_result") or {}).get("error", "")
    if compile_error:
        return compile_error.splitlines()[0][:200]
    failed_cases = attempt.get("failed_cases") or []
    if failed_cases:
        ids = [c.get("id", "?") for c in failed_cases[:5]]
        return f"failed: {', '.join(ids)}"
    fc = attempt.get("failure_code", "")
    return fc or "unknown"


def _build_prior_attempts(attempts: list[dict]) -> list[dict]:
    """从 session attempts 投影精简轨迹（排除最后一轮=当前轮，跳过无补丁的纯 verify 轮）。"""
    prior = []
    for i, a in enumerate(attempts[:-1]):
        patch_applied = a.get("patch_applied") or {}
        if not patch_applied:
            continue
        prior.append({
            "attempt_index": a.get("attempt_index", i),
            "patch_hash": patch_applied.get("patch_hash", ""),
            "failure_code": a.get("failure_code", ""),
            "failed_count": a.get("failed_count", 0),
            "patch_files": patch_applied.get("files", []),
            "failure_summary": _summarize_failure(a),
        })
    return prior


def analyze_request_stage(session_data: dict, *,
                          ctx: StageContext | None = None) -> str:
    """从 session 最近一次 attempt 构造 AnalysisRequest，写 analysis_request.json，返回路径。"""
    del ctx  # 当前函数体不使用路径变量，保留参数保持签名一致性
    artifacts_dir = session_data.get("artifacts_dir", "")
    attempts = session_data.get("attempts", [])
    last = attempts[-1] if attempts else {}

    failed_cases = last.get("failed_cases", [])
    evidence_path = last.get("evidence_path", "")
    collectors_output: dict = {}
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
        target=session_data.get("target", ""),
        suite=session_data.get("suite", ""),
        prior_attempts=_build_prior_attempts(session_data.get("attempts", [])),
    )
    req_path = os.path.join(artifacts_dir, "analysis_request.json")
    Path(req_path).write_text(
        json.dumps(dataclasses.asdict(request), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return req_path


def decide_stage(session_data: dict, *,
                 ctx: StageContext | None = None) -> dict[str, object]:
    """判定下一步：返回 {decision, reason, should_escalate, failure_code}。"""
    del ctx  # 当前函数体不使用路径变量，保留参数保持签名一致性
    status = session_data.get("status", "PENDING")
    current = session_data.get("current_attempt", 0)
    max_att = session_data.get("max_attempts", 5)
    attempts = session_data.get("attempts", [])
    last = attempts[-1] if attempts else {}

    # 1) repeated failure code (连续两次相同 failure_code)
    if len(attempts) >= 2:
        prev_fc = attempts[-2].get("failure_code", "")
        curr_fc = last.get("failure_code", "")
        if prev_fc and curr_fc and prev_fc == curr_fc:
            return {
                "decision": "STOP",
                "reason": "same_failure_repeated",
                "should_escalate": True,
                "failure_code": curr_fc,
            }

    # 2) duplicate patch hash
    patch_applied = last.get("patch_applied", {})
    current_hash = patch_applied.get("patch_hash", "")
    if current_hash:
        for att in attempts[:-1]:
            prev_hash = att.get("patch_applied", {}).get("patch_hash", "")
            if prev_hash and prev_hash == current_hash:
                return {
                    "decision": "STOP",
                    "reason": "duplicate_patch_detected",
                    "should_escalate": True,
                    "failure_code": FailureCode.DUPLICATE_PATCH.value,
                }

    # 3) inline termination decision (原 policy.decide_termination 逻辑)
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

    # ---- inline decide_termination（PASS / max_attempts / repeated fc / RETRY）----
    if latest_stage.status == "PASS":
        decision = TerminationDecision(
            decision="STOP",
            reason_code=FailureCode.NONE,
            reason_summary="verification passed",
            can_retry=False,
            should_escalate=False,
        )
    elif current > max_att:
        decision = TerminationDecision(
            decision="STOP",
            reason_code=FailureCode.REPEATED_FAILURE,
            reason_summary="max attempts exceeded",
            can_retry=False,
            should_escalate=True,
        )
    elif prev_codes and latest_stage.failure_code == prev_codes[-1]:
        decision = TerminationDecision(
            decision="STOP",
            reason_code=FailureCode.REPEATED_FAILURE,
            reason_summary="same failure repeated",
            can_retry=False,
            should_escalate=True,
        )
    else:
        decision = TerminationDecision(
            decision="RETRY",
            reason_code=latest_stage.failure_code,
            reason_summary="retry allowed",
            can_retry=True,
            should_escalate=False,
        )

    reason_slug = decision.reason_summary.replace(" ", "_")
    return {
        "decision": decision.decision,
        "reason": reason_slug,
        "should_escalate": decision.should_escalate,
        "failure_code": decision.reason_code.value,
    }
