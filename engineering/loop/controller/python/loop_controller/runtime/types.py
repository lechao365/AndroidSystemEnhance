from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from loop_contracts.failure_codes import FailureCode


class NodeKind(StrEnum):
    INIT_SESSION = "INIT_SESSION"
    RUN_VERIFY = "RUN_VERIFY"
    DECIDE_NEXT = "DECIDE_NEXT"
    BUILD_ANALYSIS_REQUEST = "BUILD_ANALYSIS_REQUEST"
    WAIT_ANALYZER_PATCH = "WAIT_ANALYZER_PATCH"
    SELECT_BEST_CANDIDATE = "SELECT_BEST_CANDIDATE"
    APPLY_PATCH = "APPLY_PATCH"
    COMPILE_PATCH = "COMPILE_PATCH"
    DEPLOY_PATCH = "DEPLOY_PATCH"
    REVERT_PATCH = "REVERT_PATCH"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    DONE_SUCCESS = "DONE_SUCCESS"
    DONE_FAILURE = "DONE_FAILURE"


@dataclass
class NodeResult:
    node: str
    status: str
    failure_code: FailureCode = FailureCode.NONE
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class TransitionDecision:
    from_node: str
    to_node: str
    matched_guards: list[str]
    reason_summary: str
    should_escalate: bool = False


@dataclass
class GuardEvalRequest:
    guard_name: str
    attempt_count: int
    max_attempts: int
    latest_status: str
    latest_failure_code: FailureCode
    previous_failure_codes: list[FailureCode]
    current_patch_hash: str
    previous_patch_hashes: list[str]
    latest_failed_count: int = 0
    previous_failed_count: int = 0


@dataclass
class GuardEvalResult:
    matched: bool
    guard_name: str = ""
    next_node: str = ""
    reason: str = ""
