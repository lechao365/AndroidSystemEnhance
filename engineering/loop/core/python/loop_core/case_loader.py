"""声明式用例加载器。

从 YAML 加载用例集，支持：
- include: 合并其他 suite 的 cases 和 collectors
- requires: 用例间依赖声明（拓扑排序 + 环检测）
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class TestCase:
    """加载后的用例定义。

    Attributes:
        id: 用例标识（suite 内唯一）
        suite: 所属 suite 名
        fqn: 全限定名 `<suite>.<id>`，作为内部唯一引用键（向后兼容默认空）
        command: 执行的命令（空字符串表示仅探测 prompt；与 action 互斥）
        action: 触发动作类型（如 'reboot'）；与 command 互斥，默认空字符串表示命令模式
        assert_spec: 断言规格 dict {type, value/pattern}；action case 为空 dict
        severity: critical（fail 阻断）/ warn（仅记录）
        requires: 前置依赖用例 FQN 列表
        on_fail: 失败时动作 {collectors: [FQN,...]}
        tags: 用例标签
        description: 用例描述
    """

    id: str
    suite: str
    command: str
    action: str = ""
    assert_spec: dict = field(default_factory=dict)
    severity: str = "critical"
    requires: list[str] = field(default_factory=list)
    on_fail: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    description: str = ""
    run_on: str = "device"
    fqn: str = ""


@dataclass
class SuiteDefaults:
    """suite 级默认配置（可选）。

    Attributes:
        capture_timeout: 输出捕获超时（秒），None 表示未设置
        recent_limit: 最近输出保留行数，None 表示未设置
    """

    capture_timeout: float | None = None
    recent_limit: int | None = None


@dataclass
class CaseSuite:
    """用例集。

    Attributes:
        name: suite 名称
        version: suite 版本
        cases: 用例列表（拓扑序）
        collectors: collector FQN -> {commands, hints}
        defaults: suite 级默认配置（可选，向后兼容默认空）
        warnings: 加载过程中的非致命提示信息列表
        final_collectors: 无论 pass/fail 都会执行的 collector FQN 列表
    """

    name: str
    version: int
    cases: list[TestCase]
    collectors: dict[str, dict]
    defaults: SuiteDefaults = field(default_factory=SuiteDefaults)
    warnings: list[str] = field(default_factory=list)
    final_collectors: list[str] = field(default_factory=list)


def load_suite(suite_path: str, case_dirs: list[str]) -> CaseSuite:
    """加载 YAML 用例集，解析 include/requires。

    FQN 规则：
    - case FQN = `<suite>.<id>`；dedup / requires / collectors 引用均以 FQN 为准。
    - 同 suite 内的 short name requires / collector 引用按所属 suite 命名空间解析为 FQN。
    - 跨 suite 引用优先用 FQN；也支持全局唯一的短名（即末段 id 在所有已加载
      case / collector 中唯一），多匹配时按 ambiguous 报错。

    Args:
        suite_path: 主 suite YAML 文件路径
        case_dirs: include 搜索目录列表

    Returns:
        CaseSuite 实例（cases 已拓扑排序）

    Raises:
        FileNotFoundError: suite 或 include 文件不存在
        ValueError: 静态校验失败（requires 引用缺失 / collector 未知 /
            severity 非法 / 重复 FQN / requires 存在环 / 断言规格非法）
    """
    raw = _load_yaml(suite_path)
    suite_name = raw["suite"]
    suite_version = raw.get("version", 1)

    all_cases: list[TestCase] = []
    # collectors 以 FQN 为键；include 的 collector 保留其来源 suite 的命名空间
    all_collectors: dict[str, dict] = {}
    # include 带来的 final_collectors（FQN，去重后合并到主 suite）
    extra_final_collectors: list[str] = []

    # 处理 include
    for inc_name in raw.get("include", []):
        inc_path = _find_suite(inc_name, case_dirs)
        inc_raw = _load_yaml(inc_path)
        inc_suite = inc_raw["suite"]
        for case_def in inc_raw.get("cases", []):
            _validate_case_definition(case_def)
            all_cases.append(_parse_case(case_def, inc_suite))
        for cname, cspec in inc_raw.get("collectors", {}).items():
            all_collectors[f"{inc_suite}.{cname}"] = cspec
        for fc in inc_raw.get("final_collectors", []):
            fqn = fc if "." in fc else f"{inc_suite}.{fc}"
            extra_final_collectors.append(fqn)

    # 处理主 suite 的 cases（先展开参数化用例）
    parameters = raw.get("parameters", {})
    raw_cases = _expand_parameterized_cases(raw.get("cases", []), parameters)
    for case_def in raw_cases:
        _validate_case_definition(case_def)
        all_cases.append(_parse_case(case_def, suite_name))

    # 合并主 suite 的 collectors（覆盖同名 include，主 suite 命名空间）
    for cname, cspec in raw.get("collectors", {}).items():
        all_collectors[f"{suite_name}.{cname}"] = cspec

    # 计算 FQN
    for case in all_cases:
        case.fqn = f"{case.suite}.{case.id}"

    # 重复 FQN 检测（include + 主 suite 合并后唯一）
    seen_fqns: set[str] = set()
    for case in all_cases:
        if case.fqn in seen_fqns:
            raise ValueError(f"duplicate case id: {case.fqn}")
        seen_fqns.add(case.fqn)

    # 断言规格校验
    for case in all_cases:
        _validate_assertion_shape(case.assert_spec)

    # collector 静态校验（在引用解析之前，确保 collector 自身合法）
    _validate_collectors(all_collectors)

    # 校验 include 引入的 final_collectors 引用有效性
    for fc in extra_final_collectors:
        if fc not in all_collectors:
            raise ValueError(f"unknown final_collector from include: {fc}")

    # 解析 requires / collector 引用为 FQN
    _resolve_case_links(all_cases, all_collectors)

    # 拓扑排序 + 环检测
    # 传入主 suite 名，让主 suite 的无依赖 case 排在 include 的 case 前面。
    # 例如 boot-success.yaml 的 trigger_reboot 排在 common/shell 的 shell_reachable 前面，
    # 确保 boot 场景先重启再检查，不遗漏启动早期日志。
    ordered = _topological_sort(all_cases, main_suite=suite_name)

    # 解析 suite 级 defaults
    defaults = _parse_defaults(raw.get("defaults", {}))

    # 解析 final_collectors（短名 -> FQN）
    final_collectors = _resolve_collector_refs(
        raw.get("final_collectors", []), suite_name, all_collectors
    )
    # 合并 include 带来的 final_collectors（去重，主 suite 优先）
    seen_fc: set[str] = set(final_collectors)
    for fc in extra_final_collectors:
        if fc not in seen_fc:
            seen_fc.add(fc)
            final_collectors.append(fc)

    return CaseSuite(
        name=suite_name,
        version=suite_version,
        cases=ordered,
        collectors=all_collectors,
        defaults=defaults,
        final_collectors=final_collectors,
    )


def _load_yaml(path: str) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def _find_suite(name: str, case_dirs: list[str]) -> str:
    for d in case_dirs:
        p = Path(d) / f"{name}.yaml"
        if p.exists():
            return str(p)
    raise FileNotFoundError(f"suite '{name}' not found in dirs: {case_dirs}")


def _parse_case(defn: dict, suite: str) -> TestCase:
    """从 YAML 节点构建 TestCase。fqn 在 load_suite 中统一填充。"""
    return TestCase(
        id=defn["id"],
        suite=suite,
        command=defn.get("command", ""),
        action=defn.get("action", ""),
        assert_spec=defn.get("assert", {}),
        severity=defn.get("severity", "critical"),
        requires=list(defn.get("requires", [])),
        on_fail=dict(defn.get("on_fail", {})),
        tags=list(defn.get("tags", [])),
        description=defn.get("description", ""),
        run_on=defn.get("run_on", "device"),
    )


def _expand_parameterized_cases(raw_cases: list[dict], parameters: dict) -> list[dict]:
    """展开参数化用例。

    遍历原始 case 列表，对声明了 `foreach` 的用例按 `parameters[foreach]`
    列表逐项克隆并替换 `${item}` 占位符；无 `foreach` 的用例原样透传。

    规则：
    1. `foreach` 引用 `parameters` 中已定义的键，否则报错。
    2. 对应的 parameter 值必须是 list，否则报错。
    3. 对每个 item 克隆 case，id 设为 `{原id}_{item}`，并在 command /
       description / assert.value / assert.pattern 中替换 `${item}`。
    4. 替换发生在原始 dict 上，后续 _parse_case 拿到的就是已展开数据，
       保持展开逻辑与解析逻辑解耦。

    Args:
        raw_cases: 从 YAML 解析出的原始 case dict 列表
        parameters: suite 级 parameters 字段

    Returns:
        展开后的 case dict 列表

    Raises:
        ValueError: foreach 引用未知 parameter 或 parameter 非 list
    """
    expanded: list[dict] = []
    for case_def in raw_cases:
        foreach = case_def.get("foreach")
        if not foreach:
            expanded.append(case_def)
            continue
        if foreach not in parameters:
            raise ValueError(
                f"unknown parameter '{foreach}' in case '{case_def.get('id')}'"
            )
        values = parameters[foreach]
        if not isinstance(values, list):
            raise ValueError(
                f"parameter '{foreach}' must be a list (case '{case_def.get('id')}')"
            )
        for item in values:
            cloned = copy.deepcopy(case_def)
            item_str = str(item)
            cloned["id"] = f"{case_def['id']}_{item_str}"
            # 在 command / description / assert.value / assert.pattern 中替换 ${item}
            cloned["command"] = cloned.get("command", "").replace("${item}", item_str)
            if cloned.get("description") is not None:
                cloned["description"] = cloned["description"].replace(
                    "${item}", item_str
                )
            assert_spec = cloned.get("assert", {})
            if isinstance(assert_spec, dict):
                if assert_spec.get("value") is not None:
                    assert_spec["value"] = str(assert_spec["value"]).replace(
                        "${item}", item_str
                    )
                if assert_spec.get("pattern") is not None:
                    assert_spec["pattern"] = str(assert_spec["pattern"]).replace(
                        "${item}", item_str
                    )
            expanded.append(cloned)
    return expanded


# 允许的 severity 取值
_VALID_SEVERITIES = {"critical", "warn"}
# 允许的 assertion type 取值
_VALID_ASSERT_TYPES = {
    "contains",
    "regex",
    "equals",
    "prompt_visible",
    "not_contains",
    "exit_code_zero",
    "json_field",
}
# 允许的 action 取值（与 command 互斥）
_VALID_ACTIONS = {"reboot"}
# 允许的 run_on 取值
_VALID_RUN_ON = {"device", "host"}


def _validate_case_definition(defn: dict) -> None:
    """静态校验单条用例定义：必需键、severity 合法性、action/command 互斥。"""
    required_keys = {"id", "assert"}
    missing = required_keys - set(defn)
    if missing:
        raise ValueError(f"case missing required keys: {sorted(missing)}")
    severity = defn.get("severity", "critical")
    if severity not in _VALID_SEVERITIES:
        raise ValueError(f"invalid severity: {severity}")
    action = defn.get("action", "")
    command = defn.get("command", "")
    if action and command:
        raise ValueError(
            f"action and command are mutually exclusive in case '{defn.get('id')}'"
        )
    if action and action not in _VALID_ACTIONS:
        raise ValueError(
            f"unknown action: {action} (allowed: {sorted(_VALID_ACTIONS)})"
        )
    run_on = defn.get("run_on", "device")
    if run_on not in _VALID_RUN_ON:
        raise ValueError(f"invalid run_on: {run_on}")
    if action == "reboot" and run_on != "device":
        raise ValueError(
            f"reboot action requires run_on=device (case '{defn.get('id')}')"
        )
    assert_type = defn.get("assert", {}).get("type")
    if assert_type == "prompt_visible" and run_on != "device":
        raise ValueError(
            f"prompt_visible requires run_on=device (case '{defn.get('id')}')"
        )
    if run_on == "host" and not command:
        raise ValueError(
            f"host case requires non-empty command (case '{defn.get('id')}')"
        )


def _validate_collectors(collectors: dict[str, dict]) -> None:
    """静态校验 collector 定义：run_on 取值、mode 与 run_on 兼容性、host 命令非空、adb_pull 必填项。"""
    for cname, cspec in collectors.items():
        run_on = cspec.get("run_on", "device")
        if run_on not in _VALID_RUN_ON:
            raise ValueError(f"invalid run_on: {run_on}")
        mode = cspec.get("mode", "")
        if mode == "serial_context" and run_on != "device":
            raise ValueError(
                f"serial_context collector requires run_on=device (collector '{cname}')"
            )
        if mode == "adb_pull" and not cspec.get("remote_paths"):
            raise ValueError("adb_pull collector requires remote_paths")
        commands = cspec.get("commands", [])
        if run_on == "host" and not commands:
            raise ValueError(
                f"host collector requires at least one command (collector '{cname}')"
            )


def _resolve_collector_refs(
    refs: list[str], suite_name: str, all_collectors: dict[str, dict]
) -> list[str]:
    """将 collector 引用解析为 FQN。

    解析顺序：
    1. 本地命名空间 `<suite>.<ref>` 命中即用。
    2. ref 本身已是已加载 FQN。
    3. 未命中报 ValueError。

    Args:
        refs: 原始引用列表（短名或 FQN）
        suite_name: 主 suite 名（用于短名命名空间解析）
        all_collectors: 以 FQN 为键的 collector 字典

    Returns:
        解析后的 FQN 列表（顺序保持）
    """
    resolved: list[str] = []
    for ref in refs:
        fqn_attempt = ref if "." in ref else f"{suite_name}.{ref}"
        if fqn_attempt in all_collectors:
            resolved.append(fqn_attempt)
        elif ref in all_collectors:
            resolved.append(ref)
        else:
            raise ValueError(f"unknown collector reference: {ref}")
    return resolved


def _resolve_case_links(
    cases: list[TestCase], collectors: dict[str, dict]
) -> None:
    """将 requires / on_fail.collectors 引用解析为 FQN 并就地写回。

    requires 解析顺序：
    1. 本地命名空间：`<case.suite>.<dep_id>` 命中即用。
    2. 精确 FQN：`dep_id` 本身即为已加载 FQN（显式跨 suite 引用）。
    3. 全局短名唯一匹配：`dep_id` 作为末段在所有 FQN 中唯一匹配
       （支持 include 后的跨 suite 短名引用，如 requires:[shell_reachable]）。
       多个匹配则报 ambiguous；无匹配报 missing。
    on_fail.collectors 同理。

    Args:
        cases: 全部用例（fqn 字段已填充）
        collectors: 以 FQN 为键的 collector 字典
    """
    fqn_set = {c.fqn for c in cases}
    # 末段短名 -> FQN 列表（用于全局唯一匹配回退）
    by_suffix: dict[str, list[str]] = {}
    for fqn in fqn_set:
        by_suffix.setdefault(fqn.rsplit(".", 1)[-1], []).append(fqn)
    collector_by_suffix: dict[str, list[str]] = {}
    for fqn in collectors:
        collector_by_suffix.setdefault(fqn.rsplit(".", 1)[-1], []).append(fqn)

    def _resolve(ref: str, namespace: str, local_map: set, suffix_map: dict,
                 kind: str, owner_fqn: str) -> str:
        local_fqn = f"{namespace}.{ref}"
        if local_fqn in local_map:
            return local_fqn
        if ref in local_map:
            return ref
        candidates = suffix_map.get(ref, [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise ValueError(
                f"ambiguous {kind} '{ref}' referenced by '{owner_fqn}': {candidates}"
            )
        if kind == "required case":
            raise ValueError(
                f"missing required case '{ref}' referenced by '{owner_fqn}'"
            )
        raise ValueError(
            f"unknown collector '{ref}' referenced by '{owner_fqn}'"
        )

    for case in cases:
        case.requires = [
            _resolve(dep, case.suite, fqn_set, by_suffix, "required case", case.fqn)
            for dep in case.requires
        ]
        if "collectors" in case.on_fail:
            case.on_fail["collectors"] = [
                _resolve(cname, case.suite, set(collectors), collector_by_suffix,
                         "collector", case.fqn)
                for cname in case.on_fail["collectors"]
            ]


def _validate_assertion_shape(assert_spec: dict) -> None:
    """校验断言规格结构：type 合法、必填参数齐备。

    action case（如 action: reboot）的 assert 为空 dict，跳过校验。
    """
    if not assert_spec:
        return  # action case 无断言
    atype = assert_spec.get("type")
    if atype in {"contains", "equals", "not_contains"} and "value" not in assert_spec:
        raise ValueError(f"assert type '{atype}' requires value")
    if atype == "regex" and "pattern" not in assert_spec:
        raise ValueError("assert type 'regex' requires pattern")
    if atype == "json_field":
        if "path" not in assert_spec:
            raise ValueError("assert type 'json_field' requires path")
        if "op" not in assert_spec:
            raise ValueError("assert type 'json_field' requires op")
        _VALID_JSON_FIELD_OPS = {"eq", "ne", "gt", "ge", "lt", "le", "exists", "not_exists"}
        if assert_spec["op"] not in _VALID_JSON_FIELD_OPS:
            raise ValueError(f"unknown json_field op: {assert_spec['op']}")
    if atype not in _VALID_ASSERT_TYPES:
        raise ValueError(f"unknown assertion type: {atype}")


def _parse_defaults(raw: dict) -> SuiteDefaults:
    """解析 suite 级 defaults 字段为 SuiteDefaults，缺省字段保持 None。"""
    if not raw:
        return SuiteDefaults()
    capture_timeout = raw.get("capture_timeout")
    recent_limit = raw.get("recent_limit")
    return SuiteDefaults(
        capture_timeout=float(capture_timeout) if capture_timeout is not None else None,
        recent_limit=int(recent_limit) if recent_limit is not None else None,
    )


def _topological_sort(cases: list[TestCase], main_suite: str = "") -> list[TestCase]:
    """拓扑排序：被依赖的用例排在前面。检测环。

    main_suite 不改变拓扑语义（requires 仍然优先），但当多个 case 之间无依赖
    关系时，主 suite 的 case 排在 include 的 case 前面。这样主 suite 可以
    把关键前置操作（如 trigger_reboot）放在 include 的检查用例之前。
    """
    case_map = {c.fqn: c for c in cases}
    visited: dict[str, int] = {}  # 0=visiting, 1=done
    result: list[TestCase] = []

    def visit(fqn: str, path: list[str]):
        if fqn not in case_map:
            return  # defensive: requires 已在 _resolve_case_links 校验
        state = visited.get(fqn)
        if state == 1:
            return
        if state == 0:
            cycle_path = " -> ".join(path + [fqn])
            raise ValueError(f"cycle detected in requires: {cycle_path}")
        visited[fqn] = 0
        for dep in case_map[fqn].requires:
            visit(dep, path + [fqn])
        visited[fqn] = 1
        result.append(case_map[fqn])

    # 主 suite 的 case 先于 include 的 case 做拓扑遍历，
    # 确保无依赖的主 suite case（如 trigger_reboot）排在 include case（如 shell_reachable）前面
    main_cases = [c for c in cases if c.suite == main_suite]
    other_cases = [c for c in cases if c.suite != main_suite]

    for case in main_cases + other_cases:
        visit(case.fqn, [])

    return result
