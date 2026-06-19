"""loop_core/rules.py 单元测试。"""
from loop_core.models import RuleMatch
from loop_core.rules import classify, evaluate_rules


class StubRule:
    """测试用规则桩。"""

    def __init__(self, name: str, matched: bool, confidence: float = 0.9):
        self.name = name
        self._matched = matched
        self._confidence = confidence

    def match(self, snapshot) -> RuleMatch:
        return RuleMatch(
            rule_id=self.name,
            matched=self._matched,
            confidence=self._confidence if self._matched else 0.0,
            severity="high" if self._matched else "low",
            evidence=[f"{self.name} evidence"] if self._matched else [],
            phase="CLASSIFY_FAILURE",
            suggested_actions=["action_a"] if self._matched else [],
        )


class TestEvaluateRules:
    def test_returns_all_results(self):
        rules = [
            StubRule("rule_a", True),
            StubRule("rule_b", False),
        ]
        results = evaluate_rules(snapshot=None, rules=rules, phase="CLASSIFY_FAILURE")
        assert len(results) == 2
        assert results[0].rule_id == "rule_a"
        assert results[0].matched is True
        assert results[1].rule_id == "rule_b"
        assert results[1].matched is False

    def test_phase_set_on_unmatched(self):
        """未命中的规则 phase 应被传入 phase 覆盖。"""
        rules = [StubRule("rule_a", False)]
        results = evaluate_rules(snapshot=None, rules=rules, phase="OBSERVE_BOOT")
        assert results[0].phase == "OBSERVE_BOOT"

    def test_empty_rules(self):
        results = evaluate_rules(snapshot=None, rules=[], phase="TEST")
        assert results == []


class TestClassify:
    def test_returns_first_matched_by_priority(self):
        matches = [
            RuleMatch("rule_a", False, 0.0, "low", [], "P", []),
            RuleMatch("rule_b", True, 0.9, "high", [], "P", []),
            RuleMatch("rule_c", True, 0.8, "medium", [], "P", []),
        ]
        priority = ["rule_c", "rule_b", "rule_a"]
        assert classify(matches, priority) == "rule_c"

    def test_returns_unknown_when_none_matched(self):
        matches = [
            RuleMatch("rule_a", False, 0.0, "low", [], "P", []),
        ]
        assert classify(matches, ["rule_a"]) == "unknown"

    def test_empty_matches(self):
        assert classify([], ["rule_a"]) == "unknown"

    def test_priority_order_matters(self):
        matches = [
            RuleMatch("low_prio", True, 0.5, "low", [], "P", []),
            RuleMatch("high_prio", True, 0.95, "high", [], "P", []),
        ]
        assert classify(matches, ["high_prio", "low_prio"]) == "high_prio"
        assert classify(matches, ["low_prio", "high_prio"]) == "low_prio"
