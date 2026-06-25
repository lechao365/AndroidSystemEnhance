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
| `python/loop_controller/runtime_cli.py` | **runtime CLI**：`le runtime {init,run,resume,status,explain}` | `le.sh runtime` |
| `python/loop_controller/stages.py` | 可复用阶段 handler（run_verify / analyze_request / decide 纯函数）+ 通用 helpers | 被 runtime engine 与 control_cli 共用 |
| `python/loop_controller/control_cli.py` | 旧 `le control` 子命令（break-glass，等 engine 完整接管后删除） | `le.sh control` |
| `python/loop_controller/patch_applier.py` | `apply_file_changes`：将 `FileChange[]` 写入 workspace | 被 nodes/control_cli 调用 |
| `python/loop_controller/patch_guard.py` | `check_white_list` / `detect_risk` / `check_syntax` | 被 nodes/control_cli 调用 |
| `python/loop_controller/analyzer_protocol.py` | `AnalysisRequest` / `FileChange` / `PatchSuggestion` / `LlmAnalyzer` | analyzer 边界契约 |
| `python/loop_controller/policy.py` | `decide_termination`：旧终止规则（被 stages.decide_stage 复用） | 被 stages 调用 |

## 使用方式

### Runtime CLI（新主入口）

```bash
# 初始化 session
le runtime init --target lciod --suite <suite.yaml> --max-attempts 5 --artifacts-dir <dir>

# 全自动闭环（verify → decide → analyze → patch → compile → deploy → rerun）
le runtime run --session <session.json>

# 从最近 checkpoint 恢复
le runtime resume --session <session.json>

# 查看 session 状态
le runtime status --session <session.json>

# 解释 runtime 状态机
le runtime explain
```

### 旧 `le control`（break-glass / 调试）

旧 `le control {init,run-verify,decide,analyze-request,apply-patch,compile,deploy,revert,status}` 仍可用，用于人工干预与调试。等 runtime engine 完整接管 patch/compile/deploy 节点后将从项目中删除。

### 测试

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python:engineering/loop/controller/python:engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/controller/python/tests/ -v
```

## Runtime 架构

### 状态机

```text
INIT_SESSION -> RUN_VERIFY -> DECIDE_NEXT
  -> DONE_SUCCESS (全 PASS)
  -> BUILD_ANALYSIS_REQUEST -> WAIT_ANALYZER_PATCH -> ESCALATE_HUMAN (需 AI 产出补丁)
  -> ESCALATE_HUMAN (FAIL >= max_attempts / 重复失败 / 重复补丁)
```

### Guard 清单

| Guard | 类型 | 触发终态/跳转 |
|---|---|---|
| `all_cases_passed` | success | DONE_SUCCESS |
| `attempt_limit_reached` | terminal | ESCALATE_HUMAN |
| `repeated_failure_code` | terminal | ESCALATE_HUMAN |
| `duplicate_patch_hash` | terminal | ESCALATE_HUMAN |
| `patch_rejected` | terminal | ESCALATE_HUMAN |
| `kernel_dead_no_shell` | terminal | ESCALATE_HUMAN |
| `attempts_below_limit` | retry | BUILD_ANALYSIS_REQUEST |
| `compile_failed_but_recoverable` | retry | REVERT_PATCH |
| `deploy_failed_but_recoverable` | retry | DECIDE_NEXT |
| `patch_applied_successfully` | transition | COMPILE_PATCH |
| `deploy_success_and_verify_passed` | success | DONE_SUCCESS |

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
