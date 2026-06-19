"""runner.py 单元测试。

覆盖 boot-failure 状态机全链路：
- normal_boot -> EXIT_SUCCESS / shell_prompt_available
- kernel_panic -> EXIT_FAILURE / kernel_panic_detected
- boot_hang -> EXIT_FAILURE / kernel_boot_hang
- no_output -> EXIT_FAILURE / no_output_after_attach
- reboot_loop -> EXIT_FAILURE / reboot_loop_detected

状态机阶段：PREPARE -> ATTACH_SERIAL -> OBSERVE_BOOT -> CLASSIFY_FAILURE
           -> COLLECT_EVIDENCE -> REASSESS -> EXIT_SUCCESS / EXIT_FAILURE
"""
from pathlib import Path

import pytest

from boot_failure_debug.config import load_profiles
from boot_failure_debug.models import LoopAttempt
from boot_failure_debug.runner import BootFailureRunner
from boot_failure_debug.transport import FixtureTransport

REPO = Path(__file__).resolve().parents[6]
DEVICE_PROFILE = REPO / "engineering/loop/connection/profiles/devices/rp5/default.json"
WORKFLOW_PROFILE = REPO / "engineering/loop/profiles/boot-failure-debug/default.json"
FIXTURES = REPO / "engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures"


def _cfg(**override):
    return load_profiles(str(DEVICE_PROFILE), str(WORKFLOW_PROFILE), override=override or None)


def _runner(fixture_name: str, **override) -> BootFailureRunner:
    cfg = _cfg(**override)
    transport = FixtureTransport.from_jsonl(str(FIXTURES / f"{fixture_name}.jsonl"))
    return BootFailureRunner(cfg, transport)


# ============================================================================
# 状态机收口
# ============================================================================

class TestStateMachineOutcome:
    def test_normal_boot_exits_success(self):
        runner = _runner("normal_boot")
        attempt = runner.run()
        assert attempt.outcome == "EXIT_SUCCESS"
        assert attempt.final_classification == "shell_prompt_available"

    def test_kernel_panic_exits_failure(self):
        runner = _runner("kernel_panic")
        attempt = runner.run()
        assert attempt.outcome == "EXIT_FAILURE"
        assert attempt.final_classification == "kernel_panic_detected"

    def test_boot_hang_exits_failure(self):
        # 用大 timeout 让 quiet_window 触发 hang 规则
        runner = _runner("boot_hang", observe_timeout_sec=90, quiet_window_sec=5)
        attempt = runner.run()
        assert attempt.outcome == "EXIT_FAILURE"
        assert attempt.final_classification == "kernel_boot_hang"

    def test_no_output_exits_failure(self):
        runner = _runner("no_output")
        attempt = runner.run()
        assert attempt.outcome == "EXIT_FAILURE"
        assert attempt.final_classification == "no_output_after_attach"

    def test_reboot_loop_exits_failure(self):
        runner = _runner("reboot_loop")
        attempt = runner.run()
        assert attempt.outcome == "EXIT_FAILURE"
        assert attempt.final_classification == "reboot_loop_detected"


# ============================================================================
# attempt 结构
# ============================================================================

class TestAttemptStructure:
    def test_attempt_has_attempt_id(self):
        runner = _runner("normal_boot")
        attempt = runner.run()
        assert attempt.attempt_id.startswith("att-")

    def test_attempt_has_device_id(self):
        runner = _runner("normal_boot")
        attempt = runner.run()
        assert attempt.device_id == "rp5"

    def test_attempt_has_matched_rules(self):
        runner = _runner("kernel_panic")
        attempt = runner.run()
        assert len(attempt.matched_rules) == 6

    def test_attempt_has_actions(self):
        runner = _runner("normal_boot")
        attempt = runner.run()
        # prompt 可达时应规划 L1 命令
        assert len(attempt.actions) >= 4

    def test_attempt_has_boot_cycle_count(self):
        runner = _runner("reboot_loop")
        attempt = runner.run()
        assert attempt.boot_cycle_count >= 2

    def test_attempt_boot_cycle_count_single_for_normal(self):
        runner = _runner("normal_boot")
        attempt = runner.run()
        assert attempt.boot_cycle_count == 1

    def test_attempt_returns_loop_attempt_type(self):
        runner = _runner("normal_boot")
        attempt = runner.run()
        assert isinstance(attempt, LoopAttempt)


# ============================================================================
# REASSESS
# ============================================================================

class TestReassess:
    def test_reassess_does_not_change_panic_result(self):
        """REASSESS 轮对 panic 结果不变。"""
        runner = _runner("kernel_panic", max_reassess_rounds=1)
        attempt = runner.run()
        assert attempt.outcome == "EXIT_FAILURE"
        assert attempt.final_classification == "kernel_panic_detected"

    def test_reassess_zero_rounds_still_works(self):
        runner = _runner("normal_boot", max_reassess_rounds=0)
        attempt = runner.run()
        assert attempt.outcome == "EXIT_SUCCESS"


# ============================================================================
# 动作执行
# ============================================================================

class TestActionExecution:
    def test_normal_boot_executes_l1_commands(self):
        runner = _runner("normal_boot")
        attempt = runner.run()
        cmds = [a.command for a in attempt.actions]
        assert "dmesg" in cmds
        assert "getprop" in cmds

    def test_kernel_panic_does_not_execute_l1(self):
        runner = _runner("kernel_panic")
        attempt = runner.run()
        # panic 下不应执行 L1 只读命令
        l1_cmds = [a for a in attempt.actions if a.level == "L1" and a.command in ("dmesg", "getprop", "mount", "ps")]
        assert len(l1_cmds) == 0

    def test_action_results_are_updated(self):
        runner = _runner("normal_boot")
        attempt = runner.run()
        # 执行的动作应该有 OK/SKIP 结果（不是 PLANNED）
        executed = [a for a in attempt.actions if a.command in ("dmesg", "getprop", "mount", "ps")]
        assert all(a.result in ("OK", "SKIP") for a in executed)
