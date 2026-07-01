from __future__ import annotations

import json
import logging
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
from loop_controller.stages import StageContext
from loop_controller.runtime import nodes as _runtime_nodes

_logger = logging.getLogger("loop_runtime_engine")

# Linear transitions: node -> next node (no branch condition required).
_LINEAR_NEXT: dict[str, str] = {
    NodeKind.INIT_SESSION.value: NodeKind.RUN_VERIFY.value,
    NodeKind.RUN_VERIFY.value: NodeKind.DECIDE_NEXT.value,
    NodeKind.BUILD_ANALYSIS_REQUEST.value: NodeKind.WAIT_ANALYZER_PATCH.value,
    NodeKind.WAIT_ANALYZER_PATCH.value: NodeKind.SELECT_BEST_CANDIDATE.value,
    NodeKind.SELECT_BEST_CANDIDATE.value: NodeKind.APPLY_PATCH.value,
    NodeKind.APPLY_PATCH.value: NodeKind.COMPILE_PATCH.value,
    NodeKind.DEPLOY_PATCH.value: NodeKind.RUN_VERIFY.value,
    NodeKind.REVERT_PATCH.value: NodeKind.DECIDE_NEXT.value,
}


class LoopRuntime:
    """State-graph runtime engine for loop automation.

    Drives INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT -> (DONE_SUCCESS |
    BUILD_ANALYSIS_REQUEST -> WAIT_ANALYZER_PATCH -> ESCALATE_HUMAN).
    """

    def __init__(self, session: LoopSession, cases_dir: str, device_profile: str, adb_endpoint: str = "", initial_terminal_state: RuntimeTerminalState = RuntimeTerminalState.NONE, serial_shell_provider: callable | None = None, analyzer: "LlmAnalyzer | None" = None) -> None:
        self._session = session
        self._state = RuntimeState(current_node=NodeKind.INIT_SESSION.value)
        self._state.terminal_state = initial_terminal_state
        self._store = CheckpointStore(session.artifacts_dir, session.session_id)
        self._adb_endpoint = adb_endpoint
        self._serial_shell_provider = serial_shell_provider
        self._cases_dir = cases_dir
        self._device_profile = device_profile
        self._deploy_context: dict = {}
        # Per-session stage 执行上下文，消除 stages 模块级全局状态依赖
        self._stage_ctx = StageContext(
            cases_dir=cases_dir, device_profile=device_profile,
            artifacts_dir=session.artifacts_dir, session_id=session.session_id,
        )
        # ISSUE-1：注入 LlmAnalyzer，缺省用 ScriptedAnalyzer（规则库留空，产出失败则退人工）
        self._analyzer = analyzer
        # 知识库归档路径（由 runtime_cli 注入，DONE_SUCCESS 时归档成功补丁）
        self._kb_path: str = ""
        # 置信度阈值（低于阈值的补丁触发人工 gate；runtime_cli 可覆盖）
        self._confidence_threshold: float = 0.7
        # human-in-loop 触发场景（由 analyzer.yaml 的 human_gate.triggers 注入）
        self._human_gate_triggers: list[str] = ["low_confidence", "kernel_patch", "dd_boot_reboot"]
        # G5: 节点耗时测量 + wall_clock 预算闸
        self._session_start: float = 0.0
        self._last_node_duration_ms: int = 0
        # G9: 指标埋点计数器（随 engine 生命周期，终态时快照）
        self._layer_hits: dict[str, int] = {}
        self._first_hit_layer: str = ""
        self._hg_count: int = 0
        self._fc_dist: dict[str, int] = {}
        self._kb_hit: bool = False

    def resume(self) -> RuntimeState:
        # 幂等：已终态的 session 不恢复
        if self._state.terminal_state != RuntimeTerminalState.NONE:
            return self._state
        # G9: 重置 wall_clock 起点 + 重建 failure_code 分布
        self._session_start = time.perf_counter()
        self._rebuild_fc_dist_from_checkpoints()
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
        # 从 attempts 恢复 deploy_context（防止 resume 后设备回滚被跳过）
        if self._session.attempts:
            latest = self._session.attempts[-1]
            if isinstance(latest, dict) and latest.get("deploy_context"):
                self._deploy_context = latest["deploy_context"]
        return self._state

    def run(self, max_iterations: int = 100) -> RuntimeState:
        self._session_start = time.perf_counter()
        iterations = 0
        while self._state.terminal_state == RuntimeTerminalState.NONE:
            iterations += 1
            if iterations > max_iterations:
                self._state.terminal_state = RuntimeTerminalState.DONE_FAILURE
                self._state.transition_reason = f"max_iterations({max_iterations}) exceeded"
                break
            t_start = time.perf_counter()
            self._execute_current_node()
            elapsed = time.perf_counter() - t_start
            self._last_node_duration_ms = int(elapsed * 1000)
            # pending_human_gate：等待人工决策，不设终态、不继续推进
            if self._state.pending_human_gate:
                self._persist_session()
                return self._state
            if self._state.terminal_state != RuntimeTerminalState.NONE:
                break
            # G5: wall_clock 预算闸
            if self._session.wall_clock_limit > 0:
                wall = time.perf_counter() - self._session_start
                if wall > self._session.wall_clock_limit:
                    self._state.terminal_state = RuntimeTerminalState.DONE_FAILURE
                    self._state.transition_reason = (
                        f"wall_clock budget exceeded: {wall:.0f}s > "
                        f"{self._session.wall_clock_limit}s"
                    )
                    self._checkpoint(
                        "wall_clock budget exceeded",
                        FailureCode.WALL_CLOCK_BUDGET_EXCEEDED,
                    )
                    break
            self._transition()
        # G9: 终态聚合指标（human gate 提前返回处不算终态，不聚合）
        self._session.metrics = self._compute_session_metrics()
        self._persist_session()
        return self._state

    # -- node execution -----------------------------------------------------

    def _read_suggestion_meta(self) -> dict | None:
        """读取 patch_suggestion.json 的元数据（patches/confidence/rationale）。

        兼容两种格式：
        - 新格式 dict：{"patches": [...], "confidence": float, "rationale": str}
        - 旧格式 list：[FileChange, ...] → 视为 confidence=1.0（高置信，不触发 gate）
        解析失败或文件缺失返回 None。
        """
        patch_path = os.path.join(self._session.artifacts_dir, "patch_suggestion.json")
        if not os.path.isfile(patch_path):
            return None
        try:
            data = json.loads(Path(patch_path).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if isinstance(data, dict) and "patches" in data:
            return data
        if isinstance(data, list):
            return {"patches": data, "confidence": 1.0}
        return None

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
        elif node == NodeKind.SELECT_BEST_CANDIDATE.value:
            self._execute_select_best_candidate()
        elif node == NodeKind.APPLY_PATCH.value:
            # confidence 阈值检查：低于阈值的补丁触发人工 gate，不自动 apply
            suggestion_meta = self._read_suggestion_meta()
            if suggestion_meta:
                conf = suggestion_meta.get("confidence", 1.0)
                # 低置信触发人工 gate；但若已人工 approve（human_gate_approved）则跳过，继续 apply
                if conf < self._confidence_threshold and not self._state.human_gate_approved:
                    self._state.node_status = "LOW_CONFIDENCE"
                    self._set_human_gate()
                    self._checkpoint(
                        f"confidence {conf} below threshold {self._confidence_threshold}",
                        FailureCode.NONE,
                    )
                    return
            # 已 approve 或高置信：消费 approve 标记后真正落盘补丁（避免后续轮次误跳过 gate）
            self._state.human_gate_approved = False
            patch_path = os.path.join(self._session.artifacts_dir, "patch_suggestion.json")
            if not os.path.isfile(patch_path):
                self._state.node_status = "NO_PATCH_FILE"
                self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                self._set_human_gate()
                self._checkpoint("no patch file found", FailureCode.PATCH_REJECTED)
                return
            # ISSUE-2：尝试创建独立 worktree 隔离补丁，失败降级到 stash 模式
            # 注意：worktree 从 baseline commit 创建，不含当前 working tree 的修改。
            # 当 analyzer 补丁的 old_marker 针对含故障的代码时，worktree（baseline）
            # 中找不到匹配。因此默认禁用 worktree，用 stash 模式（直接 apply 到
            # 当前 workspace，失败时 git stash apply 回滚）。
            # 如需 worktree 隔离，设置 LE_WORKTREE_ISOLATION=1。
            worktree_handle = None
            if os.environ.get("LE_WORKTREE_ISOLATION", "0") == "1":
                try:
                    from loop_controller.workspace_isolation import create_patch_worktree
                    # 优先用 LE_PATCH_GIT_ROOT（vendor/lechao 本地 git），支持 worktree 隔离；
                    # 回退到 AOSP_ROOT（兼容旧环境）
                    ws_root = os.environ.get("LE_PATCH_GIT_ROOT") or os.environ.get(
                        "AOSP_ROOT", os.path.expanduser("~/workspace/aosp"))
                    worktree_handle = create_patch_worktree(
                        ws_root, self._session.session_id, self._session.current_attempt,
                    )
                except Exception:
                    worktree_handle = None
            result = _runtime_nodes.node_apply_patch(
                patch_path, self._to_session_dict(), "",
                worktree_handle=worktree_handle,
            )
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
                    "files": result.get("files", []),
                }
                # ISSUE-1：从 result 提取 worktree_handle（若有），供 COMPILE 定位 worktree
                wt_from_result = result.get("worktree_handle")
                if wt_from_result:
                    pa["worktree_handle"] = wt_from_result
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
                # kernel_patch 触发：补丁涉及内核文件时需人工确认
                risk = result.get("risk", {})
                risk_level = risk.get("level", "") if isinstance(risk, dict) else ""
                if (risk_level == "KERNEL"
                        and not self._state.human_gate_approved
                        and "kernel_patch" in self._human_gate_triggers):
                    self._state.node_status = "KERNEL_PATCH_REVIEW"
                    self._set_human_gate()
                    self._checkpoint("kernel patch requires human review", FailureCode.NONE)
                    return
            guard_req = self._build_guard_eval_request()
            guard_result = guard_chain(["patch_rejected", "patch_applied_successfully"], guard_req)
            matched_guards = []
            if guard_result.matched:
                matched_guards.append(guard_result.guard_name)
                next_nk = NodeKind(guard_result.next_node)
                if next_nk == NodeKind.ESCALATE_HUMAN:
                    self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                    self._set_human_gate()
            self._checkpoint(f"apply_patch={result['status']}", fc, matched_guards=matched_guards)
        elif node == NodeKind.COMPILE_PATCH.value:
            # ISSUE-1：若补丁应用在独立 worktree，compile 必须在 worktree 内执行
            compile_ws_root = ""
            if self._session.attempts and isinstance(self._session.attempts[-1], dict):
                wt_handle = self._session.attempts[-1].get("patch_applied", {}).get("worktree_handle")
                if isinstance(wt_handle, dict) and wt_handle.get("worktree_path"):
                    compile_ws_root = wt_handle["worktree_path"]
            result = _runtime_nodes.node_compile(self._to_session_dict(), compile_ws_root)
            self._state.node_status = result["status"]
            fc = result.get("failure_code", FailureCode.NONE)
            if isinstance(fc, str):
                fc = FailureCode(fc)
            self._session.latest_failure_code = fc
            # COMPILE 结果写入 attempts 历史，供 guard 判定与审计
            if self._session.attempts and isinstance(self._session.attempts[-1], dict):
                att = self._session.attempts[-1]
                att["compile_result"] = {
                    "status": result["status"],
                    "failure_code": fc.value,
                    "error": result.get("error", ""),
                    "artifacts": result.get("artifacts", []),
                }
            if result["status"] == "COMPILE_FAILED":
                guard_req = self._build_guard_eval_request()
                guard_result = guard_chain(["compile_failed_but_recoverable"], guard_req)
                if guard_result.matched:
                    self._state.node_status = "COMPILE_FAILED_REVERT"
            self._checkpoint(f"compile={result['status']}", fc)
        elif node == NodeKind.DEPLOY_PATCH.value:
            result = _runtime_nodes.node_deploy(self._to_session_dict(),
                                                adb_endpoint=self._adb_endpoint,
                                                serial_shell_provider=self._serial_shell_provider)
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
                "block_device": result.get("block_device", ""),
            }
            # DEPLOY 结果 + deploy_context 写入 attempts 历史，供 guard 判定与审计
            if self._session.attempts and isinstance(self._session.attempts[-1], dict):
                latest_att = self._session.attempts[-1]
                latest_att["deploy_context"] = self._deploy_context
                latest_att["deploy_result"] = {
                    "status": result["status"],
                    "failure_code": fc.value,
                    "mode": result.get("mode", ""),
                    "error": result.get("error", ""),
                }
            # dd_boot_reboot 触发：dd 写设备前需人工确认（仅 deploy 成功路径）
            deploy_mode = result.get("mode", "")
            if (result["status"] == "DEPLOYED"
                    and deploy_mode == "dd_boot_reboot"
                    and not self._state.human_gate_approved
                    and "dd_boot_reboot" in self._human_gate_triggers):
                self._state.node_status = "DD_BOOT_REVIEW"
                self._set_human_gate()
                self._checkpoint("dd_boot_reboot requires human review", FailureCode.NONE)
                return
            if result["status"] in ("DEPLOY_FAILED", "KERNEL_DEAD", "BOOT_TIMEOUT", "DEPLOY_TIMEOUT"):
                guard_req = self._build_guard_eval_request()
                guard_result = guard_chain(
                    ["kernel_dead_no_shell", "boot_timeout_kernel_panic", "deploy_failed_but_recoverable"], guard_req,
                )
                if guard_result.matched:
                    next_nk = NodeKind(guard_result.next_node)
                    if next_nk == NodeKind.ESCALATE_HUMAN:
                        self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                        self._set_human_gate()
                    elif next_nk == NodeKind.REVERT_PATCH:
                        # 只有实际写入设备的错误才做设备回滚；未写入的直接走源码回滚
                        if result.get("needs_rollback", False):
                            self._state.node_status = "DEPLOY_FAILED_REVERT"
                        else:
                            self._state.node_status = "DEPLOY_FAILED_NO_DEVICE_ROLLBACK"
                            self._deploy_context = {}  # 清空 deploy_context，REVERT 节点跳过设备回滚
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
                    adb_endpoint=self._adb_endpoint,
                )
                if d_result["status"] != "REVERTED":
                    # 设备回滚失败 → 立即退人工
                    self._state.node_status = d_result["status"]
                    self._session.latest_failure_code = d_result.get("failure_code", FailureCode.ROLLBACK_FAILED)
                    self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                    self._set_human_gate()
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
                self._set_human_gate()
            self._checkpoint(f"revert={ws_result['status']}", fc)

    def _execute_run_verify(self) -> None:
        session_path = Path(self._session.artifacts_dir) / "session.json"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        session_path.write_text(
            json.dumps(self._to_session_dict(), ensure_ascii=False), encoding="utf-8"
        )
        updated, stage_result = stages.run_verify_stage(
            str(session_path), self._session.suite, self._adb_endpoint,
            cases_dir=self._cases_dir, device_profile=self._device_profile,
            ctx=self._stage_ctx,
        )
        self._session.current_attempt = updated.get("current_attempt", self._session.current_attempt)
        self._session.status = updated.get("status", stage_result.status)
        self._session.attempts = updated.get("attempts", self._session.attempts)
        self._session.latest_failure_code = stage_result.failure_code
        self._state.node_status = stage_result.status
        self._checkpoint(f"verify {stage_result.status}", stage_result.failure_code)

    def _execute_decide_next(self) -> None:
        # guard_chain is the single source of truth for transition decisions.
        # ISSUE-3：progress_converging 在 repeated_failure_code 之前，
        # 让"失败用例数严格下降"的会话即使 failure_code 重复也宽限 RETRY。
        # 严重错误（kernel_dead/patch_rejected/transport_unrecoverable 等）
        # 由各自的专用 guard 在 progress_converging 之前拦截，不受收敛宽限影响。
        guard_req = self._build_guard_eval_request()
        guard_result = guard_chain(
            [
                "all_cases_passed",
                "duplicate_patch_hash",
                "kernel_dead_no_shell",
                "patch_rejected",
                "session_state_corrupted",
                "transport_unrecoverable",
                "rollback_failed",
                "boot_timeout_no_recovery",
                "progress_converging",
                "repeated_failure_code",
                "attempt_limit_reached",
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
                self._set_human_gate()
            elif next_nk == NodeKind.DONE_SUCCESS:
                self._state.terminal_state = RuntimeTerminalState.DONE_SUCCESS
                # ISSUE-3：DONE_SUCCESS 时把成功补丁归档到知识库（Reflexion 模式）
                self._archive_to_knowledge_base()
                # ISSUE-3：成功收敛后清理所有 attempts 中的 worktree（生命周期对齐）
                self._cleanup_all_worktrees()
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

    def _cleanup_all_worktrees(self) -> None:
        """成功收敛后清理所有 attempts 中的 worktree（失败保留供 debug）。"""
        try:
            from loop_controller.workspace_isolation import WorktreeHandle, remove_patch_worktree
            for att in self._session.attempts:
                if not isinstance(att, dict):
                    continue
                wt_dict = att.get("patch_applied", {}).get("worktree_handle")
                if isinstance(wt_dict, dict) and wt_dict.get("worktree_path"):
                    handle = WorktreeHandle(
                        worktree_path=wt_dict["worktree_path"],
                        branch=wt_dict.get("branch", ""),
                        workspace_root=wt_dict.get("workspace_root", ""),
                        created=wt_dict.get("created", False),
                    )
                    remove_patch_worktree(handle)
        except Exception as e:
            # P2-3：worktree 清理失败不阻断流程，但需记录诊断（资源泄漏可追溯）
            _logger.warning("patch worktree 清理失败: %s", e)

    def _archive_to_knowledge_base(self) -> None:
        """DONE_SUCCESS 时把最后一次成功补丁归档到知识库（Reflexion 模式）。

        幂等性：update_kb 按 fingerprint 去重，相同指纹覆盖更新而非追加。
        容错：任何异常静默吞掉，绝不影响主流程的成功路径。
        """
        if not self._kb_path:
            return
        try:
            from loop_controller.analyzer_protocol import (
                AnalysisRequest,
                _compute_fingerprint,
                update_kb,
            )
            patch_path = os.path.join(
                self._session.artifacts_dir, "patch_suggestion.json")
            if not os.path.isfile(patch_path):
                return
            raw = json.loads(Path(patch_path).read_text(encoding="utf-8"))
            # 兼容两种格式：{"patches": [...]} 或裸 list[FileChange dict]
            if isinstance(raw, dict) and "patches" in raw:
                patches = raw["patches"]
            elif isinstance(raw, list):
                patches = raw
            else:
                return
            if not isinstance(patches, list) or not patches:
                return
            # P0-1：用"最近一次失败 attempt"（failed_cases 非空）的 failed_cases 算指纹，
            # 与 KnowledgeBaseAnalyzer 查询侧的指纹来源（失败那次）保持一致，
            # 否则归档指纹（成功那次=空）与查询指纹永不匹配，Reflexion 召回失效。
            # 注意：attempt 为扁平结构（run_verify_stage 写入），failed_cases 在顶层。
            failed_cases: list = []
            for att in reversed(self._session.attempts):
                if not isinstance(att, dict):
                    continue
                fc = att.get("failed_cases", [])
                if not fc:
                    # 兜底：从 case_results 提取 fail/error 用例
                    fc = [c for c in att.get("case_results", [])
                          if isinstance(c, dict) and c.get("status") in ("fail", "error")]
                if fc:
                    failed_cases = fc
                    break
            req = AnalysisRequest(
                session_id=self._session.session_id,
                attempt_index=self._session.current_attempt,
                failed_cases=failed_cases,
                target=self._session.target,
                suite=self._session.suite,
            )
            fp = _compute_fingerprint(req)
            update_kb(
                self._kb_path, fp, {}, patches,
                description=f"自动归档 from {self._session.session_id}",
                deploy_mode_hint="",
                source_session=self._session.session_id,
                source_attempt=self._session.current_attempt,
            )
        except Exception as e:
            # P2-3：归档失败不阻断主闭环，但必须留下诊断痕迹（CXX-004 故障静默）
            _logger.warning("KB 归档失败（session=%s）: %s", self._session.session_id, e)

    def _execute_build_analysis_request(self) -> None:
        stages.analyze_request_stage(self._to_session_dict(), ctx=self._stage_ctx)
        self._state.node_status = "ANALYSIS_READY"
        self._checkpoint("analysis_request written", FailureCode.NONE)

    def _execute_wait_analyzer_patch(self) -> None:
        # ISSUE-1：缺 patch_suggestion.json 时先调 analyzer 产出，产出失败再退人工。
        patch_path = os.path.join(self._session.artifacts_dir, "patch_suggestion.json")
        if os.path.isfile(patch_path):
            self._state.node_status = "PATCH_READY"
            self._checkpoint("patch file ready for apply", FailureCode.NONE)
            return
        # 无现成 patch 文件 → 调注入的 analyzer 自动产出
        if self._analyzer is not None:
            try:
                from loop_controller.stages import analyze_request_stage
                session_dict = self._to_session_dict()
                req = analyze_request_stage(session_dict, ctx=self._stage_ctx)
                from loop_controller.analyzer_protocol import AnalysisRequest
                import dataclasses
                req_data = json.loads(Path(req).read_text(encoding="utf-8"))
                request = AnalysisRequest(**{
                    k: v for k, v in req_data.items()
                    if k in AnalysisRequest.__dataclass_fields__
                })
                suggestion = self._analyzer.analyze(request)
                if suggestion.target_files:
                    # G9: 累积 analyzer 层级命中
                    layer = suggestion.matched_layer or "unknown"
                    self._layer_hits[layer] = self._layer_hits.get(layer, 0) + 1
                    if not self._first_hit_layer:
                        self._first_hit_layer = layer
                    if layer == "KnowledgeBaseAnalyzer":
                        self._kb_hit = True
                    # 落盘为 patch_suggestion.json（新格式：带 confidence/rationale 元数据）
                    import dataclasses
                    patch_data = {
                        "patches": [dataclasses.asdict(fc) for fc in suggestion.target_files],
                        "confidence": suggestion.confidence,
                        "rationale": suggestion.rationale,
                    }
                    Path(patch_path).write_text(
                        json.dumps(patch_data, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                    )
                    self._state.node_status = "PATCH_READY"
                    self._checkpoint("analyzer produced patch", FailureCode.NONE)
                    return
                # analyzer 无产出（target_files 为空）
                import logging
                logging.warning("analyzer returned empty target_files: confidence=%s rationale=%s",
                                suggestion.confidence, suggestion.rationale[:100])
            except Exception as e:
                # analyzer 异常不致命，降级到退人工
                import logging
                logging.warning("analyzer exception: %s: %s", type(e).__name__, e)
                self._state.node_status = "ANALYZER_ERROR"
                self._checkpoint(f"analyzer error: {e}", FailureCode.NONE)
        # analyzer 无产出或未注入 → 退人工
        self._state.node_status = "WAITING_PATCH"
        self._set_human_gate()
        self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
        self._checkpoint("waiting for analyzer patch", FailureCode.NONE)

    def _execute_select_best_candidate(self) -> None:
        """G2: best-of-N 候选评估。candidates=1 时透传。"""
        N = self._session.candidates_per_attempt
        if N <= 1:
            self._state.node_status = "CANDIDATE_SELECTED"
            self._checkpoint("single candidate passthrough", FailureCode.NONE)
            return
        # candidates > 1：完整评估逻辑在 Task 9 实现
        # 当前骨架：假设 patch_candidates/ 已存在，直接选第一个
        from pathlib import Path
        import json
        cands_dir = Path(self._session.artifacts_dir) / "patch_candidates"
        if not cands_dir.is_dir():
            self._state.node_status = "NO_CANDIDATES"
            self._set_human_gate()
            self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
            self._checkpoint("no patch_candidates dir", FailureCode.NONE)
            return
        cand_files = sorted(cands_dir.glob("c*_patch_suggestion.json"))
        if not cand_files:
            self._state.node_status = "NO_CANDIDATES"
            self._set_human_gate()
            self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
            self._checkpoint("no candidate files", FailureCode.NONE)
            return
        # 骨架：直接选第一个候选写入 patch_suggestion.json
        first_cand = json.loads(cand_files[0].read_text(encoding="utf-8"))
        patch_path = Path(self._session.artifacts_dir) / "patch_suggestion.json"
        patch_path.write_text(json.dumps(first_cand, ensure_ascii=False, indent=2), encoding="utf-8")
        self._state.node_status = "CANDIDATE_SELECTED"
        self._checkpoint(f"selected {first_cand.get('candidate_id', 'c0')} (stub)", FailureCode.NONE)

    def _set_human_gate(self) -> None:
        """G9: 统一 human gate 触发入口，同时计数。"""
        self._state.pending_human_gate = True
        self._hg_count += 1

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
        # human gate 暂停在 APPLY_PATCH（补丁尚未真正 apply）：next_node 指回 APPLY_PATCH，
        # 使 approve 后 resume 回到 APPLY_PATCH 重新执行并真正落盘补丁（修复：原先线性
        # 指向 COMPILE_PATCH 会跳过补丁应用，导致 approve 续跑后补丁丢失 → DONE_FAILURE）。
        if node == NodeKind.APPLY_PATCH.value and self._state.node_status == "LOW_CONFIDENCE":
            return NodeKind.APPLY_PATCH.value
        if node == NodeKind.COMPILE_PATCH.value:
            if self._state.node_status.startswith("COMPILE_FAILED"):
                return NodeKind.REVERT_PATCH.value
            return NodeKind.DEPLOY_PATCH.value
        if node == NodeKind.DEPLOY_PATCH.value and self._state.node_status == "DEPLOY_FAILED_RECOVERABLE":
            return NodeKind.DECIDE_NEXT.value
        if node == NodeKind.DEPLOY_PATCH.value and self._state.node_status == "DEPLOY_FAILED_REVERT":
            return NodeKind.REVERT_PATCH.value
        if node == NodeKind.DEPLOY_PATCH.value and self._state.node_status == "DEPLOY_FAILED_NO_DEVICE_ROLLBACK":
            return NodeKind.REVERT_PATCH.value
        return _LINEAR_NEXT.get(node, "")

    def _checkpoint(self, reason: str, failure_code: FailureCode,
                    matched_guards: list[str] | None = None,
                    duration_ms: int | None = None) -> None:
        next_node = self._compute_next_node()
        if duration_ms is None:
            duration_ms = getattr(self, "_last_node_duration_ms", 0)
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
            duration_ms=duration_ms,
        )
        self._store.save(cp)
        self._state.last_checkpoint_at = cp.timestamp
        # G9: 累积 failure_code 分布
        code = failure_code.value if failure_code else "NONE"
        self._fc_dist[code] = self._fc_dist.get(code, 0) + 1

    def _build_guard_eval_request(self):
        from loop_controller.runtime.types import GuardEvalRequest
        previous_codes: list[FailureCode] = []
        previous_hashes: list[str] = []
        latest_attempt: dict = {}
        if self._session.attempts:
            for att in self._session.attempts[:-1]:
                if isinstance(att, dict):
                    ph = att.get("patch_applied", {}).get("patch_hash", "")
                    # 聚合顶层 + 嵌套 compile/deploy failure_code
                    for fc_str in self._collect_failure_codes_from_attempt(att):
                        try:
                            previous_codes.append(FailureCode(fc_str))
                        except ValueError:
                            pass
                else:
                    fc_str = getattr(att, "failure_code", "") or ""
                    if fc_str:
                        try:
                            previous_codes.append(FailureCode(fc_str))
                        except ValueError:
                            pass
                    ph = ""
                if ph:
                    previous_hashes.append(ph)
            latest = self._session.attempts[-1] if self._session.attempts else {}
            latest_attempt = latest if isinstance(latest, dict) else {}
        current_hash = latest_attempt.get("patch_applied", {}).get("patch_hash", "") if isinstance(latest_attempt, dict) else ""

        # ISSUE-3：提取 failed_count 用于 progress_converging 收敛判定
        latest_failed_count = 0
        previous_failed_count = 0
        if isinstance(latest_attempt, dict):
            latest_failed_count = latest_attempt.get("failed_count", 0) or 0
        # 上一次 attempt 的 failed_count（倒数第二个）
        if len(self._session.attempts) >= 2:
            prev = self._session.attempts[-2]
            if isinstance(prev, dict):
                previous_failed_count = prev.get("failed_count", 0) or 0

        return GuardEvalRequest(
            guard_name="",
            attempt_count=self._session.current_attempt,
            max_attempts=self._session.max_attempts,
            latest_status=self._state.node_status or self._session.status,
            latest_failure_code=self._session.latest_failure_code,
            previous_failure_codes=previous_codes,
            current_patch_hash=current_hash,
            previous_patch_hashes=previous_hashes,
            latest_failed_count=latest_failed_count,
            previous_failed_count=previous_failed_count,
        )

    def _collect_failure_codes_from_attempt(self, att: dict) -> list[str]:
        """从一个 attempt dict 中收集所有 failure_code（顶层 + 嵌套结果），过滤 NONE。"""
        codes: list[str] = []
        if not isinstance(att, dict):
            return codes
        # 顶层（verify 失败时由 run_verify_stage 写入）
        top_fc = att.get("failure_code", "")
        if top_fc and top_fc != "NONE":
            codes.append(top_fc)
        # 嵌套 compile_result
        compile_result = att.get("compile_result", {})
        if isinstance(compile_result, dict):
            fc = compile_result.get("failure_code", "")
            if fc and fc != "NONE":
                codes.append(fc)
        # 嵌套 deploy_result
        deploy_result = att.get("deploy_result", {})
        if isinstance(deploy_result, dict):
            fc = deploy_result.get("failure_code", "")
            if fc and fc != "NONE":
                codes.append(fc)
        return codes

    def _compute_session_metrics(self) -> "SessionMetrics":
        """G9: 终态时把实例变量 + wall_clock 快照为 SessionMetrics。"""
        from loop_contracts.models import SessionMetrics
        wall_used_ms = int((time.perf_counter() - self._session_start) * 1000)
        wall_budget_ms = (self._session.wall_clock_limit or 0) * 1000
        return SessionMetrics(
            success=self._state.terminal_state == RuntimeTerminalState.DONE_SUCCESS,
            terminal_state=self._state.terminal_state.value,
            attempt_count=self._session.current_attempt,
            wall_clock_used_ms=wall_used_ms,
            wall_clock_budget_ms=wall_budget_ms,
            analyzer_layer_hits=dict(self._layer_hits),
            analyzer_first_hit_layer=self._first_hit_layer,
            failure_code_distribution=dict(self._fc_dist),
            human_gate_triggered=self._hg_count > 0,
            human_gate_count=self._hg_count,
            kb_hit=self._kb_hit,
        )

    def _rebuild_fc_dist_from_checkpoints(self) -> None:
        """G9: 从当前 session 的全部 checkpoint 重建 failure_code 分布。"""
        records = self._store.all()
        for r in records:
            code = r.failure_code.value if r.failure_code else "NONE"
            self._fc_dist[code] = self._fc_dist.get(code, 0) + 1

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
            "pending_human_gate": self._state.pending_human_gate,
            "last_checkpoint_at": self._state.last_checkpoint_at,
        }
        # G9: metrics 段（仅终态时非 None）
        if self._session.metrics is not None:
            from dataclasses import asdict
            data["metrics"] = asdict(self._session.metrics)
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
            "suite": self._session.suite,
        }
