# lcview-adb-run

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：loop engineering 专属多阶段 workflow（串口 bootstrap → adb feature suite → 失败 serial fallback）。
- **职责边界**：loop 专属，不放 `harness/workflows/`。
- **上下游依赖**：依赖 `loop/connection`（rp5-serial + adb provider）、`loop/controller`、`loop/core`，被 `le.sh` / `/le` 编排。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 文件清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 入口脚本 + 典型参数 | 实际使用时 |
| [关联资源](#关联资源) | WORKFLOW.md、设计文档链接 | 深入理解时 |

## 目录说明

| 文件 | 职责 | 关键入口 |
|------|------|---------|
| `WORKFLOW.md` | workflow 契约：5 阶段定义 / 输入参数 / 7 个 failure code / 归属规则 | 被 `.opencode/commands/le.md` 间接消费 |
| `run_lcview_adb_suite.sh` | bash 入口脚本，编排 bootstrap → feature → fallback | CLI 直接调用 |

## 使用方式

### 快速开始

```bash
bash engineering/loop/workflows/lcview-adb-run/run_lcview_adb_suite.sh \
  --serial-host 127.0.0.1 --serial-port 9700 \
  --serial-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --adb-profile engineering/loop/connection/profiles/devices/rp5/adb.json \
  --artifacts-dir engineering/output/runs/lcview-adb-run
```

参数完整说明见 `WORKFLOW.md`。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `./WORKFLOW.md` | 完整流程契约（D-WF 保留） |
| 设计文档 | `docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md` | lcview adb 设计 |
