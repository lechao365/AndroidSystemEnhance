# G5 loop 可观测 + 预算闸设计

> 日期：2026-06-29
> 关联：`2026-06-28-loop-engineering-comprehensive-review-report.md` §7 G5
> 范围：仅 `engineering/loop/`，不涉及 `~/workspace/` 源码树
> 改动纪律：严格 TDD（先写复现失败测试，再改实现）

---

## 1. 背景与目标

检视报告 §7 G5 指出：

> **G5 缺 loop 级可观测与预算**：checkpoint JSONL 为流水，无聚合视图；无 token/时间/编译次数预算总账。

探索确认现状：

- CheckpointStore 只负责 JSONL append + 逐行读取，**无任何聚合方法**
- CheckpointRecord 仅 10 字段，**无 duration_ms / token_count / cost 字段**
- engine.py 的 `_checkpoint()` 不记录节点耗时（无 `time.perf_counter()`）
- OpencodeAnalyzer 调用 opencode 后**丢弃所有 token/usage 信息**
- 现有限制仅 `max_attempts`（计数闸，默认 5）和 `max_iterations`（死循环防护，默认 100），**无时间/token 闸**
- CLI `status` 只 dump session.json 原始字段，无聚合视图

### 1.1 本期范围（用户确认）

**观测为主，预算为辅**：

- trace 聚合视图（节点级耗时 + 总耗时 + CLI 增强）
- wall_clock 预算闸（session 总耗时上限，超时即 DONE_FAILURE）

**本期不做**（留作后续）：

- token 预算（需解析 opencode JSONL usage 事件，改动大）
- 编译次数预算（COMPILE_PATCH 节点计数）
- attempt 级耗时上限

### 1.2 成功标准

1. engine 每次节点执行后，checkpoint 记录非零 `duration_ms`
2. CLI `le runtime status` 输出含 `trace_summary` 段（节点计数、总耗时、逐节点列表）
3. `analyzer.yaml` 配置 `budget.wall_clock_seconds` 后，超限触发 `DONE_FAILURE` + `WALL_CLOCK_BUDGET_EXCEEDED`
4. `wall_clock_seconds = 0`（默认）时不限制
5. 旧 checkpoint JSONL / session.json 反序列化向后兼容

---

## 2. 数据模型变更（contracts 层）

### 2.1 CheckpointRecord 扩展

文件：`engineering/loop/contracts/python/loop_contracts/models.py` 第 67-83 行。

新增 1 个字段：

```python
@dataclass
class CheckpointRecord:
    checkpoint_id: str
    session_id: str
    attempt_index: int
    current_node: str
    input_summary: dict
    output_summary: dict
    failure_code: FailureCode
    matched_guards: list[str]
    next_node: str
    timestamp: str
    duration_ms: int = 0          # 新增：节点执行耗时（毫秒）
```

- 放最后 + 默认值 0，向后兼容（旧 JSONL 反序列化自动填 0）
- `to_dict()` 自动包含（dataclass 序列化）

### 2.2 LoopSession 扩展

同文件第 38-50 行，新增 1 个字段：

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
    wall_clock_limit: int = 0      # 新增：session 总耗时上限（秒），0=不限制
```

- 默认 0 = 不限制，向后兼容
- 由 analyzer.yaml 的 `budget.wall_clock_seconds` 注入到 session

### 2.3 新增 FailureCode

文件：`engineering/loop/contracts/python/loop_contracts/failure_codes.py`。

```python
WALL_CLOCK_BUDGET_EXCEEDED = "WALL_CLOCK_BUDGET_EXCEEDED"
```

- FailureCode 17→18 项
- 复用现有命名风格（大写 + 下划线）

### 2.4 不改动

- `RuntimeState`、`RuntimeTerminalState`、`AttemptState`、`StageResult`、`TerminationDecision` 不动
- `SessionState` alias 不动

---

## 3. engine 层改动（controller/runtime）

### 3.1 主循环统一计时

文件：`engineering/loop/controller/python/loop_controller/runtime/engine.py`。

**核心思路**：在 `run()` 主循环中统一计时，而非改 17 处 `_checkpoint()` 调用点。

```python
def run(self, max_iterations: int = 100) -> None:
    self._session_start = time.perf_counter()   # 新增
    self._last_node_duration_ms = 0              # 新增
    ...
    for _ in range(max_iterations):
        node = self._current_node
        t_start = time.perf_counter()            # 新增：节点开始

        self._dispatch_node(node)                 # 现有：执行节点逻辑

        elapsed = time.perf_counter() - t_start
        self._last_node_duration_ms = int(elapsed * 1000)   # 暂存

        # wall_clock 预算检查
        if self._session.wall_clock_limit > 0:
            wall = time.perf_counter() - self._session_start
            if wall > self._session.wall_clock_limit:
                self._set_terminal(
                    RuntimeTerminalState.DONE_FAILURE,
                    f"wall_clock budget exceeded: {wall:.0f}s > {self._session.wall_clock_limit}s",
                    FailureCode.WALL_CLOCK_BUDGET_EXCEEDED,
                )
                self._checkpoint(
                    "wall_clock budget exceeded",
                    FailureCode.WALL_CLOCK_BUDGET_EXCEEDED,
                    duration_ms=self._last_node_duration_ms,
                )
                break
    ...
```

### 3.2 _checkpoint() 取暂存值

`_checkpoint()` 方法改为优先取暂存的 `duration_ms`：

```python
def _checkpoint(self, reason, failure_code, matched_guards=None, duration_ms=None):
    if duration_ms is None:
        duration_ms = getattr(self, "_last_node_duration_ms", 0)
    cp = CheckpointRecord(
        ...,
        duration_ms=duration_ms,
    )
    self._ckpt_store.save(cp)
```

**好处**：

- 只改 `run()` + `_checkpoint()` 两处，17 个调用点零改动
- `_dispatch_node()` 内部不需感知计时
- wall_clock 超时的 `_checkpoint()` 仍可手动传 `duration_ms`

### 3.3 初始化

engine 的 `__init__` 或 `_init_session` 中新增：

```python
self._session_start = 0.0
self._last_node_duration_ms = 0
```

`run()` 入口设 `self._session_start = time.perf_counter()`。

### 3.4 wall_clock_limit 注入

`runtime_cli.py` 构造 LoopSession 时从 analyzer.yaml 读取 budget 配置：

```python
budget_cfg = analyzer_config.get("budget", {})
wall_clock_limit = budget_cfg.get("wall_clock_seconds", 0)

session = LoopSession(
    ...,
    wall_clock_limit=wall_clock_limit,
)
```

---

## 4. CheckpointStore 聚合 + CLI 增强

### 4.1 CheckpointStore.summary()

文件：`engineering/loop/controller/python/loop_controller/runtime/checkpoint_store.py`。

新增聚合方法：

```python
def summary(self) -> dict:
    """聚合当前 session 的 checkpoint 流水为 trace 摘要。"""
    records = self.all()
    if not records:
        return {"node_count": 0, "total_duration_ms": 0, "nodes": []}

    total_ms = sum(r.duration_ms for r in records)
    return {
        "node_count": len(records),
        "total_duration_ms": total_ms,
        "total_duration_human": _format_duration(total_ms),
        "nodes": [
            {
                "node": r.current_node,
                "attempt": r.attempt_index,
                "duration_ms": r.duration_ms,
                "failure_code": r.failure_code.value if r.failure_code else "",
                "reason": r.output_summary.get("reason", ""),
                "timestamp": r.timestamp,
            }
            for r in records
        ],
    }


def _format_duration(ms: int) -> str:
    """毫秒 → 人类可读（如 '2m 30s' / '1h 5m'）。"""
    s = ms / 1000
    if s < 60:
        return f"{s:.1f}s"
    m, s = divmod(int(s), 60)
    if m < 60:
        return f"{m}m {s}s"
    h, m = divmod(m, 60)
    return f"{h}h {m}m"
```

**设计要点**：

- `total_duration_ms` 用 `duration_ms` 求和（节点耗时精确值）
- `_format_duration` 人类可读输出（秒/分/时边界）

### 4.2 CLI status 增强

文件：`engineering/loop/controller/python/loop_controller/runtime_cli.py`，`_handle_status`。

输出增加 `trace_summary` 段：

```python
def _handle_status(self, args) -> None:
    session = self._load_session(args.session)
    base = self._session_to_dict(session)

    # 新增：trace 聚合
    ckpt_store = CheckpointStore(session.artifacts_dir, session.session_id)
    base["trace_summary"] = ckpt_store.summary()

    print(json.dumps(base, indent=2, ensure_ascii=False))
```

**输出示例**（节选）：

```json
{
  "session_id": "s1",
  "current_attempt": 2,
  "status": "RUNNING",
  ...,
  "trace_summary": {
    "node_count": 8,
    "total_duration_ms": 145200,
    "total_duration_human": "2m 25s",
    "nodes": [
      {"node": "INIT_SESSION", "attempt": 0, "duration_ms": 12, ...},
      {"node": "RUN_VERIFY", "attempt": 0, "duration_ms": 45200, ...},
      ...
    ]
  }
}
```

### 4.3 analyzer.yaml 配置

文件：`engineering/loop/config/analyzer.yaml`。

新增 budget 段：

```yaml
budget:
  wall_clock_seconds: 3600   # session 总耗时上限，0=不限制
```

---

## 5. 测试策略（TDD）

| 测试 | 验证点 | 阶段 |
|------|--------|------|
| `test_checkpoint_record_duration_ms_default` | 新字段默认 0，旧 JSON 反序列化兼容 | GREEN |
| `test_checkpoint_record_serialization_with_duration` | `to_dict()` 含 `duration_ms` | RED→GREEN |
| `test_loop_session_wall_clock_limit_default` | 新字段默认 0（不限制） | GREEN |
| `test_checkpoint_store_summary_aggregates_duration` | `summary()` 正确聚合 total_duration_ms + nodes 列表 | RED→GREEN |
| `test_checkpoint_store_summary_empty` | 无 checkpoint 时返回零值 | RED→GREEN |
| `test_format_duration_human_readable` | 毫秒→人类可读（秒/分/时边界） | RED→GREEN |
| `test_engine_records_duration_ms` | engine 执行节点后 checkpoint 含非零 duration_ms | RED→GREEN |
| `test_engine_wall_clock_budget_exceeds` | 超限时设 DONE_FAILURE + FailureCode.WALL_CLOCK_BUDGET_EXCEEDED | RED→GREEN |
| `test_engine_wall_clock_zero_means_unlimited` | wall_clock_limit=0 时不触发预算闸 | GREEN |
| `test_runtime_status_includes_trace_summary` | CLI status 输出含 `trace_summary` 段 | RED→GREEN |

---

## 6. G8 元测试同步 + 文档同步

### 6.1 G8 元测试更新

文件：`engineering/loop/controller/python/tests/test_docs_consistency.py`。

新增 2 个守护点（原 8 个 → 10 个）：

| # | 守护目标 | 断言方式 |
|---|---------|---------|
| 9 | FailureCode 成员数=18 | `len(list(FailureCode)) == 18` + README 含 "18 项" |
| 10 | FailureCode 含 `WALL_CLOCK_BUDGET_EXCEEDED` | 名字出现在 contracts/README.md |

同时已有守护点 1 的硬编码 `17` → `18`。

### 6.2 文档同步

- `contracts/README.md`：FailureCode 17→18 项，列出 `WALL_CLOCK_BUDGET_EXCEEDED`
- `controller/README.md`：Terminal State 段落补充 wall_clock 超时说明

---

## 7. 文件变更清单

| 文件 | 动作 | 说明 |
|------|------|------|
| `contracts/python/loop_contracts/models.py` | 修改 | CheckpointRecord + LoopSession 各加 1 字段 |
| `contracts/python/loop_contracts/failure_codes.py` | 修改 | 新增 WALL_CLOCK_BUDGET_EXCEEDED |
| `controller/python/loop_controller/runtime/engine.py` | 修改 | run() 加计时 + wall_clock 检查；_checkpoint() 加 duration_ms |
| `controller/python/loop_controller/runtime/checkpoint_store.py` | 修改 | 新增 summary() + _format_duration() |
| `controller/python/loop_controller/runtime_cli.py` | 修改 | _handle_status() 加 trace_summary；session 初始化读 budget 配置 |
| `config/analyzer.yaml` | 修改 | 新增 budget.wall_clock_seconds |
| `controller/python/tests/test_docs_consistency.py` | 修改 | G8 守护点更新（17→18，新增 2 个） |
| `contracts/README.md` | 修改 | FailureCode 17→18 |
| `controller/README.md` | 修改 | Terminal State 补充 wall_clock 说明 |
| `controller/python/tests/test_runtime_engine.py` | 修改 | 新增 wall_clock + duration 测试 |
| `controller/python/tests/test_checkpoint_store.py` | 修改 | 新增 summary 测试 |
| `contracts/python/tests/test_models.py` | 修改 | 新增字段默认值测试 |
| `contracts/python/tests/test_runtime_models.py` | 修改 | CheckpointRecord 序列化测试 |

---

## 8. 验收标准

### 8.1 功能验收

- [ ] CheckpointRecord 新增 `duration_ms` 字段，engine 写入非零值
- [ ] CheckpointStore.summary() 返回正确的聚合视图
- [ ] CLI `le runtime status` 输出含 `trace_summary` 段
- [ ] wall_clock_limit > 0 时超限触发 DONE_FAILURE + 正确 FailureCode
- [ ] wall_clock_limit = 0 时不限制
- [ ] analyzer.yaml 的 budget 配置被正确读取

### 8.2 兼容性验收

- [ ] 旧 checkpoint JSONL（无 duration_ms）反序列化不报错，填默认 0
- [ ] 旧 session.json（无 wall_clock_limit）反序列化不报错，填默认 0

### 8.3 回归验收

- [ ] 全量测试通过（基线 617 + G5 新增 10 = 627）
- [ ] G8 元测试更新后 10 个全通过

---

## 9. 风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| wall_clock 检查在 human gate 暂停期间持续累积导致误触发 | 中 | wall_clock 用 `time.perf_counter()` 测量真实墙钟时间（含 human gate 等待）；若需排除等待时间，后续可改为仅累计节点执行时间。本期按真实墙钟实现，超时即停是正确语义 |
| 旧 JSONL 反序列化失败 | 极低 | 新字段有默认值 + dataclass 自动兼容；CheckpointStore 已有坏行容错（P2-1） |
| duration_ms 精度问题 | 低 | `time.perf_counter()` 精度足够（微秒级），取整为毫秒后最小值 1ms，足够区分节点耗时 |
