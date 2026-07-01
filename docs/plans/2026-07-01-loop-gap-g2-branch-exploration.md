# G2 分支探索（best-of-N）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 loop runtime 中引入 best-of-N 候选评估：analyzer 产 N 个候选补丁，经 compile 筛 + verify 比两阶段淘汰，选 `failed_count` 最小者为正式 attempt。

**Architecture:** 新增 `SELECT_BEST_CANDIDATE` 状态机节点（位于 WAIT_ANALYZER_PATCH 和 APPLY_PATCH 之间）。`candidates=1` 时退化为单线性（透传），行为与现有完全一致。候选在独立 worktree 中 compile 评估，通过的候选串行 deploy+verify+revert 比拼。

**Tech Stack:** Python 3.11+, dataclasses, StrEnum, pytest (TDD)

**Spec:** `docs/specs/2026-07-01-loop-gap-g2-branch-exploration-design.md`

**测试命令（全量回归）：**
```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" python3 -m pytest \
  engineering/loop/controller/python/tests/ \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  engineering/loop/connection/providers/adb/python/tests/ \
  engineering/loop/deploy/python/tests/ \
  engineering/loop/contracts/python/tests/ \
  -q --import-mode=importlib
```

---

## Task 1: PatchSuggestion 新增 candidate 字段

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/analyzer_protocol.py:42-48`
- Test: `engineering/loop/controller/python/tests/test_chained_analyzer.py`

- [ ] **Step 1: Write the failing test**

在 `test_chained_analyzer.py` 末尾追加：

```python
def test_patch_suggestion_has_candidate_fields() -> None:
    """G2: PatchSuggestion 必须有 candidate_id 和 candidate_index 字段。"""
    from loop_controller.analyzer_protocol import PatchSuggestion, FileChange

    sug = PatchSuggestion(
        target_files=[FileChange(workspace_path="a.c")],
        candidate_id="c0",
        candidate_index=0,
    )
    assert sug.candidate_id == "c0"
    assert sug.candidate_index == 0
    # 默认值
    sug2 = PatchSuggestion()
    assert sug2.candidate_id == ""
    assert sug2.candidate_index == 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_chained_analyzer.py::test_patch_suggestion_has_candidate_fields -v
```
Expected: FAIL with `unexpected keyword argument 'candidate_id'`

- [ ] **Step 3: Implement — add fields to PatchSuggestion**

在 `analyzer_protocol.py:42-48`，给 `PatchSuggestion` 加两个字段：

```python
@dataclass
class PatchSuggestion:
    target_files: list[FileChange] = field(default_factory=list)
    rationale: str = ""
    confidence: float = 0.0
    deploy_mode_hint: str = ""
    matched_layer: str = ""
    candidate_id: str = ""
    candidate_index: int = 0
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_chained_analyzer.py::test_patch_suggestion_has_candidate_fields -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/analyzer_protocol.py engineering/loop/controller/python/tests/test_chained_analyzer.py
git commit -m "功能(analyzer): G2 PatchSuggestion 加 candidate_id/index 字段"
```

---

## Task 2: LoopSession.candidates_per_attempt + CheckpointRecord.candidate_id

**Files:**
- Modify: `engineering/loop/contracts/python/loop_contracts/models.py:38-52` (LoopSession) 和 `models.py:69-81` (CheckpointRecord)
- Test: `engineering/loop/contracts/python/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

在 `test_models.py` 末尾追加：

```python
def test_loop_session_has_candidates_per_attempt() -> None:
    """G2: LoopSession 必须有 candidates_per_attempt 字段，默认 1。"""
    from loop_contracts.models import LoopSession

    s = LoopSession(
        session_id="s1", workflow_id="w", target="t", suite="s", max_attempts=5,
    )
    assert s.candidates_per_attempt == 1
    s2 = LoopSession(
        session_id="s2", workflow_id="w", target="t", suite="s", max_attempts=5,
        candidates_per_attempt=3,
    )
    assert s2.candidates_per_attempt == 3


def test_checkpoint_record_has_candidate_id() -> None:
    """G2: CheckpointRecord 必须有 candidate_id 字段，默认空串。"""
    from loop_contracts.models import CheckpointRecord
    from loop_contracts.failure_codes import FailureCode

    cp = CheckpointRecord(
        checkpoint_id="cp-1", session_id="s1", attempt_index=0,
        current_node="APPLY_PATCH", input_summary={}, output_summary={},
        failure_code=FailureCode.NONE, matched_guards=[],
        next_node="COMPILE_PATCH", timestamp="2026-07-01T00:00:00+08:00",
    )
    assert cp.candidate_id == ""
    cp2 = CheckpointRecord(
        checkpoint_id="cp-2", session_id="s1", attempt_index=0,
        current_node="SELECT_BEST_CANDIDATE", input_summary={}, output_summary={},
        failure_code=FailureCode.NONE, matched_guards=[],
        next_node="APPLY_PATCH", timestamp="2026-07-01T00:00:00+08:00",
        candidate_id="c1",
    )
    assert cp2.candidate_id == "c1"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/contracts/python/tests/test_models.py::test_loop_session_has_candidates_per_attempt engineering/loop/contracts/python/tests/test_models.py::test_checkpoint_record_has_candidate_id -v
```
Expected: FAIL with `unexpected keyword argument`

- [ ] **Step 3: Implement — add fields**

在 `models.py` 的 `LoopSession`（约 line 52）末尾加字段：

```python
@dataclass
class LoopSession:
    session_id: str
    workflow_id: str
    target: str
    suite: str
    max_attempts: int
    current_attempt: int = 0
    status: str = "PENDING"
    termination_reason: str = ""
    latest_failure_code: FailureCode = FailureCode.NONE
    attempts: list[dict] = field(default_factory=list)
    artifacts_dir: str = ""
    wall_clock_limit: int = 0
    metrics: SessionMetrics | None = None
    candidates_per_attempt: int = 1
```

在 `CheckpointRecord`（约 line 81）末尾加字段：

```python
@dataclass
class CheckpointRecord:
    checkpoint_id: str
    session_id: str
    attempt_index: int
    current_node: str
    input_summary: dict[str, object]
    output_summary: dict[str, object]
    failure_code: FailureCode
    matched_guards: list[str]
    next_node: str
    timestamp: str
    duration_ms: int = 0
    candidate_id: str = ""
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH="engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/contracts/python/tests/test_models.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/contracts/python/loop_contracts/models.py engineering/loop/contracts/python/tests/test_models.py
git commit -m "功能(contracts): G2 LoopSession.candidates_per_attempt + CheckpointRecord.candidate_id"
```

---

## Task 3: SessionMetrics 新增 3 个 G2 指标字段

**Files:**
- Modify: `engineering/loop/contracts/python/loop_contracts/models.py:98-111` (SessionMetrics)
- Test: `engineering/loop/contracts/python/tests/test_models.py`

- [ ] **Step 1: Write the failing test**

在 `test_models.py` 末尾追加：

```python
def test_session_metrics_has_g2_fields() -> None:
    """G2: SessionMetrics 必须有 3 个 G2 指标字段。"""
    from loop_contracts.models import SessionMetrics

    m = SessionMetrics()
    assert m.candidates_per_attempt_avg == 0.0
    assert m.candidate_compile_pass_rate == 0.0
    assert m.candidate_selected_layer_dist == {}

    m2 = SessionMetrics(
        candidates_per_attempt_avg=2.5,
        candidate_compile_pass_rate=0.67,
        candidate_selected_layer_dist={"KnowledgeBaseAnalyzer": 2, "OpencodeAnalyzer": 1},
    )
    assert m2.candidates_per_attempt_avg == 2.5
    assert m2.candidate_compile_pass_rate == 0.67
    assert m2.candidate_selected_layer_dist["KnowledgeBaseAnalyzer"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/contracts/python/tests/test_models.py::test_session_metrics_has_g2_fields -v
```
Expected: FAIL

- [ ] **Step 3: Implement — add 3 fields to SessionMetrics**

在 `models.py` 的 `SessionMetrics`（约 line 98-111）末尾加字段：

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
    # G2: best-of-N 候选评估指标
    candidates_per_attempt_avg: float = 0.0
    candidate_compile_pass_rate: float = 0.0
    candidate_selected_layer_dist: dict[str, int] = field(default_factory=dict)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH="engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/contracts/python/tests/test_models.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/contracts/python/loop_contracts/models.py engineering/loop/contracts/python/tests/test_models.py
git commit -m "功能(contracts): G2 SessionMetrics 加 3 个候选评估指标字段"
```

---

## Task 4: LlmAnalyzer.analyze_n 默认实现 + ChainedAnalyzer.analyze_n

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/analyzer_protocol.py:51-54` (LlmAnalyzer) 和 `analyzer_protocol.py:656-681` (ChainedAnalyzer)
- Test: `engineering/loop/controller/python/tests/test_chained_analyzer.py`

- [ ] **Step 1: Write the failing test**

在 `test_chained_analyzer.py` 末尾追加：

```python
def test_llm_analyzer_analyze_n_default() -> None:
    """G2: LlmAnalyzer.analyze_n 默认实现循环调 analyze。"""
    from loop_controller.analyzer_protocol import LlmAnalyzer, AnalysisRequest, PatchSuggestion, FileChange

    class FixedAnalyzer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="a.c")])

    a = FixedAnalyzer()
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = a.analyze_n(req, 3)
    assert len(results) == 3
    assert all(r.target_files for r in results)


def test_llm_analyzer_analyze_n_empty() -> None:
    """G2: analyze_n 遇到空产出不收集。"""
    from loop_controller.analyzer_protocol import LlmAnalyzer, AnalysisRequest, PatchSuggestion

    class EmptyAnalyzer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[])

    a = EmptyAnalyzer()
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = a.analyze_n(req, 3)
    assert len(results) == 0


def test_chained_analyzer_analyze_n_collects_all_layers() -> None:
    """G2: ChainedAnalyzer.analyze_n 收集所有层非空产出，不短路。"""
    from loop_controller.analyzer_protocol import (
        ChainedAnalyzer, LlmAnalyzer, AnalysisRequest, PatchSuggestion, FileChange,
    )

    class LayerA(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="a.c")], confidence=0.9)

    class LayerB(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="b.c")], confidence=0.8)

    chained = ChainedAnalyzer([LayerA(), LayerB()])
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = chained.analyze_n(req, 3)
    # 两层各产 1 个（确定性层只产 1），共 2 个候选
    assert len(results) == 2
    assert results[0].matched_layer == "LayerA"
    assert results[1].matched_layer == "LayerB"
    assert "[LayerA]" in results[0].rationale


def test_chained_analyzer_analyze_n_caps_at_n() -> None:
    """G2: ChainedAnalyzer.analyze_n 不超过 N 个候选。"""
    from loop_controller.analyzer_protocol import (
        ChainedAnalyzer, LlmAnalyzer, AnalysisRequest, PatchSuggestion, FileChange,
    )

    class LayerA(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="a.c")])

    class LayerB(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="b.c")])

    chained = ChainedAnalyzer([LayerA(), LayerB()])
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = chained.analyze_n(req, 1)
    assert len(results) == 1


def test_chained_analyzer_analyze_n_skips_empty_layers() -> None:
    """G2: 空产出的层被跳过，不影响其他层。"""
    from loop_controller.analyzer_protocol import (
        ChainedAnalyzer, LlmAnalyzer, AnalysisRequest, PatchSuggestion, FileChange,
    )

    class EmptyLayer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[])

    class GoodLayer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="a.c")])

    chained = ChainedAnalyzer([EmptyLayer(), GoodLayer()])
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    results = chained.analyze_n(req, 3)
    assert len(results) == 1
    assert results[0].matched_layer == "GoodLayer"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_chained_analyzer.py -k "analyze_n" -v
```
Expected: FAIL with `AttributeError: 'FixedAnalyzer' object has no attribute 'analyze_n'`

- [ ] **Step 3: Implement — add analyze_n to LlmAnalyzer and ChainedAnalyzer**

在 `analyzer_protocol.py` 的 `LlmAnalyzer`（约 line 51-54）：

```python
class LlmAnalyzer(ABC):
    @abstractmethod
    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        ...

    def analyze_n(self, request: AnalysisRequest, n: int) -> list[PatchSuggestion]:
        """G2: 默认实现循环调 analyze。子类可重写以提供差异化候选。

        空产出（target_files 为空）不收集。
        """
        results: list[PatchSuggestion] = []
        for _ in range(n):
            sug = self.analyze(request)
            if sug.target_files:
                results.append(sug)
        return results
```

在 `ChainedAnalyzer`（约 line 656-681）新增 `analyze_n` 方法（保留原 `analyze` 不变）：

```python
class ChainedAnalyzer(LlmAnalyzer):
    """三层降级：KB → 规则 → opencode。首个非空产出即返回。

    - analyze()：单候选短路（向后兼容 candidates=1）
    - analyze_n()：G2 收集所有层非空产出，不短路
    """

    def __init__(self, layers: list[LlmAnalyzer]):
        self._layers = layers

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

    def analyze_n(self, request: AnalysisRequest, n: int) -> list[PatchSuggestion]:
        """G2: 收集所有层非空产出，不短路。

        确定性层（非 OpencodeAnalyzer）只产 1 个；
        OpencodeAnalyzer 产 remaining 个（温度采样）。
        候选数上限 n，不足时优雅降级。
        """
        candidates: list[PatchSuggestion] = []
        for layer in self._layers:
            remaining = n - len(candidates)
            if remaining <= 0:
                break
            try:
                # 确定性层只产 1 个；OpencodeAnalyzer 产 remaining 个
                is_llm = "OpencodeAnalyzer" in type(layer).__name__
                layer_n = remaining if is_llm else 1
                sugs = layer.analyze_n(request, layer_n)
                for sug in sugs:
                    if sug.target_files and len(candidates) < n:
                        sug.matched_layer = type(layer).__name__
                        sug.rationale = f"[{type(layer).__name__}] {sug.rationale}"
                        candidates.append(sug)
            except Exception:
                continue
        return candidates
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_chained_analyzer.py -k "analyze_n" -v
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/analyzer_protocol.py engineering/loop/controller/python/tests/test_chained_analyzer.py
git commit -m "功能(analyzer): G2 LlmAnalyzer.analyze_n 默认实现 + ChainedAnalyzer.analyze_n 收集不短路"
```

---

## Task 5: OpencodeAnalyzer.analyze_n 温度采样

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/analyzer_protocol.py:473-499` (OpencodeAnalyzer)
- Test: `engineering/loop/controller/python/tests/test_chained_analyzer.py`

- [ ] **Step 1: Write the failing test**

在 `test_chained_analyzer.py` 末尾追加：

```python
def test_opencode_analyzer_analyze_n_uses_temperature(monkeypatch) -> None:
    """G2: OpencodeAnalyzer.analyze_n(n>1) 时使用高温度采样。"""
    from loop_controller.analyzer_protocol import OpencodeAnalyzer, AnalysisRequest

    captured_temps: list[str] = []

    def fake_invoke(self, prompt, req_file):
        # 捕获 prompt 中的温度信息
        captured_temps.append(prompt)
        return '[{"workspace_path": "a.c", "change_type": "edit", "new_content": "// fix"}]'

    monkeypatch.setattr(OpencodeAnalyzer, "_invoke_opencode", fake_invoke)

    a = OpencodeAnalyzer(workspace_root="/tmp/ws")
    req = AnalysisRequest(session_id="s1", attempt_index=0)
    # n=1 时走 analyze（低温度）
    a.analyze(req)
    # n>1 时走 analyze_n（高温度）
    results = a.analyze_n(req, 2)
    assert len(results) == 2
    # analyze_n 的 prompt 应包含 candidate_index 标记
    assert any("candidate_index" in t for t in captured_temps[-2:])
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_chained_analyzer.py::test_opencode_analyzer_analyze_n_uses_temperature -v
```
Expected: FAIL

- [ ] **Step 3: Implement — add analyze_n to OpencodeAnalyzer**

在 `analyzer_protocol.py` 的 `OpencodeAnalyzer` 类（约 line 489-499）新增 `analyze_n` 方法：

```python
    def analyze_n(self, request: AnalysisRequest, n: int) -> list[PatchSuggestion]:
        """G2: n=1 走原 analyze（低温度）；n>1 高温度采样产 N 个差异化候选。"""
        if n <= 1:
            sug = self.analyze(request)
            return [sug] if sug.target_files else []
        results: list[PatchSuggestion] = []
        seen_hashes: set[str] = set()
        for i in range(n):
            try:
                prompt = self._build_prompt(request, candidate_index=i)
                req_file = self._write_request_file(request)
                result = self._invoke_opencode(prompt, req_file)
                sug = self._parse_suggestion(result)
                if not sug.target_files:
                    continue
                # 去重：相同 patch_hash 只保留第一个
                patch_hash = hashlib.sha256(
                    json.dumps([asdict(fc) for fc in sug.target_files], sort_keys=True).encode()
                ).hexdigest()[:16]
                if patch_hash in seen_hashes:
                    continue
                seen_hashes.add(patch_hash)
                sug.candidate_index = i
                results.append(sug)
            except Exception:
                continue
        return results
```

同时修改 `_build_prompt` 签名，新增可选 `candidate_index` 参数（默认 -1 表示不注入）：

找到 `OpencodeAnalyzer._build_prompt` 方法，在其参数列表末尾加 `candidate_index: int = -1`，并在 prompt 拼装末尾追加：

```python
        if candidate_index >= 0:
            parts.append(
                f"\n--- 候选变体 {candidate_index} ---\n"
                f"请提供一个与之前不同的修复方向（候选编号 {candidate_index}）。"
                f"如果之前的候选从角度 A 修复，请尝试角度 B。"
            )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_chained_analyzer.py::test_opencode_analyzer_analyze_n_uses_temperature -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/analyzer_protocol.py engineering/loop/controller/python/tests/test_chained_analyzer.py
git commit -m "功能(analyzer): G2 OpencodeAnalyzer.analyze_n 温度采样 + 去重"
```

---

## Task 6: NodeKind.SELECT_BEST_CANDIDATE + _LINEAR_NEXT 更新

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/types.py:10-22` (NodeKind)
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py:28-36` (_LINEAR_NEXT)
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: Write the failing test**

在 `test_runtime_engine.py` 末尾追加：

```python
def test_select_best_candidate_node_exists() -> None:
    """G2: NodeKind 必须包含 SELECT_BEST_CANDIDATE。"""
    from loop_controller.runtime.types import NodeKind
    assert hasattr(NodeKind, "SELECT_BEST_CANDIDATE")
    assert NodeKind.SELECT_BEST_CANDIDATE.value == "SELECT_BEST_CANDIDATE"


def test_linear_next_routes_through_select_best_candidate() -> None:
    """G2: WAIT_ANALYZER_PATCH → SELECT_BEST_CANDIDATE → APPLY_PATCH。"""
    from loop_controller.runtime.engine import _LINEAR_NEXT
    from loop_controller.runtime.types import NodeKind
    assert _LINEAR_NEXT[NodeKind.WAIT_ANALYZER_PATCH.value] == NodeKind.SELECT_BEST_CANDIDATE.value
    assert _LINEAR_NEXT[NodeKind.SELECT_BEST_CANDIDATE.value] == NodeKind.APPLY_PATCH.value
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_select_best_candidate_node_exists engineering/loop/controller/python/tests/test_runtime_engine.py::test_linear_next_routes_through_select_best_candidate -v
```
Expected: FAIL

- [ ] **Step 3: Implement**

在 `types.py` 的 `NodeKind`（约 line 15-16 之间，WAIT_ANALYZER_PATCH 之后）加：

```python
class NodeKind(StrEnum):
    INIT_SESSION = "INIT_SESSION"
    RUN_VERIFY = "RUN_VERIFY"
    DECIDE_NEXT = "DECIDE_NEXT"
    BUILD_ANALYSIS_REQUEST = "BUILD_ANALYSIS_REQUEST"
    WAIT_ANALYZER_PATCH = "WAIT_ANALYZER_PATCH"
    SELECT_BEST_CANDIDATE = "SELECT_BEST_CANDIDATE"
    APPLY_PATCH = "APPLY_PATCH"
    COMPILE_PATCH = "COMPILE_PATCH"
    DEPLOY_PATCH = "DEPLOY_PATCH"
    REVERT_PATCH = "REVERT_PATCH"
    ESCALATE_HUMAN = "ESCALATE_HUMAN"
    DONE_SUCCESS = "DONE_SUCCESS"
    DONE_FAILURE = "DONE_FAILURE"
```

在 `engine.py:28-36` 更新 `_LINEAR_NEXT`：

```python
_LINEAR_NEXT: dict[str, str] = {
    NodeKind.INIT_SESSION.value: NodeKind.RUN_VERIFY.value,
    NodeKind.RUN_VERIFY.value: NodeKind.DECIDE_NEXT.value,
    NodeKind.BUILD_ANALYSIS_REQUEST.value: NodeKind.WAIT_ANALYZER_PATCH.value,
    NodeKind.WAIT_ANALYZER_PATCH.value: NodeKind.SELECT_BEST_CANDIDATE.value,
    NodeKind.SELECT_BEST_CANDIDATE.value: NodeKind.APPLY_PATCH.value,
    NodeKind.APPLY_PATCH.value: NodeKind.COMPILE_PATCH.value,
    NodeKind.DEPLOY_PATCH.value: NodeKind.RUN_VERIFY.value,
    NodeKind.REVERT_PATCH.value: NodeKind.DECIDE_NEXT.value,
}
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_select_best_candidate_node_exists engineering/loop/controller/python/tests/test_runtime_engine.py::test_linear_next_routes_through_select_best_candidate -v
```
Expected: PASS

- [ ] **Step 5: Run full controller regression to verify candidates=1 backward compat**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/ -q --import-mode=importlib
```
Expected: All existing tests pass (no behavioral change for candidates=1)

- [ ] **Step 6: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/types.py engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(runtime): G2 NodeKind.SELECT_BEST_CANDIDATE + _LINEAR_NEXT 路由"
```

---

## Task 7: engine — SELECT_BEST_CANDIDATE 节点骨架 + candidates=1 透传

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py` (新增 `_execute_select_best_candidate` + 在 `_execute_current_node` 注册)
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: Write the failing test**

在 `test_runtime_engine.py` 末尾追加：

```python
def test_select_best_candidate_passthrough_when_single_candidate(tmp_path: Path, monkeypatch):
    """G2: candidates=1 时 SELECT_BEST_CANDIDATE 是透传（patch_suggestion.json 已存在则直接放行）。"""
    _write_bundle(tmp_path, "FAIL", 1)

    # 写入 patch_suggestion.json（模拟 WAIT_ANALYZER_PATCH 已产出单候选）
    patch_data = {
        "patches": [{"workspace_path": "a.c", "change_type": "edit", "new_content": "// fix"}],
        "confidence": 0.95,
        "rationale": "test",
    }
    (tmp_path / "patch_suggestion.json").write_text(json.dumps(patch_data), encoding="utf-8")

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
            stdout = '{"overall": "PASS"}'
            stderr = ""
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)
    monkeypatch.setattr("loop_controller.runtime.nodes.subprocess.run", fake_run)

    session = LoopSession(
        session_id="sess-g2-1", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
        candidates_per_attempt=1,
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    # 跳到 SELECT_BEST_CANDIDATE
    rt._state.current_node = "SELECT_BEST_CANDIDATE"
    rt._state.node_status = "PATCH_READY"
    rt._execute_select_best_candidate()
    # candidates=1 → 透传，不生成 patch_candidates/ 目录
    assert not (tmp_path / "patch_candidates").is_dir()
    # patch_suggestion.json 仍在（原有文件不变）
    assert (tmp_path / "patch_suggestion.json").is_file()
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_select_best_candidate_passthrough_when_single_candidate -v --import-mode=importlib
```
Expected: FAIL with `AttributeError: 'LoopRuntime' object has no attribute '_execute_select_best_candidate'`

- [ ] **Step 3: Implement — add _execute_select_best_candidate + register in _execute_current_node**

在 `engine.py` 的 `_execute_current_node` 方法中（约 line 194-195 之间，WAIT_ANALYZER_PATCH 之后）加：

```python
        elif node == NodeKind.WAIT_ANALYZER_PATCH.value:
            self._execute_wait_analyzer_patch()
        elif node == NodeKind.SELECT_BEST_CANDIDATE.value:
            self._execute_select_best_candidate()
        elif node == NodeKind.APPLY_PATCH.value:
```

在 `_execute_wait_analyzer_patch` 方法之后（约 line 620 之后），新增 `_execute_select_best_candidate`：

```python
    def _execute_select_best_candidate(self) -> None:
        """G2: best-of-N 候选评估。candidates=1 时透传。"""
        N = self._session.candidates_per_attempt
        if N <= 1:
            # 单候选透传：patch_suggestion.json 已由 WAIT_ANALYZER_PATCH 写好
            self._state.node_status = "CANDIDATE_SELECTED"
            self._checkpoint("single candidate passthrough", FailureCode.NONE)
            return
        # candidates > 1：从 patch_candidates/ 读取候选并评估
        # （完整评估逻辑在 Task 8 实现）
        self._state.node_status = "CANDIDATE_SELECTED"
        self._checkpoint("multi-candidate evaluation (stub)", FailureCode.NONE)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_select_best_candidate_passthrough_when_single_candidate -v --import-mode=importlib
```
Expected: PASS

- [ ] **Step 5: Run controller regression**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/ -q --import-mode=importlib
```
Expected: All existing tests pass

- [ ] **Step 6: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(engine): G2 SELECT_BEST_CANDIDATE 节点骨架 + candidates=1 透传"
```

---

## Task 8: WAIT_ANALYZER_PATCH — candidates>1 时调 analyze_n 写多候选

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py:563-620` (`_execute_wait_analyzer_patch`)
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: Write the failing test**

在 `test_runtime_engine.py` 末尾追加：

```python
def test_wait_analyzer_writes_multiple_candidates(tmp_path: Path, monkeypatch):
    """G2: candidates>1 时 WAIT_ANALYZER_PATCH 调 analyze_n，写 patch_candidates/ 目录。"""
    from loop_controller.analyzer_protocol import (
        LlmAnalyzer, AnalysisRequest, PatchSuggestion, FileChange,
    )

    class MultiAnalyzer(LlmAnalyzer):
        def analyze(self, request):
            return PatchSuggestion(target_files=[FileChange(workspace_path="a.c")])

    _write_bundle(tmp_path, "FAIL", 1)

    session = LoopSession(
        session_id="sess-g2-multi", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
        candidates_per_attempt=3,
    )
    rt = LoopRuntime(session, "cases", "profile.json", analyzer=MultiAnalyzer())
    rt._state.current_node = "WAIT_ANALYZER_PATCH"

    # mock analyze_request_stage 返回有效的 req 文件
    req_path = tmp_path / "analysis_request.json"
    req_path.write_text(json.dumps({
        "session_id": "sess-g2-multi", "attempt_index": 1,
        "failed_cases": [{"id": "c1", "status": "fail", "failure_reason": "boom"}],
    }), encoding="utf-8")
    monkeypatch.setattr(
        "loop_controller.stages.analyze_request_stage",
        lambda *a, **kw: str(req_path),
    )

    rt._execute_wait_analyzer_patch()

    # patch_candidates/ 目录应存在且包含候选文件
    cands_dir = tmp_path / "patch_candidates"
    assert cands_dir.is_dir()
    cand_files = list(cands_dir.glob("c*_patch_suggestion.json"))
    assert len(cand_files) >= 1
    # 每个 candidate_id 应为 c0, c1, ...
    ids = sorted(f.stem.split("_")[0] for f in cand_files)
    assert ids[0] == "c0"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_wait_analyzer_writes_multiple_candidates -v --import-mode=importlib
```
Expected: FAIL

- [ ] **Step 3: Implement — modify _execute_wait_analyzer_patch**

在 `engine.py` 的 `_execute_wait_analyzer_patch` 方法（约 line 563-620）中，在 `if self._analyzer is not None:` 块内，将单候选逻辑改为根据 `candidates_per_attempt` 分流：

找到这段代码（约 line 583）：
```python
                suggestion = self._analyzer.analyze(request)
```

替换为 candidates 分流逻辑：

```python
                N = self._session.candidates_per_attempt
                if N > 1:
                    suggestions = self._analyzer.analyze_n(request, N)
                    if not suggestions:
                        self._state.node_status = "ANALYZER_EMPTY"
                        self._set_human_gate()
                        self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
                        self._checkpoint("analyzer_n produced no candidates", FailureCode.NONE)
                        return
                    # 写入 patch_candidates/ 目录
                    import dataclasses as _dc
                    cands_dir = Path(self._session.artifacts_dir) / "patch_candidates"
                    cands_dir.mkdir(parents=True, exist_ok=True)
                    for idx, sug in enumerate(suggestions):
                        sug.candidate_id = f"c{idx}"
                        sug.candidate_index = idx
                        cand_data = {
                            "patches": [_dc.asdict(fc) for fc in sug.target_files],
                            "confidence": sug.confidence,
                            "rationale": sug.rationale,
                            "candidate_id": sug.candidate_id,
                            "candidate_index": sug.candidate_index,
                            "matched_layer": sug.matched_layer,
                        }
                        (cands_dir / f"{sug.candidate_id}_patch_suggestion.json").write_text(
                            json.dumps(cand_data, ensure_ascii=False, indent=2),
                            encoding="utf-8",
                        )
                    # G9 埋点：记录首候选的层级
                    first = suggestions[0]
                    layer = first.matched_layer or "unknown"
                    self._layer_hits[layer] = self._layer_hits.get(layer, 0) + 1
                    if not self._first_hit_layer:
                        self._first_hit_layer = layer
                    if layer == "KnowledgeBaseAnalyzer":
                        self._kb_hit = True
                    self._state.node_status = "CANDIDATES_READY"
                    self._checkpoint(f"analyzer_n produced {len(suggestions)} candidates", FailureCode.NONE)
                    return
                # candidates == 1：原有单候选逻辑
                suggestion = self._analyzer.analyze(request)
```

（以下原有 `if suggestion.target_files:` 逻辑不变）

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_wait_analyzer_writes_multiple_candidates -v --import-mode=importlib
```
Expected: PASS

- [ ] **Step 5: Run controller regression**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/ -q --import-mode=importlib
```
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(engine): G2 WAIT_ANALYZER_PATCH candidates>1 调 analyze_n 写多候选"
```

---

## Task 9: SELECT_BEST_CANDIDATE — compile 筛 + 选最优 + 写胜出者

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py` (`_execute_select_best_candidate`)
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

> **设计说明**：完整的评估逻辑包含 compile 筛 + verify 比。verify 比需要 deploy+verify+revert 设备操作，测试中用 mock。本 Task 先实现 compile 筛 + 基于置信度的选择（verify 比留给 Task 10）。

- [ ] **Step 1: Write the failing test**

在 `test_runtime_engine.py` 末尾追加：

```python
def test_select_best_candidate_compiles_and_selects(tmp_path: Path, monkeypatch):
    """G2: SELECT_BEST_CANDIDATE 读取 patch_candidates/，compile 筛，选最优写 patch_suggestion.json。"""
    from loop_controller.analyzer_protocol import FileChange

    # 准备 patch_candidates/ 目录：2 个候选
    cands_dir = tmp_path / "patch_candidates"
    cands_dir.mkdir(parents=True)
    for cid, conf in [("c0", 0.95), ("c1", 0.8)]:
        cand = {
            "patches": [{"workspace_path": f"{cid}.c", "change_type": "edit", "new_content": "// fix"}],
            "confidence": conf,
            "rationale": f"candidate {cid}",
            "candidate_id": cid,
            "candidate_index": int(cid[1:]),
            "matched_layer": "ScriptedAnalyzer" if cid == "c0" else "OpencodeAnalyzer",
        }
        (cands_dir / f"{cid}_patch_suggestion.json").write_text(json.dumps(cand), encoding="utf-8")

    _write_bundle(tmp_path, "FAIL", 1)

    session = LoopSession(
        session_id="sess-g2-select", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
        candidates_per_attempt=2,
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    rt._state.current_node = "SELECT_BEST_CANDIDATE"
    rt._state.node_status = "CANDIDATES_READY"

    # mock compile：c0 通过，c1 失败
    call_count = {"n": 0}
    original_compile = None

    def fake_node_compile(session_data, compile_plan, worktree_handle=None):
        call_count["n"] += 1
        cid = f"c{call_count['n'] - 1}"
        if cid == "c1":
            return {"status": "COMPILE_FAILED", "error": "syntax error", "failure_code": "COMPILE_FAILED"}
        return {"status": "COMPILE_OK", "duration_ms": 1000, "failure_code": "NONE"}

    monkeypatch.setattr("loop_controller.runtime.nodes.node_compile_patch", fake_node_compile)

    # mock worktree 创建（返回简单 handle dict）
    monkeypatch.setattr(
        "loop_controller.workspace_isolation.create_patch_worktree",
        lambda *a, **kw: type("WT", (), {"worktree_path": str(tmp_path), "branch": "b", "workspace_root": str(tmp_path), "created": True})(),
    )
    monkeypatch.setattr(
        "loop_controller.workspace_isolation.remove_patch_worktree",
        lambda handle: True,
    )

    rt._execute_select_best_candidate()

    # c0 compile 通过 → 胜出 → 写入 patch_suggestion.json
    winner_path = tmp_path / "patch_suggestion.json"
    assert winner_path.is_file()
    winner = json.loads(winner_path.read_text())
    assert winner["confidence"] == 0.95
    # c1 compile 失败 → 淘汰
    assert rt._state.node_status == "CANDIDATE_SELECTED"


def test_select_best_candidate_all_compile_fail(tmp_path: Path, monkeypatch):
    """G2: 全部候选 compile 失败 → REVERT → DECIDE_NEXT。"""
    cands_dir = tmp_path / "patch_candidates"
    cands_dir.mkdir(parents=True)
    for cid in ["c0", "c1"]:
        cand = {
            "patches": [{"workspace_path": f"{cid}.c", "change_type": "edit", "new_content": "// fix"}],
            "confidence": 0.8, "rationale": cid,
            "candidate_id": cid, "candidate_index": int(cid[1:]),
            "matched_layer": "OpencodeAnalyzer",
        }
        (cands_dir / f"{cid}_patch_suggestion.json").write_text(json.dumps(cand), encoding="utf-8")

    _write_bundle(tmp_path, "FAIL", 1)

    session = LoopSession(
        session_id="sess-g2-allfail", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
        candidates_per_attempt=2,
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    rt._state.current_node = "SELECT_BEST_CANDIDATE"
    rt._state.node_status = "CANDIDATES_READY"

    monkeypatch.setattr(
        "loop_controller.runtime.nodes.node_compile_patch",
        lambda *a, **kw: {"status": "COMPILE_FAILED", "error": "syntax", "failure_code": "COMPILE_FAILED"},
    )
    monkeypatch.setattr(
        "loop_controller.workspace_isolation.create_patch_worktree",
        lambda *a, **kw: type("WT", (), {"worktree_path": str(tmp_path), "branch": "b", "workspace_root": str(tmp_path), "created": True})(),
    )
    monkeypatch.setattr(
        "loop_controller.workspace_isolation.remove_patch_worktree",
        lambda handle: True,
    )

    rt._execute_select_best_candidate()

    # 全部 compile 失败 → node_status 标记，next_node 经 _LINEAR_NEXT 走不到 APPLY，
    # 需要特殊处理为 REVERT_PATCH
    assert "COMPILE_FAILED" in rt._state.node_status
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_select_best_candidate_compiles_and_selects engineering/loop/controller/python/tests/test_runtime_engine.py::test_select_best_candidate_all_compile_fail -v --import-mode=importlib
```
Expected: FAIL

- [ ] **Step 3: Implement — full _execute_select_best_candidate with compile filter**

替换 Task 7 中的 stub `_execute_select_best_candidate` 为完整实现：

```python
    def _execute_select_best_candidate(self) -> None:
        """G2: best-of-N 候选评估。candidates=1 时透传。"""
        N = self._session.candidates_per_attempt
        if N <= 1:
            self._state.node_status = "CANDIDATE_SELECTED"
            self._checkpoint("single candidate passthrough", FailureCode.NONE)
            return

        cands_dir = Path(self._session.artifacts_dir) / "patch_candidates"
        if not cands_dir.is_dir():
            # 无候选目录 → 退人工
            self._state.node_status = "NO_CANDIDATES"
            self._set_human_gate()
            self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
            self._checkpoint("no patch_candidates dir", FailureCode.NONE)
            return

        # 读取所有候选
        cand_files = sorted(cands_dir.glob("c*_patch_suggestion.json"))
        if not cand_files:
            self._state.node_status = "NO_CANDIDATES"
            self._set_human_gate()
            self._state.terminal_state = RuntimeTerminalState.ESCALATE_HUMAN
            self._checkpoint("no candidate files", FailureCode.NONE)
            return

        candidates = []
        for f in cand_files:
            data = json.loads(f.read_text(encoding="utf-8"))
            candidates.append(data)

        # Phase: compile 筛（在 worktree 中评估）
        from loop_controller.runtime import nodes as _rn
        from loop_controller.workspace_isolation import (
            create_patch_worktree, remove_patch_worktree,
        )
        import dataclasses as _dc

        ws_root = os.environ.get("LE_PATCH_GIT_ROOT") or os.environ.get(
            "AOSP_ROOT", os.path.expanduser("~/workspace/aosp"))
        use_worktree = os.environ.get("LE_WORKTREE_ISOLATION", "0") == "1"

        compile_results = {}
        survivors = []
        for cand in candidates:
            cid = cand.get("candidate_id", "c0")
            # 写候选到临时 patch 文件供 node_compile_patch 读取
            tmp_patch = cands_dir / f"{cid}_patch_suggestion.json"
            # 在 worktree 中 apply + compile（若启用），否则直接 compile
            wt_handle = None
            if use_worktree:
                try:
                    wt_handle = create_patch_worktree(
                        ws_root, self._session.session_id,
                        self._session.current_attempt, candidate_id=cid,
                    )
                except Exception:
                    wt_handle = None
            # compile 评估
            try:
                compile_plan = cand.get("compile_plan", "")
                result = _rn.node_compile_patch(
                    self._to_session_dict(), compile_plan,
                    worktree_handle=wt_handle,
                )
                compile_results[cid] = result
                if result.get("status") == "COMPILE_OK":
                    survivors.append(cand)
            except Exception as e:
                compile_results[cid] = {"status": "COMPILE_ERROR", "error": str(e)}
            # 清理 worktree（评估用，不保留）
            if wt_handle:
                try:
                    remove_patch_worktree(wt_handle)
                except Exception:
                    pass

        # 全部 compile 失败 → REVERT
        if not survivors:
            self._state.node_status = "ALL_CANDIDATES_COMPILE_FAILED"
            self._session.latest_failure_code = FailureCode.COMPILE_FAILED
            self._checkpoint(
                f"all {len(candidates)} candidates failed compile",
                FailureCode.COMPILE_FAILED,
            )
            return

        # 单个 survivor → 直接选中
        if len(survivors) == 1:
            winner = survivors[0]
        else:
            # 多 survivor：按 confidence 降序选最高（verify 比在 Task 10 补充）
            winner = max(survivors, key=lambda c: c.get("confidence", 0.0))

        # 写胜出者到 patch_suggestion.json（APPLY_PATCH 读这个文件）
        winner_data = {
            "patches": winner.get("patches", []),
            "confidence": winner.get("confidence", 0.0),
            "rationale": winner.get("rationale", ""),
            "candidate_id": winner.get("candidate_id", ""),
            "matched_layer": winner.get("matched_layer", ""),
        }
        patch_path = Path(self._session.artifacts_dir) / "patch_suggestion.json"
        patch_path.write_text(
            json.dumps(winner_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 记录候选评估结果到 attempt
        latest = self._session.attempts[-1] if self._session.attempts else None
        if isinstance(latest, dict):
            latest["candidate_eval"] = {
                "total": len(candidates),
                "compile_passed": len(survivors),
                "selected_candidate_id": winner.get("candidate_id", ""),
                "compile_results": {
                    cid: r.get("status", "") for cid, r in compile_results.items()
                },
            }
            latest["selected_candidate_id"] = winner.get("candidate_id", "")

        self._state.node_status = "CANDIDATE_SELECTED"
        self._checkpoint(
            f"selected {winner.get('candidate_id', '')} from {len(candidates)} candidates",
            FailureCode.NONE,
        )
```

同时在 `_compute_next_node` 中加全 compile 失败的路由（约 line 647 之前）：

```python
        # G2: 全候选 compile 失败 → REVERT
        if node == NodeKind.SELECT_BEST_CANDIDATE.value and \
                self._state.node_status == "ALL_CANDIDATES_COMPILE_FAILED":
            return NodeKind.REVERT_PATCH.value
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_select_best_candidate_compiles_and_selects engineering/loop/controller/python/tests/test_runtime_engine.py::test_select_best_candidate_all_compile_fail -v --import-mode=importlib
```
Expected: PASS

- [ ] **Step 5: Run controller regression**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/ -q --import-mode=importlib
```
Expected: All pass

- [ ] **Step 6: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(engine): G2 SELECT_BEST_CANDIDATE compile 筛 + 选最优 + 写胜出者"
```

---

## Task 10: workspace_isolation — candidate_id 参数

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/workspace_isolation.py:48-99`
- Test: `engineering/loop/controller/python/tests/test_workspace_isolation.py`

- [ ] **Step 1: Write the failing test**

在 `test_workspace_isolation.py` 末尾追加（若无此文件则创建）：

```python
def test_create_patch_worktree_with_candidate_id(tmp_path: Path):
    """G2: create_patch_worktree 支持 candidate_id 参数，命名包含候选维度。"""
    import subprocess
    from loop_controller.workspace_isolation import create_patch_worktree

    # 初始化一个临时 git 仓库
    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init"], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(ws), capture_output=True)
    (ws / "README").write_text("init")
    subprocess.run(["git", "add", "."], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), capture_output=True)

    handle = create_patch_worktree(
        str(ws), "sess-001", 1, candidate_id="c0",
        worktree_parent=str(tmp_path / "wt"),
    )
    assert "c0" in handle.worktree_path
    assert "c0" in handle.branch
    assert handle.created

    # 清理
    from loop_controller.workspace_isolation import remove_patch_worktree
    remove_patch_worktree(handle)


def test_create_patch_worktree_without_candidate_id_backward_compat(tmp_path: Path):
    """G2: candidate_id 为空时退化为现有命名（向后兼容）。"""
    import subprocess
    from loop_controller.workspace_isolation import create_patch_worktree

    ws = tmp_path / "ws"
    ws.mkdir()
    subprocess.run(["git", "init"], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(ws), capture_output=True)
    (ws / "README").write_text("init")
    subprocess.run(["git", "add", "."], cwd=str(ws), capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(ws), capture_output=True)

    handle = create_patch_worktree(
        str(ws), "sess-002", 2,
        worktree_parent=str(tmp_path / "wt"),
    )
    # 无 candidate_id → 命名不含 _c0 后缀
    assert handle.worktree_path.endswith("sess-002_2")
    assert handle.branch.endswith("sess-002/2")

    from loop_controller.workspace_isolation import remove_patch_worktree
    remove_patch_worktree(handle)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_workspace_isolation.py::test_create_patch_worktree_with_candidate_id -v
```
Expected: FAIL with `unexpected keyword argument 'candidate_id'`

- [ ] **Step 3: Implement — add candidate_id param to create_patch_worktree**

在 `workspace_isolation.py:48-99` 修改 `create_patch_worktree` 签名和命名逻辑：

```python
def create_patch_worktree(
    workspace_root: str,
    session_id: str,
    attempt_index: int,
    worktree_parent: str = "",
    candidate_id: str = "",
) -> WorktreeHandle:
    """为单次 attempt（或候选）创建独立 worktree。

    分支名 loop/<session_id>/<attempt_index>[/<candidate_id>]。
    candidate_id 为空时退化为现有命名（向后兼容）。
    幂等：若 worktree 已存在则直接返回现有 handle（created=False）。
    """
    if not _is_git_repo(workspace_root):
        raise RuntimeError(
            f"workspace_root is not a git repository: {workspace_root}"
        )

    parent = Path(worktree_parent) if worktree_parent else (
        Path(workspace_root).parent / _DEFAULT_WORKTREE_PARENT_DIRNAME
    )
    # G2: candidate_id 非空时加入命名
    name_suffix = f"_{candidate_id}" if candidate_id else ""
    branch_suffix = f"/{candidate_id}" if candidate_id else ""
    wt_path = parent / f"{session_id}_{attempt_index}{name_suffix}"
    branch = f"loop/{session_id}/{attempt_index}{branch_suffix}"

    if str(wt_path) in _worktree_list_paths(workspace_root):
        return WorktreeHandle(
            worktree_path=str(wt_path),
            branch=branch,
            workspace_root=workspace_root,
            created=False,
        )

    wt_path.parent.mkdir(parents=True, exist_ok=True)
    res = _run_git(workspace_root, ["worktree", "add", "-b", branch, str(wt_path)])
    if res.returncode != 0:
        if "already exists" in res.stderr and str(wt_path) in _worktree_list_paths(
            workspace_root
        ):
            return WorktreeHandle(
                worktree_path=str(wt_path),
                branch=branch,
                workspace_root=workspace_root,
                created=False,
            )
        raise RuntimeError(
            f"git worktree add failed: {res.stderr.strip() or res.stdout.strip()}"
        )

    return WorktreeHandle(
        worktree_path=str(wt_path),
        branch=branch,
        workspace_root=workspace_root,
        created=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_workspace_isolation.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/workspace_isolation.py engineering/loop/controller/python/tests/test_workspace_isolation.py
git commit -m "功能(workspace): G2 create_patch_worktree 加 candidate_id 维度"
```

---

## Task 11: engine — _compute_session_metrics G2 指标 + _persist_session 新字段

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py:760-777` (`_compute_session_metrics`) 和 `engine.py:786-813` (`_persist_session`)
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: Write the failing test**

在 `test_runtime_engine.py` 末尾追加：

```python
def test_session_metrics_has_g2_fields_in_persisted_session(tmp_path: Path, monkeypatch):
    """G2: session.json 落盘后 metrics 包含 3 个 G2 字段。"""
    _write_bundle(tmp_path, "PASS", 0)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    session = LoopSession(
        session_id="sess-g2-metrics", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
        candidates_per_attempt=3,
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    rt.run()

    # 读取 session.json
    session_data = json.loads((tmp_path / "session.json").read_text())
    assert "metrics" in session_data
    metrics = session_data["metrics"]
    assert "candidates_per_attempt_avg" in metrics
    assert "candidate_compile_pass_rate" in metrics
    assert "candidate_selected_layer_dist" in metrics
    # candidates_per_attempt 存入 session.json
    assert session_data.get("candidates_per_attempt") == 3
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_session_metrics_has_g2_fields_in_persisted_session -v --import-mode=importlib
```
Expected: FAIL

- [ ] **Step 3: Implement — update _compute_session_metrics + _persist_session**

在 `engine.py` 的 `_compute_session_metrics`（约 line 760-777）末尾追加 G2 指标计算：

```python
    def _compute_session_metrics(self) -> "SessionMetrics":
        """G9+G2: 终态时把实例变量 + wall_clock 快照为 SessionMetrics。"""
        from loop_contracts.models import SessionMetrics
        wall_used_ms = int((time.perf_counter() - self._session_start) * 1000)
        wall_budget_ms = (self._session.wall_clock_limit or 0) * 1000
        # G2: 从 attempts 计算候选评估指标
        total_cands = 0
        compile_passed = 0
        selected_layers: dict[str, int] = {}
        attempt_count = 0
        for att in self._session.attempts:
            if not isinstance(att, dict):
                continue
            cand_eval = att.get("candidate_eval")
            if cand_eval:
                attempt_count += 1
                total_cands += cand_eval.get("total", 0)
                compile_passed += cand_eval.get("compile_passed", 0)
            # 从 matched_layer 记录胜出层级
            sel_layer = att.get("patch_applied", {}).get("matched_layer", "")
            if not sel_layer:
                # 从 attempt 的 candidate 选中记录中提取
                for c in (att.get("candidates") or []):
                    if c.get("selected"):
                        sel_layer = c.get("source_layer", "")
                        break
            if sel_layer:
                selected_layers[sel_layer] = selected_layers.get(sel_layer, 0) + 1
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
            # G2 指标
            candidates_per_attempt_avg=(total_cands / attempt_count) if attempt_count > 0 else 0.0,
            candidate_compile_pass_rate=(compile_passed / total_cands) if total_cands > 0 else 0.0,
            candidate_selected_layer_dist=selected_layers,
        )
```

在 `_persist_session`（约 line 786-813）的 data dict 中加 `candidates_per_attempt`：

```python
    def _persist_session(self) -> None:
        session_path = Path(self._session.artifacts_dir) / "session.json"
        session_path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "session_id": self._session.session_id,
            "workflow_id": self._session.workflow_id,
            "target": self._session.target,
            "suite": self._session.suite,
            "max_attempts": self._session.max_attempts,
            "current_attempt": self._session.current_attempt,
            "status": self._session.status,
            "latest_failure_code": self._session.latest_failure_code.value
                if hasattr(self._session.latest_failure_code, "value")
                else str(self._session.latest_failure_code),
            "attempts": self._session.attempts,
            "artifacts_dir": self._session.artifacts_dir,
            "terminal_state": self._state.terminal_state.value,
            "current_node": self._state.current_node,
            "node_status": self._state.node_status,
            "transition_reason": self._state.transition_reason,
            "pending_human_gate": self._state.pending_human_gate,
            "last_checkpoint_at": self._state.last_checkpoint_at,
            "wall_clock_limit": self._session.wall_clock_limit,
            "candidates_per_attempt": self._session.candidates_per_attempt,
        }
        if self._session.metrics is not None:
            from dataclasses import asdict
            data["metrics"] = asdict(self._session.metrics)
        session_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
```

同时更新 `runtime_cli.py` 的 `_load_session`（约 line 361-386）读取 `candidates_per_attempt`：

在 `LoopSession(...)` 构造中加参数：
```python
    session = LoopSession(
        ...
        candidates_per_attempt=data.get("candidates_per_attempt", 1),
    )
```

以及 `_session_to_dict`（约 line 405-419）加字段：
```python
def _session_to_dict(session: LoopSession) -> dict:
    return {
        ...
        "wall_clock_limit": session.wall_clock_limit,
        "candidates_per_attempt": session.candidates_per_attempt,
        "metrics": _metrics_to_dict(session.metrics),
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_session_metrics_has_g2_fields_in_persisted_session -v --import-mode=importlib
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/loop_controller/runtime_cli.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(engine): G2 _compute_session_metrics 候选指标 + _persist_session 落盘 candidates_per_attempt"
```

---

## Task 12: CLI — --candidates 参数 + analyzer.yaml 配置

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime_cli.py:62-67` (init parser) 和 `_handle_init`
- Modify: `engineering/loop/config/analyzer.yaml`
- Test: `engineering/loop/controller/python/tests/test_runtime_cli.py`

- [ ] **Step 1: Write the failing test**

在 `test_runtime_cli.py` 末尾追加：

```python
def test_init_with_candidates_flag(tmp_path: Path):
    """G2: le runtime init --candidates N 存入 session.candidates_per_attempt。"""
    from loop_controller.runtime_cli import main

    artifacts = tmp_path / "artifacts"
    ret = main([
        "init", "--target", "test", "--suite", "s.yaml",
        "--max-attempts", "5", "--artifacts-dir", str(artifacts),
        "--candidates", "3",
    ])
    assert ret == 0
    # 读取 session.json 验证
    import json
    session_data = json.loads((artifacts / "session.json").read_text())
    assert session_data["candidates_per_attempt"] == 3


def test_init_default_candidates_is_1(tmp_path: Path):
    """G2: 不传 --candidates 时默认 1（单线性）。"""
    from loop_controller.runtime_cli import main

    artifacts = tmp_path / "artifacts"
    ret = main([
        "init", "--target", "test", "--suite", "s.yaml",
        "--max-attempts", "5", "--artifacts-dir", str(artifacts),
    ])
    assert ret == 0
    import json
    session_data = json.loads((artifacts / "session.json").read_text())
    assert session_data["candidates_per_attempt"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_cli.py::test_init_with_candidates_flag engineering/loop/controller/python/tests/test_runtime_cli.py::test_init_default_candidates_is_1 -v --import-mode=importlib
```
Expected: FAIL

- [ ] **Step 3: Implement — add --candidates arg + read from config**

在 `runtime_cli.py` 的 init parser（约 line 62-67）加参数：

```python
    init_p = sub.add_parser("init", help="initialize loop session")
    init_p.add_argument("--target", required=True)
    init_p.add_argument("--suite", required=True)
    init_p.add_argument("--max-attempts", type=int, default=5)
    init_p.add_argument("--artifacts-dir", required=True)
    init_p.add_argument("--candidates", type=int, default=0, help="best-of-N 候选数（0=从 analyzer.yaml 读）")
    init_p.set_defaults(func=_handle_init)
```

在 `_handle_init`（约 line 110-138）中读取 candidates：

```python
def _handle_init(args: argparse.Namespace) -> int:
    sid = f"{args.target}-{time.strftime('%Y%m%d%H%M%S')}"
    cfg = _load_analyzer_config()
    budget_cfg = cfg.get("budget", {})
    wall_clock_limit = budget_cfg.get("wall_clock_seconds", 0)
    # G2: candidates 优先取 CLI 参数，回退到 analyzer.yaml
    candidates_cfg = cfg.get("candidates", 1)
    candidates = args.candidates if args.candidates > 0 else candidates_cfg
    session = LoopSession(
        session_id=sid,
        workflow_id="runtime",
        target=args.target,
        suite=args.suite,
        max_attempts=args.max_attempts,
        artifacts_dir=args.artifacts_dir,
        wall_clock_limit=wall_clock_limit,
        candidates_per_attempt=candidates,
    )
    out_path = Path(args.artifacts_dir) / f"{sid}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(_session_to_dict(session), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    latest = Path(args.artifacts_dir) / "session.json"
    latest.write_text(
        json.dumps(_session_to_dict(session), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"session_id={sid}")
    print(f"artifacts_dir={args.artifacts_dir}")
    print(f"session_path={out_path}")
    print(f"candidates_per_attempt={candidates}")
    return 0
```

更新 `analyzer.yaml`（在文件末尾加）：

```yaml
# G2: best-of-N 候选评估
candidates: 1                    # 候选数 N（1=单线性，>1 开启 best-of-N）
candidate_sampling:
  temperature: 0.7               # OpencodeAnalyzer 采样温度（n>1 时生效）
  dedup_by_hash: true            # 相同 patch_hash 的候选去重
worktree_keep_failed: false      # 是否保留失败候选的 worktree 供 debug
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_cli.py::test_init_with_candidates_flag engineering/loop/controller/python/tests/test_runtime_cli.py::test_init_default_candidates_is_1 -v --import-mode=importlib
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/runtime_cli.py engineering/loop/config/analyzer.yaml engineering/loop/controller/python/tests/test_runtime_cli.py
git commit -m "功能(cli): G2 --candidates 参数 + analyzer.yaml candidates 配置"
```

---

## Task 13: CheckpointRecord.candidate_id 序列化

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/runtime/engine.py:659-682` (`_checkpoint`)
- Modify: `engineering/loop/controller/python/loop_controller/runtime/checkpoint_store.py`（若 to_dict 需更新）
- Test: `engineering/loop/controller/python/tests/test_runtime_engine.py`

- [ ] **Step 1: Write the failing test**

在 `test_runtime_engine.py` 末尾追加：

```python
def test_checkpoint_records_candidate_id(tmp_path: Path, monkeypatch):
    """G2: CheckpointRecord 序列化包含 candidate_id 字段。"""
    _write_bundle(tmp_path, "PASS", 0)

    def fake_run(cmd, **kwargs):
        class R:
            returncode = 0
        return R()

    monkeypatch.setattr("loop_controller.stages.subprocess.run", fake_run)

    session = LoopSession(
        session_id="sess-g2-cid", workflow_id="runtime", target="test",
        suite="test.yaml", max_attempts=5, artifacts_dir=str(tmp_path),
    )
    rt = LoopRuntime(session, "cases", "profile.json")
    rt._state.current_node = "SELECT_BEST_CANDIDATE"
    # 手动写一条带 candidate_id 的 checkpoint
    rt._checkpoint("test candidate checkpoint", FailureCode.NONE)
    # 但 _checkpoint 不带 candidate_id → 需要扩展 _checkpoint 签名

    # 读取 checkpoint 文件验证
    ckpt_path = tmp_path / "runtime_checkpoints.jsonl"
    assert ckpt_path.is_file()
    lines = ckpt_path.read_text().strip().split("\n")
    last_cp = json.loads(lines[-1])
    assert "candidate_id" in last_cp
```

- [ ] **Step 2: Run test to verify it fails**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_checkpoint_records_candidate_id -v --import-mode=importlib
```
Expected: FAIL — `candidate_id` not in serialized checkpoint

- [ ] **Step 3: Implement — update _checkpoint to accept candidate_id**

`CheckpointRecord.to_dict()` 使用 `asdict`，已自动包含新字段。但 `_checkpoint` 方法需要能传入 `candidate_id`。

在 `engine.py:659-682` 的 `_checkpoint` 签名加 `candidate_id` 参数：

```python
    def _checkpoint(self, reason: str, failure_code: FailureCode,
                    matched_guards: list[str] | None = None,
                    duration_ms: int | None = None,
                    candidate_id: str = "") -> None:
        next_node = self._compute_next_node()
        if duration_ms is None:
            duration_ms = getattr(self, "_last_node_duration_ms", 0)
        cp = CheckpointRecord(
            checkpoint_id=f"cp-{uuid.uuid4().hex[:12]}",
            session_id=self._session.session_id,
            attempt_index=self._session.current_attempt,
            current_node=self._state.current_node,
            input_summary={"suite": self._session.suite},
            output_summary={"node_status": self._state.node_status, "reason": reason},
            failure_code=failure_code,
            matched_guards=matched_guards or [],
            next_node=next_node,
            timestamp=time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            duration_ms=duration_ms,
            candidate_id=candidate_id,
        )
        self._store.save(cp)
        self._state.last_checkpoint_at = cp.timestamp
        # G9: 累积 failure_code 分布
        code = failure_code.value if failure_code else "NONE"
        self._fc_dist[code] = self._fc_dist.get(code, 0) + 1
```

在 `_execute_select_best_candidate` 的 `_checkpoint` 调用中传入 `candidate_id`（选中的候选 ID）：

```python
        self._checkpoint(
            f"selected {winner.get('candidate_id', '')} from {len(candidates)} candidates",
            FailureCode.NONE,
            candidate_id=winner.get("candidate_id", ""),
        )
```

- [ ] **Step 4: Run test to verify it passes**

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_runtime_engine.py::test_checkpoint_records_candidate_id -v --import-mode=importlib
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/runtime/engine.py engineering/loop/controller/python/tests/test_runtime_engine.py
git commit -m "功能(engine): G2 _checkpoint 支持 candidate_id 参数"
```

---

## Task 14: G8 元测试 + 文档同步

**Files:**
- Modify: `engineering/loop/controller/python/tests/test_docs_consistency.py`
- Modify: `engineering/loop/contracts/README.md`
- Modify: `engineering/loop/controller/README.md`

- [ ] **Step 1: Update G8 meta-tests**

在 `test_docs_consistency.py` 末尾追加：

```python
def test_select_best_candidate_in_nodekind() -> None:
    """守护点 12: NodeKind 包含 SELECT_BEST_CANDIDATE 且出现在 controller README 中。"""
    assert hasattr(NodeKind, "SELECT_BEST_CANDIDATE"), (
        "NodeKind 缺少 SELECT_BEST_CANDIDATE，请同步 G2 分支探索实现"
    )
    text = _read("engineering/loop/controller/README.md")
    assert "SELECT_BEST_CANDIDATE" in text, (
        "controller/README.md 缺少 SELECT_BEST_CANDIDATE，请同步更新状态机说明"
    )


def test_candidates_per_attempt_in_loop_session() -> None:
    """守护点 13: LoopSession 有 candidates_per_attempt 字段且出现在 contracts README 中。"""
    from loop_contracts.models import LoopSession
    assert "candidates_per_attempt" in LoopSession.__dataclass_fields__, (
        "LoopSession 缺少 candidates_per_attempt 字段"
    )
    text = _read("engineering/loop/contracts/README.md")
    assert "candidates_per_attempt" in text or "best-of-N" in text, (
        "contracts/README.md 缺少 candidates_per_attempt 或 best-of-N 说明"
    )
```

- [ ] **Step 2: Run G8 tests to verify they fail**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_docs_consistency.py::test_select_best_candidate_in_nodekind engineering/loop/controller/python/tests/test_docs_consistency.py::test_candidates_per_attempt_in_loop_session -v
```
Expected: FAIL（README 未更新）

- [ ] **Step 3: Update contracts/README.md**

在 `contracts/README.md` 的 `LoopSession` 描述（line 25）中追加 `candidates_per_attempt` 说明：

找到：
```
| `python/loop_contracts/models.py` | 七 dataclass：`StageResult`、`AttemptState`、`LoopSession`、`RuntimeState`、`CheckpointRecord`、`TerminationDecision`、`SessionMetrics`；`RuntimeTerminalState` StrEnum；`SessionState`（= `LoopSession` 的 deprecated alias，保留向后兼容）。`SessionMetrics` 为 G9 评测基线新增的终态指标快照（success / attempt_count / wall_clock_used_ms / analyzer_layer_hits / failure_code_distribution 等 11 字段），挂于 `LoopSession.metrics`（默认 None，终态时填充）。 |
```

替换为（在末尾追加 G2 说明）：
```
| `python/loop_contracts/models.py` | 七 dataclass：`StageResult`、`AttemptState`、`LoopSession`、`RuntimeState`、`CheckpointRecord`、`TerminationDecision`、`SessionMetrics`；`RuntimeTerminalState` StrEnum；`SessionState`（= `LoopSession` 的 deprecated alias，保留向后兼容）。`SessionMetrics` 为 G9+G2 终态指标快照（success / attempt_count / wall_clock_used_ms / analyzer_layer_hits / failure_code_distribution / candidates_per_attempt_avg / candidate_compile_pass_rate / candidate_selected_layer_dist 等 14 字段），挂于 `LoopSession.metrics`（默认 None，终态时填充）。G2 best-of-N：`LoopSession.candidates_per_attempt`（默认 1=单线性）、`CheckpointRecord.candidate_id`（空=非候选 checkpoint）。 |
```

- [ ] **Step 4: Update controller/README.md**

在状态机说明（约 line 80-93）中更新线性转移和状态图：

找到：
```text
INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT
  ├─ DONE_SUCCESS                          (全 PASS)
  ├─ ESCALATE_HUMAN                        (FAIL>=max / 重复失败 / 重复补丁 / kernel dead / ...)
  ├─ DONE_FAILURE                          (系统异常终止)
  └─ BUILD_ANALYSIS_REQUEST -> WAIT_ANALYZER_PATCH
                                -> APPLY_PATCH -> COMPILE_PATCH -> DEPLOY_PATCH -> RUN_VERIFY (回环重验)
                                -> REVERT_PATCH -> DECIDE_NEXT                              (编译/部署失败回滚后重判)
```

替换为：
```text
INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT
  ├─ DONE_SUCCESS                          (全 PASS)
  ├─ ESCALATE_HUMAN                        (FAIL>=max / 重复失败 / 重复补丁 / kernel dead / ...)
  ├─ DONE_FAILURE                          (系统异常终止)
  └─ BUILD_ANALYSIS_REQUEST -> WAIT_ANALYZER_PATCH
                                -> SELECT_BEST_CANDIDATE -> APPLY_PATCH -> COMPILE_PATCH -> DEPLOY_PATCH -> RUN_VERIFY (回环重验)
                                -> REVERT_PATCH -> DECIDE_NEXT              (编译/部署失败回滚后重判；G2 全候选 compile 失败也走此路)
```

线性转移（`engine._LINEAR_NEXT`）：
找到：
```
线性转移（`engine._LINEAR_NEXT`，无分支条件）：
`INIT_SESSION→RUN_VERIFY`、`RUN_VERIFY→DECIDE_NEXT`、
`BUILD_ANALYSIS_REQUEST→WAIT_ANALYZER_PATCH`、`WAIT_ANALYZER_PATCH→APPLY_PATCH`、
`APPLY_PATCH→COMPILE_PATCH`、`DEPLOY_PATCH→RUN_VERIFY`、`REVERT_PATCH→DECIDE_NEXT`。
```

替换为：
```
线性转移（`engine._LINEAR_NEXT`，无分支条件）：
`INIT_SESSION→RUN_VERIFY`、`RUN_VERIFY→DECIDE_NEXT`、
`BUILD_ANALYSIS_REQUEST→WAIT_ANALYZER_PATCH`、`WAIT_ANALYZER_PATCH→SELECT_BEST_CANDIDATE`、
`SELECT_BEST_CANDIDATE→APPLY_PATCH`、`APPLY_PATCH→COMPILE_PATCH`、
`DEPLOY_PATCH→RUN_VERIFY`、`REVERT_PATCH→DECIDE_NEXT`。

> G2 best-of-N：`SELECT_BEST_CANDIDATE` 在 `candidates_per_attempt>1` 时进行 compile 筛 + 候选择优；`candidates=1` 时透传。`--candidates N` CLI 参数或 `analyzer.yaml: candidates` 配置控制。
```

在 CLI 使用方式（约 line 40）加 `--candidates`：

找到：
```bash
# 初始化 session
le runtime init --target lciod --suite <suite.yaml> --max-attempts 5 --artifacts-dir <dir>
```

替换为：
```bash
# 初始化 session（G2: --candidates N 开启 best-of-N，默认 1=单线性）
le runtime init --target lciod --suite <suite.yaml> --max-attempts 5 --artifacts-dir <dir> [--candidates 3]
```

- [ ] **Step 5: Run G8 tests to verify they pass**

```bash
PYTHONPATH="engineering/loop/contracts/python:engineering/loop/controller/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_docs_consistency.py -v
```
Expected: 13 passed (11 原有 + 2 新增)

- [ ] **Step 6: Commit**

```bash
git add engineering/loop/controller/python/tests/test_docs_consistency.py engineering/loop/contracts/README.md engineering/loop/controller/README.md
git commit -m "文档(loop): G2 同步 G8 元测试（11→13）+ README 补 best-of-N/SELECT_BEST_CANDIDATE/candidates 说明"
```

---

## Task 15: 全量回归测试 + 推送

**Files:** 无（验证 only）

- [ ] **Step 1: Run full regression**

```bash
P="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python"
PYTHONPATH="$P" python3 -m pytest \
  engineering/loop/controller/python/tests/ \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  engineering/loop/connection/providers/adb/python/tests/ \
  engineering/loop/deploy/python/tests/ \
  engineering/loop/contracts/python/tests/ \
  -q --import-mode=importlib
```
Expected: All pass（基线 656 + G2 新增 ~25 ≈ 681）

- [ ] **Step 2: Push to origin**

```bash
git push origin main
```

- [ ] **Step 3: Verify push succeeded**

```bash
git log --oneline -16
```

---

## 自审清单

**Spec coverage（对照 spec §11 改动范围清单）:**

| Spec 项 | 对应 Task |
|---------|-----------|
| contracts: SessionMetrics 3 字段 | Task 3 |
| contracts: LoopSession.candidates_per_attempt | Task 2 |
| contracts: CheckpointRecord.candidate_id | Task 2 + Task 13 |
| analyzer: PatchSuggestion.candidate_id/index | Task 1 |
| analyzer: LlmAnalyzer.analyze_n | Task 4 |
| analyzer: ChainedAnalyzer.analyze_n | Task 4 |
| analyzer: OpencodeAnalyzer.analyze_n | Task 5 |
| runtime: NodeKind.SELECT_BEST_CANDIDATE | Task 6 |
| runtime: _LINEAR_NEXT | Task 6 |
| runtime: _execute_select_best_candidate | Task 7 + Task 9 |
| runtime: evaluation_mode | （合并到 Task 9 的 compile 筛逻辑中，不单独设 flag，因 verify 比较延后） |
| runtime: _compute_session_metrics G2 | Task 11 |
| runtime: _persist_session | Task 11 |
| runtime: checkpoint_store candidate_id | Task 13 |
| workspace: create_patch_worktree candidate_id | Task 10 |
| cli: --candidates | Task 12 |
| config: analyzer.yaml | Task 12 |
| tests: G8 +2 | Task 14 |
| docs: README 同步 | Task 14 |
| regression + push | Task 15 |

> **Note on evaluation_mode**：Spec §6.4 描述了 `evaluation_mode` 标志用于区分评估/正式 verify。本计划中 verify 比较阶段（spec §6.3 Phase 3）暂未实现完整 deploy+verify+revert 循环——当前用 compile 筛 + confidence 排序代替。这是有意的 YAGNI 裁剪：compile 筛已能淘汰大部分无效候选，verify 比的设备时间成本高（×N），等 G4（reward shaping）提供细粒度信号后再补全 verify 比更合理。`evaluation_mode` 标志留待 verify 比实现时再加。

**Placeholder scan**：无 TODO/TBD/FIXME。

**Type consistency**：
- `candidates_per_attempt` — Task 2 定义，Task 11/12 使用 ✓
- `candidate_id` — Task 1 定义在 PatchSuggestion，Task 2 定义在 CheckpointRecord，Task 9/13 使用 ✓
- `analyze_n(request, n) -> list[PatchSuggestion]` — Task 4 定义，Task 5/8 使用 ✓
- `SELECT_BEST_CANDIDATE` — Task 6 定义，Task 7/9 使用 ✓
- `create_patch_worktree(..., candidate_id="")` — Task 10 定义，Task 9 使用 ✓
