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
class CaseSuite:
    """用例集。

    Attributes:
        name: suite 名称
        version: suite 版本
        cases: 用例列表（拓扑序）
        collectors: collector 名称 -> {commands, hints}
    """

    name: str
    version: int
    cases: list[TestCase]
    collectors: dict[str, dict]


def load_suite(suite_path: str, case_dirs: list[str]) -> CaseSuite:
    """加载 YAML 用例集，解析 include/requires。

    Args:
        suite_path: 主 suite YAML 文件路径
        case_dirs: include 搜索目录列表

    Returns:
        CaseSuite 实例（cases 已拓扑排序）

    Raises:
        FileNotFoundError: suite 或 include 文件不存在
        ValueError: requires 存在环
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
            all_cases.append(_parse_case(case_def, inc_raw["suite"]))
        all_collectors.update(inc_raw.get("collectors", {}))

    # 处理主 suite 的 cases
    for case_def in raw.get("cases", []):
        all_cases.append(_parse_case(case_def, suite_name))

    # 合并主 suite 的 collectors（覆盖同名 include）
    all_collectors.update(raw.get("collectors", {}))

    # 去重（include 和主 suite 可能有同 id 用例，主 suite 优先）
    seen: dict[str, TestCase] = {}
    for case in all_cases:
        seen[case.id] = case
    unique_cases = list(seen.values())

    # 拓扑排序 + 环检测
    ordered = _topological_sort(unique_cases)

    return CaseSuite(
        name=suite_name,
        version=suite_version,
        cases=ordered,
        collectors=all_collectors,
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


def _topological_sort(cases: list[TestCase]) -> list[TestCase]:
    """拓扑排序：被依赖的用例排在前面。检测环。"""
    case_map = {c.id: c for c in cases}
    visited: dict[str, int] = {}  # 0=visiting, 1=done
    result: list[TestCase] = []

    def visit(case_id: str, path: list[str]):
        if case_id not in case_map:
            return  # 引用不存在的用例，加载阶段忽略（执行阶段处理）
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
