"""case_loader 测试：YAML 加载、include 解析、requires 拓扑排序、环检测。"""
import pytest
from pathlib import Path

from loop_core.case_loader import CaseSuite, TestCase, load_suite


def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def test_load_minimal_suite(tmp_path):
    """加载最小 suite（1 条用例，无 include）。"""
    path = _write(tmp_path, "test.yaml", """
suite: test-suite
version: 1
cases:
  - id: case_a
    description: "test case A"
    command: "echo hello"
    assert:
      type: contains
      value: "hello"
    severity: critical
""")
    suite = load_suite(path, [str(tmp_path)])
    assert suite.name == "test-suite"
    assert suite.version == 1
    assert len(suite.cases) == 1
    assert suite.cases[0].id == "case_a"
    assert suite.cases[0].command == "echo hello"
    assert suite.cases[0].assert_spec == {"type": "contains", "value": "hello"}
    assert suite.cases[0].severity == "critical"


def test_load_with_collectors(tmp_path):
    """加载含 collector 定义的 suite。"""
    path = _write(tmp_path, "test.yaml", """
suite: test-suite
version: 1
cases:
  - id: case_a
    command: "true"
    assert: {type: exit_code_zero}
    severity: critical
    on_fail:
      collectors: [debug_log]
collectors:
  debug_log:
    commands: ["dmesg", "logcat -d"]
    hints: "check kernel/android logs"
""")
    suite = load_suite(path, [str(tmp_path)])
    assert "debug_log" in suite.collectors
    assert suite.collectors["debug_log"]["commands"] == ["dmesg", "logcat -d"]
    assert suite.collectors["debug_log"]["hints"] == "check kernel/android logs"


def test_include_merges_cases(tmp_path):
    """include 合并被引用 suite 的 cases 和 collectors。"""
    _write(tmp_path, "common.yaml", """
suite: common
version: 1
cases:
  - id: shell_reachable
    command: ""
    assert: {type: prompt_visible}
    severity: critical
""")
    path = _write(tmp_path, "system.yaml", """
suite: system-suite
version: 1
include:
  - common
cases:
  - id: boot_done
    command: "getprop sys.boot_completed"
    assert: {type: contains, value: "1"}
    severity: critical
""")
    suite = load_suite(path, [str(tmp_path)])
    case_ids = {c.id for c in suite.cases}
    assert case_ids == {"shell_reachable", "boot_done"}
    assert suite.name == "system-suite"  # name 取主 suite


def test_include_merges_collectors(tmp_path):
    """include 合并 collector 定义。"""
    _write(tmp_path, "common.yaml", """
suite: common
version: 1
collectors:
  base_log:
    commands: ["dmesg"]
cases:
  - id: c1
    command: "true"
    assert: {type: exit_code_zero}
    severity: critical
""")
    path = _write(tmp_path, "sys.yaml", """
suite: sys
version: 1
include: [common]
collectors:
  crash_log:
    commands: ["logcat -b crash"]
cases:
  - id: c2
    command: "true"
    assert: {type: exit_code_zero}
    severity: critical
""")
    suite = load_suite(path, [str(tmp_path)])
    assert "base_log" in suite.collectors
    assert "crash_log" in suite.collectors


def test_requires_field_parsed(tmp_path):
    """requires 字段正确解析。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: "true"
    assert: {type: exit_code_zero}
    severity: critical
  - id: b
    command: "true"
    assert: {type: exit_code_zero}
    severity: critical
    requires: [a]
""")
    suite = load_suite(path, [str(tmp_path)])
    case_b = [c for c in suite.cases if c.id == "b"][0]
    assert case_b.requires == ["a"]


def test_topological_order(tmp_path):
    """用例按拓扑序排列（被依赖的在前）。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: c
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    requires: [b]
  - id: a
    command: ""
    assert: {type: prompt_visible}
    severity: critical
  - id: b
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    requires: [a]
""")
    suite = load_suite(path, [str(tmp_path)])
    ids = [c.id for c in suite.cases]
    assert ids.index("a") < ids.index("b")
    assert ids.index("b") < ids.index("c")


def test_cycle_detection_raises(tmp_path):
    """requires 形成环时报错。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    requires: [b]
  - id: b
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    requires: [a]
""")
    with pytest.raises(ValueError, match="cycle"):
        load_suite(path, [str(tmp_path)])


def test_requires_nonexistent_warns_but_loads(tmp_path):
    """requires 引用不存在的用例时，加载成功（执行时该用例会 skip）。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    requires: [nonexistent]
""")
    suite = load_suite(path, [str(tmp_path)])
    assert len(suite.cases) == 1  # 加载成功，执行时处理


def test_default_severity_is_critical(tmp_path):
    """severity 未指定时默认 critical。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: ""
    assert: {type: prompt_visible}
""")
    suite = load_suite(path, [str(tmp_path)])
    assert suite.cases[0].severity == "critical"
