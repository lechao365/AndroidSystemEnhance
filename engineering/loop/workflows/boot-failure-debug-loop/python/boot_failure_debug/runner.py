"""boot-failure-debug-loop 状态机主编排。

状态机阶段：
    PREPARE -> ATTACH_SERIAL -> OBSERVE_BOOT -> CLASSIFY_FAILURE
            -> COLLECT_EVIDENCE -> REASSESS -> EXIT_SUCCESS / EXIT_FAILURE

消费 loop_core 提供的通用框架：
- loop_core.observer.capture_snapshot（参数化）
- loop_core.cycles.count_cycles
- loop_core.rules.evaluate_rules / classify（通过本地 rules.py 包装）
- loop_core.models.ActionRecord / LoopAttempt
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from loop_core.models import ActionRecord, LoopAttempt
from loop_core.observer import capture_snapshot as core_capture_snapshot
from loop_core.cycles import count_cycles

from boot_failure_debug.actions import execute_action, plan_actions
from boot_failure_debug.rules import classify, evaluate_rules

if TYPE_CHECKING:
    from boot_failure_debug.config import BootFailureConfig


def _capture(transport, cfg, timeout_sec):
    """调用 core capture_snapshot 并传入 boot-failure 特有参数。"""
    return core_capture_snapshot(
        transport=transport,
        timeout_sec=timeout_sec,
        prompt_markers=cfg.prompt_markers,
        recent_limit=cfg.recent_lines_limit,
        quiet_window_sec=cfg.quiet_window_sec,
        cycle_markers=cfg.reboot_markers,
    )


class BootFailureRunner:
    """boot-failure-debug-loop 状态机 runner。"""

    def __init__(
        self,
        cfg: "BootFailureConfig",
        transport,
        attempt_id: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.transport = transport
        self.attempt_id = attempt_id or f"att-{uuid4().hex[:8]}"

    def _run_wait_prompt(self, action: ActionRecord) -> ActionRecord:
        matched = self.transport.wait_for_pattern(
            self.cfg.prompt_markers,
            self.cfg.prompt_wait_sec,
            self.cfg.recent_lines_limit,
        )
        return ActionRecord(
            action_id=action.action_id,
            level=action.level,
            command=action.command,
            reason=action.reason,
            result="OK" if matched else "FAIL",
            evidence_ref=action.evidence_ref,
            output_lines=[matched.text] if matched else [],
            metadata={"pattern_matched": bool(matched)},
        )

    def _run_l1_capture(self, action: ActionRecord) -> ActionRecord:
        self.transport.send_line(action.command)
        window = self.transport.capture_window(
            timeout_sec=self.cfg.capture_window_sec,
            recent_limit=self.cfg.recent_lines_limit,
        )
        output_lines = [line.text for line in window]
        return ActionRecord(
            action_id=action.action_id,
            level=action.level,
            command=action.command,
            reason=action.reason,
            result="OK",
            evidence_ref=action.evidence_ref,
            output_lines=output_lines,
            metadata={
                "captured_line_count": len(output_lines),
                "sent_inputs": [action.command],
            },
        )

    def _execute_planned_actions(self, planned: list[ActionRecord]) -> list[ActionRecord]:
        executed: list[ActionRecord] = []
        for action in planned:
            if action.command == "wait_prompt":
                executed.append(self._run_wait_prompt(action))
                continue
            if action.command in self.cfg.l1_commands:
                executed.append(self._run_l1_capture(action))
                continue
            executed.append(
                execute_action(action, self.transport, l1_commands=self.cfg.l1_commands)
            )
        return executed

    def run(self) -> LoopAttempt:
        """执行完整状态机闭环。"""
        state = "PREPARE"
        reassess_round = 0
        all_actions: list[ActionRecord] = []
        snapshot = None
        matches = []
        classification = "unknown"
        boot_cycle_count = 0

        try:
            while state not in ("EXIT_SUCCESS", "EXIT_FAILURE"):
                if state == "PREPARE":
                    state = "ATTACH_SERIAL"

                elif state == "ATTACH_SERIAL":
                    if not self.transport.acquire_writer():
                        classification = "writer_busy"
                        state = "EXIT_FAILURE"
                        continue
                    state = "OBSERVE_BOOT"

                elif state == "OBSERVE_BOOT":
                    snapshot = _capture(
                        self.transport, self.cfg, self.cfg.observe_timeout_sec
                    )
                    boot_cycle_count = count_cycles(snapshot.lines) if snapshot else 0
                    state = "CLASSIFY_FAILURE"

                elif state == "CLASSIFY_FAILURE":
                    if snapshot is not None:
                        matches = evaluate_rules(snapshot, self.cfg)
                        classification = classify(matches)
                    state = "COLLECT_EVIDENCE"

                elif state == "COLLECT_EVIDENCE":
                    if matches:
                        planned = plan_actions(matches, self.cfg.l1_commands)
                        executed = self._execute_planned_actions(planned)
                        all_actions.extend(executed)

                        wait_prompt_ok = any(
                            action.command == "wait_prompt" and action.result == "OK"
                            for action in executed
                        )
                        if classification == "login_prompt_not_reached" and wait_prompt_ok:
                            snapshot = _capture(
                                self.transport,
                                self.cfg,
                                self.cfg.capture_window_sec,
                            )
                            boot_cycle_count = count_cycles(snapshot.lines) if snapshot else 0
                            matches = evaluate_rules(snapshot, self.cfg)
                            classification = classify(matches)
                            if classification == "shell_prompt_available":
                                l1_executed = self._execute_planned_actions(
                                    plan_actions(matches, self.cfg.l1_commands)
                                )
                                all_actions.extend(l1_executed)
                    state = "REASSESS"

                elif state == "REASSESS":
                    if reassess_round < self.cfg.max_reassess_rounds:
                        if classification in ("no_output_after_attach", "kernel_boot_hang"):
                            reassess_round += 1
                            snapshot = _capture(
                                self.transport,
                                self.cfg,
                                self.cfg.observe_timeout_sec + self.cfg.capture_window_sec,
                            )
                            boot_cycle_count = (
                                count_cycles(snapshot.lines) if snapshot else 0
                            )
                            matches = evaluate_rules(snapshot, self.cfg)
                            new_classification = classify(matches)
                            if new_classification != "unknown":
                                classification = new_classification
                            if classification == "shell_prompt_available":
                                state = "COLLECT_EVIDENCE"
                                continue
                        else:
                            reassess_round = self.cfg.max_reassess_rounds

                    if classification == "shell_prompt_available":
                        state = "EXIT_SUCCESS"
                    else:
                        state = "EXIT_FAILURE"
        finally:
            self.transport.release()

        outcome = "EXIT_SUCCESS" if state == "EXIT_SUCCESS" else "EXIT_FAILURE"

        # 注入 boot_cycle 到 extra_summary_lines
        extra_lines = [f"boot_cycle: {boot_cycle_count}"]

        return LoopAttempt(
            attempt_id=self.attempt_id,
            device_id=self.cfg.device_id,
            outcome=outcome,
            final_classification=classification,
            boot_cycle_count=boot_cycle_count,
            matched_rules=matches,
            actions=all_actions,
            artifacts_dir="",
            extra_summary_lines=extra_lines,
        )
