# lc-revert-code-from-patchs

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。

## 定位

- **是什么**：patchs → workspace 回退 workflow（灾难恢复，将 `~/workspace/` 偏离 patchs 基线的部分拉回一致）。
- **职责边界**：`lc-sync-code-to-patchs` 的逆操作；`patchs/rpi5/` 是已知良好基线（promoted baseline），workspace 是操作对象。
- **上下游依赖**：读 `patchs/rpi5/` + `manifest.yaml`，写 `~/workspace/`。

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
| `WORKFLOW.md` | workflow 契约：trigger / preconditions / 受控例外说明 / 计划→确认→执行→校验 | 被 `.opencode/commands/lc-revert-code-from-patchs.md` `@` 消费 |
| `revert_code_from_patchs.sh` | bash 入口：生成回退计划 → AI 逐条确认 → 执行 → 落盘校验 | 由 workflow 编排 |

## 使用方式

本目录无可独立调用的入口，由 workflow 编排触发。

| 触发方式 | 说明 |
|---------|------|
| `/lc-revert-code-from-patchs` | 生成回退计划 → AI 逐条确认 → 执行 → 落盘校验 |

> **注意**：本 workflow 是 `source-code-modify.md` 的受控例外（灾难恢复），仅当 workspace 处于不可用坏状态时使用。使用前 patchs 基线必须是 promoted baseline。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 关联 workflow | `./WORKFLOW.md` | 完整流程契约（D-WF 保留） |
| 关联规则 | `../../rules/source-code-modify.md` | 受控例外（SRC-004） |
| 关联配置 | `../../config/scope-mapping.yaml` | commit scope 判定规则 |
