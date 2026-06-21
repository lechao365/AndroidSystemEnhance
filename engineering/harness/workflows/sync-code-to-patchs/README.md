# sync-code-to-patchs

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：workspace → patchs 全量镜像归档 workflow（将 `~/workspace/` 编译源码树的定制改动镜像到 `patchs/rpi5/`）。
- **职责边界**：`patchs/rpi5/` 是 workspace 精确镜像（含删除对齐）；自动更新 manifest + README 文件映射表。
- **上下游依赖**：读 `~/workspace/`，写 `patchs/rpi5/` + `manifest.yaml` + `patchs/rpi5/README.md` 文件映射表。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 文件清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 触发命令 | 实际使用时 |
| [关联资源](#关联资源) | WORKFLOW.md、规则、配置链接 | 深入理解时 |

## 目录说明

| 文件 | 职责 | 关键入口 |
|------|------|---------|
| `WORKFLOW.md` | workflow 契约：trigger / preconditions / 全量镜像语义 / 删除对齐规则 | 被 `.opencode/commands/sync-code-to-patchs.md` `@` 消费 |
| `sync_code_to_patchs.sh` | bash 入口：扫描 workspace 改动 → 全量镜像 → 更新 manifest + README 映射表 | 由 workflow 编排 |

## 使用方式

本目录无可独立调用的入口，由 workflow 编排触发。

| 触发方式 | 说明 |
|---------|------|
| `/sync-code-to-patchs` | 全量镜像同步（默认含删除对齐） |
| `/sync-code-to-patchs --check-only` | 仅检查不执行 |

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `./WORKFLOW.md` | 完整流程契约（D-WF 保留） |
| 关联规则 | `../../rules/source-code-modify.md` | workspace 是源头，patchs 是归档 |
| 关联配置 | `../../config/scope-mapping.yaml` | commit scope 判定规则 |
