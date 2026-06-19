"""声明式用例加载器。

从 YAML 加载用例集，支持：
- include: 合并其他 suite 的 cases 和 collectors
- requires: 用例间依赖声明（拓扑排序 + 环检测）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class TestCase:
    """加载后的用例定义。

    Attributes:
        id: 用例标识（suite 内唯一）
        suite: 所属 suite 名
        command: 执行的命令（空字符串表示仅探测 prompt）
        assert_spec: 断言规格 dict {type, value/pattern}
        severity: critical（fail 阻断）/ warn（仅记录）
        requires: 前置依赖用例 id 列表
        on_fail: 失败时动作 {collectors: [...]}
        tags: 用例标签
        description: 用例描述
    """

    id: str
    suite: str
    command: str
    assert_spec: dict
    severity: str = "critical"
    requires: list[str] = field(default_factory=list)
    on_fail: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    description: str = ""


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
        collectors: collector 名称 -> {commands, hints}
        defaults: suite 级默认配置（可选，向后兼容默认空）
        warnings: 加载过程中的非致命提示信息列表
    """

    name: str
    version: int
    cases: list[TestCase]
    collectors: dict[str, dict]
    defaults: SuiteDefaults = field(default_factory=SuiteDefaults)
    warnings: list[str] = field(default_factory=list)


def load_suite(suite_path: str, case_dirs: list[str]) -> CaseSuite:
    """加载 YAML 用例集，解析 include/requires。

    Args:
        suite_path: 主 suite YAML 文件路径
        case_dirs: include 搜索目录列表

    Returns:
        CaseSuite 实例（cases 已拓扑排序）

    Raises:
        FileNotFoundError: suite 或 include 文件不存在
        ValueError: 静态校验失败（requires 引用缺失 / collector 未知 /
            severity 非法 / 重复 case id / requires 存在环 / 断言规格非法）
    """
    raw = _load_yaml(suite_path)
    suite_name = raw["suite"]
    suite_version = raw.get("version", 1)

    all_cases: list[TestCase] = []
    all_collectors: dict[str, dict] = {}

    # 处理 include
    for inc_name in raw.get("include", []):
        inc_path = _find_suite(inc_name, case_dirs)
        inc_raw = _load_yaml(inc_path)
        for case_def in inc_raw.get("cases", []):
            _validate_case_definition(case_def)
            all_cases.append(_parse_case(case_def, inc_raw["suite"]))
        all_collectors.update(inc_raw.get("collectors", {}))

    # 处理主 suite 的 cases
    for case_def in raw.get("cases", []):
        _validate_case_definition(case_def)
        all_cases.append(_parse_case(case_def, suite_name))

    # 合并主 suite 的 collectors（覆盖同名 include）
    all_collectors.update(raw.get("collectors", {}))

    # 重复 case id 检测（include + 主 suite 合并后唯一）
    seen_ids: set[str] = set()
    for case in all_cases:
        if case.id in seen_ids:
            raise ValueError(f"duplicate case id: {case.id}")
        seen_ids.add(case.id)

    # 断言规格校验
    for case in all_cases:
        _validate_assertion_shape(case.assert_spec)

    # requires / collector 引用校验
    _validate_case_links(all_cases, all_collectors)

    # 拓扑排序 + 环检测
    ordered = _topological_sort(all_cases)

    # 解析 suite 级 defaults
    defaults = _parse_defaults(raw.get("defaults", {}))

    return CaseSuite(
        name=suite_name,
        version=suite_version,
        cases=ordered,
        collectors=all_collectors,
        defaults=defaults,
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
    return TestCase(
        id=defn["id"],
        suite=suite,
        command=defn.get("command", ""),
        assert_spec=defn.get("assert", {}),
        severity=defn.get("severity", "critical"),
        requires=defn.get("requires", []),
        on_fail=defn.get("on_fail", {}),
        tags=defn.get("tags", []),
        description=defn.get("description", ""),
    )


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
}


def _validate_case_definition(defn: dict) -> None:
    """静态校验单条用例定义：必需键、severity 合法性。"""
    required_keys = {"id", "assert"}
    missing = required_keys - set(defn)
    if missing:
        raise ValueError(f"case missing required keys: {sorted(missing)}")
    severity = defn.get("severity", "critical")
    if severity not in _VALID_SEVERITIES:
        raise ValueError(f"invalid severity: {severity}")


def _validate_case_links(cases: list[TestCase], collectors: dict[str, dict]) -> None:
    """校验 requires 与 on_fail.collectors 引用是否都指向已定义目标。"""
    case_ids = {c.id for c in cases}
    for case in cases:
        for dep_id in case.requires:
            if dep_id not in case_ids:
                raise ValueError(
                    f"missing required case '{dep_id}' referenced by '{case.id}'"
                )
        for collector_name in case.on_fail.get("collectors", []):
            if collector_name not in collectors:
                raise ValueError(
                    f"unknown collector '{collector_name}' referenced by '{case.id}'"
                )


def _validate_assertion_shape(assert_spec: dict) -> None:
    """校验断言规格结构：type 合法、必填参数齐备。"""
    atype = assert_spec.get("type")
    if atype in {"contains", "equals", "not_contains"} and "value" not in assert_spec:
        raise ValueError(f"assert type '{atype}' requires value")
    if atype == "regex" and "pattern" not in assert_spec:
        raise ValueError("assert type 'regex' requires pattern")
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


def _topological_sort(cases: list[TestCase]) -> list[TestCase]:
    """拓扑排序：被依赖的用例排在前面。检测环。"""
    case_map = {c.id: c for c in cases}
    visited: dict[str, int] = {}  # 0=visiting, 1=done
    result: list[TestCase] = []

    def visit(case_id: str, path: list[str]):
        if case_id not in case_map:
            return  # defensive: requires 已在 _validate_case_links 校验，正常路径不可达
        state = visited.get(case_id)
        if state == 1:
            return
        if state == 0:
            cycle_path = " -> ".join(path + [case_id])
            raise ValueError(f"cycle detected in requires: {cycle_path}")
        visited[case_id] = 0
        for dep in case_map[case_id].requires:
            visit(dep, path + [case_id])
        visited[case_id] = 1
        result.append(case_map[case_id])

    for case in cases:
        visit(case.id, [])

    return result
