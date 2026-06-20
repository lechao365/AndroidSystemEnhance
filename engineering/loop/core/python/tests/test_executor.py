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
    assert "debug" in bundle.cases[0].triggered_collectors
    assert "debug" in bundle.evidence
    assert len(bundle.evidence["debug"].outputs) == 1


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

    assert "shared" in bundle.evidence
    assert len(bundle.evidence["shared"].outputs) == 1  # 只执行一次


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
    """collector 执行异常时，suite 不崩溃，记录 warning。"""
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
    # Collector error recorded as warning
    assert "warnings" in bundle.summary
    assert any("broken_collector" in w for w in bundle.summary["warnings"])


def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content)
    return str(p)
