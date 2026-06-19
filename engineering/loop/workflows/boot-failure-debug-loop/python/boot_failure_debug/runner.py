"""boot-failure-debug-loop 状态机主编排。

状态机阶段（对齐设计规格 §10.2）：

    PREPARE -> ATTACH_SERIAL -> OBSERVE_BOOT -> CLASSIFY_FAILURE
            -> COLLECT_EVIDENCE -> REASSESS -> EXIT_SUCCESS / EXIT_FAILURE

收口规则：
- shell_prompt_available -> EXIT_SUCCESS
- kernel_panic / reboot_loop / boot_hang / no_output / login_prompt_not_reached -> EXIT_FAILURE
- REASSESS 最多 max_reassess_rounds 轮
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import uuid4

from boot_failure_debug.actions import execute_actions, plan_actions
from boot_failure_debug.boot_cycles import count_boot_cycles
from boot_failure_debug.models import ActionRecord, LoopAttempt
from boot_failure_debug.observer import capture_snapshot
from boot_failure_debug.rules import classify, evaluate_rules

if TYPE_CHECKING:
    from boot_failure_debug.config import WorkflowConfig
    from boot_failure_debug.transport import BaseTransport


class BootFailureRunner:
    """boot-failure-debug-loop 状态机 runner。

    典型用法::

        runner = BootFailureRunner(cfg, transport)
        attempt = runner.run()
        print(attempt.outcome, attempt.final_classification)
    """

    def __init__(
        self,
        cfg: "WorkflowConfig",
        transport: "BaseTransport",
        attempt_id: str | None = None,
    ) -> None:
        self.cfg = cfg
        self.transport = transport
        self.attempt_id = attempt_id or f"att-{uuid4().hex[:8]}"

    def run(self) -> LoopAttempt:
        """执行完整状态机闭环。

        Returns:
            :class:`LoopAttempt` 包含最终分类、动作记录、boot cycle 计数
        """
        state = "PREPARE"
        reassess_round = 0
        all_actions: list[ActionRecord] = []
        snapshot = None
        matches = []
        classification = "unknown"
        boot_cycle_count = 0

        while state not in ("EXIT_SUCCESS", "EXIT_FAILURE"):
            if state == "PREPARE":
                state = "ATTACH_SERIAL"

            elif state == "ATTACH_SERIAL":
                # 申请 writer（fixture transport 总是成功）
                self.transport.acquire_writer()
                state = "OBSERVE_BOOT"

            elif state == "OBSERVE_BOOT":
                snapshot = capture_snapshot(
                    self.transport, self.cfg, timeout_sec=self.cfg.observe_timeout_sec
                )
                boot_cycle_count = count_boot_cycles(snapshot.lines) if snapshot else 0
                state = "CLASSIFY_FAILURE"

            elif state == "CLASSIFY_FAILURE":
                if snapshot is not None:
                    matches = evaluate_rules(snapshot, self.cfg)
                    classification = classify(matches)
                state = "COLLECT_EVIDENCE"

            elif state == "COLLECT_EVIDENCE":
                if matches:
                    planned = plan_actions(matches)
                    # 对 fixture transport，send_line 会被记录但不产生真实输出
                    executed = execute_actions(planned, self.transport)
                    all_actions.extend(executed)
                state = "REASSESS"

            elif state == "REASSESS":
                # 判断是否需要重观察
                # V1：REASSESS 只做结果确认，不重新观察（fixture 场景下重新观察结果相同）
                # 仅在 panic / no_output 时可考虑延长观察窗口
                if reassess_round < self.cfg.max_reassess_rounds:
                    # 检查是否值得重观察
                    if classification in ("no_output_after_attach", "kernel_boot_hang"):
                        # 尝试延长观察窗口
                        reassess_round += 1
                        snapshot = capture_snapshot(
                            self.transport,
                            self.cfg,
                            timeout_sec=self.cfg.observe_timeout_sec + self.cfg.capture_window_sec,
                        )
                        boot_cycle_count = (
                            count_boot_cycles(snapshot.lines) if snapshot else 0
                        )
                        matches = evaluate_rules(snapshot, self.cfg)
                        new_classification = classify(matches)
                        if new_classification != "unknown":
                            classification = new_classification
                        # 继续到 COLLECT_EVIDENCE 再次采样（如果分类变化）
                        if classification == "shell_prompt_available":
                            state = "COLLECT_EVIDENCE"
                            continue
                    else:
                        reassess_round = self.cfg.max_reassess_rounds

                # 收口
                if classification == "shell_prompt_available":
                    state = "EXIT_SUCCESS"
                else:
                    state = "EXIT_FAILURE"

        # 释放 writer
        self.transport.release()

        outcome = "EXIT_SUCCESS" if state == "EXIT_SUCCESS" else "EXIT_FAILURE"

        return LoopAttempt(
            attempt_id=self.attempt_id,
            device_id=self.cfg.device_id,
            outcome=outcome,
            final_classification=classification,
            boot_cycle_count=boot_cycle_count,
            matched_rules=matches,
            actions=all_actions,
            artifacts_dir="",
        )
