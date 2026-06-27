# Loop Engineering 业界对标 Gap 修复设计

> **日期**：2026-06-27
> **状态**：已确认，待实施
> **范围**：P0（打通自动修复闭环）+ P1（工程债清理）
> **前置**：基于 commit `a95981e` 的 loop engine 现状分析

---

## 1. 背景与动机

### 1.1 业界对标分析结论

对 `engineering/loop/` 全面分析后，与业界主流 loop engineering 框架（SWE-agent / OpenHands / Aider / AutoGPT / Reflexion / Agentless / LangGraph / Devin）对标，结论如下：

**做得好的部分（保持不动）**：

| 维度 | 现状 | 业界对标 |
|------|------|---------|
| 状态机 runtime | 12 节点显式状态图 + `_LINEAR_NEXT` 转换表 | 对标 LangGraph，自研零依赖 |
| Checkpoint + Resume | JSONL 追加写，按 session_id 过滤 | 对标 LangGraph checkpoint |
| Terminal State 三分 | DONE_SUCCESS / ESCALATE_HUMAN / DONE_FAILURE | 优于简单 STOP |
| Guard 一等公民 | 15 个 guard 按优先级短路求值 | `progress_converging` 逐用例收敛判定是**创新点** |
| Worktree 隔离 | 每次 attempt 独立 git worktree | 对标 SWE-bench sandbox |
| EvidenceBundle 契约 | 结构化 JSON | 对标 OpenHands observation |
| 白名单 fail-closed | target 未登记拒绝所有改动 | 安全性优于业界多数实现 |
| 结构化错误码路由 | `DeployErrorCode` → (status, FailureCode, needs_rollback) | 工程化程度高 |
| 物理安全边界 | kernel 死→串口回滚→escalate 人工 | 诚实面对硬件限制 |

**关键 Gap（需修复）**：

详见 §2（P0）和 §3（P1）。

### 1.2 核心问题

三个 P0 问题的根因是**同一条路径上的断裂**：

```
设计意图：AI 主会话 = 调度者 + 默认 Analyzer（auto-loop 设计 §1.3）
                          ↑
                    这里断了：runtime_cli 不注入 analyzer
                          ↓
实际行为：runtime 引擎 → WAIT_ANALYZER_PATCH → 无 analyzer → ESCALATE_HUMAN
```

`runtime_cli.py:138` 的 `_handle_run` 只传 serial_shell_provider，**不注入 analyzer**，导致 `WAIT_ANALYZER_PATCH` 节点永远走"无 analyzer → ESCALATE_HUMAN"路径。`le runtime` 命令目前只能跑 verify→decide，到 WAIT_ANALYZER_PATCH 就退人工，**自动修复链路是断的**。

---

## 2. P0：打通自动修复闭环

### 2.1 三层降级 Analyzer 架构

#### 设计目标

将 `WAIT_ANALYZER_PATCH` 节点从"无 analyzer → ESCALATE"改造为"三层降级"自动闭环：

```
WAIT_ANALYZER_PATCH:
  Layer 1: 知识库匹配（KnowledgeBaseAnalyzer）
    → 命中（fingerprint 匹配）→ 落盘 patch_suggestion.json → PATCH_READY
  Layer 2: 确定性规则匹配（ScriptedAnalyzer，扩充规则库）
    → 命中 → 落盘 → PATCH_READY
  Layer 3: LLM 兜底（OpencodeAnalyzer，subprocess 调 opencode run）
    → 成功 → 落盘 → PATCH_READY
    → 失败/超时 → ESCALATE_HUMAN
```

#### 业界对标

| 业界做法 | 本项目选择 | 理由 |
|---------|-----------|------|
| LLM-first（SWE-agent/OpenHands/Aider 全部直接调 LLM API） | 规则优先 + LLM 兜底 | 项目运行在 opencode 生态内，复用 credentials；bug 模式可枚举时规则更快更可控 |
| 直接调 HTTP API（业界主流） | subprocess 调 opencode run | 本项目特殊性：运行在 opencode 内，A 模式本质是"本项目特化的 B"，复用 opencode 的 model/credential/MCP 工具配置 |
| 无知识积累 / Reflexion / vector DB | Reflexion 模式 | 适合固定验收场景，成功补丁归档供后续 session 复用 |

#### 新增组件

##### 2.1.1 `KnowledgeBaseAnalyzer`（新类）

```python
class KnowledgeBaseAnalyzer(LlmAnalyzer):
    """从 patch_knowledge_base.json 加载历史成功补丁，按 fingerprint 匹配。"""
    def __init__(self, kb_path: str):
        self._kb: list[KBEntry] = self._load_kb(kb_path)

    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        fingerprint = self._compute_fingerprint(request)
        for entry in self._kb:
            if entry.fingerprint == fingerprint:
                return PatchSuggestion(
                    target_files=entry.patch,
                    rationale=f"知识库命中：{entry.description}",
                    confidence=0.98,
                    deploy_mode_hint=entry.deploy_mode_hint,
                )
        return PatchSuggestion(target_files=[], confidence=0.0)
```

- **fingerprint 计算**：对 failed_cases 的 `(case_id, failure_reason_signature)` 集合做 SHA256
- **failure_reason_signature**：取前 80 字符，归一化空白和路径后小写化
- **命中后 confidence=0.98**（历史验证过的成功补丁，可信度高于规则库的 0.95；KB 可能过时，但 patch 仍需过白名单和语法检查——实际值可配置）

##### 2.1.2 `OpencodeAnalyzer`（新类）

```python
class OpencodeAnalyzer(LlmAnalyzer):
    """通过 subprocess 调 opencode run，让 LLM 生成补丁。"""
    def __init__(self, workspace_root: str, model: str = "", timeout: int = 300):
        self._workspace_root = workspace_root
        self._model = model
        self._timeout = timeout

    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        prompt = self._build_prompt(request)
        req_file = self._write_request_file(request)
        result = self._invoke_opencode(prompt, req_file)
        return self._parse_suggestion(result)
```

- **prompt 模板约束**：
  - 必须输出严格 JSON：`[{"workspace_path": "...", "change_type": "edit", "old_marker": "...", "new_content": "..."}]`
  - 必须遵守白名单（只改 target-paths.yaml 允许的路径）
  - 附带 evidence_bundle 路径供 opencode agent 读取上下文
  - 附带 workspace_diff_so_far 避免重复修改
- **subprocess 调用**：`opencode run --format json -f <req_file> "<prompt>"`，带 timeout
- **JSON 解析**：从 opencode 的 json 输出中提取最后一条 assistant message，解析其中的 JSON 补丁
- **异常处理**：subprocess 超时/非零退出/JSON 解析失败 → 返回空 PatchSuggestion（交由上层降级到 ESCALATE）

##### 2.1.3 `ChainedAnalyzer`（新类）

```python
class ChainedAnalyzer(LlmAnalyzer):
    """三层降级：KB → 规则 → opencode。"""
    def __init__(self, layers: list[LlmAnalyzer]):
        self._layers = layers

    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        for layer in self._layers:
            suggestion = layer.analyze(request)
            if suggestion.target_files:
                suggestion.rationale = f"[{type(layer).__name__}] {suggestion.rationale}"
                return suggestion
        return PatchSuggestion(target_files=[], confidence=0.0,
                               rationale="三层 analyzer 均无产出")
```

##### 2.1.4 `ScriptedAnalyzer` 规则库扩充

基于 `docs/specs/2026-06-22-lciod-loop-verification-design.md` §5.7 的预设 bug，新增 3 条确定性规则：

| 规则名 | 指纹 | 补丁 |
|--------|------|------|
| `_rule_fv_stdout_pollution` | (已有) fault-verify stdout 污染 | printf→fprintf(stderr) |
| `_rule_lciod_hal_field_inversion` | getStats 返回字段值互换 | 修正 HAL getStats 字段映射 |
| `_rule_lciod_daemon_formula_error` | getAverageRate 计算异常 | 修正 getAverageRate 公式 |
| `_rule_lciod_hal_readdrain_missing` | readEvent 返回不完整 | 补全 readEvent 排空逻辑 |

#### 改动点

| 文件 | 改动 |
|------|------|
| `analyzer_protocol.py` | 新增 `KnowledgeBaseAnalyzer` / `OpencodeAnalyzer` / `ChainedAnalyzer` / `KBEntry`；`ScriptedAnalyzer` 扩充规则库 |
| `runtime_cli.py:138` | `_handle_run` 注入 `ChainedAnalyzer([KBAnalyzer, ScriptedAnalyzer, OpencodeAnalyzer])` |
| `engine.py:397` | 无改动（已正确调用 `self._analyzer.analyze(request)`） |

#### CLI 注入

`runtime_cli.py` `_handle_run` 改造：

```python
def _handle_run(args):
    session, ts = _load_session(args.session)
    serial_sh = _resolve_serial_shell()
    # 构建三层降级 analyzer
    kb = KnowledgeBaseAnalyzer(_kb_path)
    scripted = ScriptedAnalyzer()
    opencode_an = OpencodeAnalyzer(workspace_root=_workspace_root(), timeout=300)
    analyzer = ChainedAnalyzer([kb, scripted, opencode_an])
    rt = LoopRuntime(session, _CASES_DIR, _DEVICE_PROFILE,
                     adb_endpoint=args.adb_endpoint,
                     serial_shell_provider=serial_sh,
                     analyzer=analyzer)
    state = rt.run()
    ...
```

### 2.2 知识积累机制（Reflexion 模式）

#### 设计目标

DONE_SUCCESS 时自动将"失败指纹 → 成功补丁"归档到 `patch_knowledge_base.json`，下次 session 启动时加载，作为 `KnowledgeBaseAnalyzer` 的数据源。

#### 数据结构

`config/patch_knowledge_base.json`：

```json
{
  "version": 1,
  "entries": [
    {
      "fingerprint": "sha256:abc123...",
      "fingerprint_components": {
        "target": "lciod",
        "suite": "features.lciod.end_to_end",
        "failed_case_ids": ["HA-03", "HA-07"],
        "failure_reason_signatures": ["getstats field mismatch", "readevent incomplete"]
      },
      "patch": [
        {
          "workspace_path": "vendor/lechao/services/lechao_lciod/service.cpp",
          "change_type": "edit",
          "old_marker": "...",
          "new_content": "..."
        }
      ],
      "description": "HAL getStats 字段反转 + readEvent 排空遗漏",
      "confidence": 0.95,
      "deploy_mode_hint": "PUSH_SINGLE",
      "source_session": "lciod-20260627120000",
      "source_attempt": 2,
      "created_at": "2026-06-27T12:05:00+08:00",
      "hit_count": 0,
      "last_hit_at": ""
    }
  ]
}
```

#### 指纹计算

```python
def _compute_fingerprint(request: AnalysisRequest) -> str:
    """对 failed_cases 的 (case_id, failure_reason_signature) 集合做 SHA256。"""
    components = []
    for fc in sorted(request.failed_cases, key=lambda c: c.get("id", "")):
        case_id = fc.get("id", "")
        reason = (fc.get("failure_reason") or "")[:80]
        reason = re.sub(r"\s+", " ", reason).strip().lower()
        reason = re.sub(r"/[\w/.-]+", "<path>", reason)
        components.append(f"{case_id}:{reason}")
    raw = f"{request.target}|{request.suite}|{'|'.join(components)}"
    return "sha256:" + hashlib.sha256(raw.encode()).hexdigest()
```

#### 归档流程

在 `engine.py` 的 `_execute_decide_next` DONE_SUCCESS 分支新增：

```python
elif next_nk == NodeKind.DONE_SUCCESS:
    self._state.terminal_state = RuntimeTerminalState.DONE_SUCCESS
    self._archive_to_knowledge_base()  # 新增
    self._cleanup_all_worktrees()
```

`_archive_to_knowledge_base` 逻辑：
1. 取最新成功 attempt 的 patch（`patch_suggestion.json`）
2. 取该 attempt 对应的 failed_cases
3. 计算 fingerprint
4. 查 KB：已存在同 fingerprint → 更新 hit_count/last_hit_at/patch
5. 不存在 → 追加新 entry
6. 条目超上限（默认 100）→ 淘汰 hit_count 最低的
7. 写回 `patch_knowledge_base.json`

#### 加载流程

`KnowledgeBaseAnalyzer.__init__` 加载 KB：

```python
def _load_kb(self, kb_path: str) -> list[KBEntry]:
    if not os.path.isfile(kb_path):
        return []
    data = json.loads(Path(kb_path).read_text())
    return [KBEntry(**e) for e in data.get("entries", [])]
```

#### 安全约束

- KB 文件路径由 `config/analyzer.yaml` 配置
- 仅 DONE_SUCCESS 时写入
- patch 内容仍需过白名单校验（APPLY_PATCH 节点已有）
- KB 命中后 confidence=0.98（历史验证过的成功补丁，可信度高；可配置）

---

## 3. P1：工程债清理

### 3.1 删除旧架构（P1-1）

#### 删除清单

| 文件 | 原因 | 状态 |
|------|------|------|
| `controller/python/loop_controller/control_cli.py` | v1 break-glass，被 runtime_cli 取代 | runtime-rearch §8.1 标记"最终删除" |
| `controller/python/loop_controller/policy.py` | v1 decide_termination，被 guards.py 取代 | runtime-rearch plan 标记"删除旧形态" |
| `controller/python/loop_controller/state.py` | v1 new_session helper，被 LoopSession 取代 | 同上 |
| `controller/python/tests/test_control_cli.py` | 对应测试 | 随源码删除 |
| `controller/python/tests/test_policy.py` | 对应测试 | 随源码删除 |
| `loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh` | v1 手工编排层 | runtime-rearch §8.2 标记"最终删除" |

#### 迁移检查

删除前确认：
1. `control_cli.py` 功能全部被 `runtime_cli.py` 覆盖（init/run-verify/decide/apply-patch/compile/deploy/revert 全部有对应节点）
2. `policy.py` 无引用（或迁移引用到 guards.py）
3. `state.py` 的 `new_session` 无引用（`runtime_cli.py` 已用 `LoopSession(...)` 直接构造）
4. `le.sh` 同步删除 control 分支
5. 全局搜索 `le control` / `le.sh control` 引用

#### 文档同步

| 文件 | 改动 |
|------|------|
| `engineering/loop/README.md` | 删除 `le control` 相关段落 |
| `engineering/loop/WORKFLOW.md` | 重写 SOP 为 runtime 自动驱动描述 |
| `engineering/loop/controller/README.md` | 标注 runtime_cli 为唯一入口 |
| `engineering/loop/scripts/le.sh` | 删除 control 分支 |

#### run_lcview_adb_suite.sh 处理

本次仅删除不迁移。串口→adb 双阶段验收的编排在 runtime 层面通过"session 串联"实现（先跑 `system.network_adbd` session，再跑 `features.lcview` session），后续迭代实现。

### 3.2 消除 stages.py 全局状态（P1-2）

**现状**：`stages.py` 有模块级全局变量 `_CASES_DIR` / `_DEVICE_PROFILE`，多 session 并发会污染。

**方案**：改为 per-session context 注入。

```python
@dataclass
class StageContext:
    cases_dir: str
    device_profile: str
    artifacts_dir: str
    session_id: str

# 所有 stage 函数签名增加 ctx 参数
def run_verify_stage(session_dict: dict, ctx: StageContext) -> dict:
    ...
```

**改动范围**：`stages.py` 所有函数 + `engine.py` 调用方。

### 3.3 confidence 阈值检查（P1-3）

**现状**：`ScriptedAnalyzer` 返回 `confidence=0.95`，但 APPLY_PATCH 不检查，低置信度补丁仍会被应用。

**方案**：在 APPLY_PATCH 节点执行前检查阈值。

```python
def _execute_apply_patch(self):
    suggestion_meta = self._read_suggestion_meta()
    if suggestion_meta and suggestion_meta.get("confidence", 1.0) < self._confidence_threshold:
        self._state.node_status = "LOW_CONFIDENCE"
        self._state.pending_human_gate = True  # 触发 human-in-loop 门
        self._checkpoint(f"confidence below threshold")
        return  # 不 apply
    # 正常 apply...
```

**配置**：`config/analyzer.yaml` 中 `confidence.threshold: 0.7`。

**patch_suggestion.json 格式扩展**：从纯 `[FileChange]` 改为：
```json
{
  "patches": [FileChange],
  "confidence": 0.9,
  "rationale": "..."
}
```
向后兼容：纯 list 格式视为 confidence=1.0。

### 3.4 human-in-the-loop 审查门（P1-4）

**现状**：`ESCALATE_HUMAN` 是终态（直接停止），无法"暂停-确认-继续"。

**方案**：利用 `RuntimeState.pending_human_gate`（字段已存在但未用），实现"暂停-确认-继续"。

**状态转换**：

```
pending_human_gate=True 时，run() 主循环退出（不设终态）
  → 外部确认（le runtime approve --session <id>）
  → 清除 pending_human_gate，重置 node_status
  → le runtime resume 继续
```

**新增 CLI 命令**：

```bash
# 查看待确认项
le runtime pending --session <id>

# 批准并继续
le runtime approve --session <id>

# 拒绝（进入 ESCALATE_HUMAN 终态）
le runtime reject --session <id>
```

**触发场景**：
1. confidence < threshold（P1-3）
2. patch risk = KERNEL（修改内核文件）
3. deploy mode = DD_BOOT_REBOOT（dd 写设备）

**实现**：`engine.py` 在这三个场景设 `pending_human_gate=True` 并退出主循环。`runtime_cli.py` 新增 `pending` / `approve` / `reject` 子命令。

### 3.5 补丁格式升级（P1-5）

**现状**：`FileChange` 用 `old_marker`/`new_content` 字符串替换，要求 marker 唯一存在（count==1），无法做行级编辑。

**方案**：扩展 `FileChange` 支持三种编辑模式：

```python
@dataclass
class FileChange:
    workspace_path: str
    change_type: Literal["edit", "create", "delete"] = "edit"
    # 模式 A（现有）：marker 替换
    old_marker: str = ""
    new_content: str = ""
    # 模式 B（新增）：行级编辑
    line_range: tuple[int, int] | None = None
    # 模式 C（新增）：unified diff
    diff: str = ""
```

**应用优先级**（patch_applier.py）：
1. `diff` 非空 → `git apply --recount`
2. `line_range` 非空 → 按行号替换
3. `old_marker` 非空 → 现有 marker 替换
4. `change_type=create` → 写新文件

---

## 4. 配置文件

### 新增：`config/analyzer.yaml`

```yaml
# Analyzer 配置
opencode:
  binary: "opencode"
  model: ""
  timeout: 300
  format: "json"

knowledge_base:
  path: "config/patch_knowledge_base.json"
  max_entries: 100
  fingerprint_reason_length: 80

confidence:
  threshold: 0.7
  rule_match: 0.95
  kb_match: 0.98

human_gate:
  enabled: true
  triggers:
    - low_confidence
    - kernel_patch
    - dd_boot_reboot
```

### 新增：`config/patch_knowledge_base.json`（初始空）

```json
{
  "version": 1,
  "entries": []
}
```

---

## 5. 文件改动清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `config/analyzer.yaml` | Analyzer 配置 |
| `config/patch_knowledge_base.json` | 知识库（初始空） |
| `controller/python/tests/test_chained_analyzer.py` | 三层降级测试 |
| `controller/python/tests/test_knowledge_base.py` | 知识库读写测试 |
| `controller/python/tests/test_opencode_analyzer.py` | opencode subprocess 测试（mock） |
| `controller/python/tests/test_human_gate.py` | human-in-loop 门测试 |

### 修改文件

| 文件 | 改动 |
|------|------|
| `analyzer_protocol.py` | 新增 3 个 Analyzer 类 + KBEntry + 规则库扩充 |
| `runtime_cli.py` | 注入 ChainedAnalyzer + 新增 pending/approve/reject 子命令 |
| `engine.py` | DONE_SUCCESS 归档 + confidence 检查 + human_gate 触发 |
| `stages.py` | 消除全局状态，改为 StageContext 注入 |
| `patch_applier.py` | 支持三种编辑模式 |
| `loop_contracts/models.py` | RuntimeState 补充 human_gate 相关字段（如需） |
| `scripts/le.sh` | 删除 control 分支 |
| `loop/README.md` | 删除 le control 段落 |
| `loop/WORKFLOW.md` | 重写 SOP |
| `controller/README.md` | 更新入口说明 |

### 删除文件

| 文件 | 原因 |
|------|------|
| `controller/python/loop_controller/control_cli.py` | v1 旧架构 |
| `controller/python/loop_controller/policy.py` | v1 旧架构 |
| `controller/python/loop_controller/state.py` | v1 旧架构 |
| `controller/python/tests/test_control_cli.py` | 随源码删除 |
| `controller/python/tests/test_policy.py` | 随源码删除 |
| `loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh` | v1 手工编排 |

---

## 6. 测试策略

### 单元测试

| 测试文件 | 覆盖点 |
|---------|--------|
| `test_chained_analyzer.py` | 三层降级顺序、短路返回、全空降级到 ESCALATE |
| `test_knowledge_base.py` | fingerprint 计算、KB 加载/写入/去重/LRU 淘汰 |
| `test_opencode_analyzer.py` | subprocess 调用（mock）、JSON 解析、超时/异常降级 |
| `test_human_gate.py` | pending/approve/reject 流程、三种触发场景 |
| `test_analyzer_protocol.py`（扩充） | 新增 3 条 lciod 规则的匹配/不匹配 |
| `test_patch_applier.py`（扩充） | line_range / diff 模式 |
| `test_runtime_engine.py`（扩充） | DONE_SUCCESS 归档、confidence 阈值拦截 |

### 集成测试

- **CLI 注入验证**：`le runtime run` 实际注入 ChainedAnalyzer（通过 mock 验证）
- **三层降级端到端**：构造 failed_cases → KB 空 → 规则不匹配 → mock opencode → 产出补丁
- **知识积累闭环**：session DONE_SUCCESS → 检查 KB 新增 entry → 新 session 命中

### 回归测试

- 现有 178 个测试全部通过（零回归）
- 删除的旧架构测试（test_control_cli / test_policy）从基线中移除

---

## 7. 风险与缓解

| 风险 | 等级 | 缓解 |
|------|------|------|
| OpencodeAnalyzer subprocess 不稳定 | 中 | timeout + 异常降级到 ESCALATE，不阻塞闭环 |
| 知识库被错误补丁污染 | 中 | 仅 DONE_SUCCESS 归档；KB 命中后仍过白名单；可配置人工审核开关 |
| human-in-loop 门导致闭环卡住 | 低 | pending 状态可被 approve/reject 推进，不永久阻塞 |
| 规则库扩充引入误匹配 | 中 | 每条规则有独立测试；confidence < 1.0 时走 human_gate |
| 旧架构删除 break 外部引用 | 中 | 删除前全局搜索 `le control` 引用 |

---

## 8. 不在本次范围

| 项目 | 原因 | 后续 |
|------|------|------|
| 串口→adb 双阶段 session 串联 | 需 runtime 多 session 编排能力 | 后续迭代 |
| 可观测性 dashboard | 需独立 tracing/可视化栈 | 后续迭代 |
| 端到端集成测试（真实设备） | 需硬件在环 | 后续迭代 |
| retry 策略差异化 | 需更复杂的 guard 逻辑 | 后续迭代 |
| cost/token budget 追踪 | 仅在 opencode analyzer 大量调用后才有意义 | 后续迭代 |

---

## 9. 实施顺序建议

1. **Phase 1：analyzer 核心架构**（analyzer_protocol.py 新增 3 类 + ChainedAnalyzer + 配置文件）
2. **Phase 2：CLI 注入 + 知识积累**（runtime_cli 注入 + engine.py 归档逻辑 + KB 读写）
3. **Phase 3：规则库扩充**（lciod 3 bug 规则）
4. **Phase 4：human-in-loop 门**（engine.py 三种触发 + runtime_cli pending/approve/reject）
5. **Phase 5：旧架构删除**（删除 6 文件 + 文档同步 + le.sh 改动）
6. **Phase 6：工程债清理**（stages.py 全局状态消除 + confidence 阈值 + 补丁格式升级）
7. **Phase 7：测试补全 + 全量回归**

每个 Phase 独立可测试，可单独提交。
