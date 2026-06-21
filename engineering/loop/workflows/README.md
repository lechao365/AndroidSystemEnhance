# Loop Workflows

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：loop engineering 专属 workflow 与 phase plan 承载层。
- **职责边界**：凡直接服务 loop suite / transport / fallback / rerun 的流程放此目录，**不放** `harness/workflows/`（通用工程 workflow 才进 harness）。
- **上下游依赖**：依赖 `loop/controller`、`loop/connection`、`loop/core`，被 `le.sh` 编排。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 子目录清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 入口脚本 | 实际使用时 |
| [关联资源](#关联资源) | WORKFLOW.md、设计文档链接 | 深入理解时 |

## 目录说明

| 子目录/文件 | 职责 | 关键入口 |
|------------|------|---------|
| `lcview-adb-run/` | 串口 bootstrap 后切 adb 跑 lcview suite，失败补采 serial fallback；含 `WORKFLOW.md` 契约 + `run_lcview_adb_suite.sh` 入口 | 被 `le.sh` 编排 |
| `python/loop_workflows/base.py` | `WorkflowDefinition` dataclass（workflow_id + phases） | 被 import |
| `python/loop_workflows/builtin.py` | `SingleRunVerifyWorkflow`（run→verify）/ `MultiPhaseVerifyWorkflow`（bootstrap→feature→fallback） | 被 import |

## 使用方式

本目录无可执行入口（workflow 由 `le.sh` 或 `run_lcview_adb_suite.sh` 触发）。

### 入口清单

| 入口 | 作用 | 调用方式 |
|------|------|---------|
| `lcview-adb-run/run_lcview_adb_suite.sh` | 多阶段编排（serial bootstrap → adb feature → fallback） | `bash run_lcview_adb_suite.sh --serial-host ... --adb-profile ...` |

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `lcview-adb-run/WORKFLOW.md` | 被 `.opencode/commands/le.md` 间接编排 |
| 设计文档 | `docs/specs/2026-06-19-loop-engineering-design.md` | workflow 归属规则 |
