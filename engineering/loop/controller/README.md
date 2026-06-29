# Loop Controller — Runtime 控制中心

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：loop engineering 控制面与 runtime 编排中心——状态图引擎、guard 判定、checkpoint 持久化、阶段 handler、补丁应用与防护。
- **职责边界**：决策与编排层，不含 transport / case 定义 / 产物 IO。
- **上下游依赖**：依赖 `loop/contracts`（数据模型 + FailureCode）、`loop/core`（验证引擎）、`loop/deploy`（编译部署）；被 `le.sh` 与 `runtime_cli` 调用。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 模块清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | runtime CLI + 测试命令 | 实际使用时 |
| [Runtime 架构](#runtime-架构) | 状态机 / guard / checkpoint / 终态 | 深入理解时 |
| [关联资源](#关联资源) | 设计文档、spec 链接 | 深入理解时 |

## 目录说明

| 子目录/文件 | 职责 | 关键入口 |
|------------|------|---------|
| `python/loop_controller/runtime/` | **状态图 runtime 引擎**（types / guards / checkpoint_store / engine / nodes） | `LoopRuntime.run()` / `resume()` |
| `python/loop_controller/runtime_cli.py` | **runtime CLI**：`le runtime {init,run,resume,status,explain}`（唯一入口） | `le.sh runtime` |
| `python/loop_controller/stages.py` | 可复用阶段 handler（run_verify / analyze_request / decide 纯函数）+ 通用 helpers | 被 runtime engine 调用 |
| `python/loop_controller/patch_applier.py` | `apply_file_changes`：将 `FileChange[]` 写入 workspace | 被 runtime nodes 调用 |
| `python/loop_controller/patch_guard.py` | `check_white_list` / `detect_risk` / `check_syntax` | 被 runtime nodes 调用 |
| `python/loop_controller/analyzer_protocol.py` | `AnalysisRequest` / `FileChange` / `PatchSuggestion` / `LlmAnalyzer` / `ChainedAnalyzer` / `KnowledgeBaseAnalyzer` / `OpencodeAnalyzer` / `ScriptedAnalyzer` | 三层降级 analyzer 架构 |
| `python/loop_controller/workspace_isolation.py` | `create_patch_worktree` / `remove_patch_worktree` / `WorktreeHandle` | git worktree 隔离 |

## 使用方式

### Runtime CLI（唯一主入口）

```bash
# 初始化 session
le runtime init --target lciod --suite <suite.yaml> --max-attempts 5 --artifacts-dir <dir>

# 全自动闭环（verify → decide → analyze → patch → compile → deploy → rerun）
le runtime run --session <session.json>

# 从最近 checkpoint 恢复
le runtime resume --session <session.json>

# 查看 session 状态
le runtime status --session <session.json>

# human-in-loop 门：查看待确认项
le runtime pending --session <session.json>

# human-in-loop 门：批准低置信度补丁并继续
le runtime approve --session <session.json>

# human-in-loop 门：拒绝补丁，升级人工
le runtime reject --session <session.json>

# 解释 runtime 状态机
le runtime explain
```

> v1 的 `le control {init,run-verify,decide,...}` 子命令已彻底删除，全部能力由 runtime engine 自动驱动状态机承接。

### 测试

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/ -v
```

## Runtime 架构

### 状态机

```text
INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT
  ├─ DONE_SUCCESS                          (全 PASS)
  ├─ ESCALATE_HUMAN                        (FAIL>=max / 重复失败 / 重复补丁 / kernel dead / ...)
  └─ BUILD_ANALYSIS_REQUEST -> WAIT_ANALYZER_PATCH
                                -> APPLY_PATCH -> COMPILE_PATCH -> DEPLOY_PATCH -> RUN_VERIFY (回环重验)
                                -> REVERT_PATCH -> DECIDE_NEXT                              (编译/部署失败回滚后重判)
```

线性转移（`engine._LINEAR_NEXT`，无分支条件）：
`INIT_SESSION→RUN_VERIFY`、`RUN_VERIFY→DECIDE_NEXT`、
`BUILD_ANALYSIS_REQUEST→WAIT_ANALYZER_PATCH`、`WAIT_ANALYZER_PATCH→APPLY_PATCH`、
`APPLY_PATCH→COMPILE_PATCH`、`DEPLOY_PATCH→RUN_VERIFY`、`REVERT_PATCH→DECIDE_NEXT`。

> `APPLY_PATCH` 节点内可能因 low_confidence / kernel_patch / dd_boot_reboot 触发 human gate（`pending_human_gate=True`，不设终态，等 `le runtime approve/reject`）。

### Guard 清单（16 个）

> 数量源于 `guards.py` 的 `_GUARD_REGISTRY`，`engine.py` 在 DECIDE_NEXT/APPLY/DEPLOY/REVERT 各节点按序调用对应 guard 子链。

| Guard | 类型 | 触发终态/跳转 |
|---|---|---|
| `all_cases_passed` | success | DONE_SUCCESS |
| `attempts_below_limit` | retry | BUILD_ANALYSIS_REQUEST |
| `progress_converging` | convergence | 严格下降→BUILD_ANALYSIS_REQUEST；持平/上升→ESCALATE_HUMAN |
| `attempt_limit_reached` | terminal | ESCALATE_HUMAN |
| `repeated_failure_code` | terminal | ESCALATE_HUMAN |
| `duplicate_patch_hash` | terminal | ESCALATE_HUMAN |
| `patch_rejected` | terminal | ESCALATE_HUMAN |
| `kernel_dead_no_shell` | terminal | ESCALATE_HUMAN |
| `session_state_corrupted` | terminal | ESCALATE_HUMAN |
| `transport_unrecoverable` | terminal | ESCALATE_HUMAN |
| `rollback_failed` | terminal | ESCALATE_HUMAN |
| `boot_timeout_no_recovery` | terminal | ESCALATE_HUMAN（已尝试回滚仍 boot timeout） |
| `patch_applied_successfully` | transition | COMPILE_PATCH |
| `compile_failed_but_recoverable` | revert | REVERT_PATCH |
| `deploy_failed_but_recoverable` | revert | REVERT_PATCH |
| `boot_timeout_kernel_panic` | revert | REVERT_PATCH |

### Analyzer 架构（三层降级）

WAIT_ANALYZER_PATCH 节点通过 `ChainedAnalyzer` 编排三层降级分析器：

1. **KnowledgeBaseAnalyzer**（confidence=0.98）：从 `patch_knowledge_base.json` 按 fingerprint 匹配历史成功补丁（Reflexion 模式）。fingerprint 使用归一化算法消除动态数值差异：文件路径→`<path>`、十六进制地址→`<hex>`、整数→`<num>`，使同一故障的不同运行能匹配同一 KB 条目
2. **ScriptedAnalyzer**（confidence=0.95）：确定性规则库，含 fault-verify stdout 污染、lciod HAL 字段反转/Daemon 公式/readEvent 排空、lcview HAL connect 故障等规则。规则支持两种匹配路径：直接文本匹配（failure_reason 含关键词）和 case_id 匹配（verify 用例的 command 是 grep|wc -l 时，failure_reason 只有计数）
3. **OpencodeAnalyzer**（confidence=0.8）：通过 subprocess 调 `opencode run` 让 LLM 生成补丁

配置：`config/analyzer.yaml`；知识库：`config/patch_knowledge_base.json`。DONE_SUCCESS 时自动归档成功补丁到知识库。

### Human-in-the-Loop 门

当 confidence < threshold（默认 0.7）时，`pending_human_gate` 被设置，runtime 主循环暂停（不设终态）。通过 `le runtime pending/approve/reject` 子命令控制流转。

### Terminal State

- `DONE_SUCCESS`：全 PASS，自动结束。
- `ESCALATE_HUMAN`：达到人工门槛（FAIL>=max / 重复失败 / 重复补丁 / kernel dead / patch rejected / transport 不可恢复 / rollback 失败）。
- `DONE_FAILURE`：系统异常终止。

### Checkpoint

每个节点执行后写一条 JSONL checkpoint（`runtime_checkpoints.jsonl`），支持 resume。Checkpoint 按 session_id 过滤，多 session 共享同一 artifacts_dir 时互不干扰。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/specs/2026-06-26-loop-runtime-rearchitecture-design.md` | runtime 重构设计（权威） |
| 实施计划 | `docs/plans/2026-06-26-loop-runtime-rearchitecture.md` | 渐进迁移计划 |
| 关联契约 | `../contracts/` | 数据模型源（LoopSession / RuntimeState / CheckpointRecord） |
