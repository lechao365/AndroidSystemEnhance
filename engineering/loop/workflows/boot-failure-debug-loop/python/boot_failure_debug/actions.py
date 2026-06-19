"""L1/L2 动作规划与执行。

V1 动作边界（对齐设计规格 §10.5）：
- L1（只读采样）：dmesg / getprop / mount / ps
- L2（低风险探测）：send_enter / wait_prompt / capture_recent_context / extend_observe_window

V1 明确不做：
- L3（恢复动作）：修改系统文件、持久化配置写入
- L4（高风险动作）：破坏性修复

动作规划逻辑：
- shell_prompt_available -> 执行全部 L1 命令
- login_prompt_not_reached / boot_hang -> 仅用 L2 安全动作
- kernel_panic / reboot_loop -> capture_recent_context + 报告，不执行 L1
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from boot_failure_debug.models import ActionRecord, RuleMatch

if TYPE_CHECKING:
    from boot_failure_debug.transport import BaseTransport

# L1 只读采样命令（按 workflow profile l1_commands 执行）
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

def plan_actions(matches: list[RuleMatch]) -> list[ActionRecord]:
    """根据规则匹配结果规划动作清单。

    Args:
        matches: 规则匹配结果列表

    Returns:
        :class:`ActionRecord` 列表（result=PLANNED）
    """
    matched_ids = {m.rule_id for m in matches if getattr(m, "matched", False)}

    # 无匹配 -> 不规划动作
    if not matched_ids:
        return []

    actions: list[ActionRecord] = []

    # shell_prompt_available -> 执行全部 L1 命令
    if "shell_prompt_available" in matched_ids:
        for i, cmd in enumerate(L1_COMMANDS):
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
    action: ActionRecord, transport: "BaseTransport"
) -> ActionRecord:
    """执行单个动作并更新 result。

    Args:
        action: 待执行的动作（result=PLANNED）
        transport: transport 实例

    Returns:
        更新后的 :class:`ActionRecord`（result=OK/SKIP/FAIL）
    """
    # wait_prompt / extend_observe_window / capture_recent_context 在 runner 层处理
    if action.command in ("wait_prompt", "extend_observe_window", "capture_recent_context"):
        return ActionRecord(
            action_id=action.action_id,
            level=action.level,
            command=action.command,
            reason=action.reason,
            result="SKIP",
        )

    # send_enter -> 发送空字符串
    if action.command == "send_enter":
        transport.send_line("")
        return ActionRecord(
            action_id=action.action_id,
            level=action.level,
            command=action.command,
            reason=action.reason,
            result="OK",
        )

    # L1 命令 -> send_line(command)
    if action.command in L1_COMMANDS:
        transport.send_line(action.command)
        return ActionRecord(
            action_id=action.action_id,
            level=action.level,
            command=action.command,
            reason=action.reason,
            result="OK",
        )

    # 未知的 L2 动作 -> SKIP
    return ActionRecord(
        action_id=action.action_id,
        level=action.level,
        command=action.command,
        reason=action.reason,
        result="SKIP",
    )


def execute_actions(
    actions: list[ActionRecord], transport: "BaseTransport"
) -> list[ActionRecord]:
    """执行动作列表并返回更新后的结果。

    Args:
        actions: 动作列表（result=PLANNED）
        transport: transport 实例

    Returns:
        更新后的动作列表
    """
    return [execute_action(a, transport) for a in actions]