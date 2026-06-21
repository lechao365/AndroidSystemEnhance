# git-push-to-server

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：git 提交推送 workflow（收集 diff → AI 生成中文 commit message → 单次确认 → 提交推送到 origin）。
- **职责边界**：脚本做机械工作（diff 收集、git add/commit/push），AI 做语义工作（理解 diff、生成 message、多轮编辑交互）。
- **上下游依赖**：消费 `config/scope-mapping.yaml` 判定 commit scope，写入 `origin`。

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 文件清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 触发命令 | 实际使用时 |
| [关联资源](#关联资源) | WORKFLOW.md、配置链接 | 深入理解时 |

## 目录说明

| 文件 | 职责 | 关键入口 |
|------|------|---------|
| `WORKFLOW.md` | workflow 契约：trigger / preconditions / inputs / 完整阶段 | 被 `.opencode/commands/git-push-to-server.md` `@` 消费 |
| `collect_diff.sh` | diff 收集（生成结构化 diff 摘要供 AI 理解） | 由 workflow 编排，不单独调用 |
| `commit_and_push.sh` | 提交推送（git add → commit → push） | 由 workflow 编排，不单独调用 |

## 使用方式

本目录无可独立调用的入口，由 workflow 编排触发。

| 触发方式 | 说明 |
|---------|------|
| `/git-push-to-server` | 完整流程：collect → AI 生成 message → 确认 → commit + push |
| `/git-push-to-server --dry-run` | 只 collect + 生成 message 展示，不 commit 不 push |
| `/git-push-to-server --no-push` | 确认后只 commit 不 push |

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `./WORKFLOW.md` | 完整流程契约（D-WF 保留） |
| 关联配置 | `../../config/scope-mapping.yaml` | commit scope 判定规则 |
