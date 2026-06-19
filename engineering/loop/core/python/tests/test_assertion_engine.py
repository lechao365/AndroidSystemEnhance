"""assertion_engine 断言引擎测试。覆盖全部 6 种断言类型。"""
import pytest

from loop_core.assertion_engine import AssertionContext, AssertionEngine, AssertionResult


@pytest.fixture
def engine():
    return AssertionEngine()


class TestContains:
    def test_pass(self, engine):
        ctx = AssertionContext(output="running\n")
        result = engine.evaluate({"type": "contains", "value": "running"}, ctx)
        assert result.passed is True

    def test_fail(self, engine):
        ctx = AssertionContext(output="stopped\n")
        result = engine.evaluate({"type": "contains", "value": "running"}, ctx)
        assert result.passed is False
        assert "stopped" in result.reason or "running" in result.reason

    def test_multiline_output(self, engine):
        ctx = AssertionContext(output="line1\nrunning\nline3")
        result = engine.evaluate({"type": "contains", "value": "running"}, ctx)
        assert result.passed is True


class TestRegex:
    def test_pass(self, engine):
        ctx = AssertionContext(output="inet 192.168.1.100/24")
        result = engine.evaluate(
            {"type": "regex", "pattern": r"inet \d+\.\d+\.\d+\.\d+"}, ctx
        )
        assert result.passed is True

    def test_fail(self, engine):
        ctx = AssertionContext(output="inet6 fe80::1")
        result = engine.evaluate(
            {"type": "regex", "pattern": r"inet \d+\.\d+\.\d+\.\d+"}, ctx
        )
        assert result.passed is False


class TestEquals:
    def test_pass(self, engine):
        ctx = AssertionContext(output="1")
        result = engine.evaluate({"type": "equals", "value": "1"}, ctx)
        assert result.passed is True

    def test_fail(self, engine):
        ctx = AssertionContext(output="0")
        result = engine.evaluate({"type": "equals", "value": "1"}, ctx)
        assert result.passed is False


class TestPromptVisible:
    def test_pass(self, engine):
        ctx = AssertionContext(output="", prompt_visible=True)
        result = engine.evaluate({"type": "prompt_visible"}, ctx)
        assert result.passed is True

    def test_fail(self, engine):
        ctx = AssertionContext(output="", prompt_visible=False)
        result = engine.evaluate({"type": "prompt_visible"}, ctx)
        assert result.passed is False


class TestNotContains:
    def test_pass(self, engine):
        ctx = AssertionContext(output="all good")
        result = engine.evaluate({"type": "not_contains", "value": "error"}, ctx)
        assert result.passed is True

    def test_fail(self, engine):
        ctx = AssertionContext(output="error: something")
        result = engine.evaluate({"type": "not_contains", "value": "error"}, ctx)
        assert result.passed is False


class TestExitCodeZero:
    def test_pass(self, engine):
        ctx = AssertionContext(output="", exit_code=0)
        result = engine.evaluate({"type": "exit_code_zero"}, ctx)
        assert result.passed is True

    def test_fail(self, engine):
        ctx = AssertionContext(output="", exit_code=1)
        result = engine.evaluate({"type": "exit_code_zero"}, ctx)
        assert result.passed is False

    def test_no_exit_code(self, engine):
        ctx = AssertionContext(output="", exit_code=None)
        result = engine.evaluate({"type": "exit_code_zero"}, ctx)
        assert result.passed is False


class TestUnknownType:
    def test_raises_value_error(self, engine):
        ctx = AssertionContext(output="")
        with pytest.raises(ValueError, match="unknown.*assertion.*type"):
            engine.evaluate({"type": "nonexistent"}, ctx)
