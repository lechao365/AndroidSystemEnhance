# G2：分支探索（best-of-N）设计

- **状态**：设计已确认，待实施
- **日期**：2026-07-01
- **关联**：[全面审查报告](./2026-06-28-loop-engineering-comprehensive-review-report.md) §7 G2、[G9 评测基线](./2026-06-30-loop-gap-g9-evaluation-baseline-design.md)
- **前置条件**：G3（轨迹上下文）、G5（可观测+预算）、G8（文档元测试）、G9（评测基线）均已完成

---

## 1. 背景与动机

### 1.1 现状（"单线性修复"）

当前 loop 引擎每次 attempt 只产出一个补丁、只 apply 一次、只走一条路径。失败后 revert 重来。`progress_converging` guard 仅做 `failed_count` 单调下降判定，没有"回退换方向"能力。

**10 个"单线性"证据点**：

| # | 位置 | 证据 |
|---|------|------|
| 1 | `engine.py:28-36` `_LINEAR_NEXT` | 状态转换表每节点唯一下一节点，无分支扇出 |
| 2 | `engine.py:635-657` `_compute_next_node` | 无候选分支决策逻辑 |
| 3 | `analyzer_protocol.py:51-54` `LlmAnalyzer.analyze` | 协议返回单个 `PatchSuggestion` |
| 4 | `analyzer_protocol.py:656-681` `ChainedAnalyzer.analyze` | 三层降级短路，首个非空即返回 |
| 5 | `engine.py:565,593-602` `patch_suggestion.json` | 全局单文件，覆盖写 |
| 6 | `workspace_isolation.py:67-68` worktree 命名 | 按 `<session_id>_<attempt_index>` 无 candidate 维度 |
| 7 | `models.py:69-81` `CheckpointRecord` | 无 `candidate_id`/`branch_id` |
| 8 | `models.py:38-52` `LoopSession.attempts` | 一个 attempt dict 对应一条线性轨迹 |
| 9 | `engine.py:238-241` `node_apply_patch` 调用 | 单次调用，无候选遍历 |
| 10 | `stages.py:285-295` `analyze_request_stage` | `AnalysisRequest` 无 `candidate_index` |

### 1.2 业界做法

- **AlphaCodium**：flow 架构，多分支并行探索，公共测试筛选
- **SWE-agent / OpenHands**：trajectory 驱动，支持候选生成与择优
- **LangGraph**：状态图支持条件边，天然支持分支

### 1.3 G2 目标

在不破坏现有单线性向后兼容的前提下，引入 **best-of-N 候选评估**：analyzer 一次产 N 个候选补丁，经 compile 筛 + verify 比两阶段淘汰，选 `failed_count` 最小者为正式 attempt。

---

## 2. 设计决策

| 决策项 | 选择 | 理由 |
|--------|------|------|
| **复杂度** | B：隔离 best-of-N | worktree 隔离保证评估干净，避免 C（树搜索）的分支管理复杂度；与 G9 指标配合可 A/B 验证 |
| **候选来源** | 策略3：配置驱动混合 | KB/Scripted 各产≤1个（确定性，免费），OpencodeAnalyzer 温度采样补足到 N；不足时优雅降级 |
| **评估方式** | 两阶段淘汰 | compile 筛（免费本地）→ 通过的串行 deploy+verify+revert 比拼 → 选 `failed_count` 最小者 |
| **隔离机制** | worktree 隔离 | 每个候选独立 worktree，compile 互不污染；设备串行复用 |
| **默认值** | `candidates=1`（单线性）| 向后兼容，零开销 |

---

## 3. 整体架构

```
INIT → RUN_VERIFY → DECIDE_NEXT ──────┐
                       │              │
                       ├─ FAIL ──────► BUILD_ANALYSIS_REQUEST
                       │              │
                       │              ▼
                       │         WAIT_ANALYZER_PATCH
                       │         (产 N 候选)
                       │              │
                       │              ▼
                       │    SELECT_BEST_CANDIDATE  ★新增
                       │     ├─ candidate 0: worktree_0 → compile → (pass?)─┐
                       │     ├─ candidate 1: worktree_1 → compile → (pass?)─┤
                       │     └─ candidate 2: worktree_2 → compile → (pass?)─┘
                       │                                    │
                       │                     compile 通过者 / 串行 deploy+verify+revert
                       │                                    │
                       │                                    ▼
                       │                          选 failed_count 最小者
                       │                                    │
                       │                   APPLY_PATCH(胜出者) → DEPLOY → RUN_VERIFY
                       │
                       └─ PASS ───────► DONE_SUCCESS
```

**向后兼容**：`candidates=1` 时，`SELECT_BEST_CANDIDATE` 透传（不做评估），行为与现有完全一致。

---

## 4. 协议层变更

### 4.1 `LlmAnalyzer.analyze_n`

```python
class LlmAnalyzer(ABC):
    @abstractmethod
    def analyze(self, request: AnalysisRequest) -> PatchSuggestion: ...

    def analyze_n(self, request: AnalysisRequest, n: int) -> list[PatchSuggestion]:
        """默认实现：循环调 analyze。子类可重写以提供差异化候选。"""
        results = []
        for _ in range(n):
            sug = self.analyze(request)
            if sug.target_files:
                results.append(sug)
        return results
```

### 4.2 `ChainedAnalyzer.analyze_n`

收集所有层非空产出，不短路。确定性层（KB/Scripted）只产 1 个，LLM 层补足剩余。

```python
class ChainedAnalyzer(LlmAnalyzer):
    def analyze_n(self, request: AnalysisRequest, n: int) -> list[PatchSuggestion]:
        candidates: list[PatchSuggestion] = []
        for layer in self._layers:
            remaining = n - len(candidates)
            if remaining <= 0:
                break
            try:
                # 确定性层只产 1 个；OpencodeAnalyzer 产 remaining 个
                layer_n = 1 if not isinstance(layer, OpencodeAnalyzer) else remaining
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

### 4.3 `OpencodeAnalyzer.analyze_n`

n=1 时走原 `analyze`（低温度，确定性）；n>1 时高温度采样，prompt 中注入 `candidate_index` 引导差异化。

### 4.4 `PatchSuggestion` 新增字段

```python
@dataclass
class PatchSuggestion:
    target_files: list[FileChange] = field(default_factory=list)
    rationale: str = ""
    confidence: float = 0.0
    deploy_mode_hint: str = ""
    matched_layer: str = ""
    candidate_id: str = ""       # ★新增：候选标识，如 "c0", "c1"
    candidate_index: int = 0     # ★新增：采样序号（0-based）
```

### 4.5 向后兼容

- `analyze()` 签名不变；`analyze_n` 是新增方法
- `ChainedAnalyzer.analyze()`（单候选短路）保留——`candidates=1` 时引擎调它
- `candidates>1` 时引擎调 `analyze_n`

---

## 5. 状态机 + 数据模型

### 5.1 新增状态机节点

```python
class NodeKind(StrEnum):
    ...
    SELECT_BEST_CANDIDATE  # ★新增
```

`_LINEAR_NEXT` 变更：

```python
# 旧
WAIT_ANALYZER_PATCH: APPLY_PATCH
# 新
WAIT_ANALYZER_PATCH: SELECT_BEST_CANDIDATE
SELECT_BEST_CANDIDATE: APPLY_PATCH
```

### 5.2 `CheckpointRecord` 新增字段

```python
@dataclass
class CheckpointRecord:
    ...
    candidate_id: str = ""  # ★新增：空表示非候选相关 checkpoint
```

### 5.3 `LoopSession` 新增字段

```python
@dataclass
class LoopSession:
    ...
    candidates_per_attempt: int = 1  # ★新增
```

### 5.4 候选评估记录（attempt dict 内嵌）

```json
{
  "attempt_index": 3,
  "candidates": [
    {
      "candidate_id": "c0",
      "source_layer": "KnowledgeBaseAnalyzer",
      "confidence": 0.98,
      "compile_result": {"status": "COMPILE_OK", "duration_ms": 12000},
      "verify_result": null,
      "failed_count": null,
      "selected": true
    },
    {
      "candidate_id": "c1",
      "source_layer": "OpencodeAnalyzer",
      "confidence": 0.8,
      "compile_result": {"status": "COMPILE_FAILED", "error": "..."},
      "verify_result": null,
      "failed_count": null,
      "selected": false
    }
  ],
  "selected_candidate_id": "c0",
  "failed_count": 2,
  "failure_code": "NONE"
}
```

- compile 失败的候选 `verify_result`/`failed_count` 为 null
- 只有胜出候选有 `verify_result`/`failed_count`（只有它走到正式 DEPLOY+RUN_VERIFY）
- `selected: true` 标记最终采纳的候选

### 5.5 候选产物目录

```
<artifacts_dir>/
  patch_candidates/               # ★新增
    c0_patch_suggestion.json
    c1_patch_suggestion.json
    c2_patch_suggestion.json
  patch_suggestion.json           # 胜出候选副本（向后兼容 APPLY_PATCH 读这个文件）
```

---

## 6. SELECT_BEST_CANDIDATE 节点执行逻辑

### 6.1 阶段 1：候选生成

1. 调 `ChainedAnalyzer.analyze_n(request, N)` 拿到 `candidates: list[PatchSuggestion]`
2. 为每个候选分配 `candidate_id`（`c0`, `c1`, ...）和 `candidate_index`
3. 全部写入 `patch_candidates/<id>_patch_suggestion.json`
4. **候选数 == 0** → 终态 `DONE_FAILURE`（与现有"三层无产出"语义一致）
5. **候选数 == 1** → 跳过评估，直接选该候选，写入 `patch_suggestion.json`，进入 `APPLY_PATCH`

### 6.2 阶段 2：Compile 筛（worktree 隔离）

对每个候选 `i`（`i=0..N-1`）：

1. `create_patch_worktree(ws_root, session_id, attempt_index, candidate_id="c{i}")`
   - worktree 路径 `<parent>/.loop-worktrees/<sid>_<attempt>_c<i>`
2. 在该 worktree 中 apply 候选 `i` 的补丁
3. 执行 `compile_plan`（与现有 COMPILE_PATCH 相同的编译命令）
4. 记录 `compile_result` 到候选记录
5. 失败候选标记淘汰；`worktree_keep_failed=true` 时保留，否则清理

**全部 compile 失败** → checkpoint（`FailureCode.COMPILE_FAILED`），走 `REVERT_PATCH → DECIDE_NEXT`。

### 6.3 阶段 3：Verify 比（串行 deploy+verify+revert）

`survivors = compile 通过的候选`

- **`len(survivors) == 1`** → 直接选它，跳过 verify 比拼
- **`len(survivors) > 1`**：对每个 survivor（按 confidence 降序）：
  1. deploy 到设备
  2. `run_verify` → 拿到 `failed_count`
  3. revert 设备 + 源码（恢复到评估前状态）
  4. 记录 `failed_count`

选 `failed_count` 最小者为胜出者（并列时取 confidence 高的）：
- 胜出者 worktree 保留，其余清理
- 胜出者补丁写入 `patch_suggestion.json`
- 胜出者重新 deploy + verify（"正式 verify"，消耗 attempt 配额）

### 6.4 evaluation_mode 标志

评估阶段的 verify 不应消耗 `max_attempts` 配额，也不应触发 `progress_converging`。

引入 `RuntimeState.evaluation_mode: bool`：
- 为 `True` 时 `RUN_VERIFY` 跳过 `current_attempt += 1`、跳过 checkpoint、跳过 guard chain
- 只有胜出候选的最终 verify 才走完整流程

### 6.5 退化场景

| 场景 | 行为 |
|------|------|
| `candidates=1` | `SELECT_BEST_CANDIDATE` 透传，零开销 |
| 候选数 == 0 | `DONE_FAILURE`（与现有一致）|
| 全部 compile 失败 | `REVERT → DECIDE_NEXT`，下轮 attempt |
| compile 通过但 verify 全失败 | 选 `failed_count` 最小者，走正式 apply+deploy+verify |
| 设备 revert 失败 | `ESCALATE_HUMAN`（fail-closed）|

---

## 7. Worktree 生命周期扩展

### 7.1 命名空间扩展

```python
# G2
create_patch_worktree(ws_root, session_id, attempt_index, candidate_id="")
# 分支 loop/<session_id>/<attempt_index>/<candidate_id>
# 路径  <parent>/.loop-worktrees/<session_id>_<attempt_index>_<candidate_id>
# candidate_id 为空时退化为现有命名
```

### 7.2 清理策略

- 评估完成后：胜出候选 worktree 保留（作为"正式 worktree"供后续 APPLY_PATCH 使用），落选候选 worktree 清理
- `worktree_keep_failed=true` 时 compile 失败的 worktree 也保留
- 最终收敛后（`DONE_SUCCESS` / `DONE_FAILURE`）：`_cleanup_all_worktrees` 清理所有（现有逻辑不变）

### 7.3 Analyzer 与 worktree 的关系

`OpencodeAnalyzer` 通过 `opencode` CLI 在主 workspace 产补丁（只读状态，不改代码）。G2 中候选生成仍在主 workspace 完成，只有 compile 评估在 worktree 中 apply 补丁。Analyzer 不感知 worktree。

---

## 8. 配置 + CLI + 指标扩展

### 8.1 analyzer.yaml

```yaml
# 现有字段不变
confidence:
  opencode: 0.8
  scripted: 0.95
  knowledge_base: 0.98

# ★新增
candidates: 1                    # 候选数 N（1=单线性，>1 开启 best-of-N）
candidate_sampling:
  temperature: 0.7               # OpencodeAnalyzer 采样温度（n>1 时生效）
  dedup_by_hash: true            # 相同 patch_hash 的候选去重
worktree_keep_failed: false      # 是否保留失败候选的 worktree 供 debug
```

### 8.2 CLI

```bash
# 新增 --candidates
le runtime start --target <t> --suite <s> --candidates 3

# 现有不变（candidates 默认 1）
le runtime start --target <t> --suite <s>
le runtime resume <session_id>
le runtime trace <session_id>
le runtime stats
```

`--candidates N` 存入 `LoopSession.candidates_per_attempt`。

### 8.3 SessionMetrics 扩展（G9）

```python
@dataclass
class SessionMetrics:
    # 现有 11 字段不变
    ...

    # ★新增 3 个 G2 指标
    candidates_per_attempt_avg: float = 0.0    # 平均每 attempt 候选数
    candidate_compile_pass_rate: float = 0.0   # 候选编译通过率
    candidate_selected_layer_dist: dict = field(default_factory=dict)
    # 胜出候选来源层分布：{"KnowledgeBaseAnalyzer": 2, "OpencodeAnalyzer": 1}
```

`stats` 命令的 `_aggregate_metrics` 已是字典遍历，自动包含新字段。

### 8.4 G8 元测试守护

新增 2 个守护点（11 → 13）：
- `test_select_best_candidate_in_nodekind`
- `test_candidates_per_attempt_in_loop_session_fields`

---

## 9. 错误处理 + 安全边界

### 9.1 评估阶段设备安全（fail-closed）

| 故障点 | 行为 | FailureCode |
|--------|------|-------------|
| 候选 compile 失败 | 该候选淘汰，继续评估 | COMPILE_FAILED（候选级）|
| 候选 deploy 失败（recoverable）| 该候选淘汰，revert 设备，继续评估 | DEPLOY_FAILED_RECOVERABLE |
| 候选 deploy 失败（no rollback）| **立即终止评估**，ESCALATE_HUMAN | DEPLOY_FATAL |
| 评估中设备 revert 失败 | **立即终止评估**，ESCALATE_HUMAN | ROLLBACK_FAILED |
| 设备 kernel dead | **立即终止评估**，ESCALATE_HUMAN | KERNEL_DEAD_NO_SHELL |
| 设备 transport 断连 | **立即终止评估**，ESCALATE_HUMAN | TRANSPORT_UNRECOVERABLE |

**核心原则**：评估阶段任何不可回滚的故障都立即升级人工（与 P0-4 safety fix 一致）。

### 9.2 评估中断恢复

若评估过程中进程崩溃：
- resume 从最近 checkpoint 恢复
- `current_node=SELECT_BEST_CANDIDATE` 时**重新开始整个评估**（不续跑半完成的评估）——因为设备状态不确定
- 评估中已 apply 的 worktree 通过 git/worktree 机制自然隔离，不污染主 workspace

### 9.3 budget 消费

- G5 的 `wall_clock_limit` **覆盖评估阶段**（评估在 `run()` 主循环内执行）
- `max_iterations` 按"逻辑节点"计——`SELECT_BEST_CANDIDATE` 内部即使评估 N 个候选也只消耗 1 个 iteration

### 9.4 候选去重

`candidate_sampling.dedup_by_hash: true` 时：
- 计算每个候选的 `patch_hash`（sha256 of raw_changes）
- 相同 hash 的候选只保留第一个（与现有 `duplicate_patch_hash` guard 语义一致）
- 去重后候选数可能 < N，自动降级

---

## 10. 测试策略

### 10.1 TDD 纪律

每个改动点先写复现测试（RED），再改实现（GREEN）。

### 10.2 单元测试（mock analyzer/nodes）

| 测试文件 | 测试点 | 预估数 |
|----------|--------|--------|
| `test_chained_analyzer.py` | `analyze_n` 收集多层、采样、去重、不足降级 | ~8 |
| `test_runtime_engine.py` | `SELECT_BEST_CANDIDATE`：1候选透传、全compile失败、verify比拼选最优、evaluation_mode 跳过 attempt+1 | ~10 |
| `test_runtime_engine.py` | worktree 命名带 candidate_id、清理策略 | ~4 |
| `test_runtime_cli.py` | `--candidates` 参数解析、存入 session | ~3 |
| `test_docs_consistency.py` | G8 守护点 +2 | 2 |
| `test_models.py` | `CheckpointRecord.candidate_id`、`LoopSession.candidates_per_attempt` 序列化 | ~3 |

### 10.3 集成测试（mock deploy/verify）

| 测试 | 验证 |
|------|------|
| best-of-N 全流程 | 3候选 → compile筛淘汰1 → verify比拼选最优 → 正式apply+verify |
| candidates=1 退化 | 与现有单线性行为完全一致 |
| 评估中 deploy 失败 fail-closed | DEPLOY_FATAL → ESCALATE_HUMAN |
| 候选去重 | 相同 hash 的候选合并 |

### 10.4 回归要求

- 所有现有 engine/CLI/analyzer 测试在 `candidates=1`（默认）下通过
- 特别关注：`progress_converging` guard 在 `evaluation_mode` 下不被触发
- 特别关注：`_persist_session`/`_load_session` 对新增字段的序列化往返

---

## 11. 改动范围清单

| 层 | 文件 | 改动 |
|----|------|------|
| **contracts** | `models.py` | `CheckpointRecord.candidate_id`、`LoopSession.candidates_per_attempt` |
| **contracts** | `models.py` | `SessionMetrics` 新增 3 个 G2 指标字段 |
| **analyzer** | `analyzer_protocol.py` | `LlmAnalyzer.analyze_n`、`ChainedAnalyzer.analyze_n`、`OpencodeAnalyzer.analyze_n`、`PatchSuggestion.candidate_id/index` |
| **runtime** | `types.py` | `NodeKind.SELECT_BEST_CANDIDATE` |
| **runtime** | `engine.py` | `_LINEAR_NEXT` 新增条目、`_execute_select_best_candidate()`、`evaluation_mode` 标志 |
| **runtime** | `engine.py` | `_compute_session_metrics()` 新增 G2 指标计算 |
| **runtime** | `engine.py` | `_persist_session` 序列化新字段 |
| **runtime** | `checkpoint_store.py` | checkpoint 序列化含 `candidate_id` |
| **runtime** | `nodes.py` | compile 评估复用 `node_compile_patch`（传入 worktree 路径）|
| **workspace** | `workspace_isolation.py` | `create_patch_worktree` 新增 `candidate_id` 参数 |
| **cli** | `runtime_cli.py` | `--candidates` 参数、`_build_analyzer` 传 N |
| **config** | `analyzer.yaml` | `candidates`/`candidate_sampling`/`worktree_keep_failed` |
| **tests** | `test_chained_analyzer.py` 等 | ~30 单元 + ~4 集成 |
| **docs** | `contracts/README.md` | 同步新字段、NodeKind 新节点 |
| **docs** | `controller/README.md` | `--candidates` 参数说明 |
| **docs** | `WORKFLOW.md` | SELECT_BEST_CANDIDATE 节点说明 |

---

## 12. 非目标（Out of Scope）

| 项 | 说明 |
|----|------|
| **树搜索/回退换方向** | 属于 C 方案，本次不做。失败 attempt 仍走 REVERT→DECIDE_NEXT 线性路径 |
| **G4 reward shaping** | 细粒度 reward 信号（哪个断言更接近通过），本次不做。G2 仍用 `failed_count` 粗粒度信号 |
| **并行 deploy 评估** | 需要多台设备，本次串行复用单台设备 |
| **候选生成成本控制** | OpencodeAnalyzer 采样 N 次的 token 成本，本次不设独立预算（由 G5 wall_clock 总预算覆盖）|

---

## 13. 风险与缓解

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| 评估阶段设备 revert 失败导致刷砖 | 中 | 高 | fail-closed：任何不可回滚故障立即 ESCALATE_HUMAN |
| OpencodeAnalyzer 采样产出的候选过于相似 | 中 | 中 | `dedup_by_hash` 去重 + prompt 注入 candidate_index 引导差异 |
| 评估 verify 次数过多导致 wall_clock 超时 | 低 | 中 | wall_clock 覆盖评估；用户可通过减小 `candidates` 或增大 `wall_clock_limit` 调节 |
| worktree 残留导致磁盘占满 | 低 | 中 | 评估后清理落选 worktree；`_cleanup_all_worktrees` 终态清理 |
| candidates=1 时行为变化 | 低 | 高 | SELECT_BEST_CANDIDATE 透传 + evaluation_mode 默认 False，零开销退化 |
