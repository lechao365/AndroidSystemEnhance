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
from loop_core.models import LoopAttempt
from boot_failure_debug.runner import BootFailureRunner
from loop_core.transport import FixtureTransport

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


class PromptRecoveringTransport:
    def __init__(self):
        self._capture_calls = 0
        self.sent: list[str] = []
        self.wait_calls: list[tuple[list[str], float, int]] = []
        self.writer_held = False

    def acquire_writer(self):
        self.writer_held = True
        return True

    def release(self):
        self.writer_held = False
        return None

    def send_line(self, text: str):
        self.sent.append(text)

    def capture_window(self, timeout_sec: float, recent_limit: int):
        from loop_core.models import ObservedLine

        self._capture_calls += 1
        if self._capture_calls == 1:
            return [ObservedLine(1.0, "init: starting service 'zygote'")]
        if self._capture_calls == 2:
            return [ObservedLine(2.0, "console:/ $")]
        return [ObservedLine(3.0, "uid=0(root) gid=0(root)")]

    def wait_for_pattern(self, patterns: list[str], timeout_sec: float, recent_limit: int):
        from loop_core.models import ObservedLine

        self.wait_calls.append((patterns, timeout_sec, recent_limit))
        return ObservedLine(2.0, "console:/ $")


class PromptTimeoutTransport:
    def __init__(self):
        self._capture_calls = 0
        self.sent: list[str] = []
        self.wait_calls: list[tuple[list[str], float, int]] = []
        self.writer_held = False

    @property
    def capture_calls(self) -> int:
        return self._capture_calls

    def acquire_writer(self):
        self.writer_held = True
        return True

    def release(self):
        self.writer_held = False
        return None

    def send_line(self, text: str):
        self.sent.append(text)

    def capture_window(self, timeout_sec: float, recent_limit: int):
        from loop_core.models import ObservedLine

        self._capture_calls += 1
        return [ObservedLine(1.0, "init: starting service 'zygote'")]

    def wait_for_pattern(self, patterns: list[str], timeout_sec: float, recent_limit: int):
        self.wait_calls.append((patterns, timeout_sec, recent_limit))
        return None


class AcquireWriterBusyTransport:
    def __init__(self):
        self.acquire_calls = 0
        self.released = False
        self.capture_called = False
        self.send_called = False
        self.wait_called = False

    def acquire_writer(self):
        self.acquire_calls += 1
        return False

    def release(self):
        self.released = True
        return None

    def send_line(self, text: str):
        self.send_called = True
        raise AssertionError("send_line should not be called when writer is busy")

    def capture_window(self, timeout_sec: float, recent_limit: int):
        self.capture_called = True
        raise AssertionError("capture_window should not be called when writer is busy")

    def wait_for_pattern(self, patterns: list[str], timeout_sec: float, recent_limit: int):
        self.wait_called = True
        raise AssertionError("wait_for_pattern should not be called when writer is busy")


class ExplodingTransport:
    def __init__(self):
        self.released = False
        self.writer_held = False

    def acquire_writer(self):
        self.writer_held = True
        return True

    def release(self):
        self.released = True
        self.writer_held = False
        return None

    def send_line(self, text: str):
        raise AssertionError("send_line should not be called")

    def capture_window(self, timeout_sec: float, recent_limit: int):
        raise RuntimeError("boom during capture")

    def wait_for_pattern(self, patterns: list[str], timeout_sec: float, recent_limit: int):
        raise AssertionError("wait_for_pattern should not be called")


class TestPromptRecovery:
    def test_login_prompt_not_reached_can_reclassify_to_shell_prompt(self):
        cfg = _cfg()
        transport = PromptRecoveringTransport()
        runner = BootFailureRunner(cfg, transport)

        attempt = runner.run()

        assert attempt.outcome == "EXIT_SUCCESS"
        assert attempt.final_classification == "shell_prompt_available"
        assert transport.sent[0] == ""
        assert transport.wait_calls == [(
            cfg.prompt_markers,
            cfg.prompt_wait_sec,
            cfg.recent_lines_limit,
        )]
        assert attempt.actions[0].metadata.get("sent_inputs") == [""]

    def test_l1_actions_capture_output_lines_after_prompt_recovery(self):
        runner = BootFailureRunner(_cfg(), PromptRecoveringTransport())

        attempt = runner.run()

        dmesg_action = next(a for a in attempt.actions if a.command == "dmesg")
        assert dmesg_action.output_lines == ["uid=0(root) gid=0(root)"]
        assert dmesg_action.metadata["captured_line_count"] >= 1

    def test_wait_prompt_timeout_keeps_failure_outcome(self):
        cfg = _cfg(quiet_window_sec=120)
        transport = PromptTimeoutTransport()
        runner = BootFailureRunner(cfg, transport)

        attempt = runner.run()

        assert attempt.outcome == "EXIT_FAILURE"
        assert attempt.final_classification == "login_prompt_not_reached"
        assert transport.wait_calls == [(
            cfg.prompt_markers,
            cfg.prompt_wait_sec,
            cfg.recent_lines_limit,
        )]
        wait_action = next(a for a in attempt.actions if a.command == "wait_prompt")
        assert wait_action.result == "FAIL"
        assert wait_action.metadata["pattern_matched"] is False

    def test_wait_prompt_timeout_does_not_enter_reobserve_success_path(self):
        cfg = _cfg(quiet_window_sec=120)
        transport = PromptTimeoutTransport()
        runner = BootFailureRunner(cfg, transport)

        attempt = runner.run()

        assert transport.capture_calls == 1
        assert all(a.command not in ("dmesg", "getprop", "mount", "ps") for a in attempt.actions)
        assert attempt.final_classification == "login_prompt_not_reached"


# ============================================================================
# 状态机收口
# ============================================================================

class TestStateMachineOutcome:
    def test_attach_serial_writer_busy_exits_failure_without_observe(self):
        runner = BootFailureRunner(_cfg(), AcquireWriterBusyTransport())

        attempt = runner.run()

        assert attempt.outcome == "EXIT_FAILURE"
        assert attempt.final_classification == "writer_busy"
        assert attempt.actions == []
        assert attempt.matched_rules == []

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
    def test_run_releases_transport_when_writer_busy(self):
        transport = AcquireWriterBusyTransport()
        runner = BootFailureRunner(_cfg(), transport)

        attempt = runner.run()

        assert attempt.final_classification == "writer_busy"
        assert transport.released is True
        assert transport.acquire_calls == 1
        assert transport.capture_called is False
        assert transport.send_called is False
        assert transport.wait_called is False

    def test_run_releases_transport_when_exception_raised(self):
        transport = ExplodingTransport()
        runner = BootFailureRunner(_cfg(), transport)

        with pytest.raises(RuntimeError, match="boom during capture"):
            runner.run()

        assert transport.released is True

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
