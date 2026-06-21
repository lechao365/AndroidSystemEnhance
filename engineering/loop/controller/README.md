# Loop Controller

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：loop engineering 控制面（session / attempt / 状态机 / terminate / retry / regression policy）。
- **职责边界**：决策层，不含 transport / case 定义 / 产物 IO。
- **上下游依赖**：依赖 `loop/contracts`（数据模型 + FailureCode），被 `loop/workflows` 与 `le.sh` 调用。

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
| `python/loop_controller/engine.py` | `apply_stage_result`：把 StageResult 应用到 SessionState | 被 workflows 调用 |
| `python/loop_controller/policy.py` | `decide_termination`：PASS→STOP / 超次数→STOP+escalate / 重复失败→STOP+escalate / 否则 RETRY | 被 workflows 调用 |
| `python/loop_controller/state.py` | `new_session`：session 工厂 | 被 workflows 调用 |
| `python/loop_controller/__init__.py` | 导出 `apply_stage_result` / `decide_termination` / `new_session` | import 入口 |
| `python/tests/` | `test_engine.py` + `test_policy.py` | pytest |

## 使用方式

本目录无可执行入口，作为 Python 控制面库被 import。

### 公开 API

| 函数 | 签名 | 说明 |
|------|------|------|
| `apply_stage_result` | `(session, *, attempt_index, stage_result, decision)` | 将阶段结果写入 SessionState |
| `decide_termination` | `(*, max_attempts, current_attempt, latest_stage, previous_failure_codes)` | 返回 TerminationDecision（STOP / RETRY） |
| `new_session` | `(session_id, workflow_id, target, max_attempts)` | 创建新 SessionState |

### 测试

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/controller/python:engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/controller/python/tests/ -v
```

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `../workflows/` | 消费 controller 决策 |
| 关联 workflow | `../contracts/` | 数据模型源 |
| 设计文档 | `docs/specs/2026-06-19-loop-engineering-design.md` | 控制面设计 |
