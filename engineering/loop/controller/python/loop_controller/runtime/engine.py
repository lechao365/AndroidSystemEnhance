from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from loop_contracts.failure_codes import FailureCode
from loop_contracts.models import (
    CheckpointRecord,
    LoopSession,
    RuntimeState,
    RuntimeTerminalState,
)
from loop_controller.runtime.types import NodeKind
from loop_controller.runtime.checkpoint_store import CheckpointStore

import loop_controller.stages as stages

# Linear transitions: node -> next node (no branch condition required).
_LINEAR_NEXT: dict[str, str] = {
    NodeKind.INIT_SESSION.value: NodeKind.RUN_VERIFY.value,
    NodeKind.RUN_VERIFY.value: NodeKind.DECIDE_NEXT.value,
    NodeKind.BUILD_ANALYSIS_REQUEST.value: NodeKind.WAIT_ANALYZER_PATCH.value,
    NodeKind.WAIT_ANALYZER_PATCH.value: NodeKind.APPLY_PATCH.value,
    NodeKind.APPLY_PATCH.value: NodeKind.COMPILE_PATCH.value,
    NodeKind.COMPILE_PATCH.value: NodeKind.DEPLOY_PATCH.value,
    NodeKind.DEPLOY_PATCH.value: NodeKind.RUN_VERIFY.value,
    NodeKind.REVERT_PATCH.value: NodeKind.DECIDE_NEXT.value,
}


class LoopRuntime:
    """State-graph runtime engine for loop automation.

    Drives INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT -> (DONE_SUCCESS |
    BUILD_ANALYSIS_REQUEST -> WAIT_ANALYZER_PATCH -> ESCALATE_HUMAN).
    """

    def __init__(self, session: LoopSession, cases_dir: str, device_profile: str) -> None:
        self._session = session
        self._state = RuntimeState(current_node=NodeKind.INIT_SESSION.value)
        self._store = CheckpointStore(session.artifacts_dir, session.session_id)
        # Inject paths into stages module (same as control_cli does)
        stages._CASES_DIR = cases_dir
        stages._DEVICE_PROFILE = device_profile

    def resume(self) -> RuntimeState:
        cp = self._store.latest()
        if cp:
            self._state.current_node = cp.next_node
            self._state.previous_node = cp.current_node
            self._state.interrupted = False
            self._state.last_checkpoint_at = cp.timestamp
        return self._state

    def run(self) -> RuntimeState:
        while self._state.terminal_state == RuntimeTerminalState.NONE:
            self._execute_current_node()
            if self._state.terminal_state != RuntimeTerminalState.NONE:
                break
            self._transition()
        return self._state

    # -- node execution -----------------------------------------------------

    def _execute_current_node(self) -> None:
        node = self._state.current_node
        if node == NodeKind.INIT_SESSION.value:
            self._state.node_status = "INITIALIZED"
            self._checkpoint("session initialized", FailureCode.NONE)
        elif node == NodeKind.RUN_VERIFY.value:
            self._execute_run_verify()
        elif node == NodeKind.DECIDE_NEXT.value:
            self._execute_decide_next()
        elif node == NodeKind.BUILD_ANALYSIS_REQUEST.value:
            self._execute_build_analysis_request()
        elif node == NodeKind.WAIT_ANALYZER_PATCH.value:
            self._execute_wait_analyzer_patch()
        elif node == NodeKind.APPLY_PATCH.value:
            # Placeholder: full implementation arrives in Task 4 (nodes.py)
            self._state.node_status = "PATCH_NODE_PENDING"
        elif node == NodeKind.COMPILE_PATCH.value:
            self._state.node_status = "COMPILE_NODE_PENDING"
        elif node == NodeKind.DEPLOY_PATCH.value:
            self._state.node_status = "DEPLOY_NODE_PENDING"
        elif node == NodeKind.REVERT_PATCH.value:
            self._state.node_status = "REVERT_NODE_PENDING"

    def _execute_run_verify(self) -> None:
        session_path = Path(self._session.artifacts_dir) / "session.json"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            json.dumps(self._to_session_dict(), ensure_ascii=False), encoding="utf-8"
        )
        updated, stage_result = stages.run_verify_stage(
            str(session_path), self._session.suite, ""
        )
        self._session.current_attempt = updated.get("current_attempt", self._session.current_attempt)
        self._session.status = updated.get("status", stage_result.status)
        self._session.attempts = updated.get("attempts", self._session.attempts)
        self._session.latest_failure_code = stage_result.failure_code
        self._state.node_status = stage_result.status
        self._checkpoint(f"verify {stage_result.status}", stage_result.failure_code)

    def _execute_decide_next(self) -> None:
        from loop_controller.runtime.guards import guard_chain
        decision = stages.decide_stage(self._to_session_dict())
        self._state.transition_reason = str(decision.get("reason", ""))
        try:
            fc = FailureCode(decision.get("failure_code", "NONE"))
        except ValueError:
            fc = FailureCode.NONE
        guard_req = self._build_guard_eval_request()
        guard_result = guard_chain(
            [
                "all_cases_passed",
                "attempt_limit_reached",
                "repeated_failure_code",
                "duplicate_patch_hash",
                "kernel_dead_no_shell",
                "patch_rejected",
                "attempts_below_limit",
            ],
            guard_req,
        )
        matched_guards: list[str] = []
        if guard_result.matched:
            matched_guards.append(guard_result.guard_name)
            next_nk = NodeKind(guard_result.next_node)
            if next_nk == NodeKind.ESCALATE_HUMAN:
                self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                self._state.pending_human_gate = True
            elif next_nk == NodeKind.DONE_SUCCESS:
                self._state.terminal_state = RuntimeTerminalState.DONE_SUCCESS
            # Non-terminal guard → drive transition via _compute_next_node
            self._state.node_status = "RETRY" if self._state.terminal_state == RuntimeTerminalState.NONE else guard_result.reason
        else:
            self._state.node_status = "RETRY"
        self._checkpoint(
            f"decide={guard_result.reason or 'RETRY'}",
            fc,
            matched_guards=matched_guards,
        )

    def _execute_build_analysis_request(self) -> None:
        stages.analyze_request_stage(self._to_session_dict())
        self._state.node_status = "ANALYSIS_READY"
        self._checkpoint("analysis_request written", FailureCode.NONE)

    def _execute_wait_analyzer_patch(self) -> None:
        # Analyzer is an external boundary (main session AI). In full-auto mode,
        # reaching this node requires a human/AI to produce a patch; the loop
        # therefore escalates here. Full patch automation arrives in Task 4.
        self._state.node_status = "WAITING_PATCH"
        self._state.pending_human_gate = True
        self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
        self._checkpoint("waiting for analyzer patch", FailureCode.NONE)

    # -- transition & checkpoint -------------------------------------------

    def _transition(self) -> None:
        next_node = self._compute_next_node()
        if next_node:
            self._state.previous_node = self._state.current_node
            self._state.current_node = next_node

    def _compute_next_node(self) -> str:
        """Compute the next node from the current state (used by both _transition and _checkpoint)."""
        node = self._state.current_node
        # DECIDE_NEXT is a branch point: STOP terminals are handled inside
        # _execute_decide_next; a RETRY routes to BUILD_ANALYSIS_REQUEST.
        if node == NodeKind.DECIDE_NEXT.value and self._state.node_status == "RETRY":
            return NodeKind.BUILD_ANALYSIS_REQUEST.value
        return _LINEAR_NEXT.get(node, "")

    def _checkpoint(self, reason: str, failure_code: FailureCode, matched_guards: list[str] | None = None) -> None:
        next_node = self._compute_next_node()
        cp = CheckpointRecord(
            checkpoint_id=f"cp-{uuid.uuid4().hex[:12]}",
            session_id=self._session.session_id,
            attempt_index=self._session.current_attempt,
            current_node=self._state.current_node,
            input_summary={"suite": self._session.suite},
            output_summary={"node_status": self._state.node_status, "reason": reason},
            failure_code=failure_code,
            matched_guards=matched_guards or [],
            next_node=next_node,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
        )
        self._store.save(cp)
        self._state.last_checkpoint_at = cp.timestamp

    def _build_guard_eval_request(self):
        from loop_controller.runtime.types import GuardEvalRequest
        previous_codes: list[FailureCode] = []
        previous_hashes: list[str] = []
        latest_attempt: dict = {}
        if self._session.attempts:
            for att in self._session.attempts[:-1]:
                if isinstance(att, dict):
                    fc_str = att.get("failure_code", "")
                    ph = att.get("patch_applied", {}).get("patch_hash", "")
                else:
                    fc_str = getattr(att, "failure_code", "") or ""
                    ph = ""
                if fc_str:
                    try:
                        previous_codes.append(FailureCode(fc_str))
                    except ValueError:
                        pass
                if ph:
                    previous_hashes.append(ph)
            latest = self._session.attempts[-1] if self._session.attempts else {}
            latest_attempt = latest if isinstance(latest, dict) else {}
        current_hash = latest_attempt.get("patch_applied", {}).get("patch_hash", "") if isinstance(latest_attempt, dict) else ""

        return GuardEvalRequest(
            guard_name="",
            attempt_count=self._session.current_attempt,
            max_attempts=self._session.max_attempts,
            latest_status=self._state.node_status or self._session.status,
            latest_failure_code=self._session.latest_failure_code,
            previous_failure_codes=previous_codes,
            current_patch_hash=current_hash,
            previous_patch_hashes=previous_hashes,
        )

    def _to_session_dict(self) -> dict:
        return {
            "session_id": self._session.session_id,
            "artifacts_dir": self._session.artifacts_dir,
            "current_attempt": self._session.current_attempt,
            "max_attempts": self._session.max_attempts,
            "attempts": self._session.attempts,
            "status": self._session.status,
            "target": self._session.target,
        }
