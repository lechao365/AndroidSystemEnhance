"""boot-failure L1/L2 动作规划与执行。

V1 动作边界：
- L1（只读采样）：dmesg / getprop / mount / ps
- L2（低风险探测）：send_enter / wait_prompt / capture_recent_context / extend_observe_window

业务特有逻辑：
- plan_actions: 规则 -> 动作清单映射
- execute_action: 具体动作分派
"""
from __future__ import annotations

from loop_core.models import ActionRecord, RuleMatch

# L1 只读采样命令（默认值，可被 cfg.l1_commands 覆盖）
L1_COMMANDS: list[str] = ["dmesg", "getprop", "mount", "ps"]

# L2 低风险探测动作
L2_SAFE_ACTIONS: list[str] = [
    "send_enter",
    "wait_prompt",
    "capture_recent_context",
    "extend_observe_window",
]


# ============================================================================
# 动作规划
# ============================================================================

def plan_actions(
    matches: list[RuleMatch], l1_commands: list[str] | None = None
) -> list[ActionRecord]:
    """根据规则匹配结果规划动作清单。

    Args:
        matches: 规则匹配结果列表
        l1_commands: 可选 L1 命令列表；None 回退默认常量，空列表尊重为空

    Returns:
        ActionRecord 列表（result=PLANNED）
    """
    matched_ids = {m.rule_id for m in matches if getattr(m, "matched", False)}
    configured_l1_commands = L1_COMMANDS if l1_commands is None else l1_commands

    if not matched_ids:
        return []

    actions: list[ActionRecord] = []

    # shell_prompt_available -> 执行全部 L1 命令
    if "shell_prompt_available" in matched_ids:
        for i, cmd in enumerate(configured_l1_commands):
            actions.append(
                ActionRecord(
                    action_id=f"a-{i + 1}",
                    level="L1",
                    command=cmd,
                    reason="prompt available",
                    result="PLANNED",
                )
            )
        return actions

    # login_prompt_not_reached / boot_hang -> L2 安全动作
    if "login_prompt_not_reached" in matched_ids or "kernel_boot_hang" in matched_ids:
        for i, cmd in enumerate(L2_SAFE_ACTIONS[:2]):  # send_enter + wait_prompt
            actions.append(
                ActionRecord(
                    action_id=f"a-{i + 1}",
                    level="L2",
                    command=cmd,
                    reason="prompt not visible",
                    result="PLANNED",
                )
            )
        return actions

    # kernel_panic / reboot_loop / no_output -> 仅 capture_recent_context
    for rule_id in ("kernel_panic_detected", "reboot_loop_detected", "no_output_after_attach"):
        if rule_id in matched_ids:
            actions.append(
                ActionRecord(
                    action_id="a-1",
                    level="L2",
                    command="capture_recent_context",
                    reason=f"{rule_id} detected",
                    result="PLANNED",
                )
            )
            return actions

    return actions


# ============================================================================
# 动作执行
# ============================================================================

def execute_action(
    action: ActionRecord,
    transport,
    l1_commands: list[str] | tuple[str, ...] | set[str] | None = None,
) -> ActionRecord:
    """执行单个动作并更新 result。

    wait_prompt / extend_observe_window / capture_recent_context 在 runner 层处理。
    send_enter -> 发送空字符串。
    L1 命令 -> send_line(command)。
    """
    configured_l1_commands = set(L1_COMMANDS if l1_commands is None else l1_commands)

    if action.command in ("wait_prompt", "extend_observe_window", "capture_recent_context"):
        return ActionRecord(
            action_id=action.action_id,
            level=action.level,
            command=action.command,
            reason=action.reason,
            result="SKIP",
            evidence_ref=action.evidence_ref,
            output_lines=list(action.output_lines),
            metadata=dict(action.metadata),
        )

    if action.command == "send_enter":
        transport.send_line("")
        return ActionRecord(
            action_id=action.action_id,
            level=action.level,
            command=action.command,
            reason=action.reason,
            result="OK",
            evidence_ref=action.evidence_ref,
            metadata={"sent_inputs": [""]},
        )

    if action.command in configured_l1_commands:
        transport.send_line(action.command)
        return ActionRecord(
            action_id=action.action_id,
            level=action.level,
            command=action.command,
            reason=action.reason,
            result="OK",
            evidence_ref=action.evidence_ref,
            metadata={"sent_inputs": [action.command]},
        )

    return ActionRecord(
        action_id=action.action_id,
        level=action.level,
        command=action.command,
        reason=action.reason,
        result="SKIP",
        evidence_ref=action.evidence_ref,
        output_lines=list(action.output_lines),
        metadata=dict(action.metadata),
    )


def execute_actions(
    actions: list[ActionRecord],
    transport,
    l1_commands: list[str] | tuple[str, ...] | set[str] | None = None,
) -> list[ActionRecord]:
    """执行动作列表并返回更新后的结果。"""
    return [execute_action(a, transport, l1_commands=l1_commands) for a in actions]
