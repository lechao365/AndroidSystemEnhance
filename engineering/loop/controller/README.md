# Loop Controller

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：loop engineering 控制面（session / attempt / 状态机 / terminate / retry / regression policy）。
- **职责边界**：决策层，不含 transport / case 定义 / 产物 IO。
- **上下游依赖**：依赖 `loop/contracts`（数据模型 + FailureCode），被 `le.sh` 调用。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 模块清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 公开 API + 测试命令 | 实际使用时 |
| [关联资源](#关联资源) | workflow、设计文档链接 | 深入理解时 |

## 目录说明

| 子目录/文件 | 职责 | 关键入口 |
|------------|------|---------|
| `python/loop_controller/engine.py` | `apply_stage_result`：把 StageResult 应用到 SessionState | 被 control_cli 调用 |
| `python/loop_controller/policy.py` | `decide_termination`：PASS→STOP / 超次数→STOP+escalate / 重复失败→STOP+escalate / 否则 RETRY | 被 control_cli 调用 |
| `python/loop_controller/state.py` | `new_session`：session 工厂 | 被 control_cli 调用 |
| `python/loop_controller/analyzer_protocol.py` | `AnalysisRequest` / `FileChange` / `PatchSuggestion` / `LlmAnalyzer` 抽象接口 | 被 control_cli / patch_applier 引用 |
| `python/loop_controller/patch_applier.py` | `apply_file_changes`：将 `FileChange[]` 写入 workspace | `le control apply-patch` 调用 |
| `python/loop_controller/patch_guard.py` | `check_white_list` / `detect_risk` / `check_syntax`：白名单校验 + 风险标记 + gcc 语法预检 | `le control apply-patch` 调用 |
| `python/loop_controller/control_cli.py` | `le control` 子命令：init / run-verify / analyze-request / deploy / decide / status / **apply-patch / compile / revert** | `le.sh` 入口 |
| `python/loop_controller/__init__.py` | 导出 `apply_stage_result` / `decide_termination` / `new_session` / `add_control_parser` / `apply_file_changes` 等 | import 入口 |
| `python/tests/` | `test_engine.py` / `test_policy.py` / `test_patch_applier.py` / `test_patch_guard.py` / `test_control_cli.py` | pytest |

## 使用方式

本目录无可执行入口，作为 Python 控制面库被 import。

### 公开 API

| 函数 | 签名 | 说明 |
|------|------|------|
| `apply_stage_result` | `(session, *, attempt_index, stage_result, decision)` | 将阶段结果写入 SessionState |
| `decide_termination` | `(*, max_attempts, current_attempt, latest_stage, previous_failure_codes)` | 返回 TerminationDecision（STOP / RETRY） |
| `new_session` | `(session_id, workflow_id, target, max_attempts)` | 创建新 SessionState |
| `apply_file_changes` | `(changes: list[FileChange], workspace_root: str) -> ApplyResult` | 应用 AI 补丁到 workspace |
| `check_white_list` | `(changes, allowed_prefixes) -> GuardResult` | 补丁白名单校验（fail-closed） |
| `add_control_parser` | `(subparsers) -> None` | 注册 `le control` 全部子命令 |

### `le control` 子命令一览

| 子命令 | 功能 | 关键产物 |
|-------|------|---------|
| `init` | 初始化 session，生成 session_id | `{sid}.json` + `session.json` |
| `run-verify` | 调 `loop_core.cli run` 跑一次验证 | attempt 追加到 session，读 `evidence_bundle.json` |
| `analyze-request` | 拼装 `AnalysisRequest` 写盘 | `analysis_request.json` |
| `deploy` | 调 `loop_core.cli deploy --diff-rev HEAD` | 部署日志 |
| `decide` | 调 `policy.decide_termination` 决策 STOP/RETRY | stdout |
| `status` | 打印 session JSON | stdout |
| `apply-patch` | 应用 AI 补丁（白名单+语法校验+stash 备份），失败自动回滚 | attempt 追加 `patch_applied` |
| `compile` | 编译当前 workspace 改动（不部署） | attempt 追加 `compile_result` |
| `revert` | 回滚最近一次 `apply-patch`（`git stash apply`） | attempt 标记 `reverted=true` |

白名单配置：`engineering/loop/config/target-paths.yaml`（target → 允许的路径前缀，未登记的 target **默认拒绝所有改动**）。

### 测试

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/controller/python:engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/controller/python/tests/ -v
```

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `../contracts/` | 数据模型源 |
| 设计文档 | `docs/specs/2026-06-19-loop-engineering-design.md` | 控制面设计 |
