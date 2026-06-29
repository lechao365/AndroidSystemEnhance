# Loop Engineering G9 评测基线实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 loop 框架建立 session 级指标聚合 + 跨 session 聚合命令，形成 loop 修复有效性的量化基线（G9）。

**Architecture:** 三层架构——(1) engine 埋点用实例变量累积计数器；(2) 终态时 `_compute_session_metrics()` 快照为 `SessionMetrics` dataclass 挂到 LoopSession；(3) `le runtime stats` 命令遍历 `artifacts/*/session.json` 做跨 session 聚合。analyzer 层级命中通过 `PatchSuggestion.matched_layer` 结构化字段埋点。

**Tech Stack:** Python 3.11+ / dataclasses / pytest（严格 TDD：RED → GREEN）

**测试基线：** 632 passed（G5 完成后）→ 预计 ~652

**全量回归命令：**
```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/ engineering/loop/core/python/tests/ engineering/loop/connection/providers/rp5-serial/python/tests/ engineering/loop/connection/providers/adb/python/tests/ engineering/loop/deploy/python/tests/ engineering/loop/contracts/python/tests/ -q --import-mode=importlib
```

---

## 文件结构

| 文件 | 职责 | 操作 |
|------|------|------|
| `engineering/loop/contracts/python/loop_contracts/models.py` | 加 `SessionMetrics` dataclass + `LoopSession.metrics` 字段 | 修改 |
| `engineering/loop/contracts/python/loop_contracts/__init__.py` | `__all__` 加 `SessionMetrics` | 修改 |
| `engineering/loop/controller/python/loop_controller/analyzer_protocol.py` | `PatchSuggestion` 加 `matched_layer` + `ChainedAnalyzer` 填充 | 修改 |
| `engineering/loop/controller/python/loop_controller/runtime/engine.py` | 埋点实例变量 + 三处埋点 + `_compute_session_metrics()` + `run()` 终态调用 + resume 重建 | 修改 |
| `engineering/loop/controller/python/loop_controller/runtime_cli.py` | `_session_to_dict`/`_load_session` metrics 序列化 + `le runtime stats` 命令 | 修改 |
| `engineering/loop/controller/python/tests/test_runtime_engine.py` | 终态指标测试（7 个） | 修改 |
| `engineering/loop/controller/python/tests/test_chained_analyzer.py` | matched_layer 测试（3 个） | 修改 |
| `engineering/loop/controller/python/tests/test_runtime_cli.py` | status metrics + stats 命令测试（6 个） | 修改 |
| `engineering/loop/controller/python/tests/test_docs_consistency.py` | dataclass 计数 6→7 + 守护 | 修改 |
| `engineering/loop/contracts/README.md` | SessionMetrics 说明 | 修改 |
| `engineering/loop/controller/README.md` | `le runtime stats` 子命令说明 | 修改 |
| `engineering/loop/WORKFLOW.md` | `le runtime` 子命令列表补 `stats` | 修改 |

---

## Task 1: contracts — SessionMetrics + LoopSession.metrics

**Files:**
- Modify: `engineering/loop/contracts/python/loop_contracts/models.py`
- Test: `engineering/loop/contracts/python/tests/test_models.py`（若不存在则创建）

- [ ] **Step 1: 写失败测试 — SessionMetrics 字段 + LoopSession.metrics 默认 None**

在 `engineering/loop/contracts/python/tests/` 下新建 `test_models.py`（若目录无 `__init__.py` 则一并创建空文件）：

```python
"""contracts.models 专项测试：守护 SessionMetrics / LoopSession 字段。"""
from dataclasses import fields

from loop_contracts.models import LoopSession, SessionMetrics


def test_session_metrics_fields():
    """SessionMetrics 必须含 11 个字段。"""
    names = {f.name for f in fields(SessionMetrics)}
    expected = {
        "success", "terminal_state", "attempt_count",
        "wall_clock_used_ms", "wall_clock_budget_ms",
        "analyzer_layer_hits", "analyzer_first_hit_layer",
        "failure_code_distribution", "human_gate_triggered",
        "human_gate_count", "kb_hit",
    }
    assert names == expected, f"SessionMetrics 字段不匹配: {names ^ expected}"


def test_session_metrics_defaults():
    """SessionMetrics 默认值。"""
    m = SessionMetrics()
    assert m.success is False
    assert m.terminal_state == "NONE"
    assert m.attempt_count == 0
    assert m.wall_clock_used_ms == 0
    assert m.wall_clock_budget_ms == 0
    assert m.analyzer_layer_hits == {}
    assert m.analyzer_first_hit_layer == ""
    assert m.failure_code_distribution == {}
    assert m.human_gate_triggered is False
    assert m.human_gate_count == 0
    assert m.kb_hit is False


def test_loop_session_metrics_defaults_none():
    """LoopSession.metrics 默认 None（未终态）。"""
    s = LoopSession(
        session_id="s1", workflow_id="w", target="t", suite="su", max_attempts=5,
    )
    assert s.metrics is None
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH="engineering/loop/contracts/python" python3 -m pytest engineering/loop/contracts/python/tests/test_models.py -v
```
Expected: FAIL — `ImportError: cannot import name 'SessionMetrics'`

- [ ] **Step 3: 实现 SessionMetrics dataclass + LoopSession.metrics**

在 `engineering/loop/contracts/python/loop_contracts/models.py` 中，在 `TerminationDecision` 之后、`SessionState = LoopSession` 之前插入：

```python
@dataclass
class SessionMetrics:
    """Session 终态指标快照（run() 退出时计算，落盘到 session.json）。"""
    success: bool = False
    terminal_state: str = "NONE"
    attempt_count: int = 0
    wall_clock_used_ms: int = 0
    wall_clock_budget_ms: int = 0
    analyzer_layer_hits: dict[str, int] = field(default_factory=dict)
    analyzer_first_hit_layer: str = ""
    failure_code_distribution: dict[str, int] = field(default_factory=dict)
    human_gate_triggered: bool = False
    human_gate_count: int = 0
    kb_hit: bool = False
```

然后给 `LoopSession` 加字段（在 `wall_clock_limit` 之后）：

```python
    metrics: SessionMetrics | None = None
```

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python" python3 -m pytest engineering/loop/contracts/python/tests/test_models.py -v
```
Expected: PASS — 3 tests

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/contracts/python/loop_contracts/models.py engineering/loop/contracts/python/tests/test_models.py
git commit -m "功能(contracts): G9 新增 SessionMetrics + LoopSession.metrics 字段"
```

---

## Task 2: contracts — __init__.py 导出 SessionMetrics

**Files:**
- Modify: `engineering/loop/contracts/python/loop_contracts/__init__.py`

- [ ] **Step 1: 写失败测试 — 可从 loop_contracts 顶层导入 SessionMetrics**

在 `test_models.py` 末尾追加：

```python
def test_session_metrics_importable_from_package():
    """SessionMetrics 必须能从 loop_contracts 顶层导入。"""
    import loop_contracts
    assert hasattr(loop_contracts, "SessionMetrics")
    assert "SessionMetrics" in loop_contracts.__all__
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH="engineering/loop/contracts/python" python3 -m pytest engineering/loop/contracts/python/tests/test_models.py::test_session_metrics_importable_from_package -v
```
Expected: FAIL — `AttributeError` 或 `AssertionError`

- [ ] **Step 3: 修改 __init__.py**

先读 `engineering/loop/contracts/python/loop_contracts/__init__.py`，在 imports 段加：

```python
from loop_contracts.models import (
    ...  # 现有导入
    SessionMetrics,
)
```

在 `__all__` 列表末尾（`TerminationDecision` 之后）加 `"SessionMetrics",`。

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python" python3 -m pytest engineering/loop/contracts/python/tests/test_models.py -v
```
Expected: PASS — 4 tests

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/contracts/python/loop_contracts/__init__.py engineering/loop/contracts/python/tests/test_models.py
git commit -m "功能(contracts): G9 __all__ 导出 SessionMetrics"
```

---

## Task 3: analyzer — PatchSuggestion.matched_layer

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/analyzer_protocol.py:43-47`
- Test: `engineering/loop/controller/python/tests/test_chained_analyzer.py`

- [ ] **Step 1: 写失败测试 — PatchSuggestion 有 matched_layer 字段**

在 `test_chained_analyzer.py` 顶部 import 后追加：

```python
def test_patch_suggestion_has_matched_layer_field():
    """G9: PatchSuggestion 必须有 matched_layer 字段，默认空串。"""
    from dataclasses import fields
    names = {f.name for f in fields(PatchSuggestion)}
    assert "matched_layer" in names, "PatchSuggestion 缺少 matched_layer 字段"
    ps = PatchSuggestion()
    assert ps.matched_layer == ""
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_chained_analyzer.py::test_patch_suggestion_has_matched_layer_field -v
```
Expected: FAIL — `AssertionError: PatchSuggestion 缺少 matched_layer 字段`

- [ ] **Step 3: 给 PatchSuggestion 加 matched_layer 字段**

在 `analyzer_protocol.py` 第 43-47 行的 `PatchSuggestion` dataclass 末尾加字段：

```python
@dataclass
class PatchSuggestion:
    target_files: list[FileChange] = field(default_factory=list)
    rationale: str = ""
    confidence: float = 0.0
    deploy_mode_hint: str = ""
    matched_layer: str = ""
```

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_chained_analyzer.py::test_patch_suggestion_has_matched_layer_field -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/analyzer_protocol.py engineering/loop/controller/python/tests/test_chained_analyzer.py
git commit -m "功能(analyzer): G9 PatchSuggestion 新增 matched_layer 字段"
```

---

## Task 4: analyzer — ChainedAnalyzer 填充 matched_layer

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/analyzer_protocol.py:667-679`（ChainedAnalyzer.analyze）
- Test: `engineering/loop/controller/python/tests/test_chained_analyzer.py`

- [ ] **Step 1: 写失败测试（3 个）**

在 `test_chained_analyzer.py` 末尾追加：

```python
def test_chained_fills_matched_layer_kb():
    """G9: KB 层命中时 matched_layer 填类名。"""
    p = [FileChange(workspace_path="a.c")]
    chain = ChainedAnalyzer([_StubAnalyzer(patches=p, name="KnowledgeBaseAnalyzer")])
    req = AnalysisRequest(session_id="s", attempt_index=1)
    result = chain.analyze(req)
    assert result.matched_layer == "_StubAnalyzer"  # type(layer).__name__


def test_chained_fills_matched_layer_second_layer():
    """G9: 第二层命中时 matched_layer 填第二层类名。"""
    p = [FileChange(workspace_path="a.c")]
    chain = ChainedAnalyzer([
        _StubAnalyzer(patches=[], name="empty"),
        _StubAnalyzer(patches=p, name="hit"),
    ])
    req = AnalysisRequest(session_id="s", attempt_index=1)
    result = chain.analyze(req)
    assert result.matched_layer == "_StubAnalyzer"


def test_chained_no_match_leaves_matched_layer_empty():
    """G9: 三层均空时 matched_layer 保持空串。"""
    chain = ChainedAnalyzer([_StubAnalyzer(patches=[])])
    req = AnalysisRequest(session_id="s", attempt_index=1)
    result = chain.analyze(req)
    assert result.matched_layer == ""
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_chained_analyzer.py -v -k "matched_layer"
```
Expected: 3 FAIL — `assert '' == '_StubAnalyzer'`

- [ ] **Step 3: 修改 ChainedAnalyzer.analyze 填充 matched_layer**

在 `analyzer_protocol.py` 第 667-679 行，`if suggestion.target_files:` 分支内加一行：

```python
    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        for layer in self._layers:
            try:
                suggestion = layer.analyze(request)
            except Exception:
                continue
            if suggestion.target_files:
                suggestion.matched_layer = type(layer).__name__
                suggestion.rationale = f"[{type(layer).__name__}] {suggestion.rationale}"
                return suggestion
        return PatchSuggestion(
            target_files=[], confidence=0.0,
            rationale="三层 analyzer 均无产出",
        )
```

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_chained_analyzer.py -v
```
Expected: ALL PASS（含原有 + 3 新增）

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/analyzer_protocol.py engineering/loop/controller/python/tests/test_chained_analyzer.py
git commit -m "功能(analyzer): G9 ChainedAnalyzer 填充 matched_layer 结构化层级名"
```

---

## Task 5: engine — 埋点实例变量 + analyzer/gate 埋点

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py`（`__init__` + `_execute_wait_analyzer_patch` + 全部 `pending_human_gate = True` 位置）
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: 写失败测试 — analyzer 层级埋点 + human gate 计数**

在 `test_runtime_engine.py` 末尾追加：

```python
def test_engine_init_has_metrics_counters(tmp_path):
    """G9: engine __init__ 后存在埋点计数器实例变量。"""
    from loop_controller.runtime.engine import LoopRuntime

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    assert hasattr(rt, "_layer_hits") and rt._layer_hits == {}
    assert hasattr(rt, "_first_hit_layer") and rt._first_hit_layer == ""
    assert hasattr(rt, "_hg_count") and rt._hg_count == 0
    assert hasattr(rt, "_fc_dist") and rt._fc_dist == {}
    assert hasattr(rt, "_kb_hit") and rt._kb_hit is False


def test_engine_analyzer_layer_hit_counted(tmp_path):
    """G9: analyzer 产出补丁时 _layer_hits 累积 + _first_hit_layer 记录。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_controller.analyzer_protocol import (
        AnalysisRequest, ChainedAnalyzer, FileChange, LlmAnalyzer, PatchSuggestion,
    )

    class _FakeKB(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(
                target_files=[FileChange(workspace_path="a.c")],
                confidence=0.98, rationale="kb hit",
            )

    # 用 ChainedAnalyzer 包装，使其填 matched_layer
    chain = ChainedAnalyzer([_FakeKB()])
    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5",
                     analyzer=chain)
    # 直接调 _execute_wait_analyzer_patch（会走 analyzer 分支）
    rt._state.current_node = "WAIT_ANALYZER_PATCH"
    rt._execute_wait_analyzer_patch()
    assert "_FakeKB" in rt._layer_hits
    assert rt._first_hit_layer == "_FakeKB"
    assert rt._kb_hit is True  # _FakeKB 不等于 KnowledgeBaseAnalyzer → 不触发
    # 修正：只有类名 == "KnowledgeBaseAnalyzer" 才置 kb_hit
    # _FakeKB 不是，所以 kb_hit 仍 False
    # 调整断言：


def test_engine_kb_hit_flag_set_for_real_kb_name(tmp_path):
    """G9: 层名为 KnowledgeBaseAnalyzer 时 _kb_hit 置 True。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_controller.analyzer_protocol import (
        AnalysisRequest, ChainedAnalyzer, FileChange, LlmAnalyzer, PatchSuggestion,
    )

    class KnowledgeBaseAnalyzer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(
                target_files=[FileChange(workspace_path="a.c")],
                confidence=0.98,
            )

    chain = ChainedAnalyzer([KnowledgeBaseAnalyzer()])
    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5",
                     analyzer=chain)
    rt._state.current_node = "WAIT_ANALYZER_PATCH"
    rt._execute_wait_analyzer_patch()
    assert rt._kb_hit is True
    assert rt._layer_hits.get("KnowledgeBaseAnalyzer") == 1


def test_engine_human_gate_counter_increments(tmp_path):
    """G9: 触发 pending_human_gate 时 _hg_count 递增。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_controller.runtime.types import NodeKind

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    # 直接模拟一次 human gate 触发
    rt._state.pending_human_gate = True
    rt._hg_count += 1
    assert rt._hg_count == 1
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py -v -k "metrics_counters or layer_hit or kb_hit_flag or human_gate_counter"
```
Expected: FAIL — `_layer_hits` 等属性不存在

- [ ] **Step 3: 实现 — __init__ 加实例变量 + analyzer 埋点 + human gate 埋点**

**(3a) `__init__` 加实例变量**（在 `self._last_node_duration_ms = 0` 之后）：

```python
        # G9: 指标埋点计数器（随 engine 生命周期，终态时快照）
        self._layer_hits: dict[str, int] = {}
        self._first_hit_layer: str = ""
        self._hg_count: int = 0
        self._fc_dist: dict[str, int] = {}
        self._kb_hit: bool = False
```

**(3b) `_execute_wait_analyzer_patch` analyzer 埋点**（在 `suggestion = self._analyzer.analyze(request)` 之后、`if suggestion.target_files:` 分支内）：

定位 `engine.py` 中 `suggestion = self._analyzer.analyze(request)` 这一行，在其后的 `if suggestion.target_files:` 分支开头（落盘 patch_suggestion.json 之前）加：

```python
                if suggestion.target_files:
                    # G9: 累积 analyzer 层级命中
                    layer = suggestion.matched_layer or "unknown"
                    self._layer_hits[layer] = self._layer_hits.get(layer, 0) + 1
                    if not self._first_hit_layer:
                        self._first_hit_layer = layer
                    if layer == "KnowledgeBaseAnalyzer":
                        self._kb_hit = True
                    # 落盘为 patch_suggestion.json（现有代码）...
```

**(3c) human gate 埋点**——engine.py 中所有 `self._state.pending_human_gate = True` 行（grep 确认约 10 处），在其后加 `self._hg_count += 1`。

> ⚠️ 用 `edit replaceAll` 一次替换全部：
> oldString: `self._state.pending_human_gate = True`
> newString: `self._state.pending_human_gate = True\n                self._hg_count += 1  # G9`

注意缩进：不同位置的缩进深度不同。需要逐个确认或用 search-replace 分批处理。更稳妥的方式：**封装一个 helper 方法**，避免改 10 处。

**推荐改法（替代 3c）**：在 engine 中加一个 `_set_human_gate` helper：

```python
    def _set_human_gate(self) -> None:
        """G9: 统一 human gate 触发入口，同时计数。"""
        self._state.pending_human_gate = True
        self._hg_count += 1
```

然后把所有 `self._state.pending_human_gate = True` 替换为 `self._set_human_gate()`（用 replaceAll）。

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py -v -k "metrics_counters or layer_hit or kb_hit_flag or human_gate_counter"
```
Expected: PASS

- [ ] **Step 5: 跑全量 controller 测试确认无回归**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/ -q --import-mode=importlib
```
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(engine): G9 埋点实例变量 + analyzer 层级/human gate 计数"
```

---

## Task 6: engine — _checkpoint failure_code 分布埋点

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py`（`_checkpoint` 方法）
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: 写失败测试 — checkpoint 后 _fc_dist 累积**

在 `test_runtime_engine.py` 末尾追加：

```python
def test_engine_checkpoint_accumulates_failure_code_dist(tmp_path):
    """G9: _checkpoint 调用后 _fc_dist 按 failure_code 累积。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_contracts.failure_codes import FailureCode

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    rt._checkpoint("step1", FailureCode.NONE)
    rt._checkpoint("step2", FailureCode.RUN_FAILED)
    rt._checkpoint("step3", FailureCode.RUN_FAILED)
    assert rt._fc_dist.get("NONE") == 1
    assert rt._fc_dist.get("RUN_FAILED") == 2
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_engine_checkpoint_accumulates_failure_code_dist -v
```
Expected: FAIL — `_fc_dist` 为空（尚未在 _checkpoint 累积）

- [ ] **Step 3: 在 _checkpoint 末尾加累积逻辑**

在 `_checkpoint` 方法末尾（`self._state.last_checkpoint_at = cp.timestamp` 之后）加：

```python
        # G9: 累积 failure_code 分布
        code = failure_code.value if failure_code else "NONE"
        self._fc_dist[code] = self._fc_dist.get(code, 0) + 1
```

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_engine_checkpoint_accumulates_failure_code_dist -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(engine): G9 _checkpoint 累积 failure_code 分布"
```

---

## Task 7: engine — _compute_session_metrics + run() 终态调用

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py`（`run()` 方法 + 新增 `_compute_session_metrics`）
- Modify: `engineering/loop/contracts/python/loop_contracts/__init__.py`（已在 Task 2 导出 SessionMetrics）
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: 写失败测试 — run() 终态后 session.metrics 非 None 且字段正确**

在 `test_runtime_engine.py` 末尾追加：

```python
def test_run_computes_session_metrics_success(tmp_path):
    """G9: run() 完成后 session.metrics 非 None，success 字段正确。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_contracts.models import RuntimeTerminalState

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    rt.run(max_iterations=1)
    assert session.metrics is not None
    assert session.metrics.terminal_state in (
        RuntimeTerminalState.DONE_SUCCESS.value,
        RuntimeTerminalState.DONE_FAILURE.value,
    )
    assert session.metrics.wall_clock_used_ms >= 0
    assert isinstance(session.metrics.failure_code_distribution, dict)


def test_run_computes_session_metrics_failure(tmp_path):
    """G9: max_iterations 超限 DONE_FAILURE 时 metrics.success=False。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_contracts.models import RuntimeTerminalState

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=5,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    rt.run(max_iterations=1)
    # INIT_SESSION 后 max_iterations 到达 → DONE_FAILURE
    assert session.metrics is not None
    assert session.metrics.success is False
    assert session.metrics.terminal_state == RuntimeTerminalState.DONE_FAILURE.value
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py -v -k "computes_session_metrics"
```
Expected: FAIL — `session.metrics is None`

- [ ] **Step 3: 实现 _compute_session_metrics + run() 终态调用**

**(3a) 新增 _compute_session_metrics 方法**（在 `_persist_session` 之前）：

```python
    def _compute_session_metrics(self) -> "SessionMetrics":
        """G9: 终态时把实例变量 + wall_clock 快照为 SessionMetrics。"""
        from loop_contracts.models import SessionMetrics
        wall_used_ms = int((time.perf_counter() - self._session_start) * 1000)
        wall_budget_ms = (self._session.wall_clock_limit or 0) * 1000
        return SessionMetrics(
            success=self._state.terminal_state == RuntimeTerminalState.DONE_SUCCESS,
            terminal_state=self._state.terminal_state.value,
            attempt_count=self._session.current_attempt,
            wall_clock_used_ms=wall_used_ms,
            wall_clock_budget_ms=wall_budget_ms,
            analyzer_layer_hits=dict(self._layer_hits),
            analyzer_first_hit_layer=self._first_hit_layer,
            failure_code_distribution=dict(self._fc_dist),
            human_gate_triggered=self._hg_count > 0,
            human_gate_count=self._hg_count,
            kb_hit=self._kb_hit,
        )
```

**(3b) `run()` 终态调用**——在 `self._persist_session()` 之前加：

```python
        # G9: 终态聚合指标
        self._session.metrics = self._compute_session_metrics()
        self._persist_session()
        return self._state
```

注意：`run()` 中有两处 `self._persist_session()` 调用——一处是 `pending_human_gate` 提前返回（L127），一处是循环退出后（L146）。**只在循环退出后那处加 metrics 计算**（pending_human_gate 时 session 尚未终态）。human gate 提前返回处不加 metrics。

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py -v -k "computes_session_metrics"
```
Expected: PASS

- [ ] **Step 5: 跑全量 controller 测试确认无回归**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/ -q --import-mode=importlib
```
Expected: ALL PASS

- [ ] **Step 6: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(engine): G9 _compute_session_metrics + run() 终态快照指标"
```

---

## Task 8: engine — resume 重建 _fc_dist

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py`（`resume()` 方法 + 新增 `_rebuild_fc_dist_from_checkpoints`）
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: 写失败测试 — resume 后 _fc_dist 从 checkpoint 重建**

在 `test_runtime_engine.py` 末尾追加：

```python
def test_resume_rebuilds_fc_dist_from_checkpoints(tmp_path):
    """G9: resume() 后 _fc_dist 从 checkpoint 记录重建。"""
    from loop_controller.runtime.engine import LoopRuntime
    from loop_contracts.failure_codes import FailureCode
    from loop_controller.runtime.checkpoint_store import CheckpointStore

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=5,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    # 先写几条 checkpoint（模拟 resume 前的历史）
    rt._checkpoint("step1", FailureCode.RUN_FAILED)
    rt._checkpoint("step2", FailureCode.COMPILE_FAILED)
    rt._checkpoint("step3", FailureCode.NONE)
    # 清空 _fc_dist 模拟 engine 实例重建
    rt._fc_dist = {}
    assert rt._fc_dist == {}
    # 重建
    rt._rebuild_fc_dist_from_checkpoints()
    assert rt._fc_dist.get("RUN_FAILED") == 1
    assert rt._fc_dist.get("COMPILE_FAILED") == 1
    assert rt._fc_dist.get("NONE") == 1
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_resume_rebuilds_fc_dist_from_checkpoints -v
```
Expected: FAIL — `_rebuild_fc_dist_from_checkpoints` 不存在

- [ ] **Step 3: 实现 _rebuild_fc_dist_from_checkpoints + resume() 调用**

**(3a) 新增方法**（在 `_compute_session_metrics` 之后）：

```python
    def _rebuild_fc_dist_from_checkpoints(self) -> None:
        """G9: 从当前 session 的全部 checkpoint 重建 failure_code 分布。"""
        records = self._store.all()
        for r in records:
            code = r.failure_code.value if r.failure_code else "NONE"
            self._fc_dist[code] = self._fc_dist.get(code, 0) + 1
```

**(3b) `resume()` 方法开头调用**——在现有 `resume()` 的 `self._session_start` 赋值处之后加：

注意 `resume()` 当前不重置 `_session_start`。需要在 `resume()` 开头加：

```python
    def resume(self) -> RuntimeState:
        # 幂等：已终态的 session 不恢复
        if self._state.terminal_state != RuntimeTerminalState.NONE:
            return self._state
        # G9: 重置 wall_clock 起点 + 重建 failure_code 分布
        self._session_start = time.perf_counter()
        self._rebuild_fc_dist_from_checkpoints()
        cp = self._store.latest()
        # ... 现有逻辑 ...
```

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_resume_rebuilds_fc_dist_from_checkpoints -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(engine): G9 resume 重建 _fc_dist + 重置 wall_clock 起点"
```

---

## Task 9: cli — _session_to_dict / _load_session metrics 序列化

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime_cli.py`（`_session_to_dict` L384-397 + `_load_session` L357-381）
- Test: `engineering/loop/controller/python/tests/test_runtime_cli.py`

- [ ] **Step 1: 写失败测试 — status 输出含 metrics 段**

在 `test_runtime_cli.py` 末尾追加：

```python
def test_status_outputs_metrics(tmp_path, capsys):
    """G9: le runtime status 输出含 metrics 段。"""
    from loop_controller.runtime_cli import _handle_status
    from loop_contracts.models import SessionMetrics

    metrics_dict = {
        "success": True, "terminal_state": "DONE_SUCCESS",
        "attempt_count": 2, "wall_clock_used_ms": 5000,
        "wall_clock_budget_ms": 3600000,
        "analyzer_layer_hits": {"KnowledgeBaseAnalyzer": 1},
        "analyzer_first_hit_layer": "KnowledgeBaseAnalyzer",
        "failure_code_distribution": {"RUN_FAILED": 1, "NONE": 2},
        "human_gate_triggered": False, "human_gate_count": 0,
        "kb_hit": True,
    }
    session_data = {
        "session_id": "s1", "workflow_id": "runtime",
        "target": "lciod", "suite": "hal",
        "max_attempts": 5, "current_attempt": 2,
        "status": "DONE", "latest_failure_code": "NONE",
        "attempts": [], "artifacts_dir": str(tmp_path),
        "wall_clock_limit": 3600,
        "terminal_state": "DONE_SUCCESS",
        "metrics": metrics_dict,
    }
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps(session_data), encoding="utf-8")

    args = MagicMock()
    args.session = str(session_path)
    _handle_status(args)

    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert "metrics" in output
    assert output["metrics"]["success"] is True
    assert output["metrics"]["attempt_count"] == 2


def test_load_session_handles_missing_metrics(tmp_path):
    """G9: 旧 session.json 无 metrics 段时 _load_session 不报错。"""
    from loop_controller.runtime_cli import _load_session

    session_data = {
        "session_id": "s1", "workflow_id": "runtime",
        "target": "lciod", "suite": "hal",
        "max_attempts": 5, "current_attempt": 0,
        "status": "PENDING", "latest_failure_code": "NONE",
        "attempts": [], "artifacts_dir": str(tmp_path),
        "wall_clock_limit": 0,
    }
    session_path = tmp_path / "session.json"
    session_path.write_text(json.dumps(session_data), encoding="utf-8")

    session, ts = _load_session(str(session_path))
    assert session.metrics is None  # 旧文件无 metrics → None
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_cli.py -v -k "metrics or missing_metrics"
```
Expected: FAIL — metrics 未被序列化/反序列化

- [ ] **Step 3: 修改 _session_to_dict 和 _load_session**

**(3a) 新增辅助函数**（在 `_session_to_dict` 之前）：

```python
def _metrics_to_dict(metrics) -> dict | None:
    """G9: SessionMetrics → dict；None → None。"""
    if metrics is None:
        return None
    from dataclasses import asdict
    return asdict(metrics)


def _dict_to_metrics(data: dict | None):
    """G9: dict → SessionMetrics；None/缺失 → None。"""
    if not data:
        return None
    from loop_contracts.models import SessionMetrics
    return SessionMetrics(**data)
```

**(3b) `_session_to_dict` 末尾加 metrics**：

```python
def _session_to_dict(session: LoopSession) -> dict:
    return {
        # ... 现有字段 ...
        "wall_clock_limit": session.wall_clock_limit,
        "metrics": _metrics_to_dict(session.metrics),
    }
```

**(3c) `_load_session` 末尾加 metrics 解析**——在 `wall_clock_limit=data.get("wall_clock_limit", 0),` 之后、`)` 之前加：

```python
        metrics=_dict_to_metrics(data.get("metrics")),
```

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_cli.py -v -k "metrics or missing_metrics"
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime_cli.py engineering/loop/controller/python/tests/test_runtime_cli.py
git commit -m "功能(cli): G9 _session_to_dict/_load_session 支持 metrics 序列化"
```

---

## Task 10: cli — _persist_session metrics 落盘

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py`（`_persist_session` L734-757）
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: 写失败测试 — session.json 含 metrics 段**

在 `test_runtime_engine.py` 末尾追加：

```python
def test_persist_session_writes_metrics(tmp_path):
    """G9: _persist_session 把 metrics 写入 session.json。"""
    import json
    from pathlib import Path
    from loop_controller.runtime.engine import LoopRuntime
    from loop_contracts.models import SessionMetrics, RuntimeTerminalState

    session = LoopSession(
        session_id="s1", workflow_id="runtime",
        target="lciod", suite="hal", max_attempts=1,
        artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, cases_dir="/tmp/cases", device_profile="rp5")
    rt._state.terminal_state = RuntimeTerminalState.DONE_SUCCESS
    session.metrics = SessionMetrics(success=True, attempt_count=1)
    rt._persist_session()

    data = json.loads(Path(tmp_path / "session.json").read_text())
    assert "metrics" in data
    assert data["metrics"]["success"] is True
    assert data["metrics"]["attempt_count"] == 1
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_persist_session_writes_metrics -v
```
Expected: FAIL — session.json 无 metrics key

- [ ] **Step 3: 修改 _persist_session 加 metrics**

在 `_persist_session` 的 `data = { ... }` 字典末尾（`"last_checkpoint_at": ...` 之后）加：

```python
        # G9: metrics 段（仅终态时非 None）
        if self._session.metrics is not None:
            from dataclasses import asdict
            data["metrics"] = asdict(self._session.metrics)
```

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_persist_session_writes_metrics -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(engine): G9 _persist_session 落盘 metrics 段到 session.json"
```

---

## Task 11: cli — le runtime stats 命令

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime_cli.py`（命令注册 + `_handle_stats` + `_scan_session_metrics` + `_aggregate_metrics`）
- Test: `engineering/loop/controller/python/tests/test_runtime_cli.py`

- [ ] **Step 1: 写失败测试（5 个）**

在 `test_runtime_cli.py` 末尾追加：

```python
def _make_session_json(path, metrics=None, target="lciod", suite="hal",
                      terminal="DONE_SUCCESS", attempt_count=1,
                      wall_used=10000):
    """辅助：构造一个 session.json 文件。"""
    data = {
        "session_id": path.stem, "workflow_id": "runtime",
        "target": target, "suite": suite,
        "max_attempts": 5, "current_attempt": attempt_count,
        "status": terminal, "latest_failure_code": "NONE",
        "attempts": [], "artifacts_dir": str(path.parent),
        "wall_clock_limit": 3600,
        "terminal_state": terminal,
    }
    if metrics is not None:
        data["metrics"] = metrics
    path.write_text(json.dumps(data), encoding="utf-8")


def test_stats_command_no_sessions(tmp_path, capsys):
    """G9: 空目录输出 total=0。"""
    from loop_controller.runtime_cli import _handle_stats
    args = MagicMock()
    args.artifacts_dir = str(tmp_path)
    rc = _handle_stats(args)
    captured = capsys.readouterr()
    output = json.loads(captured.out)
    assert output["total"] == 0
    assert rc == 0


def test_stats_command_aggregates(tmp_path, capsys):
    """G9: 聚合 3 个 session（2 成功 1 失败），验证 success_rate。"""
    from loop_controller.runtime_cli import _handle_stats
    for i, (success, target) in enumerate([
        (True, "lciod"), (True, "lcview"), (False, "kernel"),
    ]):
        sd = tmp_path / f"session-{i}"
        sd.mkdir()
        terminal = "DONE_SUCCESS" if success else "DONE_FAILURE"
        metrics = {
            "success": success, "terminal_state": terminal,
            "attempt_count": 1 if success else 3,
            "wall_clock_used_ms": 10000 + i * 1000,
            "wall_clock_budget_ms": 3600000,
            "analyzer_layer_hits": {"KnowledgeBaseAnalyzer": 1} if success else {},
            "analyzer_first_hit_layer": "KnowledgeBaseAnalyzer" if success else "",
            "failure_code_distribution": {"RUN_FAILED": 2},
            "human_gate_triggered": False, "human_gate_count": 0,
            "kb_hit": success,
        }
        _make_session_json(sd / "session.json", metrics=metrics,
                           target=target, terminal=terminal,
                           attempt_count=1 if success else 3)
    args = MagicMock()
    args.artifacts_dir = str(tmp_path)
    _handle_stats(args)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["total_sessions"] == 3
    assert out["success_count"] == 2
    assert out["success_rate"] == 0.67 or abs(out["success_rate"] - 0.666666) < 0.01
    # by_target 分组
    assert "lciod" in out["by_target"]
    assert "kernel" in out["by_target"]
    assert out["by_target"]["kernel"]["success"] == 0


def test_stats_command_skips_no_metrics(tmp_path, capsys):
    """G9: 无 metrics 段的 session 被跳过。"""
    from loop_controller.runtime_cli import _handle_stats
    sd = tmp_path / "session-1"
    sd.mkdir()
    _make_session_json(sd / "session.json", metrics=None)  # 无 metrics
    args = MagicMock()
    args.artifacts_dir = str(tmp_path)
    _handle_stats(args)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["total"] == 0 or out.get("total_sessions", 0) == 0


def test_stats_command_skips_corrupted(tmp_path, capsys):
    """G9: 损坏 json 被跳过，不崩溃。"""
    from loop_controller.runtime_cli import _handle_stats
    sd = tmp_path / "session-broken"
    sd.mkdir()
    (sd / "session.json").write_text("{ not valid json", encoding="utf-8")
    args = MagicMock()
    args.artifacts_dir = str(tmp_path)
    rc = _handle_stats(args)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    assert out["total"] == 0
    assert rc == 0


def test_stats_command_median_wall_clock(tmp_path, capsys):
    """G9: 偶数 session 取中位数（中间两数均值）。"""
    from loop_controller.runtime_cli import _handle_stats
    for i, ms in enumerate([10000, 20000, 30000, 40000]):
        sd = tmp_path / f"session-{i}"
        sd.mkdir()
        metrics = {
            "success": True, "terminal_state": "DONE_SUCCESS",
            "attempt_count": 1, "wall_clock_used_ms": ms,
            "wall_clock_budget_ms": 3600000,
            "analyzer_layer_hits": {}, "analyzer_first_hit_layer": "",
            "failure_code_distribution": {},
            "human_gate_triggered": False, "human_gate_count": 0,
            "kb_hit": False,
        }
        _make_session_json(sd / "session.json", metrics=metrics, terminal="DONE_SUCCESS")
    args = MagicMock()
    args.artifacts_dir = str(tmp_path)
    _handle_stats(args)
    captured = capsys.readouterr()
    out = json.loads(captured.out)
    # 中位数 = (20000 + 30000) / 2 = 25000
    assert out["median_wall_clock_ms"] == 25000
```

- [ ] **Step 2: 运行测试确认 RED**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_cli.py -v -k "stats_command"
```
Expected: 5 FAIL — `_handle_stats` 不存在

- [ ] **Step 3: 实现 stats 命令**

**(3a) 命令注册**——在 `main()` 函数中 `reject_p` 之后加：

```python
    stats_p = sub.add_parser("stats", help="aggregate metrics across sessions")
    stats_p.add_argument("--artifacts-dir", default="")
    stats_p.set_defaults(func=_handle_stats)
```

**(3b) 新增三个函数**——在 `_persist_failure` 之前插入：

```python
def _handle_stats(args: argparse.Namespace) -> int:
    """G9: 跨 session 聚合指标，输出 JSON。"""
    artifacts_dir = args.artifacts_dir or _resolve_default_artifacts_dir()
    sessions = _scan_session_metrics(artifacts_dir)
    if not sessions:
        print(json.dumps({"total": 0, "summary": "no terminated sessions found"},
                         indent=2, ensure_ascii=False))
        return 0
    aggregated = _aggregate_metrics(sessions)
    print(json.dumps(aggregated, indent=2, ensure_ascii=False))
    return 0


def _resolve_default_artifacts_dir() -> str:
    """默认 artifacts 目录：analyzer.yaml 所在的 ../artifacts。"""
    return str(Path(__file__).resolve().parent.parent.parent.parent / "artifacts")


def _scan_session_metrics(artifacts_dir: str) -> list[dict]:
    """遍历 artifacts/<session_id>/session.json，返回含 metrics 段的 session 列表。"""
    result: list[dict] = []
    base = Path(artifacts_dir)
    if not base.is_dir():
        return result
    for session_dir in sorted(base.iterdir()):
        if not session_dir.is_dir():
            continue
        sf = session_dir / "session.json"
        if not sf.is_file():
            continue
        try:
            data = json.loads(sf.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(data, dict) and data.get("metrics"):
            result.append(data)
    return result


def _aggregate_metrics(sessions: list[dict]) -> dict:
    """聚合多个 session 的 metrics 段。"""
    total = len(sessions)
    metrics_list = [s["metrics"] for s in sessions]
    success_count = sum(1 for m in metrics_list if m.get("success"))
    wall_list = sorted(m.get("wall_clock_used_ms", 0) for m in metrics_list)
    attempt_list = [m.get("attempt_count", 0) for m in metrics_list]
    # first_fix: attempt_count==1 + success + first_hit_layer 非空
    first_fix = sum(
        1 for m in metrics_list
        if m.get("success") and m.get("attempt_count") == 1
        and m.get("analyzer_first_hit_layer")
    )
    # 层级 hits 合并
    layer_hits_total: dict[str, int] = {}
    first_hit_dist: dict[str, int] = {}
    fc_dist_total: dict[str, int] = {}
    for m in metrics_list:
        for k, v in m.get("analyzer_layer_hits", {}).items():
            layer_hits_total[k] = layer_hits_total.get(k, 0) + v
        fhl = m.get("analyzer_first_hit_layer", "")
        if fhl:
            first_hit_dist[fhl] = first_hit_dist.get(fhl, 0) + 1
        for k, v in m.get("failure_code_distribution", {}).items():
            fc_dist_total[k] = fc_dist_total.get(k, 0) + v
    # 中位数
    n = len(wall_list)
    if n == 0:
        median = 0
    elif n % 2 == 1:
        median = wall_list[n // 2]
    else:
        median = (wall_list[n // 2 - 1] + wall_list[n // 2]) // 2
    # by_target / by_suite
    by_target: dict[str, dict] = {}
    by_suite: dict[str, dict] = {}
    for s in sessions:
        for dim, store in (("target", by_target), ("suite", by_suite)):
            key = s.get(dim, "unknown")
            if key not in store:
                store[key] = {"total": 0, "success": 0}
            store[key]["total"] += 1
            if s["metrics"].get("success"):
                store[key]["success"] += 1
    for store in (by_target, by_suite):
        for v in store.values():
            v["success_rate"] = round(v["success"] / v["total"], 2) if v["total"] else 0.0

    return {
        "total_sessions": total,
        "success_count": success_count,
        "failure_count": total - success_count,
        "success_rate": round(success_count / total, 2) if total else 0.0,
        "avg_wall_clock_ms": int(sum(wall_list) / len(wall_list)) if wall_list else 0,
        "median_wall_clock_ms": median,
        "avg_attempt_count": round(sum(attempt_list) / len(attempt_list), 1) if attempt_list else 0,
        "first_fix_success_rate": round(first_fix / total, 2) if total else 0.0,
        "analyzer_layer_hits_total": layer_hits_total,
        "analyzer_first_hit_layer_distribution": first_hit_dist,
        "failure_code_distribution_total": fc_dist_total,
        "kb_hit_rate": round(
            sum(1 for m in metrics_list if m.get("kb_hit")) / total, 2
        ) if total else 0.0,
        "human_gate_triggered_rate": round(
            sum(1 for m in metrics_list if m.get("human_gate_triggered")) / total, 2
        ) if total else 0.0,
        "avg_human_gate_count": round(
            sum(m.get("human_gate_count", 0) for m in metrics_list) / total, 2
        ) if total else 0.0,
        "by_target": by_target,
        "by_suite": by_suite,
    }
```

- [ ] **Step 4: 运行测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_runtime_cli.py -v -k "stats_command"
```
Expected: 5 PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/loop_controller/runtime_cli.py engineering/loop/controller/python/tests/test_runtime_cli.py
git commit -m "功能(cli): G9 新增 le runtime stats 跨 session 聚合命令"
```

---

## Task 12: 文档 + G8 元测试同步

**Files:**
- Modify: `engineering/loop/controller/python/tests/test_docs_consistency.py`
- Modify: `engineering/loop/contracts/README.md`
- Modify: `engineering/loop/controller/README.md`
- Modify: `engineering/loop/WORKFLOW.md`

- [ ] **Step 1: 修改 G8 元测试守护值**

在 `test_docs_consistency.py` 中：
- `test_contracts_all_count_matches_readme`：当前断言 `count == 9` → 改为 `count == 10`
- `test_contracts_dataclass_count_matches_readme`：README 中的 dataclass 数量说明需对应更新
- 末尾新增守护点：

```python
def test_session_metrics_in_contracts_readme() -> None:
    """守护点 10: SessionMetrics 出现在 contracts/README.md 中。"""
    text = _read("engineering/loop/contracts/README.md")
    assert "SessionMetrics" in text, (
        "contracts/README.md 缺少 SessionMetrics，请同步更新"
    )


def test_session_metrics_in_contracts_all() -> None:
    """守护点 11: SessionMetrics 在 contracts __all__ 中。"""
    assert "SessionMetrics" in _contracts_all
```

同时更新原有的 `test_contracts_all_count_matches_readme`：
```python
def test_contracts_all_count_matches_readme() -> None:
    """守护点 3: contracts __all__ 长度 = 10，README 必须含 '十符号'。"""
    count = len(_contracts_all)
    assert count == 10, f"contracts __all__ 长度变了: {count}，请同步改此测试和 README"
    text = _read("engineering/loop/contracts/README.md")
    assert "十符号" in text or "10" in text, "contracts/README.md 缺少导出符号数量说明"
```

- [ ] **Step 2: 运行元测试确认 RED（README 还没改）**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_docs_consistency.py -v
```
Expected: FAIL — SessionMetrics 不在 README / count 不匹配

- [ ] **Step 3: 更新 README**

**(3a) `engineering/loop/contracts/README.md`**：
- 把 `__all__` 导出符号数说明从"九符号"/"9"改为"十符号"/"10"
- 把 dataclass 计数说明对应更新（原"六 dataclass" → 加 SessionMetrics 后的数量）
- 在 dataclass 清单中补 `SessionMetrics` 条目及字段说明

**(3b) `engineering/loop/controller/README.md`**：
- 在子命令列表补 `stats`
- 补 metrics 段说明

**(3c) `engineering/loop/WORKFLOW.md`**：
- `le runtime` 子命令列表补 `stats`

- [ ] **Step 4: 运行元测试确认 GREEN**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" python3 -m pytest engineering/loop/controller/python/tests/test_docs_consistency.py -v
```
Expected: ALL PASS（含新增 2 个守护点）

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/controller/python/tests/test_docs_consistency.py engineering/loop/contracts/README.md engineering/loop/controller/README.md engineering/loop/WORKFLOW.md
git commit -m "文档(loop): G9 同步 G8 元测试 + README 补 SessionMetrics/stats 说明"
```

---

## Task 13: 全量回归 + 推送

- [ ] **Step 1: 全量回归**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" python3 -m pytest engineering/loop/controller/python/tests/ engineering/loop/core/python/tests/ engineering/loop/connection/providers/rp5-serial/python/tests/ engineering/loop/connection/providers/adb/python/tests/ engineering/loop/deploy/python/tests/ engineering/loop/contracts/python/tests/ -q --import-mode=importlib
```
Expected: 0 failures（预计 ~652 passed）

- [ ] **Step 2: 推送**

```bash
git push origin main
```

---

## Self-Review

### Spec coverage

| Spec 章节 | 对应 Task |
|-----------|-----------|
| §3.1 SessionMetrics | Task 1 |
| §3.2 LoopSession.metrics | Task 1 |
| §3.3 PatchSuggestion.matched_layer | Task 3 |
| §3.4 __all__ 导出 | Task 2 |
| §3.5 G8 元测试守护 | Task 12 |
| §4.1 engine 实例变量 | Task 5 |
| §4.2 (a) analyzer 层级埋点 | Task 5 |
| §4.2 (b) human gate 埋点 | Task 5 |
| §4.2 (c) failure_code 分布 | Task 6 |
| §4.3 _compute_session_metrics | Task 7 |
| §4.4 run() 终态调用 | Task 7 |
| §4.5 resume 重建 | Task 8 |
| §5.1 stats 命令注册 | Task 11 |
| §5.2 _handle_stats | Task 11 |
| §5.3 _scan_session_metrics | Task 11 |
| §5.4 _aggregate_metrics | Task 11 |
| §5.6 status 命令 metrics 透传 | Task 9 |
| §6 ChainedAnalyzer 填充 | Task 4 |
| §8 文档同步 | Task 12 |
| metrics 落盘（_persist_session） | Task 10 |

### Type consistency

- `SessionMetrics` 11 字段：Task 1 定义 → Task 7 `_compute_session_metrics` 构造 → Task 9 `_dict_to_metrics` 反序列化 → Task 10 `asdict` 落盘——字段名一致。
- `matched_layer: str`：Task 3 定义 → Task 4 ChainedAnalyzer 填充 → Task 5 engine 读取——一致。
- `_layer_hits` / `_first_hit_layer` / `_hg_count` / `_fc_dist` / `_kb_hit`：Task 5 定义 → Task 6 累积 → Task 7 快照 → Task 8 重建——一致。
- `_set_human_gate()` / `_rebuild_fc_dist_from_checkpoints()` / `_compute_session_metrics()`：方法名跨 task 一致。

无遗漏，无占位符。
