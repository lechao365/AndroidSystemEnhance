# Loop Engineering v2 重构实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 loop engineering 从"规则盲匹配引擎"重构为"用例驱动验收器"，完全移除 workflows/ 层，新场景零 Python 代码（仅写 YAML 用例）。

**Architecture:** 删除 rules.py/actions.py/LoopAttempt 体系，新增 assertion_engine/case_loader/evidence/executor/collector/runner/cli 七模块。通用 LoopRunner 上提到 core，场景逻辑 100% 在 cases/*.yaml 声明式用例中。opencode 作为 AI driver 消费 EvidenceBundle JSON。

**Tech Stack:** Python 3.12+, pyyaml 6.0.1（已安装）, pytest, dataclasses

**Spec:** `docs/specs/2026-06-19-loop-engineering-v2-design.md`

---

## 总体策略

分 6 个 Phase 递进，每个 Phase 结束有可验证的里程碑。全程 TDD：先写失败测试 → 最小实现 → 验证通过 → 提交。

| Phase | 内容 | 里程碑 |
|-------|------|--------|
| A | core v2 数据模型 + 断言引擎 | assertion_engine 单测全绿 |
| B | core 用例加载器 + 执行器 | case_loader + executor 单测全绿 |
| C | core 证据组装 + 报告 + 通用 Runner | EvidenceBundle JSON 可生成 |
| D | core CLI + 入口脚本 + 用例/模板 | fixture 模式端到端跑通 |
| E | 删除 v1 遗留（rules/actions/workflows） | 联合回归全绿，零 v1 引用 |
| F | README/WORKFLOW 重新生成 + profile 简化 | 文档完整，live 验证 |

---

## Phase A：core v2 数据模型 + 断言引擎

### Task A1: models.py 重写（删 v1 模型，加 v2 模型）

**Files:**
- Modify: `engineering/loop/core/python/loop_core/models.py`
- Modify: `engineering/loop/core/python/tests/test_models.py`（重写）

- [ ] **Step 1: 重写 test_models.py（先写失败测试）**

```python
# engineering/loop/core/python/tests/test_models.py
"""loop_core v2 数据模型测试。"""
from loop_core.models import (
    ObservedLine,
    TestCaseResult,
    CollectorResult,
    EvidenceBundle,
)


def test_observed_line_to_dict():
    line = ObservedLine(t=1.5, text="hello", cycle_id=2)
    assert line.to_dict() == {"t": 1.5, "text": "hello", "cycle_id": 2}


def test_test_case_result_defaults():
    r = TestCaseResult(id="zygote_running", suite="boot-success", status="pass")
    assert r.command == ""
    assert r.output == ""
    assert r.output_preview == ""
    assert r.assertion == {}
    assert r.duration_sec == 0.0
    assert r.failure_reason == ""
    assert r.skip_reason == ""
    assert r.triggered_collectors == []
    assert r.tags == []


def test_test_case_result_to_dict():
    r = TestCaseResult(
        id="zygote_running",
        suite="boot-success",
        status="fail",
        command="getprop init.svc.zygote",
        output="stopped\n",
        assertion={"type": "contains", "value": "running"},
        duration_sec=1.2,
        failure_reason="expected 'running', got 'stopped'",
        triggered_collectors=["crash_dump"],
    )
    d = r.to_dict()
    assert d["id"] == "zygote_running"
    assert d["status"] == "fail"
    assert d["triggered_collectors"] == ["crash_dump"]


def test_collector_result_to_dict():
    cr = CollectorResult(
        name="crash_dump",
        commands=["logcat -b crash -d", "ls -la /data/tombstones/"],
        outputs=[
            {"command": "logcat -b crash -d", "lines": ["crash line 1"]},
        ],
        hints="关注 abort message",
    )
    d = cr.to_dict()
    assert d["name"] == "crash_dump"
    assert len(d["commands"]) == 2
    assert d["hints"] == "关注 abort message"


def test_evidence_bundle_to_dict():
    bundle = EvidenceBundle(
        bundle_id="eb-test-001",
        device_id="rp5",
        suite="boot-success",
        timestamp="2026-06-19T22:36:06+08:00",
        summary={"total": 2, "passed": 1, "failed": 1, "skipped": 0, "overall": "FAIL"},
        cases=[
            TestCaseResult(id="shell_reachable", suite="boot-success", status="pass"),
            TestCaseResult(id="zygote_running", suite="boot-success", status="fail"),
        ],
        evidence={},
    )
    d = bundle.to_dict()
    assert d["bundle_id"] == "eb-test-001"
    assert d["summary"]["overall"] == "FAIL"
    assert len(d["cases"]) == 2
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_models.py -v --import-mode=importlib 2>&1 | head -20
```
Expected: FAIL（ImportError: cannot import name TestCaseResult）

- [ ] **Step 3: 重写 models.py（最小实现使测试通过）**

```python
# engineering/loop/core/python/loop_core/models.py
"""loop_core v2 数据模型。

v1 的 RuleMatch/ActionRecord/LoopAttempt 已删除。
v2 使用 TestCaseResult/CollectorResult/EvidenceBundle 体系。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class ObservedLine:
    """观察到的单行输出。

    Attributes:
        t: 相对时间戳
        text: 文本内容
        cycle_id: 所属 cycle 编号（语义由调用方定义）
    """

    t: float
    text: str
    cycle_id: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TestCaseResult:
    """单个用例的执行结果。

    Attributes:
        id: 用例标识
        suite: 所属 suite 名
        status: pass / fail / skipped / error
        command: 执行的命令（空命令表示仅探测 prompt）
        output: 命令的完整输出
        output_preview: 输出摘要（前 N 行拼接）
        assertion: 断言规格 {type, value/pattern}
        duration_sec: 执行耗时
        failure_reason: fail 时的原因说明
        skip_reason: skipped 时的原因
        triggered_collectors: fail 时触发的 collector 名称列表
        tags: 用例标签
    """

    id: str
    suite: str
    status: str
    command: str = ""
    output: str = ""
    output_preview: str = ""
    assertion: dict = field(default_factory=dict)
    duration_sec: float = 0.0
    failure_reason: str = ""
    skip_reason: str = ""
    triggered_collectors: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CollectorResult:
    """collector 执行结果。

    Attributes:
        name: collector 名称
        commands: 执行的命令列表
        outputs: 每条命令的输出 [{command, lines, duration_sec}]
        hints: 给 AI 的分析提示
    """

    name: str
    commands: list[str]
    outputs: list[dict]
    hints: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EvidenceBundle:
    """LE 框架输出给 AI 的证据包。

    Attributes:
        bundle_id: 证据包唯一标识
        device_id: 设备标识
        suite: 执行的 suite 名
        timestamp: ISO8601 时间戳
        summary: 汇总 {total, passed, failed, skipped, overall}
        cases: 全部用例结果
        evidence: collector 名称 -> CollectorResult
        device_profile: 设备配置摘要
    """

    bundle_id: str
    device_id: str
    suite: str
    timestamp: str
    summary: dict
    cases: list[TestCaseResult]
    evidence: dict[str, CollectorResult]
    device_profile: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_models.py -v --import-mode=importlib
```
Expected: 5 passed

- [ ] **Step 5: 验证未破坏 transport/observer（它们依赖 ObservedLine）**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_transport.py tests/test_observer.py tests/test_cycles.py tests/test_config.py -v --import-mode=importlib
```
Expected: 全部 passed（这些模块只依赖 ObservedLine，未受影响）

- [ ] **Step 6: 提交**

```bash
git add engineering/loop/core/python/loop_core/models.py engineering/loop/core/python/tests/test_models.py
git commit -m "重构(loop-core): models.py 重写为 v2 数据模型

删除 LoopAttempt/RuleMatch/ActionRecord（v1 规则引擎体系）。
新增 TestCaseResult/CollectorResult/EvidenceBundle（v2 用例驱动体系）。
保留 ObservedLine（transport/observer 依赖）。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

### Task A2: assertion_engine.py（断言引擎）

**Files:**
- Create: `engineering/loop/core/python/loop_core/assertion_engine.py`
- Create: `engineering/loop/core/python/tests/test_assertion_engine.py`

- [ ] **Step 1: 写失败测试**

```python
# engineering/loop/core/python/tests/test_assertion_engine.py
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
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_assertion_engine.py -v --import-mode=importlib 2>&1 | head -10
```
Expected: FAIL（ModuleNotFoundError: loop_core.assertion_engine）

- [ ] **Step 3: 实现 assertion_engine.py**

```python
# engineering/loop/core/python/loop_core/assertion_engine.py
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
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_assertion_engine.py -v --import-mode=importlib
```
Expected: 15 passed

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/core/python/loop_core/assertion_engine.py engineering/loop/core/python/tests/test_assertion_engine.py
git commit -m "新增(loop-core): assertion_engine 断言引擎

支持 6 种断言类型：contains/regex/equals/prompt_visible/not_contains/exit_code_zero。
替代 v1 规则引擎的模糊文本匹配，实现确定性断言。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

## Phase B：用例加载器 + 执行器

### Task B1: case_loader.py（YAML 加载 + include + requires + 环检测）

**Files:**
- Create: `engineering/loop/core/python/loop_core/case_loader.py`
- Create: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 写失败测试**

```python
# engineering/loop/core/python/tests/test_case_loader.py
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
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_case_loader.py -v --import-mode=importlib 2>&1 | head -10
```
Expected: FAIL（ModuleNotFoundError: loop_core.case_loader）

- [ ] **Step 3: 实现 case_loader.py**

```python
# engineering/loop/core/python/loop_core/case_loader.py
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
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_case_loader.py -v --import-mode=importlib
```
Expected: 10 passed

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/core/python/loop_core/case_loader.py engineering/loop/core/python/tests/test_case_loader.py
git commit -m "新增(loop-core): case_loader 声明式用例加载器

支持 YAML 加载、include 合并、requires 拓扑排序与环检测。
新场景只需写 YAML 用例，零 Python 代码。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

### Task B2: collector.py + executor.py（用例执行 + 深度采集）

**Files:**
- Create: `engineering/loop/core/python/loop_core/collector.py`
- Create: `engineering/loop/core/python/loop_core/executor.py`
- Create: `engineering/loop/core/python/tests/test_executor.py`

- [ ] **Step 1: 写失败测试（executor + collector 联合）**

```python
# engineering/loop/core/python/tests/test_executor.py
"""CaseExecutor 测试：用例执行、依赖短路、collector 去重。"""
import pytest
from pathlib import Path

from loop_core.assertion_engine import AssertionEngine
from loop_core.case_loader import load_suite
from loop_core.executor import CaseExecutor
from loop_core.models import EvidenceBundle
from loop_core.transport import FixtureTransport


def _make_transport(rows: list[dict]) -> FixtureTransport:
    return FixtureTransport(rows)


def test_all_pass(tmp_path):
    """全部用例 pass。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: shell_ok
    command: ""
    assert: {type: prompt_visible}
    severity: critical
"""
    suite = load_suite(str(Path(_write(tmp_path, "t.yaml", suite_yaml))), [str(tmp_path)])
    # fixture 中包含 prompt 行
    transport = _make_transport([{"t": 1.0, "text": "console:/ $"}])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=["console:/ $"])

    assert bundle.summary["total"] == 1
    assert bundle.summary["passed"] == 1
    assert bundle.summary["failed"] == 0
    assert bundle.summary["overall"] == "PASS"
    assert bundle.cases[0].status == "pass"


def test_fail_triggers_collector(tmp_path):
    """用例 fail 时触发 collector。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: zygote_check
    command: "getprop init.svc.zygote"
    assert: {type: contains, value: "running"}
    severity: critical
    on_fail:
      collectors: [debug]
collectors:
  debug:
    commands: ["dmesg"]
    hints: "check dmesg"
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    # fixture 输出 "stopped"（不含 "running"）
    transport = _make_transport([
        {"t": 0.5, "text": "stopped"},
        {"t": 1.0, "text": "dmesg output line"},
    ])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=[])

    assert bundle.summary["failed"] == 1
    assert bundle.cases[0].status == "fail"
    assert "debug" in bundle.cases[0].triggered_collectors
    assert "debug" in bundle.evidence
    assert len(bundle.evidence["debug"].outputs) == 1


def test_dependency_skip(tmp_path):
    """前置用例 fail 时，依赖用例 skip。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: shell_ok
    command: ""
    assert: {type: prompt_visible}
    severity: critical
  - id: zygote_ok
    command: "getprop init.svc.zygote"
    assert: {type: contains, value: "running"}
    severity: critical
    requires: [shell_ok]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    # fixture 无 prompt 行 -> shell_ok fail -> zygote_ok skip
    transport = _make_transport([{"t": 0.5, "text": "no prompt here"}])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=["console:/ $"])

    assert bundle.cases[0].status == "fail"  # shell_ok
    assert bundle.cases[1].status == "skipped"  # zygote_ok
    assert "shell_ok" in bundle.cases[1].skip_reason
    assert bundle.summary["skipped"] == 1


def test_dependency_skip_propagates(tmp_path):
    """skip 传播：a fail → b skip → c skip。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: a
    command: ""
    assert: {type: prompt_visible}
    severity: critical
  - id: b
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    requires: [a]
  - id: c
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    requires: [b]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([{"t": 0.5, "text": "no prompt"}])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=["console:/ $"])

    assert bundle.cases[0].status == "fail"
    assert bundle.cases[1].status == "skipped"
    assert bundle.cases[2].status == "skipped"


def test_collector_deduplication(tmp_path):
    """同 suite 内同 collector 只执行一次。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: check_a
    command: "true"
    assert: {type: contains, value: "no_match"}
    severity: critical
    on_fail: {collectors: [shared]}
  - id: check_b
    command: "true"
    assert: {type: contains, value: "no_match"}
    severity: critical
    on_fail: {collectors: [shared]}
collectors:
  shared:
    commands: ["dmesg"]
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([
        {"t": 0.5, "text": "output_a"},
        {"t": 0.6, "text": "output_b"},
        {"t": 1.0, "text": "dmesg_line"},
    ])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=[])

    assert "shared" in bundle.evidence
    assert len(bundle.evidence["shared"].outputs) == 1  # 只执行一次


def test_warn_severity_does_not_fail_suite(tmp_path):
    """severity=warn 的用例 fail 不影响 overall（记录但不阻断）。"""
    suite_yaml = """
suite: t
version: 1
cases:
  - id: warn_case
    command: "true"
    assert: {type: contains, value: "no_match"}
    severity: warn
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([{"t": 0.5, "text": "some output"}])
    transport.acquire_writer()

    executor = CaseExecutor(transport, AssertionEngine())
    bundle = executor.execute_suite(suite, device_id="rp5", prompt_markers=[])

    # warn 用例 fail 计入 failed 计数，但 overall 仍可为 PASS（无 critical fail）
    assert bundle.cases[0].status == "fail"
    assert bundle.summary["overall"] == "PASS"


def _write(tmp_path: Path, name: str, content: str) -> str:
    p = tmp_path / name
    p.write_text(content)
    return str(p)
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_executor.py -v --import-mode=importlib 2>&1 | head -10
```
Expected: FAIL（ModuleNotFoundError: loop_core.executor / loop_core.collector）

- [ ] **Step 3: 实现 collector.py**

```python
# engineering/loop/core/python/loop_core/collector.py
"""深度证据采集执行器。

collector 在用例 fail 时触发，执行预定义的命令列表采集诊断证据。
同 suite 内同 collector 去重（只执行一次）。
"""
from __future__ import annotations

import time

from loop_core.models import CollectorResult


class Collector:
    """深度证据采集器。

    通过 transport 执行命令列表，采集输出作为 AI 分析证据。
    """

    def __init__(self, transport) -> None:
        self.transport = transport

    def run(self, name: str, spec: dict, capture_timeout: float = 5.0,
            recent_limit: int = 400) -> CollectorResult:
        """执行一个 collector 的全部命令。

        Args:
            name: collector 名称
            spec: collector 规格 {commands: [...], hints: "..."}
            capture_timeout: 每条命令的采集超时
            recent_limit: 每条命令的行数上限

        Returns:
            CollectorResult
        """
        commands = spec.get("commands", [])
        hints = spec.get("hints", "")
        outputs: list[dict] = []

        for cmd in commands:
            start = time.monotonic()
            self.transport.send_line(cmd)
            lines = self.transport.capture_window(
                timeout_sec=capture_timeout, recent_limit=recent_limit
            )
            outputs.append({
                "command": cmd,
                "lines": [line.text for line in lines],
                "duration_sec": round(time.monotonic() - start, 3),
            })

        return CollectorResult(
            name=name,
            commands=commands,
            outputs=outputs,
            hints=hints,
        )
```

- [ ] **Step 4: 实现 executor.py**

```python
# engineering/loop/core/python/loop_core/executor.py
"""用例执行器。

职责：执行用例 → 求值断言 → 触发 collector → 组装 EvidenceBundle。
执行顺序遵循 case_loader 的拓扑排序，处理 requires 依赖短路。
"""
from __future__ import annotations

import time
from datetime import datetime
from uuid import uuid4

from loop_core.assertion_engine import AssertionContext, AssertionEngine
from loop_core.case_loader import CaseSuite, TestCase
from loop_core.collector import Collector
from loop_core.models import EvidenceBundle, TestCaseResult


class CaseExecutor:
    """用例执行器。

    消费 CaseSuite，逐条执行用例，对输出求值断言，
    fail 时触发 on_fail.collectors（同 suite 内去重）。

    Attributes:
        transport: 实现 BaseTransport 接口的实例
        engine: 断言引擎实例
    """

    def __init__(self, transport, engine: AssertionEngine) -> None:
        self.transport = transport
        self.engine = engine

    def execute_suite(
        self,
        suite: CaseSuite,
        device_id: str = "",
        prompt_markers: list[str] | None = None,
        capture_timeout: float = 5.0,
        recent_limit: int = 400,
    ) -> EvidenceBundle:
        """执行完整用例集。

        Args:
            suite: 加载后的 CaseSuite
            device_id: 设备标识
            prompt_markers: prompt 标记列表（用于 prompt_visible 断言）
            capture_timeout: 用例命令的采集超时
            recent_limit: 采集行数上限

        Returns:
            EvidenceBundle
        """
        prompt_markers = prompt_markers or []
        results: dict[str, TestCaseResult] = {}
        triggered_collectors: set[str] = set()

        for case in suite.cases:
            result = self._execute_case(
                case, results, prompt_markers, capture_timeout, recent_limit
            )
            results[case.id] = result
            # 收集需要执行的 collector（critical fail 才触发）
            if result.status == "fail" and case.severity == "critical":
                for cname in case.on_fail.get("collectors", []):
                    triggered_collectors.add(cname)

        # 执行 collector（去重）
        evidence: dict[str, CollectorResult] = {}
        if triggered_collectors:
            collector_runner = Collector(self.transport)
            for cname in triggered_collectors:
                if cname in suite.collectors:
                    evidence[cname] = collector_runner.run(
                        cname,
                        suite.collectors[cname],
                        capture_timeout=capture_timeout,
                        recent_limit=recent_limit,
                    )

        # 统计
        case_list = list(results.values())
        passed = sum(1 for r in case_list if r.status == "pass")
        failed = sum(1 for r in case_list if r.status == "fail")
        skipped = sum(1 for r in case_list if r.status == "skipped")
        critical_failed = sum(
            1 for r, c in zip(case_list, suite.cases)
            if r.status == "fail" and c.severity == "critical"
        ) if case_list else 0
        overall = "PASS" if critical_failed == 0 else "FAIL"

        return EvidenceBundle(
            bundle_id=f"eb-{uuid4().hex[:8]}",
            device_id=device_id,
            suite=suite.name,
            timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
            summary={
                "total": len(case_list),
                "passed": passed,
                "failed": failed,
                "skipped": skipped,
                "overall": overall,
            },
            cases=case_list,
            evidence=evidence,
        )

    def _execute_case(
        self,
        case: TestCase,
        results: dict[str, TestCaseResult],
        prompt_markers: list[str],
        capture_timeout: float,
        recent_limit: int,
    ) -> TestCaseResult:
        """执行单个用例（含依赖检查）。"""
        # 检查依赖
        for dep_id in case.requires:
            dep_result = results.get(dep_id)
            if dep_result is None:
                # 依赖的用例不存在 → skip
                return TestCaseResult(
                    id=case.id,
                    suite=case.suite,
                    status="skipped",
                    skip_reason=f"dependency '{dep_id}' not found",
                    tags=case.tags,
                )
            if dep_result.status in ("fail", "skipped"):
                dep_status = dep_result.status
                return TestCaseResult(
                    id=case.id,
                    suite=case.suite,
                    status="skipped",
                    skip_reason=f"dependency '{dep_id}' {dep_status}",
                    tags=case.tags,
                )

        # 执行用例
        start = time.monotonic()
        output_lines: list[str] = []
        prompt_visible = False

        if case.command:
            # 有命令：send + capture
            self.transport.send_line(case.command)
            captured = self.transport.capture_window(
                timeout_sec=capture_timeout, recent_limit=recent_limit
            )
            output_lines = [line.text for line in captured]
        else:
            # 无命令：仅探测 prompt（capture 当前缓冲）
            captured = self.transport.capture_window(
                timeout_sec=capture_timeout, recent_limit=recent_limit
            )
            output_lines = [line.text for line in captured]

        # 检测 prompt
        for line in output_lines:
            if any(marker in line for marker in prompt_markers):
                prompt_visible = True
                break

        output_text = "\n".join(output_lines)
        duration = round(time.monotonic() - start, 3)

        # 求值断言
        ctx = AssertionContext(
            output=output_text,
            prompt_visible=prompt_visible,
        )
        result = self.engine.evaluate(case.assert_spec, ctx)

        # 构建 output_preview（前 5 行）
        preview = " | ".join(output_lines[:5]) if output_lines else ""

        if result.passed:
            return TestCaseResult(
                id=case.id,
                suite=case.suite,
                status="pass",
                command=case.command,
                output=output_text,
                output_preview=preview,
                assertion=case.assert_spec,
                duration_sec=duration,
                tags=case.tags,
            )

        return TestCaseResult(
            id=case.id,
            suite=case.suite,
            status="fail",
            command=case.command,
            output=output_text,
            output_preview=preview,
            assertion=case.assert_spec,
            duration_sec=duration,
            failure_reason=result.reason,
            triggered_collectors=case.on_fail.get("collectors", []),
            tags=case.tags,
        )
```

- [ ] **Step 5: 运行测试验证通过**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_executor.py -v --import-mode=importlib
```
Expected: 7 passed

- [ ] **Step 6: 提交**

```bash
git add engineering/loop/core/python/loop_core/collector.py engineering/loop/core/python/loop_core/executor.py engineering/loop/core/python/tests/test_executor.py
git commit -m "新增(loop-core): collector + executor 用例执行器

CaseExecutor 消费 CaseSuite，逐条执行用例并求值断言。
fail 时触发 collector 深度采集（同 suite 内去重）。
requires 依赖短路：前置 fail/skip 则下游 skip。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

## Phase C：证据组装 + 报告 + 通用 Runner

### Task C1: evidence.py（EvidenceBundle JSON 输出）

**Files:**
- Create: `engineering/loop/core/python/loop_core/evidence.py`
- Create: `engineering/loop/core/python/tests/test_evidence.py`

- [ ] **Step 1: 写失败测试**

```python
# engineering/loop/core/python/tests/test_evidence.py
"""evidence.py 测试：EvidenceBundle JSON 序列化与文件输出。"""
import json
from pathlib import Path

from loop_core.evidence import write_evidence_bundle
from loop_core.models import CollectorResult, EvidenceBundle, TestCaseResult


def _make_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id="eb-test001",
        device_id="rp5",
        suite="boot-success",
        timestamp="2026-06-19T22:36:06+08:00",
        summary={"total": 2, "passed": 1, "failed": 1, "skipped": 0, "overall": "FAIL"},
        cases=[
            TestCaseResult(id="shell_reachable", suite="boot-success", status="pass"),
            TestCaseResult(
                id="zygote_running",
                suite="boot-success",
                status="fail",
                command="getprop init.svc.zygote",
                failure_reason="expected 'running'",
                triggered_collectors=["crash_dump"],
            ),
        ],
        evidence={
            "crash_dump": CollectorResult(
                name="crash_dump",
                commands=["logcat -b crash -d"],
                outputs=[{"command": "logcat -b crash -d", "lines": ["crash line"]}],
                hints="check abort msg",
            )
        },
    )


def test_write_evidence_bundle_json(tmp_path):
    """write_evidence_bundle 输出合法 JSON。"""
    bundle = _make_bundle()
    paths = write_evidence_bundle(bundle, str(tmp_path))

    assert "evidence_json" in paths
    p = Path(paths["evidence_json"])
    assert p.exists()

    data = json.loads(p.read_text())
    assert data["bundle_id"] == "eb-test001"
    assert data["summary"]["overall"] == "FAIL"
    assert len(data["cases"]) == 2
    assert "crash_dump" in data["evidence"]


def test_write_evidence_bundle_summary_txt(tmp_path):
    """write_evidence_bundle 同时输出 summary.txt。"""
    bundle = _make_bundle()
    paths = write_evidence_bundle(bundle, str(tmp_path))

    assert "summary_txt" in paths
    p = Path(paths["summary_txt"])
    assert p.exists()

    text = p.read_text()
    assert "boot-success" in text
    assert "FAIL" in text
    assert "zygote_running" in text


def test_long_output_truncated_in_json(tmp_path):
    """长输出在 JSON 中被截断（output 保留，但 preview 有限）。"""
    long_output = "x" * 5000
    bundle = EvidenceBundle(
        bundle_id="eb-long",
        device_id="rp5",
        suite="t",
        timestamp="2026-06-19T22:36:06+08:00",
        summary={"total": 1, "passed": 0, "failed": 1, "skipped": 0, "overall": "FAIL"},
        cases=[
            TestCaseResult(
                id="c1",
                suite="t",
                status="fail",
                output=long_output,
                output_preview=long_output[:200],
            ),
        ],
        evidence={},
    )
    paths = write_evidence_bundle(bundle, str(tmp_path))
    data = json.loads(Path(paths["evidence_json"]).read_text())
    assert len(data["cases"][0]["output"]) == 5000  # output 保留完整
    assert len(data["cases"][0]["output_preview"]) == 200
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_evidence.py -v --import-mode=importlib 2>&1 | head -10
```
Expected: FAIL（ModuleNotFoundError: loop_core.evidence）

- [ ] **Step 3: 实现 evidence.py**

```python
# engineering/loop/core/python/loop_core/evidence.py
"""EvidenceBundle JSON 输出。

将 CaseExecutor 产出的 EvidenceBundle 序列化为：
- evidence_bundle.json：完整结构化 JSON（供 AI 分析）
- summary.txt：人类可读摘要
"""
from __future__ import annotations

import json
from pathlib import Path

from loop_core.models import EvidenceBundle


def write_evidence_bundle(bundle: EvidenceBundle, output_dir: str) -> dict[str, str]:
    """将 EvidenceBundle 写入文件。

    Args:
        bundle: 证据包
        output_dir: 输出目录

    Returns:
        生成的文件路径 dict {"evidence_json": ..., "summary_txt": ...}
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    # evidence_bundle.json
    json_path = out / "evidence_bundle.json"
    json_path.write_text(
        json.dumps(bundle.to_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    # summary.txt
    summary_path = out / "summary.txt"
    summary_path.write_text(render_evidence_summary(bundle), encoding="utf-8")

    return {
        "evidence_json": str(json_path),
        "summary_txt": str(summary_path),
    }


def render_evidence_summary(bundle: EvidenceBundle) -> str:
    """渲染人类可读的 EvidenceBundle 摘要。

    Args:
        bundle: 证据包

    Returns:
        多行文本摘要
    """
    s = bundle.summary
    lines = [
        f"Suite: {bundle.suite}",
        f"Device: {bundle.device_id}",
        f"Timestamp: {bundle.timestamp}",
        f"Overall: {s['overall']}",
        f"Total: {s['total']}  Passed: {s['passed']}  "
        f"Failed: {s['failed']}  Skipped: {s['skipped']}",
        "",
        "=== 用例结果 ===",
    ]

    for case in bundle.cases:
        status_marker = {"pass": "[PASS]", "fail": "[FAIL]", "skipped": "[SKIP]", "error": "[ERR!]"}.get(
            case.status, "[????]"
        )
        lines.append(f"  {status_marker} {case.id}")
        if case.command:
            lines.append(f"        command: {case.command}")
        if case.failure_reason:
            lines.append(f"        reason: {case.failure_reason[:200]}")
        if case.skip_reason:
            lines.append(f"        reason: {case.skip_reason}")
        if case.triggered_collectors:
            lines.append(f"        collectors: {', '.join(case.triggered_collectors)}")

    if bundle.evidence:
        lines.append("")
        lines.append("=== 采集证据 ===")
        for name, cr in bundle.evidence.items():
            lines.append(f"  [{name}] ({len(cr.commands)} commands)")
            if cr.hints:
                lines.append(f"        hints: {cr.hints}")

    return "\n".join(lines)
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_evidence.py -v --import-mode=importlib
```
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/core/python/loop_core/evidence.py engineering/loop/core/python/tests/test_evidence.py
git commit -m "新增(loop-core): evidence EvidenceBundle JSON 输出

输出 evidence_bundle.json（供 AI 分析）+ summary.txt（人类可读）。
替代 v1 的 report.py write_report_bundle。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

### Task C2: report.py 改造（渲染 EvidenceBundle）

**Files:**
- Modify: `engineering/loop/core/python/loop_core/report.py`
- Modify: `engineering/loop/core/python/tests/test_report.py`

> **注意：** report.py 原依赖 LoopAttempt（已删除）。改造后 report.py 仅做 evidence.py 的薄封装，保留 `write_report_bundle` 名以减少调用方改动，但内部委托 `write_evidence_bundle`。但考虑到 v2.1 完全用 evidence.py，report.py 改为 re-export evidence 函数。

- [ ] **Step 1: 重写 test_report.py**

```python
# engineering/loop/core/python/tests/test_report.py
"""report.py v2 测试：验证它委托 evidence.py。"""
import json
from pathlib import Path

from loop_core.models import EvidenceBundle, TestCaseResult
from loop_core.report import write_report_bundle, render_summary


def _make_bundle() -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id="eb-rpt",
        device_id="rp5",
        suite="t",
        timestamp="2026-06-19T22:00:00+08:00",
        summary={"total": 1, "passed": 1, "failed": 0, "skipped": 0, "overall": "PASS"},
        cases=[TestCaseResult(id="c1", suite="t", status="pass")],
        evidence={},
    )


def test_write_report_bundle_delegates_to_evidence(tmp_path):
    paths = write_report_bundle(_make_bundle(), str(tmp_path))
    assert "evidence_json" in paths
    assert Path(paths["evidence_json"]).exists()
    assert Path(paths["summary_txt"]).exists()


def test_render_summary_returns_text():
    text = render_summary(_make_bundle())
    assert "Suite: t" in text
    assert "PASS" in text
    assert "c1" in text
```

- [ ] **Step 2: 重写 report.py**

```python
# engineering/loop/core/python/loop_core/report.py
"""loop_core 报告渲染（v2）。

v2 report.py 是 evidence.py 的薄封装，保持向后兼容的函数名。
实际逻辑在 evidence.py 中。
"""
from __future__ import annotations

from loop_core.evidence import render_evidence_summary, write_evidence_bundle
from loop_core.models import EvidenceBundle


def write_report_bundle(
    bundle: EvidenceBundle,
    output_dir: str,
    **kwargs,
) -> dict[str, str]:
    """写入 EvidenceBundle 报告文件。

    v2 委托 write_evidence_bundle。kwargs 忽略 v1 遗留参数（snapshot_lines/advice_map）。

    Args:
        bundle: EvidenceBundle
        output_dir: 输出目录

    Returns:
        {"evidence_json": ..., "summary_txt": ...}
    """
    return write_evidence_bundle(bundle, output_dir)


def render_summary(bundle: EvidenceBundle, **kwargs) -> str:
    """渲染 EvidenceBundle 摘要文本。

    v2 委托 render_evidence_summary。kwargs 忽略 v1 遗留参数。

    Args:
        bundle: EvidenceBundle

    Returns:
        多行文本摘要
    """
    return render_evidence_summary(bundle)
```

- [ ] **Step 3: 运行测试验证通过**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_report.py -v --import-mode=importlib
```
Expected: 2 passed

- [ ] **Step 4: 提交**

```bash
git add engineering/loop/core/python/loop_core/report.py engineering/loop/core/python/tests/test_report.py
git commit -m "重构(loop-core): report.py 改造为 evidence.py 薄封装

v2 report 委托 evidence 模块。删除 v1 LoopAttempt 渲染逻辑。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

### Task C3: runner.py（通用 LoopRunner）

**Files:**
- Create: `engineering/loop/core/python/loop_core/runner.py`
- Create: `engineering/loop/core/python/tests/test_runner.py`

- [ ] **Step 1: 写失败测试**

```python
# engineering/loop/core/python/tests/test_runner.py
"""通用 LoopRunner 测试：场景无关，纯用例驱动。"""
from pathlib import Path

from loop_core.assertion_engine import AssertionEngine
from loop_core.case_loader import load_suite
from loop_core.runner import LoopRunner
from loop_core.transport import FixtureTransport


def _write(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content)
    return str(p)


def test_runner_executes_suite_and_returns_bundle(tmp_path):
    """LoopRunner 执行 suite 返回 EvidenceBundle。"""
    path = _write(tmp_path, "t.yaml", """
suite: test-suite
version: 1
cases:
  - id: shell_check
    command: ""
    assert: {type: prompt_visible}
    severity: critical
""")
    suite = load_suite(path, [str(tmp_path)])
    transport = FixtureTransport([{"t": 1.0, "text": "console:/ $"}])

    runner = LoopRunner(
        device_id="rp5",
        prompt_markers=["console:/ $"],
        transport=transport,
        suite=suite,
    )
    bundle = runner.run()

    assert bundle.device_id == "rp5"
    assert bundle.suite == "test-suite"
    assert bundle.summary["total"] == 1
    assert bundle.cases[0].status == "pass"


def test_runner_writer_busy_returns_failure_bundle(tmp_path):
    """writer 获取失败时返回 fail bundle。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: c1
    command: ""
    assert: {type: prompt_visible}
""")
    suite = load_suite(path, [str(tmp_path)])

    class BusyTransport(FixtureTransport):
        def acquire_writer(self):
            return False

    transport = BusyTransport([])

    runner = LoopRunner("rp5", [], transport, suite)
    bundle = runner.run()

    assert bundle.summary["overall"] == "FAIL"


def test_runner_uses_custom_executor_config(tmp_path):
    """LoopRunner 支持 capture_timeout / recent_limit 配置。"""
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: c1
    command: "echo test"
    assert: {type: contains, value: "test"}
""")
    suite = load_suite(path, [str(tmp_path)])
    transport = FixtureTransport([
        {"t": 0.5, "text": "test output"},
    ])

    runner = LoopRunner("rp5", [], transport, suite, capture_timeout=2.0, recent_limit=100)
    bundle = runner.run()

    assert bundle.cases[0].status == "pass"
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_runner.py -v --import-mode=importlib 2>&1 | head -10
```
Expected: FAIL（ModuleNotFoundError: loop_core.runner）

- [ ] **Step 3: 实现 runner.py**

```python
# engineering/loop/core/python/loop_core/runner.py
"""通用 LoopRunner：场景无关，纯用例驱动。

所有场景（boot-success/lcview/lciod）共用此 runner。
新场景只需写 YAML 用例，零 Python 代码。
"""
from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from loop_core.assertion_engine import AssertionEngine
from loop_core.case_loader import CaseSuite
from loop_core.executor import CaseExecutor
from loop_core.models import EvidenceBundle


class LoopRunner:
    """通用 LE 执行器。

    消费 CaseSuite + transport，产出 EvidenceBundle。
    不含任何场景特有逻辑。

    Attributes:
        device_id: 设备标识
        prompt_markers: prompt 标记列表
        transport: 实现 BaseTransport 接口的实例
        suite: 加载后的 CaseSuite
        capture_timeout: 用例命令采集超时（秒）
        recent_limit: 采集行数上限
    """

    def __init__(
        self,
        device_id: str,
        prompt_markers: list[str],
        transport,
        suite: CaseSuite,
        capture_timeout: float = 5.0,
        recent_limit: int = 400,
    ) -> None:
        self.device_id = device_id
        self.prompt_markers = prompt_markers
        self.transport = transport
        self.suite = suite
        self.capture_timeout = capture_timeout
        self.recent_limit = recent_limit
        self.executor = CaseExecutor(transport, AssertionEngine())

    def run(self) -> EvidenceBundle:
        """执行用例集并返回证据包。

        Returns:
            EvidenceBundle
        """
        if not self.transport.acquire_writer():
            return self._build_failure_bundle("writer busy")

        try:
            return self.executor.execute_suite(
                self.suite,
                device_id=self.device_id,
                prompt_markers=self.prompt_markers,
                capture_timeout=self.capture_timeout,
                recent_limit=self.recent_limit,
            )
        finally:
            self.transport.release()

    def _build_failure_bundle(self, reason: str) -> EvidenceBundle:
        """构建 writer 获取失败时的 EvidenceBundle。"""
        return EvidenceBundle(
            bundle_id=f"eb-{uuid4().hex[:8]}",
            device_id=self.device_id,
            suite=self.suite.name,
            timestamp=datetime.now().astimezone().isoformat(timespec="seconds"),
            summary={
                "total": 0,
                "passed": 0,
                "failed": 0,
                "skipped": 0,
                "overall": "FAIL",
                "error": reason,
            },
            cases=[],
            evidence={},
        )
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/test_runner.py -v --import-mode=importlib
```
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/core/python/loop_core/runner.py engineering/loop/core/python/tests/test_runner.py
git commit -m "新增(loop-core): runner 通用 LoopRunner

场景无关的用例执行 runner。acquire → execute_suite → release。
替代 v1 的 per-workflow BootFailureRunner。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

## Phase D：CLI + 入口脚本 + 用例/模板

### Task D1: cli.py（统一 CLI 入口）

**Files:**
- Create: `engineering/loop/core/python/loop_core/cli.py`

- [ ] **Step 1: 实现 cli.py**

```python
# engineering/loop/core/python/loop_core/cli.py
"""LE 统一 CLI 入口。

子命令：
- run：执行用例集，输出 EvidenceBundle
- gen-cases：AI 辅助用例生成（第二步实现，当前占位）
- deploy：部署 binary/image（第二步实现，当前占位）

用法：
    python3 -m loop_core.cli run --suite boot-success --fixture <jsonl> ...
    python3 -m loop_core.cli run --suite boot-success --host 127.0.0.1 --port 9700 ...
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from loop_core.case_loader import load_suite
from loop_core.config import DeviceProfile
from loop_core.evidence import write_evidence_bundle
from loop_core.report import render_summary
from loop_core.runner import LoopRunner
from loop_core.transport import FixtureTransport


def main(argv: list[str] | None = None) -> int:
    """CLI 主入口。

    Returns:
        退出码（0=成功，非零=失败）
    """
    parser = argparse.ArgumentParser(
        description="Loop Engineering v2：用例驱动验收器"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run 子命令
    run_parser = sub.add_parser("run", help="执行用例集")
    run_parser.add_argument("--suite", required=True, help="suite YAML 文件路径")
    run_parser.add_argument("--fixture", help="JSONL fixture 文件路径（离线回放模式）")
    run_parser.add_argument("--host", default="127.0.0.1", help="host 地址（live 模式）")
    run_parser.add_argument("--port", type=int, default=9700, help="host 端口（live 模式）")
    run_parser.add_argument(
        "--device-profile", required=True, help="设备 profile JSON 路径"
    )
    run_parser.add_argument(
        "--case-dirs",
        default="",
        help="include 搜索目录（逗号分隔）",
    )
    run_parser.add_argument(
        "--artifacts-dir", required=True, help="artifacts 输出目录"
    )

    # gen-cases 占位
    sub.add_parser("gen-cases", help="AI 辅助用例生成（第二步实现）")

    # deploy 占位
    sub.add_parser("deploy", help="部署 binary/image（第二步实现）")

    args = parser.parse_args(argv)

    if args.command == "run":
        return _cmd_run(args)
    if args.command == "gen-cases":
        print("gen-cases 命令将在第二步实现", file=sys.stderr)
        return 1
    if args.command == "deploy":
        print("deploy 命令将在第二步实现", file=sys.stderr)
        return 1
    return 1


def _cmd_run(args) -> int:
    """执行 run 子命令。"""
    # 加载 device profile
    device_raw = json.loads(Path(args.device_profile).read_text(encoding="utf-8"))
    profile = DeviceProfile(**{
        k: v for k, v in device_raw.items()
        if k in DeviceProfile.__dataclass_fields__  # type: ignore[attr-defined]
    })

    # 解析 case-dirs
    case_dirs = [d.strip() for d in args.case_dirs.split(",") if d.strip()] if args.case_dirs else []
    # suite 文件所在目录自动加入搜索路径
    suite_dir = str(Path(args.suite).parent)
    if suite_dir not in case_dirs:
        case_dirs.append(suite_dir)

    # 加载 suite
    suite = load_suite(args.suite, case_dirs)

    # 选择 transport
    if args.fixture:
        transport = FixtureTransport.from_jsonl(args.fixture)
    else:
        try:
            from rp5_serial.client.automation import AutomationClient
            from rp5_serial.transport import Rp5SerialTransport
        except ImportError:
            print(
                "ERROR: live mode 需要 rp5_serial provider，请设置 PYTHONPATH",
                file=sys.stderr,
            )
            return 1
        client = AutomationClient(args.host, args.port)
        try:
            client.connect()
        except OSError as e:
            print(f"ERROR: 无法连接 host {args.host}:{args.port}: {e}", file=sys.stderr)
            return 1
        transport = Rp5SerialTransport(client)

    # 执行
    runner = LoopRunner(
        device_id=profile.device_id,
        prompt_markers=profile.prompt_markers,
        transport=transport,
        suite=suite,
        capture_timeout=5.0,
        recent_limit=400,
    )
    bundle = runner.run()

    # 输出
    paths = write_evidence_bundle(bundle, args.artifacts_dir)
    print(render_summary(bundle))
    print(f"\nEvidenceBundle: {paths['evidence_json']}")

    return 0 if bundle.summary["overall"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: 提交（cli 没有 TDD 因为它是集成层，端到端测试在 D3 覆盖）**

```bash
git add engineering/loop/core/python/loop_core/cli.py
git commit -m "新增(loop-core): cli 统一 CLI 入口

支持 le run（执行用例集）。gen-cases/deploy 占位（第二步实现）。
替代 v1 的 boot_failure_debug.cli。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

### Task D2: 用例文件 + 模板 + 入口脚本

**Files:**
- Create: `engineering/loop/cases/common/shell.yaml`
- Create: `engineering/loop/cases/system/boot-success.yaml`
- Create: `engineering/loop/templates/case-template.md`
- Create: `engineering/loop/bin/le.sh`

- [ ] **Step 1: 创建 cases/common/shell.yaml**

```yaml
# engineering/loop/cases/common/shell.yaml
# 公共 shell 可达性用例。被其他 suite include 复用。
suite: shell
version: 1

cases:
  - id: shell_reachable
    description: "shell prompt 可见，设备串口可达"
    command: ""
    assert:
      type: prompt_visible
    severity: critical
    on_fail:
      collectors: []
    tags: [shell, reachable]
```

- [ ] **Step 2: 创建 cases/system/boot-success.yaml**

```yaml
# engineering/loop/cases/system/boot-success.yaml
# 系统级 boot 成功验收用例。
# include shell suite 的 shell_reachable 用例。
suite: boot-success
version: 1

include:
  - shell

cases:
  - id: boot_completed
    description: "sys.boot_completed 属性为 1"
    command: "getprop sys.boot_completed"
    assert:
      type: contains
      value: "1"
    severity: critical
    requires: [shell_reachable]
    on_fail:
      collectors: [boot_log, init_log]
    tags: [boot, system]

  - id: zygote_running
    description: "zygote 服务处于 running 状态"
    command: "getprop init.svc.zygote"
    assert:
      type: contains
      value: "running"
    severity: critical
    requires: [shell_reachable]
    on_fail:
      collectors: [crash_dump, init_log]
    tags: [boot, android_core]

  - id: surfaceflinger_running
    description: "surfaceflinger 服务处于 running 状态"
    command: "getprop init.svc.surfaceflinger"
    assert:
      type: contains
      value: "running"
    severity: critical
    requires: [shell_reachable]
    on_fail:
      collectors: [crash_dump, init_log]
    tags: [boot, android_core]

collectors:
  crash_dump:
    commands:
      - "logcat -b crash -d"
      - "ls -la /data/tombstones/"
    hints: "关注 abort message / signal / fault addr / backtrace"
  init_log:
    commands:
      - "getprop init.svc.*"
      - "logcat -b system -d"
    hints: "关注 service 重启频率 / 退出信号 / last_reason"
  boot_log:
    commands:
      - "dmesg"
    hints: "关注 boot 时序 / init 阶段卡点 / kernel 错误"
```

- [ ] **Step 3: 创建 templates/case-template.md**

````markdown
# Loop Engineering 用例生成模板

> 本文档约束 AI（opencode）从模块代码 + 需求文档生成验收用例时的格式、质量、coverage。

## 1. 用例文件格式规范（YAML schema）

每个用例文件必须包含以下顶层字段：

```yaml
suite: <suite名称，snake_case，与文件名一致>
version: 1

include:           # 可选：引入其他 suite 的 cases 和 collectors
  - common/shell

cases:             # 必填：用例列表
  - id: <用例ID，snake_case，全局唯一>
    description: "<用例描述，一句话说清楚验证什么>"
    command: "<执行的 shell 命令，空字符串表示仅探测 prompt>"
    assert:        # 必填：断言规格
      type: <断言类型>
      value: <断言值>  # contains/equals/not_contains 需要
      pattern: <正则>  # regex 需要
    severity: <critical|warn>  # critical=fail阻断，warn=仅记录。默认 critical
    requires: [<前置用例ID>]    # 可选：依赖声明
    on_fail:       # 可选：失败时的动作
      collectors: [<collector名称>]
    tags: [<标签>]  # 可选

collectors:        # 可选：collector 定义
  <collector名称>:
    commands: [<命令1>, <命令2>]
    hints: "<给AI的分析提示>"
```

### 必填字段 checklist
- [ ] suite（与文件名一致）
- [ ] version
- [ ] cases（至少 1 条）
- [ ] 每条 case 有 id / description / command / assert

## 2. 断言类型选择矩阵

| 场景 | 推荐断言 | 示例 |
|------|---------|------|
| 进程/service 状态 | `contains` | value: "running" |
| IP 地址/网络格式 | `regex` | pattern: "inet \\d+\\.\\d+\\.\\d+\\.\\d+" |
| 布尔属性（0/1） | `equals` | value: "1" |
| shell prompt 可见 | `prompt_visible` | （无参数） |
| 确认无错误输出 | `not_contains` | value: "error" |
| 命令执行成功 | `exit_code_zero` | （无参数） |

## 3. coverage 要求

生成用例时必须覆盖以下维度（视模块功能而定）：

- **每个 init service**：至少 1 条 `getprop init.svc.<name>` 用例
- **每个公开 HAL 接口**：至少 1 条存在性/可用性用例
- **每个设备节点**：至少 1 条 `ls -l /dev/<node>` 存在性检查
- **关键系统属性**：sys.boot_completed / ro.boottime.* 等必须覆盖
- **网络连通性**：wlan 连接状态、IP 分配、DNS 解析

用例 description 中标注来源：
- `[code]` 来自代码分析
- `[spec]` 来自需求文档

## 4. 命名规范

- suite 名：snake_case，与文件名一致（如 `lcview` 对应 `lcview.yaml`）
- case id：snake_case，suite 内唯一，语义清晰（如 `zygote_running`）
- collector 名：语义化（`crash_dump` / `init_log` / `network_log`）

## 5. collector 选择指南

| fail 类型 | 推荐 collector | 典型命令 |
|-----------|--------------|---------|
| 进程崩溃/abort | `crash_dump` | logcat -b crash -d, ls /data/tombstones/ |
| 服务未启动/异常退出 | `init_log` | getprop init.svc.*, logcat -b system -d |
| 网络问题 | `network_log` | ip addr, logcat -b system -d, ping |
| boot 卡死/时序问题 | `boot_log` | dmesg, getprop ro.boottime.* |
| SELinux/权限问题 | `security_log` | getenforce, dmesg | grep avc |

## 6. 好用例 vs 坏用例

### 好用例（确定性、可重复、单一职责）

```yaml
- id: zygote_running
  description: "zygote 处于 running 状态"
  command: "getprop init.svc.zygote"
  assert: {type: contains, value: "running"}
  severity: critical
```

### 坏用例（模糊、多职责、不可重复）

```yaml
- id: system_ok          # ❌ 太模糊
  description: "系统正常"  # ❌ 不具体
  command: "getprop && dmesg && logcat"  # ❌ 一条用例查太多
  assert: {type: not_contains, value: "error"}  # ❌ 不精确
```

## 7. 生成 checklist

- [ ] 每条用例有清晰的 description
- [ ] severity 明确（critical/warn）
- [ ] 依赖声明完整（requires 拓扑无环）
- [ ] on_fail 指定合理 collector
- [ ] 命名符合 snake_case
- [ ] suite/version 字段存在
- [ ] coverage 覆盖所有关键功能点
- [ ] 用例 description 标注来源（code/spec）
````

- [ ] **Step 4: 创建 bin/le.sh**

```bash
#!/bin/bash
# le.sh — Loop Engineering v2 统一 CLI 入口
# 用法:
#   le.sh run --suite boot-success --fixture <jsonl> --device-profile <json> --case-dirs <dirs> --artifacts-dir <dir>
#   le.sh run --suite boot-success --host 127.0.0.1 --port 9700 --device-profile <json> --case-dirs <dirs> --artifacts-dir <dir>
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../harness/lib/harness_bootstrap.sh
source "$SCRIPT_DIR/../../harness/lib/harness_bootstrap.sh"

harness_init "le"

CORE_ROOT="$SCRIPT_DIR/../core/python"
PROVIDER_ROOT="$SCRIPT_DIR/../connection/providers/rp5-serial/python"
export PYTHONPATH="$CORE_ROOT:$PROVIDER_ROOT${PYTHONPATH:+:$PYTHONPATH}"

python3 -m loop_core.cli "$@"
rc=$?

harness_exit "$rc"
```

- [ ] **Step 5: 设置 le.sh 可执行权限**

```bash
chmod +x engineering/loop/bin/le.sh
```

- [ ] **Step 6: 提交**

```bash
git add engineering/loop/cases/common/shell.yaml engineering/loop/cases/system/boot-success.yaml engineering/loop/templates/case-template.md engineering/loop/bin/le.sh
git commit -m "新增(loop): v2 用例/模板/入口脚本

cases: common/shell.yaml + system/boot-success.yaml（首个系统级场景）
templates: case-template.md（AI 生成用例约束模板）
bin: le.sh（统一 CLI 入口，替代 loop_boot_failure_debug.sh）

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

### Task D3: 端到端 fixture 模式验证

- [ ] **Step 1: 迁移 fixture 文件到 core/tests/fixtures/**

```bash
mkdir -p engineering/loop/core/python/tests/fixtures
cp engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/*.jsonl \
   engineering/loop/core/python/tests/fixtures/
ls engineering/loop/core/python/tests/fixtures/
```
Expected: 5 个 jsonl 文件

- [ ] **Step 2: 创建一个适配 boot-success 的 fixture（prompt 可见 + zygote running）**

```bash
cat > engineering/loop/core/python/tests/fixtures/boot_success.jsonl << 'EOF'
{"t": 0.0, "text": "Booting Linux on physical CPU 0x0"}
{"t": 1.0, "text": "Linux version 6.1.0-android14-loom"}
{"t": 3.0, "text": "init: starting service 'zygote'"}
{"t": 8.0, "text": "console:/ $"}
EOF
```

- [ ] **Step 3: 端到端运行 le.sh run（fixture 模式）**

```bash
mkdir -p /tmp/opencode/le-e2e-test
bash engineering/loop/bin/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --fixture engineering/loop/core/python/tests/fixtures/boot_success.jsonl \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases/common,engineering/loop/cases/system \
  --artifacts-dir /tmp/opencode/le-e2e-test 2>&1
```
Expected: 运行成功，输出 summary（含 Suite: boot-success），生成 evidence_bundle.json

- [ ] **Step 4: 验证 EvidenceBundle JSON 合法性**

```bash
python3 -c "
import json
data = json.load(open('/tmp/opencode/le-e2e-test/evidence_bundle.json'))
assert 'bundle_id' in data
assert data['suite'] == 'boot-success'
assert 'cases' in data
assert len(data['cases']) > 0
print(f'OK: {data[\"summary\"]}')"
```

- [ ] **Step 5: 提交 fixture 迁移**

```bash
git add engineering/loop/core/python/tests/fixtures/
git commit -m "新增(loop-core): 迁移 fixture 文件到 core/tests/fixtures/

从 workflows/boot-failure-debug-loop 迁移 5 个 fixture + 新增 boot_success.jsonl。
用于 v2 fixture 模式端到端验证。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

## Phase E：删除 v1 遗留

### Task E1: 删除 v1 core 模块（rules.py + actions.py）及其测试

**Files:**
- Delete: `engineering/loop/core/python/loop_core/rules.py`
- Delete: `engineering/loop/core/python/loop_core/actions.py`
- Delete: `engineering/loop/core/python/tests/test_rules.py`
- Delete: `engineering/loop/core/python/tests/test_actions.py`

- [ ] **Step 1: 删除文件**

```bash
rm engineering/loop/core/python/loop_core/rules.py
rm engineering/loop/core/python/loop_core/actions.py
rm engineering/loop/core/python/tests/test_rules.py
rm engineering/loop/core/python/tests/test_actions.py
```

- [ ] **Step 2: 验证 core 层无残留引用**

```bash
cd engineering/loop && rg "from loop_core.rules|from loop_core.actions|loop_core\.rules|loop_core\.actions" core/ --files-with-matches
```
Expected: 无输出（零引用）

- [ ] **Step 3: 运行 core 全量测试验证无破坏**

```bash
cd engineering/loop/core/python && PYTHONPATH=. python3 -m pytest tests/ -v --import-mode=importlib -k "not test_rules and not test_actions"
```
Expected: 全部 passed

- [ ] **Step 4: 提交**

```bash
git add -A engineering/loop/core/python/loop_core/ engineering/loop/core/python/tests/
git commit -m "删除(loop-core): v1 rules.py + actions.py 及其测试

rules.py（规则引擎）被 assertion_engine.py 替代。
actions.py（批量执行器）被 executor.py + collector.py 替代。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

### Task E2: 删除整个 workflows/ 目录

**Files:**
- Delete: `engineering/loop/workflows/`（整个目录）

- [ ] **Step 1: 删除 workflows/ 目录**

```bash
rm -rf engineering/loop/workflows/
```

- [ ] **Step 2: 验证全项目无残留引用**

```bash
rg "boot_failure_debug|loop_boot_failure_debug|BootFailureRunner|BootFailureConfig" \
   engineering/loop/ --files-with-matches -g "!*.jsonl" -g "!docs/"
```
Expected: 无输出（零引用，排除 fixture 和 docs）

- [ ] **Step 3: 验证联合回归（core + provider）**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib -k "not test_rules and not test_actions" 2>&1 | tail -20
```
Expected: 全部 passed

- [ ] **Step 4: 提交**

```bash
git add -A engineering/loop/workflows/
git commit -m "删除(loop): workflows/ 目录完全移除

v1 的 boot-failure-debug-loop workflow 整体删除。
通用 LoopRunner 已上提到 core，新场景零 Python 代码（仅写 YAML）。
fixture 文件已迁移到 core/tests/fixtures/。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

### Task E3: 简化 profile 配置

**Files:**
- Modify: `engineering/loop/profiles/boot-failure-debug/default.json`
- Modify: `engineering/loop/connection/profiles/devices/rp5/default.json`

- [ ] **Step 1: 简化 workflow profile**

```bash
# profiles/boot-failure-debug/default.json 不再需要 v1 阈值
# 但 profile 目录可能不再需要（配置已在 suite YAML 中）
# 检查是否有其他引用
rg "boot-failure-debug/default" engineering/loop/ --files-with-matches
```

如果没有引用，删除 profiles/boot-failure-debug/：

```bash
rm -rf engineering/loop/profiles/boot-failure-debug/
# 如果 profiles/ 目录空了也删除
rmdir engineering/loop/profiles/ 2>/dev/null || true
```

- [ ] **Step 2: 简化 device profile（删除 v1 markers）**

更新 `engineering/loop/connection/profiles/devices/rp5/default.json`：

```json
{
  "device_id": "rp5",
  "transport": "serial",
  "prompt_markers": ["console:/ $", "console:/ #", "localhost:/ #", "# ", "$ "],
  "reboot_markers": ["reboot: Restarting system", "U-Boot"],
  "line_ending": "\n"
}
```

> 删除了 boot_markers/panic_markers/hang_markers（迁入用例 collectors）

- [ ] **Step 3: 验证 device profile 能被 CLI 正确加载**

```bash
bash engineering/loop/bin/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --fixture engineering/loop/core/python/tests/fixtures/boot_success.jsonl \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases/common,engineering/loop/cases/system \
  --artifacts-dir /tmp/opencode/le-profile-test 2>&1 | head -10
```
Expected: 运行成功

- [ ] **Step 4: 提交**

```bash
git add -A engineering/loop/profiles/ engineering/loop/connection/profiles/devices/rp5/default.json
git commit -m "简化(loop): profile 配置移除 v1 阈值/markers

删除 profiles/boot-failure-debug/（配置已迁入 suite YAML）。
device profile 删除 boot/panic/hang markers（迁入用例 collectors）。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

## Phase F：文档重新生成

### Task F1: 重新生成 README.md + WORKFLOW.md

**Files:**
- Rewrite: `engineering/loop/README.md`
- Rewrite: `engineering/loop/WORKFLOW.md`

- [ ] **Step 1: 重写 README.md**

```markdown
# Loop Engineering

AI 驱动的设备验收闭环：用例驱动 + EvidenceBundle + opencode AI 分析修复。

## 架构

```
opencode (AI Driver)
    ↓ le run
LE 框架 (loop_core)
    ├── case_loader   YAML 用例加载（include/requires）
    ├── assertion_engine  确定性断言（6 种类型）
    ├── executor      用例执行 + collector 触发
    ├── runner        通用 LoopRunner（场景无关）
    └── evidence      EvidenceBundle JSON 输出
    ↓ transport
connection (rp5-serial provider)
```

## 目录结构

```
engineering/loop/
├── bin/le.sh                    统一 CLI 入口
├── core/python/loop_core/       LE 框架（通用层）
├── cases/                       声明式用例（YAML）
│   ├── common/                    公共原子用例
│   ├── modules/                   模块级用例
│   └── system/                    系统级用例
├── templates/                   AI 生成约束模板
├── connection/                  连接层（provider）
└── scripts/                     辅助脚本
```

## 快速开始

### fixture 模式（离线回放）

```bash
bash engineering/loop/bin/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --fixture <jsonl路径> \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases/common,engineering/loop/cases/system \
  --artifacts-dir <输出目录>
```

### live 模式

```bash
# 先启动 Windows Host（COM5）
# 然后在 WSL2 执行：
bash engineering/loop/bin/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --host 127.0.0.1 --port 9700 \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases/common,engineering/loop/cases/system \
  --artifacts-dir <输出目录>
```

## 添加新场景

只需写 1 个 YAML 用例文件，零 Python 代码：

```bash
# 1. 参照模板编写用例
cp engineering/loop/templates/case-template.md <参考>

# 2. 创建用例文件
# engineering/loop/cases/system/<your-scenario>.yaml

# 3. 执行
bash engineering/loop/bin/le.sh run --suite <path> ...
```

## 测试

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib
```

## 设计文档

- `docs/specs/2026-06-19-loop-engineering-v2-design.md`（v2 架构）
- `docs/specs/2026-06-19-loop-core-extraction-design.md`（core 抽取）
- `docs/specs/2026-06-19-loop-engineering-design.md`（v1 原始设计）
```

- [ ] **Step 2: 重写 WORKFLOW.md**

```markdown
---
name: loop-engineering
description: loop engineering v2 工作流（用例驱动 + AI 修复闭环）
---

# Loop Engineering v2 Workflow

## 目标

AI 接管设备验收：执行用例 → 输出证据 → AI 分析 → 修复代码 → 重测 → 循环直到全 pass。

## 核心流程

```
1. AI 读代码/spec + template → 生成 YAML 用例
2. le run 执行用例 → EvidenceBundle JSON
3. 全 pass → 功能 OK
4. 有 fail → AI 读 EvidenceBundle 分析根因
5. AI 修改 workspace 代码
6. 编译部署（binary 自动 / 镜像确认）
7. goto 2，直到全 pass 或 N=5 回退人工
```

## 分层职责

| 层 | 职责 |
|----|------|
| opencode (AI) | 生成用例 / 分析证据 / 修复代码 |
| loop_core | 用例加载 / 断言求值 / 执行 / 证据输出 |
| cases/*.yaml | 场景定义（声明式，零 Python） |
| connection | 传输层（串口/ADB） |

## core 模块清单

| 模块 | 职责 |
|------|------|
| `models.py` | ObservedLine / TestCaseResult / CollectorResult / EvidenceBundle |
| `assertion_engine.py` | 确定性断言（contains/regex/equals/prompt_visible/not_contains/exit_code_zero） |
| `case_loader.py` | YAML 加载 + include + requires 拓扑排序 |
| `executor.py` | 用例执行 + collector 触发（去重） |
| `collector.py` | 深度证据采集 |
| `runner.py` | 通用 LoopRunner（场景无关） |
| `evidence.py` | EvidenceBundle JSON 输出 |
| `report.py` | evidence.py 薄封装 |
| `cli.py` | 统一 CLI（le run / gen-cases / deploy） |
| `config.py` | DeviceProfile / merge_profiles |
| `transport.py` | BaseTransport + FixtureTransport |
| `observer.py` | capture_snapshot（prompt 探测） |
| `cycles.py` | cycle 切分工具（可选） |

## 扩展新场景

1. 参照 `templates/case-template.md`
2. 在 `cases/system/` 下创建 `<scenario>.yaml`
3. `le.sh run --suite <path> ...`

无需写任何 Python 代码。

## 断言类型

| type | 用途 |
|------|------|
| `contains` | 输出包含文本 |
| `regex` | 输出匹配正则 |
| `equals` | 输出完全等于 |
| `prompt_visible` | shell prompt 可见 |
| `not_contains` | 输出不包含文本 |
| `exit_code_zero` | 退出码为 0 |

## 遗留点

1. **gen-cases / deploy 未实现**：第二步实现 AI 用例生成和 binary/image 部署
2. **loop_ctrl 未实现**：第三步实现循环控制（N=5 / 回归检测 / 升级人工）
3. **参数化用例**：case_loader 预留 parameters 字段，第一步未实现展开
```

- [ ] **Step 3: 检查并更新其他引用 loop 的 README**

```bash
rg "loop_boot_failure_debug|boot_failure_debug|workflows/boot-failure" \
   engineering/loop/ --files-with-matches -g "README.md"
```
逐个检查命中文件并更新。

- [ ] **Step 4: 提交**

```bash
git add engineering/loop/README.md engineering/loop/WORKFLOW.md
git commit -m "文档(loop): README + WORKFLOW 重新生成（v2 架构）

README: v2 架构说明 + 目录结构 + 快速开始 + 测试命令。
WORKFLOW: v2 流程 + core 模块清单 + 扩展指南。

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

### Task F2: 最终联合回归 + 清理

- [ ] **Step 1: 清理构建产物**

```bash
find engineering/loop -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
find engineering/loop -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
```

- [ ] **Step 2: 全量联合回归**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib 2>&1 | tail -30
```
Expected: 全部 passed，零 failure

- [ ] **Step 3: 验证零 v1 残留**

```bash
rg "LoopAttempt|RuleMatch|ActionRecord|RULE_PRIORITY|plan_actions|execute_action|BootFailureRunner|BootFailureConfig|KernelPanicDetected|RebootLoopDetected|loop_boot_failure_debug" \
   engineering/loop/ --files-with-matches
```
Expected: 无输出（零 v1 残留）

- [ ] **Step 4: 最终提交（如果有清理改动）**

```bash
git add -A engineering/loop/
git status
# 如果有改动
git commit -m "清理(loop): 移除构建产物 + v1 残留引用

Refs: docs/specs/2026-06-19-loop-engineering-v2-design.md"
```

---

## 验收标准（全部 Phase 完成后）

- [ ] core 全量测试通过（assertion_engine / case_loader / executor / evidence / runner / models / report / transport / observer / cycles / config）
- [ ] provider 测试通过（rp5-serial 全部）
- [ ] `le.sh run` fixture 模式端到端跑通
- [ ] EvidenceBundle JSON 格式正确
- [ ] workflows/ 目录完全移除
- [ ] rules.py / actions.py 完全删除
- [ ] LoopAttempt / RuleMatch / ActionRecord 零残留
- [ ] README.md / WORKFLOW.md 反映 v2 架构
- [ ] device profile 简化（无 v1 markers）
