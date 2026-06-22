"""断言引擎：对用例输出求值确定性断言。

支持 6 种断言类型：
- contains: 输出包含指定文本
- regex: 输出匹配正则
- equals: 输出完全等于
- prompt_visible: prompt 标记可见
- not_contains: 输出不包含指定文本
- exit_code_zero: 命令退出码为 0
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class AssertionContext:
    """断言求值上下文。

    Attributes:
        output: 命令输出文本
        prompt_visible: prompt 是否可见
        exit_code: 命令退出码（不可获取时为 None）
    """

    output: str
    prompt_visible: bool = False
    exit_code: int | None = None


@dataclass
class AssertionResult:
    """断言求值结果。

    Attributes:
        passed: 是否通过
        reason: 失败原因（passed=True 时为空）
    """

    passed: bool
    reason: str = ""


class AssertionEngine:
    """断言求值引擎。"""

    def evaluate(self, assertion: dict, context: AssertionContext) -> AssertionResult:
        """求值单条断言。

        Args:
            assertion: YAML 中的 assert 字段 {type, ...}
            context: 求值上下文

        Returns:
            AssertionResult

        Raises:
            ValueError: 未知断言类型
        """
        atype = assertion.get("type")
        if atype == "contains":
            return self._contains(assertion, context)
        if atype == "regex":
            return self._regex(assertion, context)
        if atype == "equals":
            return self._equals(assertion, context)
        if atype == "prompt_visible":
            return self._prompt_visible(context)
        if atype == "not_contains":
            return self._not_contains(assertion, context)
        if atype == "exit_code_zero":
            return self._exit_code_zero(context)
        if atype == "json_field":
            return self._json_field(assertion, context)
        if atype == "exit_code_equals":
            return self._exit_code_equals(assertion, context)
        if atype == "contains_any":
            return self._contains_any(assertion, context)
        raise ValueError(f"unknown assertion type: {atype}")

    def _contains(self, assertion: dict, ctx: AssertionContext) -> AssertionResult:
        value = assertion["value"]
        if value in ctx.output:
            return AssertionResult(passed=True)
        return AssertionResult(
            passed=False,
            reason=f"expected output to contain '{value}', got: {ctx.output[:200]}",
        )

    def _regex(self, assertion: dict, ctx: AssertionContext) -> AssertionResult:
        pattern = assertion["pattern"]
        if re.search(pattern, ctx.output):
            return AssertionResult(passed=True)
        return AssertionResult(
            passed=False,
            reason=f"expected output to match /{pattern}/, got: {ctx.output[:200]}",
        )

    def _equals(self, assertion: dict, ctx: AssertionContext) -> AssertionResult:
        value = assertion["value"]
        if ctx.output.strip() == value:
            return AssertionResult(passed=True)
        return AssertionResult(
            passed=False,
            reason=f"expected output to equal '{value}', got: '{ctx.output.strip()[:200]}'",
        )

    def _prompt_visible(self, ctx: AssertionContext) -> AssertionResult:
        if ctx.prompt_visible:
            return AssertionResult(passed=True)
        return AssertionResult(passed=False, reason="prompt not visible")

    def _not_contains(self, assertion: dict, ctx: AssertionContext) -> AssertionResult:
        value = assertion["value"]
        if value not in ctx.output:
            return AssertionResult(passed=True)
        return AssertionResult(
            passed=False,
            reason=f"expected output to NOT contain '{value}', but it does",
        )

    def _exit_code_zero(self, ctx: AssertionContext) -> AssertionResult:
        if ctx.exit_code == 0:
            return AssertionResult(passed=True)
        if ctx.exit_code is None:
            return AssertionResult(
                passed=False, reason="exit code not available"
            )
        return AssertionResult(
            passed=False, reason=f"expected exit code 0, got {ctx.exit_code}"
        )

    def _json_field(self, assertion: dict, ctx: AssertionContext) -> AssertionResult:
        import json as _json
        path = assertion["path"]
        op = assertion["op"]
        expected = assertion.get("value")

        try:
            data = _json.loads(ctx.output)
        except _json.JSONDecodeError as e:
            return AssertionResult(passed=False, reason=f"output is not valid JSON: {e}")

        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit():
                idx = int(part)
                if idx < len(current):
                    current = current[idx]
                else:
                    return AssertionResult(passed=False, reason=f"path '{path}' array index {idx} out of range (len={len(current)})")
            else:
                if op == "not_exists":
                    return AssertionResult(passed=True)
                return AssertionResult(passed=False, reason=f"path '{path}' not found, missing key '{part}'")

        if op == "exists":
            return AssertionResult(passed=True)
        if op == "not_exists":
            return AssertionResult(passed=False, reason=f"path '{path}' exists but expected not_exists, value={current!r}")

        try:
            actual_num = float(current)
            exp_num = float(expected)
            ops_map = {
                "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
                "gt": lambda a, b: a > b, "ge": lambda a, b: a >= b,
                "lt": lambda a, b: a < b, "le": lambda a, b: a <= b,
            }
            if op not in ops_map:
                return AssertionResult(passed=False, reason=f"unknown op: {op}")
            ok = ops_map[op](actual_num, exp_num)
            if ok:
                return AssertionResult(passed=True)
            return AssertionResult(passed=False, reason=f"json_field '{path}' {op} {expected} failed, actual={current}")
        except (ValueError, TypeError):
            actual_str = str(current).lower()
            exp_str = str(expected).lower()
            if op == "eq":
                ok = actual_str == exp_str
            elif op == "ne":
                ok = actual_str != exp_str
            else:
                return AssertionResult(passed=False, reason=f"cannot compare non-numeric '{current!r}' with op '{op}'")
            if ok:
                return AssertionResult(passed=True)
            return AssertionResult(passed=False, reason=f"json_field '{path}' {op} {expected} failed, actual={current}")

    def _exit_code_equals(self, assertion: dict, ctx: AssertionContext) -> AssertionResult:
        expected = assertion["value"]
        if ctx.exit_code is None:
            return AssertionResult(passed=False, reason="exit code not available")
        if ctx.exit_code == expected:
            return AssertionResult(passed=True)
        return AssertionResult(passed=False, reason=f"expected exit code {expected}, got {ctx.exit_code}")

    def _contains_any(self, assertion: dict, ctx: AssertionContext) -> AssertionResult:
        values = assertion.get("values", [])
        if not values:
            return AssertionResult(passed=False, reason="contains_any requires non-empty values list")
        for v in values:
            if v in ctx.output:
                return AssertionResult(passed=True)
        return AssertionResult(passed=False, reason=f"output contains none of {values}")
