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
    """加载含 collector 定义的 suite；collector 以 FQN 存储，on_fail 引用解析为 FQN。"""
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
    # collector 以 FQN 存储
    assert "test-suite.debug_log" in suite.collectors
    assert suite.collectors["test-suite.debug_log"]["commands"] == ["dmesg", "logcat -d"]
    assert suite.collectors["test-suite.debug_log"]["hints"] == "check kernel/android logs"
    # on_fail.collectors 解析为 FQN
    case_a = suite.cases[0]
    assert "test-suite.debug_log" in case_a.on_fail.get("collectors", [])


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
    """include 合并 collector 定义；include 的 collector 保留其来源 suite 的 FQN。"""
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
    assert "common.base_log" in suite.collectors
    assert "sys.crash_log" in suite.collectors


def test_requires_field_parsed(tmp_path):
    """requires 字段正确解析；同 suite 内 short name 解析为 FQN。"""
    path = _write(tmp_path, "t.yaml", """
suite: my.suite
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
    assert case_b.requires == ["my.suite.a"]


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
    """同 FQN 的 case（include 与主 suite 同名 suite + 同 id）加载阶段即抛 ValueError。"""
    _write(tmp_path, "common.yaml", """
suite: common
version: 1
cases:
  - id: shared
    command: ""
    assert: {type: prompt_visible}
""")
    path = _write(tmp_path, "system.yaml", """
suite: common
version: 1
include: [common]
cases:
  - id: shared
    command: ""
    assert: {type: prompt_visible}
""")
    # 两个 suite 均为 common，case id 均为 shared → FQN 均为 common.shared → 冲突
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


# ---------- FQN 命名模型 ----------


def test_fqn_assigned_to_cases(tmp_path):
    """case 获得基于 suite name 的 FQN。"""
    path = _write(tmp_path, "t.yaml", """
suite: my.suite
version: 1
cases:
  - id: case_a
    command: ""
    assert: {type: prompt_visible}
""")
    suite = load_suite(path, [str(tmp_path)])
    assert suite.cases[0].fqn == "my.suite.case_a"


def test_short_requires_resolves_to_fqn(tmp_path):
    """同 suite 内 short name requires 解析为 FQN。"""
    path = _write(tmp_path, "t.yaml", """
suite: my.suite
version: 1
cases:
  - id: a
    command: ""
    assert: {type: prompt_visible}
  - id: b
    command: ""
    assert: {type: prompt_visible}
    requires: [a]
""")
    suite = load_suite(path, [str(tmp_path)])
    case_b = [c for c in suite.cases if c.id == "b"][0]
    assert case_b.requires == ["my.suite.a"]


def test_cross_suite_requires_uses_fqn(tmp_path):
    """跨 suite requires 必须解析为已存在的 FQN。"""
    _write(tmp_path, "base.yaml", """
suite: common.base
version: 1
cases:
  - id: setup
    command: ""
    assert: {type: prompt_visible}
""")
    path = _write(tmp_path, "sys.yaml", """
suite: system.main
version: 1
include: [base]
cases:
  - id: check
    command: ""
    assert: {type: prompt_visible}
    requires: [common.base.setup]
""")
    suite = load_suite(path, [str(tmp_path)])
    case_check = [c for c in suite.cases if c.id == "check"][0]
    assert "common.base.setup" in case_check.requires


def test_cross_suite_short_name_fails(tmp_path):
    """跨 suite 引用既非本地、也非已知 FQN/唯一短名 时报 missing required case。"""
    _write(tmp_path, "base.yaml", """
suite: common.base
version: 1
cases:
  - id: setup
    command: ""
    assert: {type: prompt_visible}
""")
    path = _write(tmp_path, "sys.yaml", """
suite: system.main
version: 1
include: [base]
cases:
  - id: check
    command: ""
    assert: {type: prompt_visible}
    requires: [totally_missing]
""")
    # "totally_missing" 既不在本地，也不是任何已加载 FQN 的短名
    with pytest.raises(ValueError, match="missing required case"):
        load_suite(path, [str(tmp_path)])


def test_ambiguous_short_name_requires_raises(tmp_path):
    """短名在多个 suite 命名空间存在且非本地时，按 ambiguous 报错。"""
    _write(tmp_path, "a.yaml", """
suite: mod.a
version: 1
cases:
  - id: dup
    command: ""
    assert: {type: prompt_visible}
""")
    _write(tmp_path, "b.yaml", """
suite: mod.b
version: 1
cases:
  - id: dup
    command: ""
    assert: {type: prompt_visible}
""")
    path = _write(tmp_path, "main.yaml", """
suite: mod.main
version: 1
include: [a, b]
cases:
  - id: m
    command: ""
    assert: {type: prompt_visible}
    requires: [dup]
""")
    # "dup" 在 mod.a 和 mod.b 各出现一次 → 模糊
    with pytest.raises(ValueError, match="ambiguous required case"):
        load_suite(path, [str(tmp_path)])


def test_collector_fqn_resolution(tmp_path):
    """collector 也获得 FQN，on_fail 引用解析为 FQN。"""
    path = _write(tmp_path, "t.yaml", """
suite: my.suite
version: 1
cases:
  - id: a
    command: "true"
    assert: {type: contains, value: "ok"}
    on_fail:
      collectors: [debug]
collectors:
  debug:
    commands: ["dmesg"]
""")
    suite = load_suite(path, [str(tmp_path)])
    case_a = suite.cases[0]
    assert "my.suite.debug" in case_a.on_fail.get("collectors", [])
    assert "my.suite.debug" in suite.collectors


# ---------- 参数化原子用例 ----------


def test_parameterized_case_expands(tmp_path):
    """参数化用例展开为多条。"""
    path = _write(tmp_path, "t.yaml", """
suite: common.service
version: 1
parameters:
  services: [zygote, surfaceflinger]
cases:
  - id: service_running
    foreach: services
    command: "getprop init.svc.${item}"
    assert: {type: contains, value: "running"}
""")
    suite = load_suite(path, [str(tmp_path)])
    ids = [c.id for c in suite.cases]
    assert "service_running_zygote" in ids
    assert "service_running_surfaceflinger" in ids
    assert len(suite.cases) == 2


def test_parameterized_substitutes_item_in_command(tmp_path):
    """${item} 在 command 中正确替换。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
parameters:
  names: [alpha, beta]
cases:
  - id: check
    foreach: names
    command: "getprop ${item}"
    assert: {type: contains, value: "ok"}
""")
    suite = load_suite(path, [str(tmp_path)])
    commands = [c.command for c in suite.cases]
    assert "getprop alpha" in commands
    assert "getprop beta" in commands


def test_parameterized_substitutes_in_description_and_assert(tmp_path):
    """${item} 在 description 和 assert.value 中替换。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
parameters:
  names: [alpha]
cases:
  - id: check
    foreach: names
    description: "check ${item}"
    command: "echo ${item}"
    assert: {type: contains, value: "${item}_ok"}
""")
    suite = load_suite(path, [str(tmp_path)])
    case = suite.cases[0]
    assert case.description == "check alpha"
    assert case.assert_spec["value"] == "alpha_ok"


def test_parameterized_foreach_unknown_param_raises(tmp_path):
    """foreach 引用不存在的 parameter 时报错。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
parameters:
  names: [alpha]
cases:
  - id: check
    foreach: nonexistent
    command: "echo hi"
    assert: {type: contains, value: "hi"}
""")
    with pytest.raises(ValueError, match="unknown parameter"):
        load_suite(path, [str(tmp_path)])


def test_parameterized_non_list_param_raises(tmp_path):
    """parameter 值非 list 时报错。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
parameters:
  names: alpha
cases:
  - id: check
    foreach: names
    command: "echo hi"
    assert: {type: contains, value: "hi"}
""")
    with pytest.raises(ValueError, match="must be a list"):
        load_suite(path, [str(tmp_path)])


def test_non_foreach_case_not_affected(tmp_path):
    """无 foreach 的用例不受参数化影响。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
parameters:
  names: [alpha, beta]
cases:
  - id: plain_case
    command: "echo hello"
    assert: {type: contains, value: "hello"}
""")
    suite = load_suite(path, [str(tmp_path)])
    assert len(suite.cases) == 1
    assert suite.cases[0].id == "plain_case"


def test_cross_suite_collector_reference_uses_fqn(tmp_path):
    """跨 suite 引用 collector 必须用 FQN。"""
    _write(tmp_path, "base.yaml", """
suite: common.base
version: 1
collectors:
  shared_log:
    commands: ["dmesg"]
cases:
  - id: c1
    command: ""
    assert: {type: prompt_visible}
""")
    path = _write(tmp_path, "sys.yaml", """
suite: system.main
version: 1
include: [base]
cases:
  - id: c2
    command: "true"
    assert: {type: contains, value: "ok"}
    on_fail:
      collectors: [common.base.shared_log]
""")
    suite = load_suite(path, [str(tmp_path)])
    case_c2 = [c for c in suite.cases if c.id == "c2"][0]
    assert "common.base.shared_log" in case_c2.on_fail.get("collectors", [])


# ---------- action 字段（Task 3/4/5） ----------


def test_test_case_has_action_field():
    """TestCase dataclass 含 action 字段，默认空字符串。"""
    from loop_core.case_loader import TestCase

    case = TestCase(id="x", suite="s", command="", assert_spec={})
    assert case.action == ""


def test_test_case_action_can_be_set():
    """TestCase.action 可被显式设置为 'reboot'。"""
    from loop_core.case_loader import TestCase

    case = TestCase(id="x", suite="s", command="", assert_spec={}, action="reboot")
    assert case.action == "reboot"


def test_parse_case_extracts_action():
    """_parse_case 从 YAML 节点提取 action 字段。"""
    from loop_core.case_loader import _parse_case

    defn = {"id": "trigger_reboot", "action": "reboot", "assert": {}}
    case = _parse_case(defn, "system.boot")
    assert case.action == "reboot"
    assert case.command == ""


def test_parse_case_action_defaults_empty():
    """无 action 字段时默认空字符串（命令模式）。"""
    from loop_core.case_loader import _parse_case

    defn = {"id": "x", "command": "ls", "assert": {"type": "contains", "value": "x"}}
    case = _parse_case(defn, "s")
    assert case.action == ""


def test_validate_case_rejects_action_and_command_both_present():
    """action 与 command 互斥：两者都有则报错。"""
    from loop_core.case_loader import _validate_case_definition

    with pytest.raises(ValueError, match="action and command are mutually exclusive"):
        _validate_case_definition({
            "id": "x",
            "action": "reboot",
            "command": "ls",
            "assert": {"type": "contains", "value": "y"},
        })


def test_validate_case_rejects_unknown_action():
    """action 只允许已知值（当前只有 reboot）。"""
    from loop_core.case_loader import _validate_case_definition

    with pytest.raises(ValueError, match="unknown action"):
        _validate_case_definition({
            "id": "x",
            "action": "sleep",
            "assert": {"type": "contains", "value": "y"},
        })


def test_validate_case_action_reboot_does_not_require_assert_value():
    """action: reboot 的 case 不需要 assert value。"""
    from loop_core.case_loader import _validate_case_definition

    _validate_case_definition({"id": "x", "action": "reboot", "assert": {}})


def test_validate_case_still_accepts_command_only():
    """纯命令模式（无 action）仍被接受（向后兼容）。"""
    from loop_core.case_loader import _validate_case_definition

    _validate_case_definition({
        "id": "x",
        "command": "ls",
        "assert": {"type": "contains", "value": "y"},
    })


def test_load_suite_accepts_action_case_with_empty_assert(tmp_path):
    """action: reboot 的 case，assert 为空 dict 也能正常加载。"""
    from loop_core.case_loader import load_suite

    suite_file = tmp_path / "boot-test.yaml"
    suite_file.write_text(
        """
suite: system.boot_test
version: 1
cases:
  - id: trigger_reboot
    action: reboot
    description: "触发重启"
    assert: {}
  - id: boot_ok
    command: "getprop sys.boot_completed"
    assert: {type: contains, value: "1"}
    requires: [trigger_reboot]
""",
        encoding="utf-8",
    )
    suite = load_suite(str(suite_file), [str(tmp_path)])
    assert suite.cases[0].id == "trigger_reboot"
    assert suite.cases[0].action == "reboot"
    assert suite.cases[1].id == "boot_ok"
    assert suite.cases[1].requires == ["system.boot_test.trigger_reboot"]


def test_load_suite_action_case_no_assert_value_still_validates():
    """action case 的 assert 不含 type，_validate_assertion_shape 应跳过。"""
    from loop_core.case_loader import _validate_assertion_shape

    _validate_assertion_shape({})


# ---------- Task 13/14：kmsg collector + trigger_reboot ----------


def test_common_shell_yaml_has_kmsg_collector():
    """common/shell.yaml 包含 kmsg collector 定义。"""
    from pathlib import Path
    from loop_core.case_loader import load_suite

    repo_root = Path(__file__).resolve()
    while repo_root.name != "engineering":
        repo_root = repo_root.parent
        if repo_root == repo_root.parent:
            break
    cases_dir = repo_root / "loop" / "cases"
    shell_yaml = cases_dir / "common" / "shell.yaml"

    suite = load_suite(str(shell_yaml), [str(cases_dir)])
    assert "common.shell.kmsg" in suite.collectors
    kmsg_spec = suite.collectors["common.shell.kmsg"]
    assert "cat /proc/last_kmsg" in kmsg_spec["commands"][0]


def test_boot_success_yaml_has_trigger_reboot_first():
    """boot-success.yaml 首条 case 是 trigger_reboot，后续 case requires 它。"""
    from pathlib import Path
    from loop_core.case_loader import load_suite

    repo_root = Path(__file__).resolve()
    while repo_root.name != "engineering":
        repo_root = repo_root.parent
        if repo_root == repo_root.parent:
            break
    cases_dir = repo_root / "loop" / "cases"
    boot_yaml = cases_dir / "system" / "boot-success.yaml"

    suite = load_suite(str(boot_yaml), [str(cases_dir)])
    reboot_cases = [c for c in suite.cases if c.action == "reboot"]
    assert len(reboot_cases) == 1
    assert reboot_cases[0].id == "trigger_reboot"

    reboot_fqn = reboot_cases[0].fqn
    dependents = [c for c in suite.cases if reboot_fqn in c.requires]
    assert len(dependents) >= 1, "no case requires trigger_reboot"


def test_boot_success_trigger_reboot_has_early_failure_collectors():
    """trigger_reboot 失败时也会主动采集早期 boot 诊断证据。"""
    from pathlib import Path
    from loop_core.case_loader import load_suite

    repo_root = Path(__file__).resolve()
    while repo_root.name != "engineering":
        repo_root = repo_root.parent
        if repo_root == repo_root.parent:
            raise RuntimeError("engineering/ root not found")
    cases_dir = repo_root / "loop" / "cases"
    boot_yaml = cases_dir / "system" / "boot-success.yaml"

    suite = load_suite(str(boot_yaml), [str(cases_dir)])
    trigger_reboot = next(case for case in suite.cases if case.id == "trigger_reboot")

    assert trigger_reboot.on_fail["collectors"] == [
        "common.shell.serial_recent",
        "common.shell.init_log",
        "common.shell.crash_dump",
        "common.shell.kmsg",
    ]

