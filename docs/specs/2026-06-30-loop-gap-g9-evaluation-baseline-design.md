# Loop Engineering G9 评测基线设计

> 文档类型：设计规格（design spec）
> 所属模块：`engineering/loop/`
> 关联 gap：G9（无成功率基线/评测）
> 前置依赖：G5（loop 可观测 + 预算闸，已完成）、G8（文档一致性元测试，已完成）
> 设计日期：2026-06-30
> 测试基线：632 passed（G5 完成后）

---

## 1. 背景与目标

### 1.1 问题陈述

当前 loop 框架已具备完整的自动修复闭环（P0-P2 修复 + G3 轨迹上下文 + G5 可观测 + 预算闸），但**没有任何 loop 级成功率/效率指标**：

- `run()` 退出时只 `_persist_session()` 写状态字段，**不计算总轮数、实际墙钟耗时、analyzer 层级命中分布、HITL 触发次数等聚合指标**。
- `le runtime status` 只能看单 session 的 trace 流水（G5 成果），**无法跨 session 聚合**——不知道"过去 N 次修复的成功率是多少"。
- analyzer 的层级命中信息只埋在 `PatchSuggestion.rationale` 字符串前缀 `[LayerName]` 里，**engine 不解析也不统计**——无法回答"KB/Scripted/Opencode 三层各贡献了多少修复"。

### 1.2 G9 目标

建立 **session 级指标聚合 + 跨 session 聚合命令**，形成 loop 修复有效性的量化基线：

1. **单 session 指标落盘**：`run()` 终态时计算 `SessionMetrics` 并写入 `session.json`。
2. **analyzer 层级结构化埋点**：`PatchSuggestion.matched_layer` 字段让 engine 能统计每层命中。
3. **跨 session 聚合命令**：新增 `le runtime stats` 遍历 `artifacts/*/session.json` 输出聚合 JSON。

### 1.3 范围边界（本期）

| 纳入 | 不纳入（留作增量） |
|------|---------------------|
| 单 session 指标聚合（SessionMetrics） | G4 reward shaping / 断言级评分 |
| PatchSuggestion.matched_layer 埋点 | G2 分支探索 / best-of-N |
| `le runtime stats` 全量聚合（无过滤参数） | `--target`/`--suite`/`--since` 过滤参数 |
| 跨 session 按 target/suite 分组 | 时间序列趋势 / 可视化报表 |
| resume 场景从 checkpoint 重建 failure_code 分布 | resume 完整重建所有埋点 |

### 1.4 成功标准

- 所有终态 session 的 `session.json` 含 `metrics` 段。
- `le runtime stats` 能正确聚合多个 session 的成功率/层级分布/耗时。
- `ChainedAnalyzer.analyze()` 返回的 `PatchSuggestion` 含结构化 `matched_layer`。
- G8 元测试守护 dataclass 计数（9 → 10）。
- 测试基线 632 → ~650-652，零回归。

---

## 2. 总体架构

```
┌─────────────────────────────────────────────────────────┐
│                   engine.run() 主循环                    │
│   每轮 analyzer 调用 → 累积层级命中（埋点）               │
│   每轮 human gate → 累积 HITL 计数                       │
│   每轮 failure_code → 累积分布                            │
└──────────────────────┬──────────────────────────────────┘
                       │ 终态退出
                       ▼
        ┌──────────────────────────────┐
        │   _compute_session_metrics()  │  ← 新增
        │   汇总成 SessionMetrics       │
        └──────────────┬───────────────┘
                       │
                       ▼
        ┌──────────────────────────────┐
        │   _persist_session()          │
        │   session.json 加 "metrics"  │  ← 新增段
        └──────────────┬───────────────┘
                       │
        ┌──────────────┴───────────────┐
        ▼                              ▼
┌──────────────────┐        ┌────────────────────────┐
│  le runtime       │        │  le runtime stats       │  ← 新增命令
│  status           │        │  遍历 artifacts/*/      │
│  (展示单 session  │        │  session.json           │
│   的 metrics 段)  │        │  跨 session 聚合 → JSON │
└──────────────────┘        └────────────────────────┘
```

### 设计原则

1. **三层职责分离**：
   - **埋点层**（engine 实例变量）：轻量计数器，随 engine 生命周期增量更新。
   - **聚合层**（SessionMetrics + `_compute_session_metrics()`）：终态时一次性快照。
   - **展示层**（CLI）：`status` 透传单 session 指标；`stats` 新建跨 session 聚合。

2. **埋点用实例变量，终态才快照**：避免每轮 checkpoint 都算指标的开销。

3. **SessionMetrics 是只读快照**：落盘后不再更新（与 LoopSession 终态语义一致）。

4. **跨 session 聚合纯读不写**：`stats` 命令只遍历 session.json，不产生任何文件。

5. **向后兼容**：旧 session.json（无 metrics 段）能被正常加载和聚合跳过；旧 analyzer（不填 matched_layer）由 engine 兜底为 `"unknown"`。

---

## 3. 数据契约变更

### 3.1 新增 SessionMetrics dataclass

位置：`engineering/loop/contracts/python/loop_contracts/models.py`

```python
@dataclass
class SessionMetrics:
    """Session 终态指标快照（run() 退出时计算，落盘到 session.json）。"""
    success: bool = False                          # terminal_state == DONE_SUCCESS
    terminal_state: str = "NONE"                   # 终态枚举值
    attempt_count: int = 0                         # 最终 attempt 数
    wall_clock_used_ms: int = 0                    # 实际墙钟耗时
    wall_clock_budget_ms: int = 0                  # 预算上限（0=无限）
    analyzer_layer_hits: dict[str, int] = field(default_factory=dict)
    analyzer_first_hit_layer: str = ""             # 首次产出补丁的层级
    failure_code_distribution: dict[str, int] = field(default_factory=dict)
    human_gate_triggered: bool = False
    human_gate_count: int = 0
    kb_hit: bool = False                           # 是否经 KB 命中收敛
```

字段来源说明：

| 字段 | 计算来源 |
|------|----------|
| `success` | `state.terminal_state == DONE_SUCCESS` |
| `terminal_state` | `state.terminal_state.value` |
| `attempt_count` | `session.current_attempt`（终态快照） |
| `wall_clock_used_ms` | `(perf_counter() - _session_start) * 1000` |
| `wall_clock_budget_ms` | `session.wall_clock_limit * 1000` |
| `analyzer_layer_hits` | 埋点计数器 `_layer_hits` |
| `analyzer_first_hit_layer` | 埋点计数器 `_first_hit_layer` |
| `failure_code_distribution` | 埋点计数器 `_fc_dist` |
| `human_gate_triggered` | `_hg_count > 0` |
| `human_gate_count` | 埋点计数器 `_hg_count` |
| `kb_hit` | 埋点布尔 `_kb_hit` |

### 3.2 LoopSession 加字段

```python
@dataclass
class LoopSession:
    # ... 现有 12 字段不变 ...
    metrics: SessionMetrics | None = None          # G9 新增：终态指标快照
```

- 用 `None` 表示"未终态/未计算"，避免运行中误读半成品指标。
- 序列化：`None` → 不输出 `metrics` key（向后兼容旧 session.json）；非 None → 输出 dict。

### 3.3 PatchSuggestion 加字段

位置：`engineering/loop/controller/python/loop_controller/analyzer_protocol.py`

```python
@dataclass
class PatchSuggestion:
    target_files: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    rationale: str = ""
    matched_layer: str = ""                        # G9 新增：命中层级名
```

- `ChainedAnalyzer.analyze()` 命中时填充 `type(layer).__name__`（如 `"KnowledgeBaseAnalyzer"`）。
- 单层 analyzer 直接使用时留空 `""`，engine 兜底判 `"unknown"`。
- 向后兼容：旧代码不填此字段，default `""`。

### 3.4 __all__ 导出更新

`engineering/loop/contracts/python/loop_contracts/__init__.py`：

```python
__all__ = [
    "AttemptState", "CheckpointRecord", "FailureCode", "LoopSession",
    "RuntimeState", "RuntimeTerminalState", "SessionState",
    "StageResult", "TerminationDecision",
    "SessionMetrics",                               # G9 新增
]
```

### 3.5 G8 元测试守护更新

- `test_docs_consistency.py` 中 dataclass 计数守护：当前 9 → **10**（加 SessionMetrics）。
- `contracts/README.md` 同步补 SessionMetrics 说明。

---

## 4. engine 埋点 + 终态聚合

### 4.1 engine 新增实例变量（`__init__`）

```python
# G9 指标埋点计数器（随 engine 生命周期，终态时快照）
self._layer_hits: dict[str, int] = {}        # analyzer 层级 → 命中次数
self._first_hit_layer: str = ""              # 首次产出补丁的层级
self._hg_count: int = 0                      # human gate 触发次数
self._fc_dist: dict[str, int] = {}           # failure_code → 出现次数
self._kb_hit: bool = False                   # 是否经 KB 命中（任意一次）
```

### 4.2 埋点位置

#### (a) analyzer 层级埋点 — `_execute_wait_analyzer_patch()`

在 `suggestion = self._analyzer.analyze(request)` 之后：

```python
suggestion = self._analyzer.analyze(request)
if suggestion.target_files:
    layer = suggestion.matched_layer or "unknown"       # G9
    self._layer_hits[layer] = self._layer_hits.get(layer, 0) + 1
    if not self._first_hit_layer:
        self._first_hit_layer = layer
    if layer == "KnowledgeBaseAnalyzer":
        self._kb_hit = True
```

- 单层 analyzer（非 ChainedAnalyzer）不填 `matched_layer` → 兜底 `"unknown"`，不影响埋点。

#### (b) human gate 埋点 — 触发 `pending_human_gate=True` 的位置

在现有触发 human gate 的位置增量计数：

```python
self._state.pending_human_gate = True
self._hg_count += 1                                      # G9
```

- 只计触发次数，approve/reject 不重复计数（一次 gate 算 1）。

#### (c) failure_code 分布 — `_checkpoint()`

```python
def _checkpoint(self, reason: str, failure_code: FailureCode = FailureCode.NONE,
                next_node: str = "", duration_ms: int = -1):
    # ... 现有逻辑 ...
    # G9: 累积 failure_code 分布
    code = failure_code.value if failure_code else "NONE"
    self._fc_dist[code] = self._fc_dist.get(code, 0) + 1
```

- 每次 checkpoint 都记（含 `NONE`），让分布可解释"多少步是无故障推进、多少步是各类失败"。

### 4.3 终态聚合 — 新增 `_compute_session_metrics()`

在 `run()` 退出前、`_persist_session()` 前调用：

```python
def _compute_session_metrics(self) -> SessionMetrics:
    """G9: 终态时把实例变量 + wall_clock 快照为 SessionMetrics。"""
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

### 4.4 run() 终态改动

```python
def run(self, max_iterations: int = 100) -> RuntimeState:
    self._session_start = time.perf_counter()
    iterations = 0
    while ...:
        # ... 现有主循环（含 G5 wall_clock 预算闸）...
        pass
    # G9: 终态聚合指标
    self._session.metrics = self._compute_session_metrics()
    self._persist_session()
    return self._state
```

### 4.5 resume 场景的指标连续性

`resume()` 会新建 engine 实例。为避免 resume 后埋点计数器从 0 重来，采取**简化重建策略**：

- **能从 checkpoint 重建的**：`_fc_dist`（checkpoint 记录了每次的 failure_code）。
- **无法重建的**：`_layer_hits` / `_first_hit_layer` / `_hg_count` / `_kb_hit`（checkpoint 不记录这些）→ 从 0 起。

重建逻辑放在 `resume()` 方法开头：

```python
def resume(self, max_iterations: int = 100) -> RuntimeState:
    self._session_start = time.perf_counter()   # 重置起始时间
    # G9: 从 checkpoint 重建 failure_code 分布
    self._rebuild_fc_dist_from_checkpoints()
    # ... 现有 resume 逻辑 ...

def _rebuild_fc_dist_from_checkpoints(self) -> None:
    """从当前 session 的全部 checkpoint 重建 failure_code 分布。"""
    records = self._ckpt_store.all()
    for r in records:
        code = r.failure_code.value if r.failure_code else "NONE"
        self._fc_dist[code] = self._fc_dist.get(code, 0) + 1
```

**理由**：
- resume 在实践中罕见（多用于 HITL 后续跑），指标偏差可接受。
- 不引入 `incomplete` 字段（避免增加契约复杂度）。
- 若未来需要精确，可在 checkpoint 中补充层级/gate 信息（增量演进）。

---

## 5. 跨 session 聚合命令

### 5.1 新增 `le runtime stats` 子命令

**注册**（runtime_cli.py 命令注册表）：

```
stats    _handle_stats    跨 session 聚合指标，输出 JSON
```

**签名**：

```
le runtime stats [--artifacts-dir <path>]
```

- `--artifacts-dir`：可选，默认取 `analyzer.yaml` 中配置的 artifacts 根目录（与 `init` 命令同源）。
- 无 session_id 参数（全量遍历）。

### 5.2 _handle_stats 处理逻辑

```python
def _handle_stats(args: argparse.Namespace) -> int:
    artifacts_dir = args.artifacts_dir or _read_artifacts_dir_from_config()
    sessions = _scan_session_metrics(artifacts_dir)   # 遍历 artifacts/*/session.json
    if not sessions:
        print(json.dumps({"total": 0, "summary": "no sessions found"}, indent=2))
        return 0
    aggregated = _aggregate_metrics(sessions)
    print(json.dumps(aggregated, indent=2, ensure_ascii=False))
    return 0
```

### 5.3 遍历函数 _scan_session_metrics

```python
def _scan_session_metrics(artifacts_dir: str) -> list[dict]:
    """遍历 artifacts/<session_id>/session.json，返回含 metrics 段的 session 列表。"""
    result = []
    base = Path(artifacts_dir)
    if not base.is_dir():
        return result
    for session_dir in sorted(base.iterdir()):
        sf = session_dir / "session.json"
        if not sf.is_file():
            continue
        try:
            data = json.loads(sf.read_text())
        except (json.JSONDecodeError, OSError):
            continue                          # 跳过损坏文件
        if data.get("metrics"):               # 只收有 metrics 段的（已终态）
            result.append(data)
    return result
```

- 按 session_id 目录名字典序，保证输出稳定。

### 5.4 聚合函数 _aggregate_metrics 输出结构

```json
{
  "total_sessions": 10,
  "success_count": 7,
  "failure_count": 3,
  "success_rate": 0.70,
  "avg_wall_clock_ms": 124500,
  "median_wall_clock_ms": 98000,
  "avg_attempt_count": 2.8,
  "first_fix_success_rate": 0.40,
  "analyzer_layer_hits_total": {
    "KnowledgeBaseAnalyzer": 5,
    "ScriptedAnalyzer": 2,
    "OpencodeAnalyzer": 3,
    "unknown": 0
  },
  "analyzer_first_hit_layer_distribution": {
    "KnowledgeBaseAnalyzer": 4,
    "ScriptedAnalyzer": 2,
    "OpencodeAnalyzer": 4
  },
  "failure_code_distribution_total": {
    "NONE": 15,
    "RUN_FAILED": 8,
    "COMPILE_FAILED": 3
  },
  "kb_hit_rate": 0.50,
  "human_gate_triggered_rate": 0.20,
  "avg_human_gate_count": 0.3,
  "by_target": {
    "lcview": {"total": 6, "success": 5, "success_rate": 0.83},
    "kernel": {"total": 4, "success": 2, "success_rate": 0.50}
  },
  "by_suite": {
    "lcview-hal": {"total": 4, "success": 4, "success_rate": 1.0},
    "network-adbsd": {"total": 3, "success": 2, "success_rate": 0.67}
  }
}
```

### 5.5 关键指标定义

| 指标 | 计算方式 |
|------|----------|
| `success_rate` | `success_count / total_sessions` |
| `first_fix_success_rate` | `analyzer_first_hit_layer != ""` 且 `attempt_count == 1` 且 `success` 的 session 数 / total |
| `avg_*` | 算术平均 |
| `median_wall_clock_ms` | 排序取中位数（偶数取中间两数均值） |
| `kb_hit_rate` | `kb_hit=True` 的 session 数 / total |
| `analyzer_layer_hits_total` | 所有 session 的 `analyzer_layer_hits` 按层名求和 |
| `by_target` / `by_suite` | 按 session 的 `target` / `suite` 字段分组，每组算 total/success/success_rate |

### 5.6 status 命令增强

`_handle_status` 已在 G5 输出 `trace_summary`。G9 仅需确认 `_session_to_dict` 会把 `metrics` 段透传：

```python
# _session_to_dict 中补充
data["metrics"] = _metrics_to_dict(session.metrics) if session.metrics else None
```

### 5.7 错误处理

| 场景 | 处理 |
|------|------|
| artifacts_dir 不存在 | 输出 `{"total": 0}` |
| session.json 损坏 | 跳过该 session，继续聚合其余 |
| session.json 无 metrics 段（未终态） | 跳过（不计入 total_sessions） |
| 全部 session 无 metrics | 输出 `{"total": 0, "summary": "no terminated sessions"}` |

---

## 6. ChainedAnalyzer 层级填充

### 6.1 实现改动

`analyzer_protocol.py` `ChainedAnalyzer.analyze()`：

```python
class ChainedAnalyzer(LlmAnalyzer):
    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        for layer in self._layers:
            try:
                suggestion = layer.analyze(request)
            except Exception:
                continue
            if suggestion.target_files:
                # G9: 记录命中层级 + 保留 rationale 前缀（向后兼容）
                suggestion.matched_layer = type(layer).__name__
                suggestion.rationale = f"[{type(layer).__name__}] {suggestion.rationale}"
                return suggestion
        return PatchSuggestion(target_files=[], confidence=0.0,
                               rationale="三层 analyzer 均无产出")
```

- `matched_layer` 和 rationale 前缀**双写**：前者给 G9 结构化统计，后者保留给 prompt/日志可读性（已有行为，不破坏）。
- 三层均无产出时返回的空 `PatchSuggestion` 不填 `matched_layer`（保持 `""`），engine 不计数。

---

## 7. 测试策略

### 7.1 TDD 流程

所有改动严格遵循 RED → GREEN：
1. 先写复现测试（断言新行为），确认 RED。
2. 再改实现，确认 GREEN。
3. 禁止用错误假数据掩盖 bug（P0 检视教训）。

### 7.2 测试矩阵

| 测试文件 | 新增/扩展 | 关键测试用例 |
|----------|-----------|--------------|
| **test_runtime_engine.py** | 扩展 | `test_run_computes_session_metrics` — 跑完整 pass 路径，断言 `session.metrics` 非 None 且字段正确 |
| | | `test_run_metrics_success_path` — 验证 success=True / attempt_count / wall_clock_used_ms > 0 |
| | | `test_run_metrics_failure_path` — max_iterations 触发 DONE_FAILURE，验证 success=False / terminal_state |
| | | `test_run_metrics_analyzer_layer_hits` — KB 命中收敛，验证 `layer_hits` / `first_hit_layer` / `kb_hit=True` |
| | | `test_run_metrics_human_gate_count` — 触发 HITL，验证 `human_gate_triggered` / `human_gate_count` |
| | | `test_run_metrics_failure_code_distribution` — 多轮失败，验证 `_fc_dist` 含各码 |
| | | `test_resume_rebuilds_fc_dist` — resume 后 `_fc_dist` 从 checkpoint 重建 |
| **test_chained_analyzer.py** | 扩展 | `test_chained_fills_matched_layer_kb` — KB 层命中，验证 `suggestion.matched_layer` |
| | | `test_chained_fills_matched_layer_scripted` — Scripted 层命中 |
| | | `test_chained_no_match_leaves_matched_layer_empty` — 三层均空 |
| **test_runtime_cli.py** | 扩展 | `test_status_outputs_metrics` — status 输出含 `metrics` 段 |
| | | `test_stats_command_no_sessions` — 空目录输出 `{"total": 0}` |
| | | `test_stats_command_aggregates` — 构造 3 个 session.json（2 成功 1 失败），验证 success_rate / by_target / by_suite |
| | | `test_stats_command_skips_no_metrics` — 无 metrics 段的 session 被跳过 |
| | | `test_stats_command_skips_corrupted` — 损坏 json 被跳过 |
| | | `test_stats_command_median_wall_clock` — 偶数 session 取中位数 |
| **test_docs_consistency.py** | 扩展 | dataclass 计数 9 → 10；contracts/README 同步 |
| **test_session_metrics.py**（可选新建） | 新建 | `SessionMetrics` 序列化/反序列化往返测试 |

### 7.3 测试数量预估

- 新增约 **18-20 个测试**。
- 测试基线：632 → 预计 ~650-652。

### 7.4 全量回归

每批 task 完成后跑全量回归：

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
python3 -m pytest engineering/loop/controller/python/tests/ engineering/loop/core/python/tests/ engineering/loop/connection/providers/rp5-serial/python/tests/ engineering/loop/connection/providers/adb/python/tests/ engineering/loop/deploy/python/tests/ engineering/loop/contracts/python/tests/ -q --import-mode=importlib
```

---

## 8. 文档同步清单

| 文件 | 改动 |
|------|------|
| `contracts/README.md` | 补 SessionMetrics 字段说明；dataclass 计数 9 → 10 |
| `controller/README.md` | 补 `le runtime stats` 子命令说明；metrics 段说明 |
| `contracts/__init__.py` | `__all__` 加 `SessionMetrics` |
| `engineering/loop/WORKFLOW.md` | `le runtime` 子命令列表补 `stats` |

---

## 9. 实施顺序（task 拆分预告）

> 详细 task 拆分由后续 writing-plans 阶段产出，此处仅预告依赖关系。

```
Task 1: contracts — SessionMetrics + LoopSession.metrics（TDD）
Task 2: contracts — PatchSuggestion.matched_layer（TDD）
   ↓
Task 3: analyzer — ChainedAnalyzer 填充 matched_layer（TDD）
   ↓
Task 4: engine — 实例变量 + 三处埋点（TDD）
Task 5: engine — _compute_session_metrics + run() 终态调用（TDD）
Task 6: engine — resume 场景 _rebuild_fc_dist_from_checkpoints（TDD）
   ↓
Task 7: cli — _session_to_dict/_load_session metrics 序列化（TDD）
Task 8: cli — _handle_stats + _scan_session_metrics + _aggregate_metrics（TDD）
   ↓
Task 9: 文档 + G8 元测试同步
   ↓
Task 10: 全量回归 + 推送
```

---

## 10. 风险与缓解

| 风险 | 缓解 |
|------|------|
| resume 后指标不完整 | 设计已声明：仅重建 `_fc_dist`，其余从 0；属可接受偏差 |
| 旧 session.json 无 metrics 段 | `_load_session` 兼容（`metrics=None`）；`stats` 命令跳过 |
| analyzer 单层使用时 `matched_layer` 为空 | engine 兜底为 `"unknown"`，不影响埋点 |
| `_hg_count` 多处触发点遗漏 | 代码审查 + 专项测试覆盖所有 human gate 触发路径 |
| stats 聚合大量 session 性能 | 本期全量遍历，单 session 只读一个 json；后续可加索引 |
