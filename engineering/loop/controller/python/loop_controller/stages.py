"""stages：从旧 control_cli 提取的可复用阶段 handlers（纯函数，供 runtime 直接调用）。"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import StageResult
from loop_controller.analyzer_protocol import AnalysisRequest


# ---------------------------------------------------------------------------
# 路径常量（与 control_cli 相同的解析逻辑）
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


_CASES_DIR, _DEVICE_PROFILE, _TARGET_PATHS_YAML = _resolve_loop_paths()


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


def _get_workspace_diff() -> str:
    try:
        result = subprocess.run(["git", "diff", "HEAD"], capture_output=True, text=True, timeout=10)
        return result.stdout[:2000]
    except (subprocess.SubprocessError, OSError):
        return ""


def _load_target_paths(target: str, target_paths_yaml: str = "") -> list[str]:
    import yaml
    config_path = Path(target_paths_yaml or _TARGET_PATHS_YAML)
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
                     cases_dir: str = "", device_profile: str = "") -> tuple[dict, StageResult]:
    """执行一次验证，返回 (updated_session_dict, StageResult)。"""
    _cases = cases_dir or _CASES_DIR
    _profile = device_profile or _DEVICE_PROFILE
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
        "--case-dirs", _cases,
        "--device-profile", _profile,
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

    session_data.setdefault("attempts", []).append({
        "attempt_index": attempt,
        "verify_result": status,
        "evidence_path": evidence_path,
        "failed_cases": _extract_failed_cases(bundle_data),
        "failure_code": "RUN_FAILED" if status == "FAIL" else "",
    })

    fc = FailureCode.NONE if status == "PASS" else FailureCode.RUN_FAILED
    return session_data, StageResult(stage_name="RUN_VERIFY", status=status, failure_code=fc)


def analyze_request_stage(session_data: dict) -> str:
    """从 session 最近一次 attempt 构造 AnalysisRequest，写 analysis_request.json，返回路径。"""
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
    )
    req_path = os.path.join(artifacts_dir, "analysis_request.json")
    Path(req_path).write_text(
        json.dumps(dataclasses.asdict(request), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return req_path


def decide_stage(session_data: dict) -> dict[str, object]:
    """判定下一步：返回 {decision, reason, should_escalate, failure_code}。"""
    from loop_controller.policy import decide_termination

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

    # 3) delegate to policy.decide_termination
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
    return {
        "decision": decision.decision,
        "reason": reason_slug,
        "should_escalate": decision.should_escalate,
        "failure_code": decision.reason_code.value,
    }
