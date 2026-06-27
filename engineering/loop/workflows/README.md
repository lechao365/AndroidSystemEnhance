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

当前 `workflows/` 为空。v1 的手工编排脚本 `lcview-adb-run/`（serial bootstrap → adb feature → fallback）已删除，全部能力由 runtime engine 自动驱动状态机承接（详见 `../controller/README.md` 与 `../WORKFLOW.md`）。未来如有新的 loop 专属 workflow 入驻，应在本表登记。

## 使用方式

本目录当前无可执行入口。runtime 主入口在 `le runtime`（详见 `../controller/README.md`）。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 流程细节 | `../WORKFLOW.md` | loop engineering 流程单一事实源 |
| runtime 设计 | `docs/specs/2026-06-26-loop-runtime-rearchitecture-design.md` | runtime 重构设计（权威） |
| 设计文档 | `docs/specs/2026-06-19-loop-engineering-design.md` | workflow 归属规则 |
