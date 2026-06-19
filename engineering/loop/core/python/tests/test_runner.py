"""通用 LoopRunner 测试：场景无关，纯用例驱动。"""
from pathlib import Path

from loop_core.assertion_engine import AssertionEngine
from loop_core.case_loader import load_suite
from loop_core.runner import LoopRunner
from loop_core.transport import FixtureTransport


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def test_runner_executes_suite_and_returns_bundle(tmp_path):
    """LoopRunner 执行 suite 返回 EvidenceBundle。"""
    path = _write(tmp_path, "t.yaml", """
suite: test-suite
version: 1
cases:
  - id: shell_check
    command: ""
    assert: {type: prompt_visible}
    severity: critical
""")
    suite = load_suite(path, [str(tmp_path)])
    transport = FixtureTransport([{"t": 1.0, "text": "console:/ $"}])

    runner = LoopRunner(
        device_id="rp5",
        prompt_markers=["console:/ $"],
        transport=transport,
        suite=suite,
    )
    bundle = runner.run()

    assert bundle.device_id == "rp5"
    assert bundle.suite == "test-suite"
    assert bundle.summary["total"] == 1
    assert bundle.cases[0].status == "pass"


def test_runner_writer_busy_returns_failure_bundle(tmp_path):
    """writer 获取失败时返回 fail bundle。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: c1
    command: ""
    assert: {type: prompt_visible}
""")
    suite = load_suite(path, [str(tmp_path)])

    class BusyTransport(FixtureTransport):
        def acquire_writer(self):
            return False

    transport = BusyTransport([])

    runner = LoopRunner("rp5", [], transport, suite)
    bundle = runner.run()

    assert bundle.summary["overall"] == "FAIL"


def test_runner_uses_custom_executor_config(tmp_path):
    """LoopRunner 支持 capture_timeout / recent_limit 配置。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: c1
    command: "echo test"
    assert: {type: contains, value: "test"}
""")
    suite = load_suite(path, [str(tmp_path)])
    transport = FixtureTransport([
        {"t": 0.5, "text": "test output"},
    ])

    runner = LoopRunner("rp5", [], transport, suite, capture_timeout=2.0, recent_limit=100)
    bundle = runner.run()

    assert bundle.cases[0].status == "pass"
