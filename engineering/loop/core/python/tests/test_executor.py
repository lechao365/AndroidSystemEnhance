"""CaseExecutor 测试：用例执行、依赖短路、collector 去重。"""
import pytest
from pathlib import Path

from loop_core.assertion_engine import AssertionEngine
from loop_core.case_loader import load_suite
from loop_core.executor import CaseExecutor
from loop_core.models import EvidenceBundle
from loop_core.transport import FixtureTransport


def _make_transport(rows: list[dict]) -> FixtureTransport:
    return FixtureTransport(rows)


def test_all_pass(tmp_path):
    """全部用例 pass。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: shell_ok
    command: ""
    assert: {type: prompt_visible}
    severity: critical
"""
    suite = load_suite(str(Path(_write(tmp_path, "t.yaml", suite_yaml))), [str(tmp_path)])
    # fixture 中包含 prompt 行
    transport = _make_transport([{"t": 1.0, "text": "console:/ $"}])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=["console:/ $"])

    assert bundle.summary["total"] == 1
    assert bundle.summary["passed"] == 1
    assert bundle.summary["failed"] == 0
    assert bundle.summary["overall"] == "PASS"
    assert bundle.cases[0].status == "pass"


def test_fail_triggers_collector(tmp_path):
    """用例 fail 时触发 collector。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: zygote_check
    command: "getprop init.svc.zygote"
    assert: {type: contains, value: "running"}
    severity: critical
    on_fail:
      collectors: [debug]
collectors:
  debug:
    commands: ["dmesg"]
    hints: "check dmesg"
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    # fixture 输出 "stopped"（不含 "running"）
    transport = _make_transport([
        {"t": 0.5, "text": "stopped"},
        {"t": 1.0, "text": "dmesg output line"},
    ])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=[])

    assert bundle.summary["failed"] == 1
    assert bundle.cases[0].status == "fail"
    # FQN: suite=t → collector FQN 为 t.debug
    assert "t.debug" in bundle.cases[0].triggered_collectors
    assert "t.debug" in bundle.evidence
    assert len(bundle.evidence["t.debug"].outputs) == 1


def test_dependency_skip(tmp_path):
    """前置用例 fail 时，依赖用例 skip。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: shell_ok
    command: ""
    assert: {type: prompt_visible}
    severity: critical
  - id: zygote_ok
    command: "getprop init.svc.zygote"
    assert: {type: contains, value: "running"}
    severity: critical
    requires: [shell_ok]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    # fixture 无 prompt 行 -> shell_ok fail -> zygote_ok skip
    transport = _make_transport([{"t": 0.5, "text": "no prompt here"}])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=["console:/ $"])

    assert bundle.cases[0].status == "fail"  # shell_ok
    assert bundle.cases[1].status == "skipped"  # zygote_ok
    assert "shell_ok" in bundle.cases[1].skip_reason
    assert bundle.summary["skipped"] == 1


def test_dependency_pass_executes_dependent(tmp_path):
    """前置用例 pass 时，依赖用例正常执行（不被错误 skip）。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: shell_ok
    command: ""
    assert: {type: prompt_visible}
    severity: critical
  - id: boot_check
    command: "getprop sys.boot_completed"
    assert: {type: contains, value: "1"}
    severity: critical
    requires: [shell_ok]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([
        {"t": 0.5, "text": "console:/ $"},
        {"t": 0.6, "text": "1"},
        {"t": 0.7, "text": "console:/ $"},
    ])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=["console:/ $"]
    )
    assert bundle.cases[0].status == "pass"  # shell_ok
    assert bundle.cases[1].status == "pass"  # boot_check executed and passed
    assert bundle.summary["overall"] == "PASS"


def test_dependency_skip_propagates(tmp_path):
    """skip 传播：a fail → b skip → c skip。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: a
    command: ""
    assert: {type: prompt_visible}
    severity: critical
  - id: b
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    requires: [a]
  - id: c
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    requires: [b]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([{"t": 0.5, "text": "no prompt"}])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=["console:/ $"])

    assert bundle.cases[0].status == "fail"
    assert bundle.cases[1].status == "skipped"
    assert bundle.cases[2].status == "skipped"


def test_collector_deduplication(tmp_path):
    """同 suite 内同 collector 只执行一次。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: check_a
    command: "true"
    assert: {type: contains, value: "no_match"}
    severity: critical
    on_fail: {collectors: [shared]}
  - id: check_b
    command: "true"
    assert: {type: contains, value: "no_match"}
    severity: critical
    on_fail: {collectors: [shared]}
collectors:
  shared:
    commands: ["dmesg"]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([
        {"t": 0.5, "text": "output_a"},
        {"t": 0.6, "text": "output_b"},
        {"t": 1.0, "text": "dmesg_line"},
    ])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=[])

    assert "t.shared" in bundle.evidence
    assert len(bundle.evidence["t.shared"].outputs) == 1  # 只执行一次


def test_warn_severity_does_not_fail_suite(tmp_path):
    """severity=warn 的用例 fail 不影响 overall（记录但不阻断）。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: warn_case
    command: "true"
    assert: {type: contains, value: "no_match"}
    severity: warn
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([{"t": 0.5, "text": "some output"}])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=[])

    # warn 用例 fail 计入 failed 计数，但 overall 仍可为 PASS（无 critical fail）
    assert bundle.cases[0].status == "fail"
    assert bundle.summary["overall"] == "PASS"


def test_fixture_transport_capture_isolated_per_command(tmp_path):
    """每条命令只看到自己发送后的输出，不会被前一条命令的历史污染。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: first
    command: "cmd1"
    assert: {type: contains, value: "first_only"}
  - id: second
    command: "cmd2"
    assert: {type: contains, value: "second_only"}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = FixtureTransport([
        {"t": 0.1, "text": "first_only"},
        {"t": 0.2, "text": "console:/ $"},
        {"t": 0.3, "text": "second_only"},
        {"t": 0.4, "text": "console:/ $"},
    ])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=["console:/ $"]
    )
    assert bundle.cases[0].status == "pass"
    assert bundle.cases[1].status == "pass"
    # 第二条 case 不应该看到 first_only
    assert "first_only" not in bundle.cases[1].output


def test_transport_send_error_becomes_case_error(tmp_path):
    """transport send_line 异常时，case 标记为 error，不崩溃。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: c1
    command: "boom"
    assert: {type: contains, value: "ok"}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])

    class BrokenTransport(FixtureTransport):
        def send_line(self, text: str) -> None:
            raise OSError("send failed")

    transport = BrokenTransport([])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(suite, device_id="rp5")
    assert bundle.cases[0].status == "error"
    assert bundle.cases[0].error_type == "transport_error"
    assert "send failed" in bundle.cases[0].failure_reason
    assert bundle.summary["overall"] == "FAIL"


def test_critical_skipped_case_makes_suite_non_pass(tmp_path):
    """critical case 被 skip 时，overall 不能为 PASS。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: shell_ok
    command: ""
    assert: {type: prompt_visible}
    severity: critical
  - id: dependent
    command: "echo hi"
    assert: {type: contains, value: "hi"}
    severity: critical
    requires: [shell_ok]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    # fixture 无 prompt → shell_ok fail → dependent skip
    transport = _make_transport([{"t": 0.5, "text": "no prompt"}])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=["console:/ $"]
    )
    assert bundle.cases[0].status == "fail"
    assert bundle.cases[1].status == "skipped"
    # KEY: critical case skipped → overall not PASS
    assert bundle.summary["overall"] != "PASS"


def test_collector_error_does_not_crash_suite(tmp_path):
    """collector 执行异常时，suite 不崩溃；collector 自身降级为 degraded。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: failing_case
    command: "true"
    assert: {type: contains, value: "no_match"}
    severity: critical
    on_fail:
      collectors: [broken_collector]
collectors:
  broken_collector:
    commands: ["dmesg"]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])

    class CollectorBrokenTransport(FixtureTransport):
        def send_line(self, text: str) -> None:
            if text == "dmesg":
                raise OSError("collector connection lost")
            super().send_line(text)

    transport = CollectorBrokenTransport([{"t": 0.5, "text": "some output"}])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=[]
    )
    # Case itself still fails (expected), but collector error doesn't crash
    assert bundle.cases[0].status == "fail"
    # Collector 内部捕获 OSError，单命令失败 → status=error
    assert "t.broken_collector" in bundle.evidence
    cr = bundle.evidence["t.broken_collector"]
    assert cr.status == "error"
    assert cr.partial is False
    assert "collector connection lost" in cr.error


def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def test_shell_reachable_fail_triggers_serial_collector(tmp_path):
    """shell_reachable fail 时触发 serial_recent collector（mode=serial_context）"""
    from pathlib import Path as P

    suite_yaml = tmp_path / "suite.yaml"
    suite_yaml.write_text("""
suite: t
version: 1
cases:
  - id: shell_reachable
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    on_fail:
      collectors: [serial_recent]
collectors:
  serial_recent:
    commands: []
    mode: serial_context
    hints: "capture serial transcript"
""", encoding="utf-8")

    suite = load_suite(str(suite_yaml), [str(tmp_path)])

    class ContextTransport(FixtureTransport):
        def describe_runtime_context(self, artifacts_dir=None):
            del artifacts_dir
            return {
                "transcript_path": "/tmp/serial.log",
                "serial_snippet": ["line1"],
                "reboot_cycles": 1,
            }

    transport = ContextTransport([])
    transport.acquire_writer()
    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(
        suite,
        device_id="test",
        prompt_markers=["console:/ $"],
        capture_timeout=1.0,
        recent_limit=20,
    )

    assert bundle.cases[0].status == "fail"
    assert "t.serial_recent" in bundle.evidence
    assert bundle.evidence["t.serial_recent"].artifact_paths == ["/tmp/serial.log"]


class FakeTransportWithReboot:
    """模拟 transport，记录 reboot_and_wait 调用。"""

    def __init__(self) -> None:
        self.reboot_called = False
        self.reboot_args: dict = {}

    def acquire_writer(self) -> bool:
        return True

    def release(self) -> None:
        pass

    def mark_output_boundary(self) -> int:
        return 0

    def send_line(self, text: str) -> None:
        pass

    def capture_since(self, boundary, timeout_sec, recent_limit, prompt_markers=None):
        from loop_core.transport import CommandCapture
        from loop_core.models import ObservedLine
        return CommandCapture(lines=[ObservedLine(t=0, text="1")], prompt_visible=True)

    def reboot_and_wait(self, **kwargs):
        from loop_core.models import RebootResult
        self.reboot_called = True
        self.reboot_args = kwargs
        return RebootResult(
            status="pass",
            transcript_lines=["Booting Linux", "init: zygote", "1"],
            failure_reason="",
            stage_reached="l3_verified",
            boot_duration_sec=42.0,
        )


def test_executor_action_case_calls_reboot_and_wait():
    """action: reboot 的 case 触发 transport.reboot_and_wait，结果转 TestCaseResult。"""
    from loop_core.case_loader import TestCase
    from loop_core.assertion_engine import AssertionEngine
    from loop_core.executor import CaseExecutor

    fake = FakeTransportWithReboot()
    executor = CaseExecutor(fake, AssertionEngine())

    case = TestCase(
        id="trigger_reboot",
        suite="system.boot",
        command="",
        action="reboot",
        assert_spec={},
        severity="critical",
    )
    result = executor._execute_case(
        case,
        results={},
        prompt_markers=["console:/ $"],
        capture_timeout=5.0,
        recent_limit=400,
        boot_markers=["Booting Linux", "init: zygote"],
        panic_markers=["Kernel panic"],
    )

    assert fake.reboot_called is True
    assert result.status == "pass"
    assert result.id == "trigger_reboot"
    assert "Booting Linux" in result.output
    assert result.assertion == {"type": "action", "action": "reboot"}


def test_executor_action_case_fail_includes_stage_in_reason():
    """action case fail 时，failure_reason 带 stage_reached 信息。"""
    from loop_core.case_loader import TestCase
    from loop_core.assertion_engine import AssertionEngine
    from loop_core.executor import CaseExecutor

    class FakeTransportTimeout(FakeTransportWithReboot):
        def reboot_and_wait(self, **kwargs):
            from loop_core.models import RebootResult
            self.reboot_called = True
            self.reboot_args = kwargs
            return RebootResult(
                status="fail",
                transcript_lines=["Booting Linux"],
                failure_reason="timeout",
                stage_reached="l1_boot_start",
                boot_duration_sec=30.0,
            )

    fake = FakeTransportTimeout()
    executor = CaseExecutor(fake, AssertionEngine())
    case = TestCase(
        id="trigger_reboot", suite="system.boot", command="",
        action="reboot", assert_spec={}, severity="critical",
    )
    result = executor._execute_case(
        case, results={}, prompt_markers=[], capture_timeout=5.0, recent_limit=400,
        boot_markers=["Booting Linux"], panic_markers=[],
    )
    assert result.status == "fail"
    assert "timeout" in result.failure_reason
    assert "l1_boot_start" in result.failure_reason


def test_host_case_passes_with_contains_assertion(tmp_path):
    suite_yaml = """
suite: t
version: 1
cases:
  - id: host_ok
    run_on: host
    command: "echo connected to 192.168.1.55:5555"
    assert: {type: contains, value: "connected to"}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite,
        device_id="rp5",
        prompt_markers=["console:/ $"],
    )
    assert bundle.cases[0].status == "pass"
    assert "connected to" in bundle.cases[0].output


def test_host_case_fails_when_assertion_not_met(tmp_path):
    suite_yaml = """
suite: t
version: 1
cases:
  - id: host_fail
    run_on: host
    command: "echo offline"
    assert: {type: contains, value: "connected to"}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite,
        device_id="rp5",
        prompt_markers=["console:/ $"],
    )
    assert bundle.cases[0].status == "fail"
    assert "expected output to contain 'connected to'" in bundle.cases[0].failure_reason


def test_host_case_supports_exit_code_zero_assertion(tmp_path):
    suite_yaml = """
suite: t
version: 1
cases:
  - id: host_exit
    run_on: host
    command: "true"
    assert: {type: exit_code_zero}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite,
        device_id="rp5",
        prompt_markers=["console:/ $"],
    )
    assert bundle.cases[0].status == "pass"


def test_host_case_runtime_error_maps_to_error_status(tmp_path):
    suite_yaml = """
suite: t
version: 1
cases:
  - id: host_err
    run_on: host
    command: "sleep 2"
    assert: {type: contains, value: "ok"}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite,
        device_id="rp5",
        prompt_markers=["console:/ $"],
        capture_timeout=0.2,
    )
    assert bundle.cases[0].status == "error"
    assert bundle.cases[0].error_type in {"host_error", "HostCommandError"}


def test_required_final_collector_failure_makes_suite_fail(tmp_path):
    suite_yaml = """
suite: t
version: 1
final_collectors: [pull_logs]
cases:
  - id: ok
    command: ""
    assert: {type: prompt_visible}
collectors:
  pull_logs:
    mode: adb_pull
    required: true
    remote_paths: ["/data/vendor/lechao_lcview/logs"]
    failure_code: LCVIEW_EVIDENCE_FAIL
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])

    class TransportWithContext(FixtureTransport):
        def pull_artifact(self, remote_path, local_dir, timeout_sec):
            raise OSError("pull failed")

    transport = TransportWithContext([{"t": 0.1, "text": "console:/ $"}])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=["console:/ $"]
    )
    assert bundle.summary["overall"] == "FAIL"
    assert bundle.summary["failure_code"] == "LCVIEW_EVIDENCE_FAIL"


def test_required_collector_run_raising_oserror_makes_suite_fail(tmp_path):
    """required collector 的 run() 抛 OSError 逃逸到 executor 时，suite 必须 FAIL。

    回归 P0-6：executor 降级分支未设 status（默认 "ok"），导致 required 判定
    `cr.status != "ok"` 恒不成立，required collector 抛错被静默判 PASS。
    本场景走 serial_context 模式，describe_runtime_context 抛 OSError 逃逸到
    executor.py 的 `except OSError` 降级路径（区别于 adb_pull 的内部捕获路径）。
    """
    suite_yaml = """
suite: t
version: 1
final_collectors: [serial_probe]
cases:
  - id: ok
    command: ""
    assert: {type: prompt_visible}
collectors:
  serial_probe:
    mode: serial_context
    required: true
    failure_code: SERIAL_PROBE_FAIL
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])

    class ContextOSErrorTransport(FixtureTransport):
        def describe_runtime_context(self, artifacts_dir=None):
            del artifacts_dir
            raise OSError("serial context lost")

    transport = ContextOSErrorTransport([{"t": 0.1, "text": "console:/ $"}])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=["console:/ $"]
    )
    # case 本身 pass，但 required collector 抛错 → overall 必须 FAIL
    assert bundle.cases[0].status == "pass"
    assert bundle.evidence["t.serial_probe"].status != "ok"
    assert bundle.summary["overall"] == "FAIL"
    assert bundle.summary["failure_code"] == "SERIAL_PROBE_FAIL"


def test_on_fail_collectors_triggered_on_error_status(tmp_path):
    """P2-7：critical 用例 status=error（执行异常）也触发 on_fail collectors。

    回归 P2-7：原仅 status=='fail' 触发 on_fail collectors，error 用例
    （执行异常）丢失诊断证据。
    """
    suite_yaml = """
suite: t
version: 1
cases:
  - id: boom_case
    command: "run_boom"
    assert: {type: contains, value: "ok"}
    severity: critical
    on_fail:
      collectors: [diag]
collectors:
  diag:
    commands: ["echo diag"]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])

    class ErrorCaseTransport(FixtureTransport):
        def send_line(self, text: str) -> None:
            if text == "run_boom":
                raise OSError("command channel lost")
            super().send_line(text)

    transport = ErrorCaseTransport([{"t": 0.1, "text": "diag output"}])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=[]
    )
    # 用例 status=error（执行异常）
    assert bundle.cases[0].status == "error"
    # error 也应触发 on_fail collector，采集诊断证据
    assert "t.diag" in bundle.evidence


def test_warn_fail_triggered_collectors_field_is_empty(tmp_path):
    """P2-7：warn 用例 fail 时 triggered_collectors 字段为空（实际不触发采集）。

    回归 P2-7：原 triggered_collectors 字段对所有 fail 都填 on_fail.collectors，
    但 warn 用例实际不触发采集，造成"声称触发"与"实际采集"不一致。
    """
    suite_yaml = """
suite: t
version: 1
cases:
  - id: warn_fail
    command: "true"
    assert: {type: contains, value: "no_match"}
    severity: warn
    on_fail:
      collectors: [diag]
collectors:
  diag:
    commands: ["echo diag"]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])

    transport = _make_transport([{"t": 0.5, "text": "some output"}])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=[]
    )
    assert bundle.cases[0].status == "fail"
    # warn 用例 triggered_collectors 字段应为空（未真正触发）
    assert bundle.cases[0].triggered_collectors == []
    # warn 用例不应触发 collector 采集
    assert "t.diag" not in bundle.evidence


def test_final_collector_runs_on_pass(tmp_path):
    suite_yaml = """
suite: t
version: 1
final_collectors: [pull_logs]
cases:
  - id: ok
    command: ""
    assert: {type: prompt_visible}
collectors:
  pull_logs:
    mode: adb_pull
    remote_paths: ["/data/x"]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])

    class PullTransport(FixtureTransport):
        def __init__(self, rows):
            super().__init__(rows)
            self.pulled = False

        def pull_artifact(self, remote_path, local_dir, timeout_sec):
            self.pulled = True
            return []

    transport = PullTransport([{"t": 0.1, "text": "console:/ $"}])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=["console:/ $"]
    )
    assert bundle.summary["overall"] == "PASS"
    assert transport.pulled is True
    assert "t.pull_logs" in bundle.evidence


def test_exit_code_zero_passes_on_serial_transport(tmp_path):
    """端到端：serial 平台命令退出码为 0 时 exit_code_zero 断言通过（P1-1）。

    回归 P1-1：serial 平台原本不回填 exit_code，exit_code_zero/equals 恒失败。
    修复后 send_line 注入 marker、capture_since 解析回填，断言可用。
    """
    from unittest.mock import MagicMock
    from rp5_serial.transport import Rp5SerialTransport

    suite_yaml = """
suite: t
version: 1
cases:
  - id: rc_zero
    command: "true"
    assert: {type: exit_code_zero}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])

    # mock serial client：capture_recent_entries 返回非 list（走降级路径），
    # capture_recent_lines/read_until_timeout 返回含退出码 marker 的输出
    client = MagicMock()
    client.capture_recent_lines.return_value = []
    client.read_until_timeout.return_value = ["__LE_EXIT_CODE__=0", "console:/ $"]
    client.acquire_writer.return_value = True

    transport = Rp5SerialTransport(client)
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=["console:/ $"]
    )
    assert bundle.cases[0].status == "pass"
    assert bundle.summary["overall"] == "PASS"


def test_exit_code_nonzero_fails_on_serial_transport(tmp_path):
    """端到端：serial 平台命令退出码非 0 时 exit_code_zero 断言失败（P1-1）。"""
    from unittest.mock import MagicMock
    from rp5_serial.transport import Rp5SerialTransport

    suite_yaml = """
suite: t
version: 1
cases:
  - id: rc_nonzero
    command: "false"
    assert: {type: exit_code_zero}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])

    client = MagicMock()
    client.capture_recent_lines.return_value = []
    client.read_until_timeout.return_value = ["__LE_EXIT_CODE__=1", "console:/ $"]
    client.acquire_writer.return_value = True

    transport = Rp5SerialTransport(client)
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=["console:/ $"]
    )
    assert bundle.cases[0].status == "fail"
    assert bundle.summary["overall"] == "FAIL"
