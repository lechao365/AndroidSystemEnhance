"""actions.py 单元测试。

覆盖：
- plan_actions 根据 classify 结果规划动作清单
- prompt 可达时执行 L1 只读命令
- prompt 不可达时仅用 L2 安全动作
- 动作级别限制（不出现 L3/L4）
"""
from unittest.mock import MagicMock

import pytest

from boot_failure_debug.actions import (
    plan_actions,
    execute_action,
    execute_actions,
    L1_COMMANDS,
    L2_SAFE_ACTIONS,
)
from boot_failure_debug.models import ActionRecord, RuleMatch
from boot_failure_debug.transport import FixtureTransport


# ============================================================================
# 动作规划
# ============================================================================

class TestPlanActions:
    def test_shell_prompt_available_plans_l1_commands(self):
        matches = [
            RuleMatch(
                rule_id="shell_prompt_available",
                matched=True,
                confidence=0.9,
                severity="low",
                evidence=["console:/ $"],
                phase="CLASSIFY_FAILURE",
                suggested_actions=["collect_read_only"],
            )
        ]
        actions = plan_actions(matches)
        assert len(actions) >= len(L1_COMMANDS)
        assert all(a.level == "L1" for a in actions[:len(L1_COMMANDS)])
        assert [a.command for a in actions[:len(L1_COMMANDS)]] == L1_COMMANDS

    def test_shell_prompt_available_adds_result_planned(self):
        matches = [
            RuleMatch(
                rule_id="shell_prompt_available",
                matched=True,
                confidence=0.9,
                severity="low",
                evidence=["console:/ $"],
                phase="CLASSIFY_FAILURE",
                suggested_actions=[],
            )
        ]
        actions = plan_actions(matches)
        assert all(a.result == "PLANNED" for a in actions)

    def test_no_prompt_only_uses_l2_actions(self):
        matches = [
            RuleMatch(
                rule_id="login_prompt_not_reached",
                matched=True,
                confidence=0.75,
                severity="medium",
                evidence=["init: starting"],
                phase="CLASSIFY_FAILURE",
                suggested_actions=["send_enter", "wait_prompt"],
            )
        ]
        actions = plan_actions(matches)
        assert len(actions) >= 2
        assert all(a.level == "L2" for a in actions)
        assert "send_enter" in [a.command for a in actions]

    def test_kernel_panic_does_not_trigger_l1(self):
        matches = [
            RuleMatch(
                rule_id="kernel_panic_detected",
                matched=True,
                confidence=0.95,
                severity="high",
                evidence=["Kernel panic"],
                phase="CLASSIFY_FAILURE",
                suggested_actions=["capture_recent_context"],
            )
        ]
        actions = plan_actions(matches)
        # panic 下不应执行 L1 采样
        assert not any(a.level == "L1" and a.command in L1_COMMANDS for a in actions)
        # 但可能包含 capture_recent_context（L2）
        assert any(a.command == "capture_recent_context" for a in actions)

    def test_no_matched_rules_returns_empty(self):
        matches = [
            RuleMatch(
                rule_id="no_output_after_attach",
                matched=False,
                confidence=0.0,
                severity="high",
                evidence=[],
                phase="OBSERVE_BOOT",
                suggested_actions=[],
            )
        ]
        actions = plan_actions(matches)
        assert actions == []

    def test_all_action_levels_are_l1_or_l2(self):
        """V1 不允许 L3/L4 动作。"""
        matches = [
            RuleMatch(
                rule_id="shell_prompt_available",
                matched=True,
                confidence=0.9,
                severity="low",
                evidence=["console:/ $"],
                phase="CLASSIFY_FAILURE",
                suggested_actions=["collect_read_only"],
            )
        ]
        actions = plan_actions(matches)
        assert all(a.level in ("L1", "L2") for a in actions)


# ============================================================================
# 动作执行（通过 mock transport 验证接口调用）
# ============================================================================

class TestExecuteAction:
    def test_execute_send_enter_calls_send_line_empty(self):
        transport = MagicMock(spec=FixtureTransport)
        transport.acquire_writer.return_value = True
        action = ActionRecord(
            action_id="a-1",
            level="L2",
            command="send_enter",
            reason="prompt not visible",
            result="PLANNED",
        )
        result = execute_action(action, transport)
        assert result.result == "OK"
        transport.send_line.assert_called_once_with("")

    def test_execute_dmesg_calls_send_line(self):
        transport = MagicMock(spec=FixtureTransport)
        transport.acquire_writer.return_value = True
        action = ActionRecord(
            action_id="a-2",
            level="L1",
            command="dmesg",
            reason="prompt available",
            result="PLANNED",
        )
        result = execute_action(action, transport)
        assert result.result == "OK"
        transport.send_line.assert_called_once_with("dmesg")

    def test_execute_wait_prompt_skips_send(self):
        """wait_prompt 不发送命令，只在 runner 层等待。"""
        transport = MagicMock(spec=FixtureTransport)
        action = ActionRecord(
            action_id="a-3",
            level="L2",
            command="wait_prompt",
            reason="prompt not visible",
            result="PLANNED",
        )
        result = execute_action(action, transport)
        assert result.result == "SKIP"
        transport.send_line.assert_not_called()

    def test_execute_capture_recent_context_skip_on_fixture(self):
        """fixture transport 不执行 capture_recent_context（无真实 buffer）。"""
        transport = MagicMock(spec=FixtureTransport)
        action = ActionRecord(
            action_id="a-4",
            level="L2",
            command="capture_recent_context",
            reason="panic detected",
            result="PLANNED",
        )
        result = execute_action(action, transport)
        assert result.result == "SKIP"


class TestExecuteActions:
    def test_executes_all_actions_and_updates_results(self):
        transport = MagicMock(spec=FixtureTransport)
        transport.acquire_writer.return_value = True
        actions = [
            ActionRecord(
                action_id="a-1",
                level="L1",
                command="dmesg",
                reason="prompt available",
                result="PLANNED",
            ),
            ActionRecord(
                action_id="a-2",
                level="L1",
                command="getprop",
                reason="prompt available",
                result="PLANNED",
            ),
        ]
        executed = execute_actions(actions, transport)
        assert len(executed) == 2
        assert all(a.result == "OK" for a in executed)

    def test_executes_in_order(self):
        transport = MagicMock(spec=FixtureTransport)
        transport.acquire_writer.return_value = True
        actions = [
            ActionRecord(
                action_id="a-1",
                level="L1",
                command="dmesg",
                reason="prompt available",
                result="PLANNED",
            ),
            ActionRecord(
                action_id="a-2",
                level="L1",
                command="getprop",
                reason="prompt available",
                result="PLANNED",
            ),
        ]
        execute_actions(actions, transport)
        calls = [c[0][0] for c in transport.send_line.call_args_list]
        assert calls == ["dmesg", "getprop"]