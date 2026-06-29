# 短期 Gap 优化设计：G3 analyzer 轨迹上下文 + G8 文档一致性元测试

> 日期：2026-06-29
> 关联：`2026-06-28-loop-engineering-comprehensive-review-report.md` §7 G3/G8
> 范围：仅 `engineering/loop/`，不涉及 `~/workspace/` 源码树
> 改动纪律：严格 TDD（先写复现失败测试，再改实现）

---

## 1. 背景与目标

检视报告 §7 识别出 9 个业界 gap，其中 G1/G6/G7 已随 P0 批次闭环。本设计覆盖剩余两个**短期高性价比** gap：

| Gap | 问题 | 目标 |
|-----|------|------|
| **G8** | README↔实现无 CI 守护，本轮手工对齐后仍可能再次漂移 | 固化 8 个对照点为元测试，防止文档与实现脱节 |
| **G3** | analyzer 仅看当前 attempt 的 failed_cases，缺历史补丁信息，导致 LLM 重复生成已失败的补丁（仅靠 duplicate_patch_hash 事后兜底，浪费一整轮 budget） | AnalysisRequest 注入"前 N 次补丁 + 失败原因"精简轨迹，OpencodeAnalyzer prompt 消费，事前避免重复 |

### 1.1 互不依赖

G8 是纯测试新增（+1 个 README 小修），零生产代码改动；G3 是 dataclass 加字段 + 两处消费改动，向后兼容。两者可并行实施。

---

## 2. G8：文档一致性元测试

### 2.1 文件落点

`engineering/loop/controller/python/tests/test_docs_consistency.py`

与现有 controller 测试同目录，复用同一 PYTHONPATH（可 `import loop_controller` + `import loop_contracts`）。

### 2.2 复用样板

直接借鉴 `engineering/loop/core/python/tests/test_diagnosis_contract_docs.py` 的两个 helper：

- `_repo_root()` — 向上递归找 `engineering/` 根目录
- `_read(relative_path)` — 读取仓库内文件文本

### 2.3 README 前置修复

`controller/README.md` 状态机图当前仅画 11 个节点，缺 `DONE_FAILURE`（实现有 12 个 NodeKind）。元测试会触发该不一致，故**先修复 README 再守护**：

- 状态机图补 `DONE_FAILURE` 节点（12→12 对齐）
- Terminal State 段落已有 `DONE_FAILURE：系统异常终止`，无需改动

### 2.4 八个守护点

| # | 守护目标 | 实现真相源 | 文档源 | 断言方式 |
|---|---------|-----------|--------|---------|
| 1 | FailureCode 成员数=17 | `len(list(FailureCode))` | contracts/README.md | README 含 "17 项" |
| 2 | FailureCode 名字逐一对应 | `FailureCode.__members__` | contracts/README.md | 每个 `.name` 都出现在 README 文本 |
| 3 | contracts `__all__` 长度=9 | `len(loop_contracts.__all__)` | contracts/README.md | README 含 "九符号"/"9" |
| 4 | contracts `__all__` 名字逐一对应 | `loop_contracts.__all__` | contracts/README.md | 每个符号名出现在 README |
| 5 | dataclass 数=6 | 硬编码 6（StageResult/AttemptState/LoopSession/RuntimeState/CheckpointRecord/TerminationDecision） | contracts/README.md | README 含 "六 dataclass"/"6" |
| 6 | guards 数量=16 | `len(_GUARD_REGISTRY)` | controller/README.md | README 含 "16 个" |
| 7 | guards 名字逐一对应 | `_GUARD_REGISTRY.keys()` | controller/README.md | 每个 guard 名出现在 README |
| 8 | NodeKind 成员=12（含 DONE_FAILURE） | `NodeKind.__members__` | controller/README.md | 每个 NodeKind 名出现在 README |

### 2.5 断言策略

**双层守护**：

- **数量守护**：硬编码数字（如 `assert len(list(FailureCode)) == 17`），强制改动时必须同时审视测试。
- **名字守护**：宽松文本包含（`assert name in readme_text`），不要求严格格式，降低维护成本。

新增/删除成员时必须同时改 README 和测试数字，防止静默漂移。

### 2.6 测试用例清单

```
test_docs_consistency.py
├── test_failure_code_count_matches_readme        # 守护点 1
├── test_failure_code_names_in_readme             # 守护点 2
├── test_contracts_all_count_matches_readme       # 守护点 3
├── test_contracts_all_names_in_readme            # 守护点 4
├── test_contracts_dataclass_count_matches_readme # 守护点 5
├── test_guards_count_matches_readme              # 守护点 6
├── test_guards_names_in_readme                   # 守护点 7
└── test_nodekind_names_in_readme                 # 守护点 8
```

---

## 3. G3：analyzer 轨迹上下文

### 3.1 数据模型变更

`AnalysisRequest`（`analyzer_protocol.py:18-28`）新增一个字段，有默认值，向后兼容：

```python
@dataclass
class AnalysisRequest:
    # ... 现有字段不变 ...
    prior_attempts: list[dict] = field(default_factory=list)
```

每个元素为**精简轨迹帧**，结构：

```python
{
    "attempt_index": int,        # 第几轮（0-based）
    "patch_hash": str,           # 补丁 SHA256（与 duplicate_patch_hash guard 同源）
    "failure_code": str,         # 本轮失败码（NONE/COMPILE_FAILED/...）
    "failed_count": int,         # 失败用例数
    "patch_files": list[str],    # 改动了哪些文件
    "failure_summary": str,      # 一行摘要
}
```

设计依据：

- `patch_hash` 与 `duplicate_patch_hash` guard（`guards.py:39-43`）同源，analyzer 可精确知道哪个 hash 试过了。
- `failure_summary` 是一行人类可读摘要，便于 LLM prompt 直接消费，不需 LLM 自己解析结构化数据。
- 不传完整 attempt dict（含 verify_result/compile_result/deploy_result/evidence_path 等），避免 prompt 过长。

### 3.2 轨迹注入点

`stages.analyze_request_stage`（`stages.py:235-269`），从现成的 `session_data["attempts"]` 投影 `attempts[:-1]`：

```python
def _build_prior_attempts(attempts: list[dict]) -> list[dict]:
    """从 session attempts 投影精简轨迹（排除最后一轮=当前轮）。"""
    prior = []
    for i, a in enumerate(attempts[:-1]):
        patch_applied = a.get("patch_applied") or {}
        if not patch_applied:
            continue  # 跳过无补丁的纯 verify 轮（首轮）
        prior.append({
            "attempt_index": a.get("attempt_index", i),
            "patch_hash": patch_applied.get("patch_hash", ""),
            "failure_code": a.get("failure_code", ""),
            "failed_count": a.get("failed_count", 0),
            "patch_files": patch_applied.get("files", []),
            "failure_summary": _summarize_failure(a),
        })
    return prior


def _summarize_failure(attempt: dict) -> str:
    """生成一行失败摘要。"""
    compile_error = (attempt.get("compile_result") or {}).get("error", "")
    if compile_error:
        return compile_error.splitlines()[0][:200]  # 首行，截断
    failed_cases = attempt.get("failed_cases") or []
    if failed_cases:
        ids = [c.get("id", "?") for c in failed_cases[:5]]
        return f"failed: {', '.join(ids)}"
    fc = attempt.get("failure_code", "")
    return fc or "unknown"
```

然后在 `analyze_request_stage` 构造 `AnalysisRequest` 时传入：

```python
request = AnalysisRequest(
    # ... 现有字段 ...
    prior_attempts=_build_prior_attempts(session_data.get("attempts", [])),
)
```

**数据源完全现成**：`session_data["attempts"]` 已由 `run_verify_stage`（`stages.py:221-229`）和 engine 各节点逐步累积，包含全部历史。guard 层的 `_build_guard_eval_request`（`engine.py:632-683`）已有等价的历史聚合先例。

### 3.3 消费方：OpencodeAnalyzer prompt 注入

仅改 `_build_prompt`（`analyzer_protocol.py:499-529`），新增"历史尝试"段落：

```
## 历史尝试（请避免重复）

### 尝试 #1
- 补丁文件: lciod_ops.c, lciod.h
- 失败码: COMPILE_FAILED
- 失败摘要: implicit declaration of function 'foo'

### 尝试 #2
- 补丁文件: lciod_ops.c
- 失败码: EVIDENCE_FAIL
- 失败摘要: HA-03 field mismatch
```

当 `prior_attempts` 为空时不渲染该段落（零开销）。

### 3.4 不改动的部分

| 组件 | 是否改动 | 理由 |
|------|---------|------|
| KnowledgeBaseAnalyzer | 不改 | 指纹召回，命中返回固定补丁是设计意图，历史无价值 |
| ScriptedAnalyzer | 不改 | 规则匹配，历史无价值 |
| ChainedAnalyzer | 不改 | 透传 request，自动携带新字段 |
| guards / engine 路由 | 不改 | guard 层已有自己的历史聚合（`_build_guard_eval_request`） |
| duplicate_patch_hash guard | 不改 | 事后兜底保留，与 G3 事前预防互补 |

### 3.5 反序列化兼容性

`engine.py:547-551` 的反序列化已用字段过滤：

```python
request = AnalysisRequest(**{
    k: v for k, v in req_data.items()
    if k in AnalysisRequest.__dataclass_fields__
})
```

新字段有默认值，旧 checkpoint 中的 `analysis_request.json`（无 `prior_attempts` 键）**自动兼容**。

### 3.6 与 duplicate_patch_hash 的关系

| 维度 | G3 轨迹上下文 | duplicate_patch_hash guard |
|------|-------------|---------------------------|
| 时机 | **事前**（analyzer 生成补丁前已知历史） | **事后**（补丁 apply 后才发现重复） |
| 节省 | 省 1 轮 budget（apply+compile+deploy+verify） | 无（已消耗一整轮） |
| 效果 | LLM 避开已知失败方向 | 拦截后 ESCALATE_HUMAN |
| 替代 | — | 保留作为最后防线 |

两者互补不替代：G3 降低重复概率，duplicate_patch_hash 保证重复发生时一定拦截。

### 3.7 测试用例清单（TDD）

| 测试 | 验证点 | 阶段 |
|------|--------|------|
| `test_analysis_request_prior_attempts_default` | 新字段默认 `[]`，现有测试不受影响 | GREEN（兼容性确认） |
| `test_prior_attempts_projection` | `_build_prior_attempts` 正确投影 `attempts[:-1]` 为精简轨迹 | RED→GREEN |
| `test_prior_attempts_skips_no_patch` | 无 `patch_applied` 的 attempt（首轮 verify）不进轨迹 | RED→GREEN |
| `test_summarize_failure_compile_error` | compile_error 存在时取首行 | RED→GREEN |
| `test_summarize_failure_failed_cases` | 无 compile_error 时取 failed_case id | RED→GREEN |
| `test_summarize_failure_fallback` | 都没有时返回 failure_code | RED→GREEN |
| `test_opencode_prompt_includes_history` | `prior_attempts` 非空时 prompt 含"历史尝试"段落 | RED→GREEN |
| `test_opencode_prompt_empty_history` | `prior_attempts` 为空时不渲染历史段落 | RED→GREEN |
| `test_analysis_request_deserialize_compat` | 旧格式 JSON（无 `prior_attempts`）能正常反序列化 | GREEN（兼容性确认） |
| `test_analyze_request_stage_injects_prior_attempts` | stage 正确把 history 传入 request | RED→GREEN |

---

## 4. 文件变更清单

### 4.1 G8

| 文件 | 动作 | 说明 |
|------|------|------|
| `engineering/loop/controller/README.md` | 修改 | 状态机图补 `DONE_FAILURE` 节点 |
| `engineering/loop/controller/python/tests/test_docs_consistency.py` | 新增 | 8 个元测试 |

### 4.2 G3

| 文件 | 动作 | 说明 |
|------|------|------|
| `engineering/loop/controller/python/loop_controller/analyzer_protocol.py` | 修改 | AnalysisRequest 加 `prior_attempts` 字段；OpencodeAnalyzer `_build_prompt` 注入历史段落 |
| `engineering/loop/controller/python/loop_controller/stages.py` | 修改 | `analyze_request_stage` 注入 `prior_attempts`；新增 `_build_prior_attempts` / `_summarize_failure` 辅助函数 |
| `engineering/loop/controller/python/tests/test_analyzer_protocol.py` | 修改 | 新增 prior_attempts 相关测试 |

### 4.3 不改动

- `contracts/` — 无需变更（AnalysisRequest 在 controller 层）
- `core/` / `connection/` / `deploy/` — 无关
- `runtime/guards.py` / `runtime/engine.py` / `runtime/nodes.py` — 无关
- `KnowledgeBaseAnalyzer` / `ScriptedAnalyzer` / `ChainedAnalyzer` — 无关

---

## 5. 验收标准

### 5.1 G8

- [ ] `controller/README.md` 状态机图含 `DONE_FAILURE`（12 节点）
- [ ] `test_docs_consistency.py` 8 个测试全部通过
- [ ] 人为删除 README 中某个 FailureCode 名 → 对应测试失败（反证有效）

### 5.2 G3

- [ ] `AnalysisRequest.prior_attempts` 默认 `[]`，全部现有测试通过（无回归）
- [ ] `analyze_request_stage` 正确投影历史（有补丁的轮进轨迹，首轮 verify 不进）
- [ ] OpencodeAnalyzer prompt 在 `prior_attempts` 非空时含"历史尝试"段落
- [ ] OpencodeAnalyzer prompt 在 `prior_attempts` 为空时无该段落
- [ ] 旧 checkpoint JSON 反序列化正常

### 5.3 全量回归

- [ ] controller: `pytest engineering/loop/controller/python/tests/` 全通过
- [ ] core: `pytest engineering/loop/core/python/tests/` 全通过
- [ ] connection: `pytest engineering/loop/connection/providers/{rp5-serial,adb}/python/tests/` 全通过
- [ ] deploy: `pytest engineering/loop/deploy/python/tests/` 全通过
- [ ] contracts: `pytest engineering/loop/contracts/python/tests/` 全通过

---

## 6. 风险与缓解

| 风险 | 概率 | 缓解 |
|------|------|------|
| G8 元测试过严，频繁误报 | 中 | 名字守护用宽松文本包含（`name in text`），不要求严格格式；数量守护用硬编码数字 |
| G3 轨迹过长导致 LLM prompt 超限 | 低 | 精简轨迹帧（6 字段），`failure_summary` 截断 200 字符；实际 loop 通常 max_attempts=3-5，轨迹帧数有限 |
| G3 新字段影响反序列化 | 极低 | 有默认值 + 字段过滤反序列化（已验证模式） |
