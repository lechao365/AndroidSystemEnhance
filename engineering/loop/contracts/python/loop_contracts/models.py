from __future__ import annotations

from dataclasses import dataclass, field

from loop_contracts.failure_codes import FailureCode


@dataclass
class StageResult:
    stage_name: str
    status: str
    failure_code: FailureCode = FailureCode.NONE
    summary: str = ""
    artifacts: list[str] = field(default_factory=list)
    next_action_hint: str = ""


@dataclass
class AttemptState:
    attempt_index: int
    stage_results: list[StageResult] = field(default_factory=list)
    run_result_ref: str = ""
    diagnosis_result_ref: str = ""
    patch_result_ref: str = ""
    deploy_result_ref: str = ""
    verify_result_ref: str = ""
    attempt_decision: str = ""


@dataclass
class SessionState:
    session_id: str
    workflow_id: str
    target: str
    max_attempts: int
    current_attempt: int = 0
    status: str = "PENDING"
    termination_reason: str = ""
    attempts: list[AttemptState] = field(default_factory=list)


@dataclass
class TerminationDecision:
    decision: str
    reason_code: FailureCode
    reason_summary: str
    can_retry: bool
    should_escalate: bool
