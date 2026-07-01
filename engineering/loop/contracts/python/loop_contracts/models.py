from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum

from loop_contracts.failure_codes import FailureCode


class RuntimeTerminalState(StrEnum):
    NONE = "NONE"
    DONE_SUCCESS = "DONE_SUCCESS"
    DONE_FAILURE = "DONE_FAILURE"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"


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
class LoopSession:
    session_id: str
    workflow_id: str
    target: str
    suite: str
    max_attempts: int
    current_attempt: int = 0
    status: str = "PENDING"
    termination_reason: str = ""
    latest_failure_code: FailureCode = FailureCode.NONE
    attempts: list[dict] = field(default_factory=list)
    artifacts_dir: str = ""
    wall_clock_limit: int = 0
    metrics: SessionMetrics | None = None
    candidates_per_attempt: int = 1


@dataclass
class RuntimeState:
    current_node: str
    previous_node: str = ""
    node_status: str = "PENDING"
    transition_reason: str = ""
    pending_human_gate: bool = False
    human_gate_approved: bool = False
    interrupted: bool = False
    resume_token: str = ""
    last_checkpoint_at: str = ""
    terminal_state: RuntimeTerminalState = RuntimeTerminalState.NONE


@dataclass
class CheckpointRecord:
    checkpoint_id: str
    session_id: str
    attempt_index: int
    current_node: str
    input_summary: dict[str, object]
    output_summary: dict[str, object]
    failure_code: FailureCode
    matched_guards: list[str]
    next_node: str
    timestamp: str
    duration_ms: int = 0
    candidate_id: str = ""

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["failure_code"] = self.failure_code.value
        return data


@dataclass
class TerminationDecision:
    decision: str
    reason_code: FailureCode
    reason_summary: str
    can_retry: bool
    should_escalate: bool


@dataclass
class SessionMetrics:
    """Session 终态指标快照（run() 退出时计算，落盘到 session.json）。"""
    success: bool = False
    terminal_state: str = "NONE"
    attempt_count: int = 0
    wall_clock_used_ms: int = 0
    wall_clock_budget_ms: int = 0
    analyzer_layer_hits: dict[str, int] = field(default_factory=dict)
    analyzer_first_hit_layer: str = ""
    failure_code_distribution: dict[str, int] = field(default_factory=dict)
    human_gate_triggered: bool = False
    human_gate_count: int = 0
    kb_hit: bool = False
    candidates_per_attempt_avg: float = 0.0
    candidate_compile_pass_rate: float = 0.0
    candidate_selected_layer_dist: dict[str, int] = field(default_factory=dict)


# Deprecated alias: 旧 controller 体系产物，保留向后兼容，新代码请用 LoopSession
SessionState = LoopSession
