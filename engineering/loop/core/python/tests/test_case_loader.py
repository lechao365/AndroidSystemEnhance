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


def test_requires_nonexistent_raises_at_load_time(tmp_path):
    """requires 引用不存在的用例时，加载阶段即抛 ValueError（fail-fast）。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    requires: [missing_case]
""")
    with pytest.raises(ValueError, match="missing required case"):
        load_suite(path, [str(tmp_path)])


def test_unknown_collector_reference_raises_at_load_time(tmp_path):
    """on_fail.collectors 引用未定义 collector 时，加载阶段即抛 ValueError。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: "true"
    assert: {type: contains, value: "ok"}
    on_fail:
      collectors: [missing_collector]
""")
    with pytest.raises(ValueError, match="unknown collector"):
        load_suite(path, [str(tmp_path)])


def test_invalid_severity_raises_at_load_time(tmp_path):
    """severity 取值非法时，加载阶段即抛 ValueError。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: ""
    assert: {type: prompt_visible}
    severity: fatal
""")
    with pytest.raises(ValueError, match="invalid severity"):
        load_suite(path, [str(tmp_path)])


def test_include_duplicate_case_id_raises(tmp_path):
    """include 与主 suite 出现重复 case id 时，加载阶段即抛 ValueError。"""
    _write(tmp_path, "common.yaml", """
suite: common
version: 1
cases:
  - id: shared
    command: ""
    assert: {type: prompt_visible}
""")
    path = _write(tmp_path, "system.yaml", """
suite: system
version: 1
include: [common]
cases:
  - id: shared
    command: ""
    assert: {type: prompt_visible}
""")
    with pytest.raises(ValueError, match="duplicate case id"):
        load_suite(path, [str(tmp_path)])


def test_suite_defaults_parsed(tmp_path):
    """suite 级 defaults 字段正确解析到 CaseSuite.defaults。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
defaults:
  capture_timeout: 12.5
  recent_limit: 200
cases:
  - id: a
    command: ""
    assert: {type: prompt_visible}
""")
    suite = load_suite(path, [str(tmp_path)])
    assert suite.defaults.capture_timeout == 12.5
    assert suite.defaults.recent_limit == 200


def test_suite_defaults_absent(tmp_path):
    """未提供 defaults 时，CaseSuite.defaults 保持默认值。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: ""
    assert: {type: prompt_visible}
""")
    suite = load_suite(path, [str(tmp_path)])
    assert suite.defaults.capture_timeout is None
    assert suite.defaults.recent_limit is None


def test_case_missing_id_raises(tmp_path):
    """case 缺少 id 时报错。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - command: "echo hi"
    assert: {type: contains, value: "hi"}
""")
    with pytest.raises(ValueError, match="missing required keys"):
        load_suite(path, [str(tmp_path)])


def test_case_missing_assert_raises(tmp_path):
    """case 缺少 assert 时报错。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: "echo hi"
""")
    with pytest.raises(ValueError, match="missing required keys"):
        load_suite(path, [str(tmp_path)])


def test_unknown_assertion_type_raises(tmp_path):
    """未知断言类型报错。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: "echo hi"
    assert: {type: bogus_check, value: "hi"}
""")
    with pytest.raises(ValueError, match="unknown assertion type"):
        load_suite(path, [str(tmp_path)])


def test_contains_missing_value_raises(tmp_path):
    """contains 断言缺少 value 报错。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: "echo hi"
    assert: {type: contains}
""")
    with pytest.raises(ValueError, match="requires value"):
        load_suite(path, [str(tmp_path)])


def test_regex_missing_pattern_raises(tmp_path):
    """regex 断言缺少 pattern 报错。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: "echo hi"
    assert: {type: regex}
""")
    with pytest.raises(ValueError, match="requires pattern"):
        load_suite(path, [str(tmp_path)])
