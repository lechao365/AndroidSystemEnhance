from __future__ import annotations

import json
import os
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
from loop_controller.runtime.guards import guard_chain

import loop_controller.stages as stages
from loop_controller.runtime import nodes as _runtime_nodes

# Linear transitions: node -> next node (no branch condition required).
_LINEAR_NEXT: dict[str, str] = {
    NodeKind.INIT_SESSION.value: NodeKind.RUN_VERIFY.value,
    NodeKind.RUN_VERIFY.value: NodeKind.DECIDE_NEXT.value,
    NodeKind.BUILD_ANALYSIS_REQUEST.value: NodeKind.WAIT_ANALYZER_PATCH.value,
    NodeKind.WAIT_ANALYZER_PATCH.value: NodeKind.APPLY_PATCH.value,
    NodeKind.APPLY_PATCH.value: NodeKind.COMPILE_PATCH.value,
    NodeKind.DEPLOY_PATCH.value: NodeKind.RUN_VERIFY.value,
    NodeKind.REVERT_PATCH.value: NodeKind.DECIDE_NEXT.value,
}


class LoopRuntime:
    """State-graph runtime engine for loop automation.

    Drives INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT -> (DONE_SUCCESS |
    BUILD_ANALYSIS_REQUEST -> WAIT_ANALYZER_PATCH -> ESCALATE_HUMAN).
    """

    def __init__(self, session: LoopSession, cases_dir: str, device_profile: str, adb_endpoint: str = "", initial_terminal_state: RuntimeTerminalState = RuntimeTerminalState.NONE, serial_shell_provider: callable | None = None) -> None:
        self._session = session
        self._state = RuntimeState(current_node=NodeKind.INIT_SESSION.value)
        self._state.terminal_state = initial_terminal_state
        self._store = CheckpointStore(session.artifacts_dir, session.session_id)
        self._adb_endpoint = adb_endpoint
        self._serial_shell_provider = serial_shell_provider
        self._deploy_context: dict = {}
        # TODO: Replace module-level stage globals with proper DI.
        # stages module uses _CASES_DIR/_DEVICE_PROFILE as module constants;
        # this override works for now but prevents isolation in concurrent usage.
        stages._CASES_DIR = cases_dir
        stages._DEVICE_PROFILE = device_profile

    def resume(self) -> RuntimeState:
        # 幂等：已终态的 session 不恢复
        if self._state.terminal_state != RuntimeTerminalState.NONE:
            return self._state
        cp = self._store.latest()
        if not cp:
            return self._state
        # 校验 next_node 非空且合法
        if not cp.next_node:
            return self._state
        try:
            NodeKind(cp.next_node)
        except ValueError:
            return self._state
        # 不恢复到终态 node（DONE_SUCCESS/ESCALATE_HUMAN/DONE_FAILURE）
        _TERMINAL_NODES = frozenset({
            NodeKind.DONE_SUCCESS.value,
            NodeKind.ESCALATE_HUMAN.value,
            NodeKind.DONE_FAILURE.value,
        })
        if cp.next_node in _TERMINAL_NODES:
            return self._state
        # 全面恢复运行时状态
        self._state.current_node = cp.next_node
        self._state.previous_node = cp.current_node
        self._state.node_status = cp.output_summary.get("node_status", "")
        self._state.last_checkpoint_at = cp.timestamp
        self._state.interrupted = False
        # 恢复 session 级字段，保证 guard_chain 判定数据一致
        self._session.latest_failure_code = cp.failure_code
        if cp.attempt_index:
            self._session.current_attempt = cp.attempt_index
        return self._state

    def run(self, max_iterations: int = 100) -> RuntimeState:
        iterations = 0
        while self._state.terminal_state == RuntimeTerminalState.NONE:
            iterations += 1
            if iterations > max_iterations:
                self._state.terminal_state = RuntimeTerminalState.DONE_FAILURE
                self._state.transition_reason = f"max_iterations({max_iterations}) exceeded"
                break
            self._execute_current_node()
            if self._state.terminal_state != RuntimeTerminalState.NONE:
                break
            self._transition()
        self._persist_session()
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
            patch_path = os.path.join(self._session.artifacts_dir, "patch_suggestion.json")
            if not os.path.isfile(patch_path):
                self._state.node_status = "NO_PATCH_FILE"
                self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                self._state.pending_human_gate = True
                self._checkpoint("no patch file found", FailureCode.PATCH_REJECTED)
                return
            result = _runtime_nodes.node_apply_patch(patch_path, self._to_session_dict(), "")
            self._state.node_status = result["status"]
            fc = result.get("failure_code", FailureCode.NONE)
            if isinstance(fc, str):
                fc = FailureCode(fc)
            self._session.latest_failure_code = fc
            if result["status"] == "APPLIED":
                pa = {
                    "patch_hash": result.get("patch_hash", ""),
                    "stash_ref": result.get("stash_ref", ""),
                    "workspace_root": result.get("workspace_root", ""),
                    "risk": result.get("risk", {}),
                }
                latest = self._session.attempts[-1] if self._session.attempts else None
                if isinstance(latest, dict):
                    latest["patch_applied"] = pa
                    if not self._session.attempts:
                        self._session.attempts.append(latest)
                elif latest is not None and hasattr(latest, "patch_applied"):
                    latest.patch_applied = pa
                    if not self._session.attempts:
                        self._session.attempts.append(latest)
                else:
                    self._session.attempts.append({"patch_applied": pa})
            guard_req = self._build_guard_eval_request()
            guard_result = guard_chain(["patch_rejected", "patch_applied_successfully"], guard_req)
            matched_guards = []
            if guard_result.matched:
                matched_guards.append(guard_result.guard_name)
                next_nk = NodeKind(guard_result.next_node)
                if next_nk == NodeKind.ESCALATE_HUMAN:
                    self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                    self._state.pending_human_gate = True
            self._checkpoint(f"apply_patch={result['status']}", fc, matched_guards=matched_guards)
        elif node == NodeKind.COMPILE_PATCH.value:
            result = _runtime_nodes.node_compile(self._to_session_dict(), "")
            self._state.node_status = result["status"]
            fc = result.get("failure_code", FailureCode.NONE)
            if isinstance(fc, str):
                fc = FailureCode(fc)
            self._session.latest_failure_code = fc
            if result["status"] == "COMPILE_FAILED":
                guard_req = self._build_guard_eval_request()
                guard_result = guard_chain(["compile_failed_but_recoverable"], guard_req)
                if guard_result.matched:
                    self._state.node_status = "COMPILE_FAILED_REVERT"
            self._checkpoint(f"compile={result['status']}", fc)
        elif node == NodeKind.DEPLOY_PATCH.value:
            result = _runtime_nodes.node_deploy(self._to_session_dict(), adb_endpoint=self._adb_endpoint)
            self._state.node_status = result["status"]
            fc = result.get("failure_code", FailureCode.NONE)
            if isinstance(fc, str):
                fc = FailureCode(fc)
            self._session.latest_failure_code = fc
            # Save deploy context for REVERT_PATCH rollback
            self._deploy_context = {
                "mode": result.get("mode", ""),
                "backup_path": result.get("backup_path", ""),
                "backup_sha": result.get("backup_sha", ""),
                "deployed_files": result.get("deployed_files", []),
            }
            # Record deploy context into latest attempt
            if self._session.attempts:
                latest = self._session.attempts[-1]
                if isinstance(latest, dict):
                    latest["deploy_context"] = self._deploy_context
            if result["status"] in ("DEPLOY_FAILED", "KERNEL_DEAD", "DEPLOY_TIMEOUT"):
                guard_req = self._build_guard_eval_request()
                guard_result = guard_chain(
                    ["kernel_dead_no_shell", "boot_timeout_kernel_panic", "deploy_failed_but_recoverable"], guard_req,
                )
                if guard_result.matched:
                    next_nk = NodeKind(guard_result.next_node)
                    if next_nk == NodeKind.ESCALATE_HUMAN:
                        self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                        self._state.pending_human_gate = True
                    elif next_nk == NodeKind.REVERT_PATCH:
                        self._state.node_status = "DEPLOY_FAILED_REVERT"
                    elif next_nk == NodeKind.DECIDE_NEXT:
                        self._state.node_status = "DEPLOY_FAILED_RECOVERABLE"
            self._checkpoint(f"deploy={result['status']}", fc)
        elif node == NodeKind.REVERT_PATCH.value:
            # Phase 1: 设备回滚（若 deploy_context 存在）
            if self._deploy_context and self._deploy_context.get("mode"):
                d_result = _runtime_nodes.node_rollback_deploy(
                    self._to_session_dict(),
                    self._deploy_context,
                    serial_shell=self._serial_shell_provider,
                )
                if d_result["status"] != "REVERTED":
                    # 设备回滚失败 → 立即退人工
                    self._state.node_status = d_result["status"]
                    self._session.latest_failure_code = d_result.get("failure_code", FailureCode.ROLLBACK_FAILED)
                    self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                    self._state.pending_human_gate = True
                    self._checkpoint(f"revert_device=failed:{d_result['status']}", self._session.latest_failure_code)
                    return
            # Phase 2: 源码回滚（git stash apply）
            ws_result = _runtime_nodes.node_revert_workspace(self._to_session_dict())
            self._state.node_status = ws_result["status"]
            fc = ws_result.get("failure_code", FailureCode.NONE)
            if isinstance(fc, str):
                fc = FailureCode(fc)
            self._session.latest_failure_code = fc
            if ws_result["status"] != "REVERTED":
                # 源码回滚失败 → 立即退人工
                self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                self._state.pending_human_gate = True
            self._checkpoint(f"revert={ws_result['status']}", fc)

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
        # guard_chain is the single source of truth for transition decisions.
        guard_req = self._build_guard_eval_request()
        guard_result = guard_chain(
            [
                "all_cases_passed",
                "attempt_limit_reached",
                "repeated_failure_code",
                "duplicate_patch_hash",
                "kernel_dead_no_shell",
                "patch_rejected",
                "session_state_corrupted",
                "transport_unrecoverable",
                "rollback_failed",
                "boot_timeout_no_recovery",
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
            self._state.transition_reason = guard_result.reason
        else:
            # No guard matched — should not happen in normal operation.
            self._state.terminal_state = RuntimeTerminalState.DONE_FAILURE
            self._state.transition_reason = "no guard matched in DECIDE_NEXT"
            self._state.node_status = "NO_GUARD_MATCH"
        fc = self._session.latest_failure_code
        self._checkpoint(
            f"decide={guard_result.reason or 'NO_MATCH'}",
            fc,
            matched_guards=matched_guards,
        )

    def _execute_build_analysis_request(self) -> None:
        stages.analyze_request_stage(self._to_session_dict())
        self._state.node_status = "ANALYSIS_READY"
        self._checkpoint("analysis_request written", FailureCode.NONE)

    def _execute_wait_analyzer_patch(self) -> None:
        # In full-auto mode: if patch_suggestion.json already exists, proceed to APPLY_PATCH.
        # Otherwise, escalate for human/AI to produce a patch.
        patch_path = os.path.join(self._session.artifacts_dir, "patch_suggestion.json")
        if os.path.isfile(patch_path):
            self._state.node_status = "PATCH_READY"
            self._checkpoint("patch file ready for apply", FailureCode.NONE)
            return
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
        if node == NodeKind.COMPILE_PATCH.value:
            if self._state.node_status.startswith("COMPILE_FAILED"):
                return NodeKind.REVERT_PATCH.value
            return NodeKind.DEPLOY_PATCH.value
        if node == NodeKind.DEPLOY_PATCH.value and self._state.node_status == "DEPLOY_FAILED_RECOVERABLE":
            return NodeKind.DECIDE_NEXT.value
        if node == NodeKind.DEPLOY_PATCH.value and self._state.node_status == "DEPLOY_FAILED_REVERT":
            return NodeKind.REVERT_PATCH.value
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

    def _persist_session(self) -> None:
        session_path = Path(self._session.artifacts_dir) / "session.json"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": self._session.session_id,
            "workflow_id": self._session.workflow_id,
            "target": self._session.target,
            "suite": self._session.suite,
            "max_attempts": self._session.max_attempts,
            "current_attempt": self._session.current_attempt,
            "status": self._session.status,
            "latest_failure_code": self._session.latest_failure_code.value
                if hasattr(self._session.latest_failure_code, "value")
                else str(self._session.latest_failure_code),
            "attempts": self._session.attempts,
            "artifacts_dir": self._session.artifacts_dir,
            "terminal_state": self._state.terminal_state.value,
            "current_node": self._state.current_node,
            "node_status": self._state.node_status,
            "transition_reason": self._state.transition_reason,
            "last_checkpoint_at": self._state.last_checkpoint_at,
        }
        session_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

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
