# lc-sync-patchs-to-doc

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：patchs → 文档同步 workflow（patchs 变动后生成报告，按模板规范将 diff 转换为文档更新）。
- **职责边界**：`templates/*.md` 是只读契约，设计文档（`docs/specs/01-*/02-*`）是受控可变区；方案先行，确认后落盘。
- **上下游依赖**：读 `patchs/rpi5/`，写 `docs/specs/`。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 文件清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 触发命令 | 实际使用时 |
| [关联资源](#关联资源) | WORKFLOW.md、配置、模板链接 | 深入理解时 |

## 目录说明

| 文件 | 职责 | 关键入口 |
|------|------|---------|
| `WORKFLOW.md` | workflow 契约：trigger / 报告生成 / 模板校验 / 文档更新流程 | 被 `.opencode/commands/lc-sync-patchs-to-doc.md` `@` 消费 |
| `sync_patchs_to_doc.sh` | bash 入口：扫描 patchs diff → 生成结构化报告 → 按模板映射转文档更新 | 由 workflow 编排 |

## 使用方式

本目录无可独立调用的入口，由 workflow 编排触发。

| 触发方式 | 说明 |
|---------|------|
| `/lc-sync-patchs-to-doc` | 生成变动报告（通常在 `/lc-sync-code-to-patchs` 之后） |
| `/lc-sync-patchs-to-doc --full-diff` | 报告 + 完整 diff 正文 |
| `/lc-sync-patchs-to-doc --check-only` | 仅检查，不输出 |

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `./WORKFLOW.md` | 完整流程契约（D-WF 保留） |
| 关联配置 | `../../config/doc-sync-mapping.yaml` | patchs→文档映射规则 |
| 关联 workflow | `../../templates/` | 只读契约（模板约束 AI 生成文档结构） |
