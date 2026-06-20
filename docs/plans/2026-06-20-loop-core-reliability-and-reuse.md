# Loop Core 可靠性与规则复用增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 先把 `engineering/loop` 的执行结果做成可信输出，再在此基础上补齐 FQN、参数化原子用例、公共 collector 库与 AI 闭环预留契约。

**Architecture:** 本计划分两段推进。第一段先收紧执行内核契约：loader 静态校验、transport 输出边界、executor 异常收敛、EvidenceBundle 增强、CLI/config 贯通。第二段再建设规则复用层：FQN 命名、参数化原子用例、公共 collector 库、模板与文档同步，最后补 `gen-cases` / `deploy` / `loop_ctrl` 的接口预留说明。

**Tech Stack:** Python 3、pytest、YAML、rp5-serial provider、argparse、JSON artifacts

---

## 文件结构与职责映射

### 核心修改文件
- Modify: `engineering/loop/core/python/loop_core/case_loader.py`
  - 增加静态校验、FQN 解析、参数展开、suite 默认配置解析。
- Modify: `engineering/loop/core/python/loop_core/models.py`
  - 扩展 `TestCaseResult` / `CollectorResult` / `EvidenceBundle` 字段，承载 warning / error / execution_config / profile summary。
- Modify: `engineering/loop/core/python/loop_core/transport.py`
  - 重定义 transport 契约，增加 output boundary / command capture 语义；升级 `FixtureTransport`。
- Modify: `engineering/loop/core/python/loop_core/executor.py`
  - 改为消费结构化 capture 结果，增加 error 收敛、critical skip/error 语义、collector error 降级。
- Modify: `engineering/loop/core/python/loop_core/collector.py`
  - collector 只采自己的输出边界，记录 error / degraded / artifact paths。
- Modify: `engineering/loop/core/python/loop_core/runner.py`
  - 注入 execution config / device profile summary，并在顶层兜底产出 failure bundle。
- Modify: `engineering/loop/core/python/loop_core/cli.py`
  - 透传 suite 默认 / case 覆盖 / CLI fallback，增加稳定的运行参数入口。
- Modify: `engineering/loop/core/python/loop_core/config.py`
  - 收窄 v1/workflow 遗留，补 profile 默认执行参数。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py`
  - live provider 对齐新的 output boundary 契约，修复重复日志去重与时间戳语义。
- Modify: `engineering/loop/templates/case-template.md`
  - 升级为 FQN + 参数化模板 + collector 引用规则。
- Modify: `engineering/loop/README.md`
  - 更新执行参数、bundle 字段、通用规则扩展方式。
- Modify: `engineering/loop/WORKFLOW.md`
  - 更新 P0/P1 后的架构与扩展流程。

### 新增测试文件
- Create: `engineering/loop/core/python/tests/test_collector.py`
  - collector 单测：命令边界、异常降级、partial evidence。
- Create: `engineering/loop/core/python/tests/test_cli.py`
  - CLI 单测：参数透传、异常兜底、非 PASS 退出码。

### 重点修改测试文件
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`
  - 从“缺失 requires 可加载”迁为 fail-fast；新增 FQN、参数化、collector 校验用例。
- Modify: `engineering/loop/core/python/tests/test_executor.py`
  - 新增 runtime error、critical skip/error、warning/evidence 行为测试。
- Modify: `engineering/loop/core/python/tests/test_runner.py`
  - 新增 failure bundle 兜底与 profile summary / execution_config 测试。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py`
  - 新增 output boundary、合法重复日志保留、相对时间戳语义测试。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py`
  - 如需要，为 provider 契约补最小协助测试。

### 回归命令
- Core tests:
  ```bash
  PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
    python3 -m pytest engineering/loop/core/python/tests/ -v --import-mode=importlib
  ```
- Provider tests:
  ```bash
  PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
    python3 -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/ -v --import-mode=importlib
  ```
- Full regression:
  ```bash
  PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
    python3 -m pytest \
    engineering/loop/core/python/tests/ \
    engineering/loop/connection/providers/rp5-serial/python/tests/ \
    -v --import-mode=importlib
  ```

---

### Task 1: Loader 静态校验与 suite 元信息收紧

**Files:**
- Modify: `engineering/loop/core/python/loop_core/case_loader.py`
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`
- Test: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 先写失败测试，锁定 fail-fast 行为**

```python
def test_requires_nonexistent_raises_at_load_time(tmp_path):
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


def test_invalid_severity_raises(tmp_path):
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
```

- [ ] **Step 2: 运行 loader 测试，确认新增用例先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py -v --import-mode=importlib
```

Expected:
- 现有 `test_requires_nonexistent_warns_but_loads` 失败或需要改写
- 新测试因 loader 尚未 fail-fast 而失败

- [ ] **Step 3: 在 `case_loader.py` 增加静态校验入口与 suite 默认配置结构**

```python
@dataclass
class SuiteDefaults:
    capture_timeout: float | None = None
    recent_limit: int | None = None


@dataclass
class CaseSuite:
    name: str
    version: int
    cases: list[TestCase]
    collectors: dict[str, dict]
    defaults: SuiteDefaults = field(default_factory=SuiteDefaults)
    warnings: list[str] = field(default_factory=list)


def _validate_case_definition(defn: dict) -> None:
    required_keys = {"id", "assert"}
    missing = required_keys - set(defn)
    if missing:
        raise ValueError(f"case missing required keys: {sorted(missing)}")
    severity = defn.get("severity", "critical")
    if severity not in {"critical", "warn"}:
        raise ValueError(f"invalid severity: {severity}")
```

- [ ] **Step 4: 实现 `requires` / collector / assert 参数校验**

```python
def _validate_case_links(cases: list[TestCase], collectors: dict[str, dict]) -> None:
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
    atype = assert_spec.get("type")
    if atype in {"contains", "equals", "not_contains"} and "value" not in assert_spec:
        raise ValueError(f"assert type '{atype}' requires value")
    if atype == "regex" and "pattern" not in assert_spec:
        raise ValueError("assert type 'regex' requires pattern")
    if atype not in {"contains", "regex", "equals", "prompt_visible", "not_contains", "exit_code_zero"}:
        raise ValueError(f"unknown assertion type: {atype}")
```

- [ ] **Step 5: 更新现有测试断言，使 fail-fast 成为新基线**

```python
def test_requires_nonexistent_raises_at_load_time(tmp_path):
    ...


def test_include_duplicate_case_id_raises(tmp_path):
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
```

- [ ] **Step 6: 运行 loader 测试，确认全部通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py -v --import-mode=importlib
```

Expected:
- PASS

- [ ] **Step 7: Commit**

```bash
git add engineering/loop/core/python/loop_core/case_loader.py \
  engineering/loop/core/python/tests/test_case_loader.py
git commit -m "重构(loop-core): loader 静态校验与 suite defaults"
```

---

### Task 2: BaseTransport 契约升级与 FixtureTransport 游标化

**Files:**
- Modify: `engineering/loop/core/python/loop_core/transport.py`
- Modify: `engineering/loop/core/python/tests/test_executor.py`
- Test: `engineering/loop/core/python/tests/test_executor.py`

- [ ] **Step 1: 先写失败测试，锁定“每条命令只看到自己的输出”**

```python
def test_fixture_transport_capture_isolated_per_command(tmp_path):
    suite_yaml = """
suite: t
version: 1
cases:
  - id: first
    command: "cmd1"
    assert: {type: contains, value: "first_only"}
  - id: second
    command: "cmd2"
    assert: {type: contains, value: "second_only"}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = FixtureTransport([
        {"t": 0.1, "text": "first_only"},
        {"t": 0.2, "text": "console:/ $"},
        {"t": 0.3, "text": "second_only"},
        {"t": 0.4, "text": "console:/ $"},
    ])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=["console:/ $"]
    )
    assert bundle.cases[0].output == "first_only\nconsole:/ $"
    assert bundle.cases[1].output == "second_only\nconsole:/ $"
```

- [ ] **Step 2: 运行 executor 测试，确认旧实现失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_executor.py -v --import-mode=importlib
```

Expected:
- 新测试失败，第二条 case 读到第一条输出或重复前缀

- [ ] **Step 3: 在 `BaseTransport` 中引入 boundary/capture 结构**

```python
@dataclass
class CommandCapture:
    lines: list[ObservedLine]
    prompt_visible: bool = False
    exit_code: int | None = None
    warnings: list[str] = field(default_factory=list)


class BaseTransport(ABC):
    @abstractmethod
    def mark_output_boundary(self) -> object:
        """返回一个可用于后续 capture 的边界游标。"""

    @abstractmethod
    def capture_since(
        self,
        boundary: object,
        timeout_sec: float,
        recent_limit: int,
        prompt_markers: list[str] | None = None,
    ) -> CommandCapture:
        """仅返回 boundary 之后的输出。"""
```

- [ ] **Step 4: 升级 `FixtureTransport`，为每次 send/capture 维护消费指针**

```python
class FixtureTransport(BaseTransport):
    def __init__(self, rows: list[dict]) -> None:
        self._rows = rows
        self._cursor = 0
        self._writer_held = False

    def mark_output_boundary(self) -> int:
        return self._cursor

    def capture_since(self, boundary: int, timeout_sec: float, recent_limit: int,
                      prompt_markers: list[str] | None = None) -> CommandCapture:
        prompt_markers = prompt_markers or []
        selected = self._rows[boundary:]
        self._cursor = len(self._rows)
        lines = [ObservedLine(t=row["t"], text=row["text"]) for row in selected]
        if recent_limit > 0 and len(lines) > recent_limit:
            lines = lines[:recent_limit]
        prompt_visible = any(
            any(marker in line.text for marker in prompt_markers)
            for line in lines
        )
        return CommandCapture(lines=lines, prompt_visible=prompt_visible)
```

- [ ] **Step 5: 调整 executor 测试辅助断言，面向新契约**

```python
assert bundle.cases[0].output_preview.startswith("first_only")
assert bundle.cases[1].output_preview.startswith("second_only")
```

- [ ] **Step 6: 运行 executor + runner 相关测试**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/test_executor.py \
  engineering/loop/core/python/tests/test_runner.py \
  -v --import-mode=importlib
```

Expected:
- PASS

- [ ] **Step 7: Commit**

```bash
git add engineering/loop/core/python/loop_core/transport.py \
  engineering/loop/core/python/tests/test_executor.py \
  engineering/loop/core/python/tests/test_runner.py
git commit -m "重构(loop-core): transport 边界采集契约"
```

---

### Task 3: Rp5SerialTransport 对齐边界契约并修复重复日志/时间戳语义

**Files:**
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py`
- Test: `engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py`

- [ ] **Step 1: 先写 provider 侧失败测试**

```python
def test_capture_since_preserves_duplicate_lines():
    client = MagicMock()
    client.capture_recent_lines.return_value = ["line_a", "repeat"]
    client.read_until_timeout.return_value = ["repeat", "repeat", "line_c"]
    transport = Rp5SerialTransport(client)
    boundary = transport.mark_output_boundary()
    capture = transport.capture_since(boundary, timeout_sec=5, recent_limit=100)
    assert [line.text for line in capture.lines] == ["line_a", "repeat", "repeat", "line_c"]


def test_capture_since_uses_relative_timestamps():
    client = MagicMock()
    client.capture_recent_lines.return_value = []
    client.read_until_timeout.return_value = ["one", "two"]
    transport = Rp5SerialTransport(client)
    capture = transport.capture_since(transport.mark_output_boundary(), 5, 100)
    assert capture.lines[0].t == 0.0
    assert capture.lines[1].t > capture.lines[0].t
```

- [ ] **Step 2: 运行 provider transport 测试，确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py -v --import-mode=importlib
```

Expected:
- 旧实现因为全局去重和绝对时间戳导致新测试失败

- [ ] **Step 3: 实现 `mark_output_boundary()` 与 `capture_since()`**

```python
class Rp5SerialTransport(BaseTransport):
    def __init__(self, client) -> None:
        self.client = client
        self._capture_generation = 0

    def mark_output_boundary(self) -> int:
        self._capture_generation += 1
        return self._capture_generation

    def capture_since(self, boundary: int, timeout_sec: float, recent_limit: int,
                      prompt_markers: list[str] | None = None) -> CommandCapture:
        recent_raw = self.client.capture_recent_lines(recent_limit)
        pushed_raw = self.client.read_until_timeout(timeout_sec)
        merged = self._merge_boundary_overlap(recent_raw, pushed_raw)
        lines = [ObservedLine(t=i * 0.01, text=text) for i, text in enumerate(merged)]
        prompt_markers = prompt_markers or []
        prompt_visible = any(any(marker in line.text for marker in prompt_markers) for line in lines)
        return CommandCapture(lines=lines, prompt_visible=prompt_visible)
```

- [ ] **Step 4: 只做边界重叠裁剪，不做全局文本去重**

```python
def _merge_boundary_overlap(self, recent_raw: list[str], pushed_raw: list[str]) -> list[str]:
    max_overlap = min(len(recent_raw), len(pushed_raw))
    overlap = 0
    for size in range(max_overlap, 0, -1):
        if recent_raw[-size:] == pushed_raw[:size]:
            overlap = size
            break
    return recent_raw + pushed_raw[overlap:]
```

- [ ] **Step 5: 运行 provider transport 测试**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py -v --import-mode=importlib
```

Expected:
- PASS

- [ ] **Step 6: Commit**

```bash
git add engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py
git commit -m "修复(rp5-serial): 输出边界采集与日志保真"
```

---

### Task 4: Executor 异常收敛、critical 语义与 overall 规则收紧

**Files:**
- Modify: `engineering/loop/core/python/loop_core/executor.py`
- Modify: `engineering/loop/core/python/tests/test_executor.py`
- Test: `engineering/loop/core/python/tests/test_executor.py`

- [ ] **Step 1: 先写失败测试，覆盖 runtime error 与 critical skip/error**

```python
def test_transport_send_error_becomes_case_error(tmp_path):
    suite = load_suite(_write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: c1
    command: "boom"
    assert: {type: contains, value: "ok"}
"""), [str(tmp_path)])

    class BrokenTransport(FixtureTransport):
        def send_line(self, text: str) -> None:
            raise OSError("send failed")

    transport = BrokenTransport([])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(suite, device_id="rp5")
    assert bundle.cases[0].status == "error"
    assert bundle.summary["overall"] == "FAIL"


def test_critical_skipped_case_makes_suite_non_pass(tmp_path):
    ...
    assert bundle.cases[1].status == "skipped"
    assert bundle.summary["overall"] != "PASS"
```

- [ ] **Step 2: 运行 executor 测试，确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_executor.py -v --import-mode=importlib
```

Expected:
- runtime error 直接抛出异常，测试失败
- warn/critical skip 语义与现状不一致，测试失败

- [ ] **Step 3: 为 `TestCaseResult` 引入 `error_type` / `dependency_status` 字段，并在 executor 使用**

```python
ctx = AssertionContext(
    output=output_text,
    prompt_visible=capture.prompt_visible,
    exit_code=capture.exit_code,
)
```

```python
except OSError as exc:
    return TestCaseResult(
        id=case.id,
        suite=case.suite,
        status="error",
        command=case.command,
        failure_reason=str(exc),
        error_type="transport_error",
        tags=case.tags,
    )
```

- [ ] **Step 4: 收紧 dependency 与 overall 统计逻辑**

```python
critical_incomplete = sum(
    1
    for result, case in zip(case_list, suite.cases)
    if case.severity == "critical" and result.status in {"fail", "skipped", "error"}
)
overall = "PASS" if critical_incomplete == 0 else "FAIL"
```

- [ ] **Step 5: 将 collector 缺失/异常降级为 evidence warning，而不是静默忽略**

```python
try:
    evidence[cname] = collector_runner.run(...)
except OSError as exc:
    warnings.append(f"collector '{cname}' failed: {exc}")
```

- [ ] **Step 6: 运行 executor 测试**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_executor.py -v --import-mode=importlib
```

Expected:
- PASS

- [ ] **Step 7: Commit**

```bash
git add engineering/loop/core/python/loop_core/executor.py \
  engineering/loop/core/python/tests/test_executor.py \
  engineering/loop/core/python/loop_core/models.py
git commit -m "修复(loop-core): executor 异常收敛与 critical 语义"
```

---

### Task 5: Collector 可靠性增强与独立单测补齐

**Files:**
- Modify: `engineering/loop/core/python/loop_core/collector.py`
- Create: `engineering/loop/core/python/tests/test_collector.py`
- Modify: `engineering/loop/core/python/loop_core/models.py`
- Test: `engineering/loop/core/python/tests/test_collector.py`

- [ ] **Step 1: 创建 collector 单测文件，先写失败测试**

```python
from loop_core.collector import Collector
from loop_core.transport import FixtureTransport


def test_collector_capture_isolated_per_command():
    transport = FixtureTransport([
        {"t": 0.1, "text": "dmesg line 1"},
        {"t": 0.2, "text": "console:/ $"},
        {"t": 0.3, "text": "logcat line 1"},
        {"t": 0.4, "text": "console:/ $"},
    ])
    transport.acquire_writer()
    result = Collector(transport).run(
        "debug",
        {"commands": ["dmesg", "logcat -d"], "hints": "check logs"},
        capture_timeout=5.0,
        recent_limit=100,
    )
    assert result.outputs[0]["lines"] == ["dmesg line 1", "console:/ $"]
    assert result.outputs[1]["lines"] == ["logcat line 1", "console:/ $"]


def test_collector_error_is_reported_as_degraded():
    ...
```

- [ ] **Step 2: 运行 collector 测试，确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_collector.py -v --import-mode=importlib
```

Expected:
- 文件不存在或测试失败

- [ ] **Step 3: 扩展 `CollectorResult` 字段并让 collector 返回结构化状态**

```python
@dataclass
class CollectorResult:
    name: str
    commands: list[str]
    outputs: list[dict]
    hints: str = ""
    status: str = "ok"
    partial: bool = False
    error: str = ""
    artifact_paths: list[str] = field(default_factory=list)
```

```python
for cmd in commands:
    boundary = self.transport.mark_output_boundary()
    try:
        self.transport.send_line(cmd)
        capture = self.transport.capture_since(boundary, capture_timeout, recent_limit)
    except OSError as exc:
        outputs.append({"command": cmd, "lines": [], "duration_sec": 0.0, "error": str(exc)})
        status = "degraded"
        partial = True
        continue
```

- [ ] **Step 4: 运行 collector 测试**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_collector.py -v --import-mode=importlib
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/core/python/loop_core/collector.py \
  engineering/loop/core/python/loop_core/models.py \
  engineering/loop/core/python/tests/test_collector.py
git commit -m "新增(loop-core): collector 降级状态与独立测试"
```

---

### Task 6: Runner / Evidence / CLI / Config 贯通执行上下文与兜底行为

**Files:**
- Modify: `engineering/loop/core/python/loop_core/models.py`
- Modify: `engineering/loop/core/python/loop_core/evidence.py`
- Modify: `engineering/loop/core/python/loop_core/runner.py`
- Modify: `engineering/loop/core/python/loop_core/cli.py`
- Modify: `engineering/loop/core/python/loop_core/config.py`
- Create: `engineering/loop/core/python/tests/test_cli.py`
- Modify: `engineering/loop/core/python/tests/test_runner.py`
- Test: `engineering/loop/core/python/tests/test_runner.py`
- Test: `engineering/loop/core/python/tests/test_cli.py`

- [ ] **Step 1: 先写 runner/cli 失败测试**

```python
def test_runner_failure_bundle_contains_profile_summary(tmp_path):
    ...
    bundle = runner.run()
    assert bundle.device_profile["device_id"] == "rp5"
    assert bundle.summary["overall"] == "FAIL"


def test_cli_returns_nonzero_and_writes_bundle_on_runtime_failure(tmp_path, monkeypatch):
    ...
    rc = main([...])
    assert rc == 1
    assert (artifacts_dir / "evidence_bundle.json").exists()
```

- [ ] **Step 2: 运行 runner/cli 测试，确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/test_runner.py \
  engineering/loop/core/python/tests/test_cli.py \
  -v --import-mode=importlib
```

Expected:
- `test_cli.py` 文件不存在或失败
- runner failure bundle 不带 profile / execution config

- [ ] **Step 3: 收窄 `config.py` 并补运行默认值字段**

```python
@dataclass
class DeviceProfile:
    device_id: str = ""
    transport: str = "serial"
    prompt_markers: list[str] = field(default_factory=list)
    line_ending: str = "\n"
    default_capture_timeout: float = 5.0
    default_recent_limit: int = 400
```

- [ ] **Step 4: 在 runner/evidence 中注入 profile summary 与 execution config**

```python
return EvidenceBundle(
    ...,
    device_profile={
        "device_id": self.device_id,
        "prompt_markers": self.prompt_markers,
    },
    execution_config={
        "capture_timeout": self.capture_timeout,
        "recent_limit": self.recent_limit,
        "provider_type": type(self.transport).__name__,
    },
    warnings=warnings,
)
```

- [ ] **Step 5: 在 CLI 中增加 fallback 参数并兜底落盘**

```python
run_parser.add_argument("--capture-timeout", type=float)
run_parser.add_argument("--recent-limit", type=int)
```

```python
capture_timeout = args.capture_timeout or profile.default_capture_timeout
recent_limit = args.recent_limit or profile.default_recent_limit
try:
    bundle = runner.run()
except Exception as exc:
    bundle = runner.build_runtime_failure_bundle(str(exc))
paths = write_evidence_bundle(bundle, args.artifacts_dir)
```

- [ ] **Step 6: 运行 runner/cli 测试**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/test_runner.py \
  engineering/loop/core/python/tests/test_cli.py \
  -v --import-mode=importlib
```

Expected:
- PASS

- [ ] **Step 7: Commit**

```bash
git add engineering/loop/core/python/loop_core/models.py \
  engineering/loop/core/python/loop_core/evidence.py \
  engineering/loop/core/python/loop_core/runner.py \
  engineering/loop/core/python/loop_core/cli.py \
  engineering/loop/core/python/loop_core/config.py \
  engineering/loop/core/python/tests/test_runner.py \
  engineering/loop/core/python/tests/test_cli.py
git commit -m "重构(loop-core): bundle 上下文与 CLI 兜底贯通"
```

---

### Task 7: FQN 命名模型落地

**Files:**
- Modify: `engineering/loop/core/python/loop_core/case_loader.py`
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`
- Modify: `engineering/loop/cases/common/shell.yaml`
- Modify: `engineering/loop/cases/system/boot-success.yaml`
- Test: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 先写 FQN 解析失败测试**

```python
def test_short_requires_resolves_within_same_suite(tmp_path):
    ...
    suite = load_suite(path, [str(tmp_path)])
    assert suite.cases[1].requires == ["system.boot.shell_ready"]


def test_cross_suite_requires_must_use_fqn(tmp_path):
    ...
    with pytest.raises(ValueError, match="cross-suite requires must use FQN"):
        load_suite(path, [str(tmp_path)])
```

- [ ] **Step 2: 运行 loader 测试，确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py -v --import-mode=importlib
```

Expected:
- FQN 解析测试失败

- [ ] **Step 3: 为 `TestCase` 增加 `fqn` 字段，并在 loader 中统一解析**

```python
@dataclass
class TestCase:
    id: str
    suite: str
    fqn: str
    command: str
    ...
```

```python
def _case_fqn(namespace: str, case_id: str) -> str:
    return f"{namespace}.{case_id}"
```

- [ ] **Step 4: 更新现有 suite YAML，显式引入可读 namespace 约定**

```yaml
suite: common.shell
version: 1
cases:
  - id: shell_reachable
    command: ""
    assert:
      type: prompt_visible
```
```

```yaml
suite: system.boot
version: 1
include:
  - common/shell
cases:
  - id: boot_completed
    requires: [common.shell.shell_reachable]
```
```

- [ ] **Step 5: 运行 loader 与 executor 回归测试**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py \
  engineering/loop/core/python/tests/test_executor.py \
  -v --import-mode=importlib
```

Expected:
- PASS

- [ ] **Step 6: Commit**

```bash
git add engineering/loop/core/python/loop_core/case_loader.py \
  engineering/loop/core/python/tests/test_case_loader.py \
  engineering/loop/cases/common/shell.yaml \
  engineering/loop/cases/system/boot-success.yaml
git commit -m "新增(loop-core): FQN 命名与引用解析"
```

---

### Task 8: 参数化原子用例与 suite 默认配置落地

**Files:**
- Modify: `engineering/loop/core/python/loop_core/case_loader.py`
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`
- Modify: `engineering/loop/templates/case-template.md`
- Test: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 先写参数化展开失败测试**

```python
def test_parameterized_case_expands_into_multiple_cases(tmp_path):
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
    ids = [case.id for case in suite.cases]
    assert ids == ["service_running_zygote", "service_running_surfaceflinger"]
```

- [ ] **Step 2: 运行 loader 测试，确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py -v --import-mode=importlib
```

Expected:
- 参数化测试失败，loader 尚未展开

- [ ] **Step 3: 在 loader 中实现最小参数展开能力**

```python
def _expand_parameterized_cases(raw_cases: list[dict], parameters: dict) -> list[dict]:
    expanded: list[dict] = []
    for case in raw_cases:
        foreach = case.get("foreach")
        if not foreach:
            expanded.append(case)
            continue
        values = parameters.get(foreach, [])
        for item in values:
            cloned = copy.deepcopy(case)
            cloned["id"] = f"{case['id']}_{item}"
            cloned["command"] = cloned.get("command", "").replace("${item}", str(item))
            expanded.append(cloned)
    return expanded
```

- [ ] **Step 4: 为 suite 增加 defaults 字段解析**

```yaml
defaults:
  capture_timeout: 8.0
  recent_limit: 600
```

```python
def _parse_defaults(raw: dict) -> SuiteDefaults:
    defaults = raw.get("defaults", {})
    return SuiteDefaults(
        capture_timeout=defaults.get("capture_timeout"),
        recent_limit=defaults.get("recent_limit"),
    )
```

- [ ] **Step 5: 更新模板文档，明确参数化 schema**

```markdown
parameters:
  services: [zygote, surfaceflinger]

cases:
  - id: service_running
    foreach: services
    command: "getprop init.svc.${item}"
    assert: {type: contains, value: "running"}
```

- [ ] **Step 6: 运行 loader 测试**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py -v --import-mode=importlib
```

Expected:
- PASS

- [ ] **Step 7: Commit**

```bash
git add engineering/loop/core/python/loop_core/case_loader.py \
  engineering/loop/core/python/tests/test_case_loader.py \
  engineering/loop/templates/case-template.md
git commit -m "新增(loop-core): 参数化原子用例与 suite defaults"
```

---

### Task 9: 公共 collector 库与文档同步

**Files:**
- Modify: `engineering/loop/cases/common/shell.yaml`
- Modify: `engineering/loop/cases/system/boot-success.yaml`
- Modify: `engineering/loop/README.md`
- Modify: `engineering/loop/WORKFLOW.md`
- Modify: `engineering/loop/templates/case-template.md`
- Test: `engineering/loop/core/python/tests/test_case_loader.py`
- Test: `engineering/loop/core/python/tests/test_executor.py`

- [ ] **Step 1: 先写/调整测试，要求公共 collector 用 FQN 或统一引用规则可被解析**

```python
def test_common_collector_reference_resolves(tmp_path):
    _write(tmp_path, "common_shell.yaml", """
suite: common.shell
version: 1
collectors:
  boot_log:
    commands: ["dmesg"]
cases:
  - id: shell_reachable
    command: ""
    assert: {type: prompt_visible}
""")
    path = _write(tmp_path, "system_boot.yaml", """
suite: system.boot
version: 1
include: [common_shell]
cases:
  - id: boot_completed
    command: "getprop sys.boot_completed"
    assert: {type: contains, value: "1"}
    on_fail:
      collectors: [common.shell.boot_log]
""")
    suite = load_suite(path, [str(tmp_path)])
    assert "common.shell.boot_log" in suite.collectors
```

- [ ] **Step 2: 更新现有用例文件，将公共 collector 提升到 `common` 侧**

```yaml
suite: common.shell
version: 1
collectors:
  boot_log:
    commands: ["dmesg"]
    hints: "关注 boot 时序 / init 阶段卡点 / kernel 错误"
  init_log:
    commands: ["getprop init.svc.*", "logcat -b system -d"]
    hints: "关注 service 重启频率 / 退出信号 / last_reason"
```
```

```yaml
suite: system.boot
version: 1
include:
  - common/shell
cases:
  - id: boot_completed
    ...
    on_fail:
      collectors: [common.shell.boot_log, common.shell.init_log]
```
```

- [ ] **Step 3: README / WORKFLOW / 模板同步更新**

```markdown
- 通用规则优先沉淀为参数化原子用例
- 通用诊断优先沉淀为 common collector 库
- critical case 未完成执行时 overall 不得为 PASS
- bundle 输出 warning / runtime_errors / execution_config
```

- [ ] **Step 4: 运行文档相关联的核心回归**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py \
  engineering/loop/core/python/tests/test_executor.py \
  engineering/loop/core/python/tests/test_runner.py \
  -v --import-mode=importlib
```

Expected:
- PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/cases/common/shell.yaml \
  engineering/loop/cases/system/boot-success.yaml \
  engineering/loop/README.md \
  engineering/loop/WORKFLOW.md \
  engineering/loop/templates/case-template.md
git commit -m "文档(loop): 公共 collector 库与规则复用说明同步"
```

---

### Task 10: 全量回归、人工验证脚本与收尾检查

**Files:**
- Modify: `docs/plans/2026-06-20-loop-core-reliability-and-reuse.md`
- Test: 全量测试与 fixture 端到端命令

- [ ] **Step 1: 运行全量 Python 回归**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib
```

Expected:
- 全绿

- [ ] **Step 2: 运行 fixture 端到端命令验证 bundle 与 summary**

Run:
```bash
bash engineering/loop/bin/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --fixture engineering/loop/core/python/tests/fixtures/boot_success.jsonl \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases/common,engineering/loop/cases/system \
  --artifacts-dir /tmp/opencode/loop-plan-verify
```

Expected:
- 命令退出码为 0
- `/tmp/opencode/loop-plan-verify/evidence_bundle.json` 存在
- bundle 中包含 execution_config / device_profile / warnings 字段（即使为空）

- [ ] **Step 3: 检查 README 同步要求是否满足**

Run:
```bash
git diff -- engineering/loop/README.md engineering/loop/WORKFLOW.md
```

Expected:
- 文档与实现一致，无遗漏的架构/用法描述

- [ ] **Step 4: 更新本计划勾选状态与验证记录**

```markdown
- [x] Full regression passed (155 tests, core + provider)
- [x] Fixture end-to-end command verified
- [x] README / WORKFLOW updated
- [x] EvidenceBundle contains execution_config / device_profile / collector status fields
```

- [ ] **Step 5: Commit**

```bash
git add docs/plans/2026-06-20-loop-core-reliability-and-reuse.md
git commit -m "docs(plan): 勾选 loop core 增强实施计划执行结果"
```

---

## 自检清单（写计划后必须核对）

### Spec coverage
- 执行可信度：Task 1 / 2 / 3 / 4 / 5 / 6 覆盖
- FQN 命名：Task 7 覆盖
- 参数化原子用例：Task 8 覆盖
- 公共 collector 库：Task 9 覆盖
- AI 闭环接口预留：Task 6 / 8 / 9 通过 schema、bundle、文档约定覆盖
- README / WORKFLOW 同步：Task 9 / 10 覆盖

### Placeholder scan
- 本计划不允许出现 “TODO / TBD / implement later / write tests for above” 这类占位表达。
- 每个 task 都给出了文件路径、测试命令、预期结果与最小代码块。

### Type consistency
- transport 新契约统一使用 `mark_output_boundary()` / `capture_since()` / `CommandCapture`
- suite 默认统一使用 `SuiteDefaults`
- bundle 增强字段统一使用 `warnings` / `runtime_errors` / `execution_config`
- critical 未完成执行统一以 `overall != PASS` 约束
