"""loop_core 规则引擎框架（B1 弱约束）。

提供：
- Rule Protocol：规则只需实现 name + match(snapshot)
- evaluate_rules：遍历规则集，返回 RuleMatch 列表
- classify：按优先级裁决最终分类

规则在构造时自行持有所需配置（markers / thresholds），
core 不假设 cfg 类型。
"""
from __future__ import annotations

from typing import Protocol

from loop_core.models import RuleMatch


class Rule(Protocol):
    """规则协议。

    实现者需提供 name 属性和 match 方法。
    规则在构造时自行持有所需配置。
    """

    name: str

    def match(self, snapshot) -> RuleMatch:
        """对观察快照求值，返回 RuleMatch。"""
        ...


def evaluate_rules(
    snapshot,
    rules: list[Rule],
    phase: str,
) -> list[RuleMatch]:
    """运行全部规则。

    Args:
        snapshot: ObservationSnapshot
        rules: 规则实例列表
        phase: 当前状态机阶段名

    Returns:
        RuleMatch 列表（不论是否命中）
    """
    results: list[RuleMatch] = []
    for rule in rules:
        match_result = rule.match(snapshot)
        # 确保 phase 正确
        if match_result.phase != phase and not match_result.matched:
            # 未命中的规则，用传入 phase 覆盖
            from loop_core.models import RuleMatch as _RM

            match_result = _RM(
                rule_id=match_result.rule_id,
                matched=match_result.matched,
                confidence=match_result.confidence,
                severity=match_result.severity,
                evidence=match_result.evidence,
                phase=phase,
                suggested_actions=match_result.suggested_actions,
            )
        results.append(match_result)
    return results


def classify(matches: list[RuleMatch], priority: list[str]) -> str:
    """根据规则匹配结果返回最终分类。

    按 priority 顺序取第一个命中的规则 ID。
    无命中返回 "unknown"。

    Args:
        matches: evaluate_rules 的返回值
        priority: 分类优先级列表（高 -> 低）

    Returns:
        分类字符串（规则 ID 或 "unknown"）
    """
    matched_ids = {m.rule_id for m in matches if getattr(m, "matched", False)}
    for rule_id in priority:
        if rule_id in matched_ids:
            return rule_id
    return "unknown"
