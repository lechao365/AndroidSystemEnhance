"""cycle_orchestrator：分阶段编排辅助，被 control_cli 和主会话调用。"""
from __future__ import annotations

import json
from pathlib import Path
from loop_contracts.models import SessionState, StageResult, TerminationDecision
from loop_contracts.failure_codes import FailureCode
from loop_controller.policy import decide_termination
from loop_controller.engine import apply_stage_result
from loop_controller.analyzer_protocol import AnalysisRequest


def build_analysis_request(
    session: SessionState,
    evidence_bundle_path: str,
    workspace_diff: str = "",
) -> AnalysisRequest:
    attempt_index = session.current_attempt
    failed_cases = []
    collectors_output = {}

    if evidence_bundle_path and Path(evidence_bundle_path).exists():
        bundle = json.loads(Path(evidence_bundle_path).read_text(encoding="utf-8"))
        for case in bundle.get("cases", []):
            if case.get("status") in ("fail", "error"):
                failed_cases.append({
                    "id": case.get("id", ""),
                    "status": case.get("status", ""),
                    "failure_reason": case.get("failure_reason", ""),
                    "command": case.get("command", ""),
                })
        collectors_output = bundle.get("evidence", {})

    return AnalysisRequest(
        session_id=session.session_id,
        attempt_index=attempt_index,
        failed_cases=failed_cases,
        evidence_bundle_path=evidence_bundle_path,
        collectors_output=collectors_output,
        workspace_diff_so_far=workspace_diff,
        hints=f"Attempt {attempt_index}/{session.max_attempts}. "
              f"Failed cases: {len(failed_cases)}. Check on_fail collectors for diagnostics.",
    )


def record_stage(session: SessionState, stage_name: str, status: str,
                 summary: str = "", failure_code: FailureCode = FailureCode.NONE) -> SessionState:
    stage = StageResult(stage_name=stage_name, status=status,
                        failure_code=failure_code, summary=summary)
    decision = "pending"
    return apply_stage_result(session, attempt_index=session.current_attempt,
                              stage_result=stage, decision=decision)


def decide_next_from_session(session: SessionState) -> TerminationDecision:
    latest = None
    if session.attempts and session.attempts[-1].stage_results:
        latest = session.attempts[-1].stage_results[-1]
    prev_codes = [r.failure_code for attempt in session.attempts
                  for r in attempt.stage_results if r.failure_code != FailureCode.NONE]
    return decide_termination(
        max_attempts=session.max_attempts,
        current_attempt=session.current_attempt,
        latest_stage=latest or StageResult(stage_name="unknown", status="fail",
                                           failure_code=FailureCode.RUN_FAILED),
        previous_failure_codes=prev_codes,
    )
