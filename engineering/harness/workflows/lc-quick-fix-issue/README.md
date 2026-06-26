# lc-quick-fix-issue

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：根据自由文本检视意见自动修复代码并提交推送的一键工作流。
- **职责边界**：脚本做确定性工作（探测测试环境、git 提交推送），AI 做语义工作（理解检视意图、定位源码、设计方案、修复代码、调试）。
- **上下游依赖**：消费 `detect_test_env.sh`（探测）和 `git-push-to-server/commit_and_push.sh`（提交推送）；依赖 `config/scope-mapping.yaml`（commit scope）。

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
| `WORKFLOW.md` | workflow 契约：trigger / preconditions / inputs / 七阶段流程 / 零确认门 | 被 `.opencode/commands/lc-quick-fix-issue.md` `@` 消费 |
| `detect_test_env.sh` | 探测 TEST_CMD 和 PYTHONPATH | 由 workflow 编排，也可独立运行验证 |

## 使用方式

| 触发方式 | 说明 |
|---------|------|
| `/lc-quick-fix-issue <检视意见>` | 完整流程：分析→修复→测试→提交推送（零确认） |

```bash
# 独立运行探测脚本验证环境
bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh
```

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `./WORKFLOW.md` | 完整流程契约 |
| 关联 workflow | `../git-push-to-server/commit_and_push.sh` | Stage 7 调用 |
| 关联配置 | `../../config/scope-mapping.yaml` | commit scope 判定规则 |
| 关联配置 | `../../config/harness-paths.conf` | PYTHON_PATH_ROOTS（PYTHONPATH 事实源） |
