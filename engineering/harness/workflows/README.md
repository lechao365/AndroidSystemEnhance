# Workflows

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：多步闭环工作流集——每个子目录是一个完整流程（可执行脚本 + WORKFLOW.md 流程契约）
- **职责边界**：做 harness 公共工程 workflow（git / 归档 / 回退 / 文档同步）；不做 loop 专属 workflow（在 `../../loop/workflows/`）
- **上下游依赖**：被 `.opencode/commands/*.md`（5 份）通过 `@WORKFLOW.md` 注入 AI 上下文；依赖 `lib/`（bootstrap）、`config/`（scope / doc-sync mapping）、`rules/`

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [大纲](#大纲) | 本 README 章节索引 | 判断需要读哪些段 |
| [目录说明](#目录说明) | 5 个工作流清单（触发场景 / 核心语义 / 入口） | 了解结构时 |
| [使用方式](#使用方式) | 进入方式与入口脚本调用示例 | 实际使用时 |
| [结构约定](#结构约定) | 子目录组成 + WORKFLOW.md 工具消费事实 + 产物位置 | 新增 / 修改 workflow 时 🔖 |
| [关联资源](#关联资源) | 设计文档、规则、配置、workflow 链接 | 深入理解时 |

## 目录说明

| 工作流 | 触发场景 | 核心语义 | 入口 |
|--------|---------|---------|------|
| [git-push-to-server](./git-push-to-server/) | 收集 diff → 生成 commit → 推送 | 脚本做机械工作，AI 做语义工作（生成 message） | `collect_diff.sh` → AI → `commit_and_push.sh` |
| [sync-code-to-patchs](./sync-code-to-patchs/) | workspace 源码归档到 patchs 镜像 | 将 workspace 变更受控归档到 patchs；archive 不自动等同于 promoted baseline | `sync_code_to_patchs.sh` |
| [revert-code-from-patchs](./revert-code-from-patchs/) | workspace 坏了，从 patchs 基线回退 | 仅允许以 promoted baseline 执行恢复；用于 workspace 坏状态回退 | `revert_code_from_patchs.sh` |
| [sync-patchs-to-doc](./sync-patchs-to-doc/) | patchs 变动后同步更新技术文档 | 方案先行（动作清单），确认后落盘，模板只读 | `sync_patchs_to_doc.sh` |
| [lc-quick-fix-issue](./lc-quick-fix-issue/) | 根据检视意见自动修复代码→测试→零确认提交推送 | 脚本做确定性工作（探测/提交），AI 做语义工作（分析/定位/修复） | `detect_test_env.sh` → AI → `commit_and_push.sh` |

## 使用方式

按触发场景进入对应子目录，先读其 `WORKFLOW.md` 了解流程契约与确认门，再执行入口脚本。

```bash
# git 推送
bash engineering/harness/workflows/git-push-to-server/collect_diff.sh

# workspace → patchs 归档
bash engineering/harness/workflows/sync-code-to-patchs/sync_code_to_patchs.sh

# patchs → workspace 回退
bash engineering/harness/workflows/revert-code-from-patchs/revert_code_from_patchs.sh

# patchs → 文档同步
bash engineering/harness/workflows/sync-patchs-to-doc/sync_patchs_to_doc.sh

# 根据检视意见自动修复并提交（通过 /lc-quick-fix-issue 命令触发，无需手动调用脚本）
bash engineering/harness/workflows/lc-quick-fix-issue/detect_test_env.sh
```

## 结构约定

每个工作流子目录包含：
- **脚本**（`*.sh`）：承担机械工作（diff 收集、git 操作、文件拷贝、归档）。统一通过 `lib/shell/harness_bootstrap.sh` 接入维测库，遵循 `rules/script-observability.md`。
- **`WORKFLOW.md`**：流程契约，定义步骤、AI 与脚本的分工、参数、边界处理、异常处理。被 `.opencode/commands/*.md` 通过 `@` 注入 AI 上下文，且被 `validate_harness_docs.sh` 校验 front matter（`name`/`description`）。
- **`README.md`**：极简入口（一句话定位 + 指向 WORKFLOW.md），不重复流程内容。

> 详细流程、参数清单、边界处理请阅读对应子目录的 `WORKFLOW.md`。

### 脚本默认产物位置

- 日志：`engineering/output/log/<script-name>/latest.log`
- 中间产物（manifest、plan、verify、build-report 等）：`engineering/output/log/<script-name>/artifacts/<ts>-<name>`
- 临时文件：同 artifacts 目录下 `<ts>-tmp-<name>`（不再裸写 `/tmp/`）
- 需要让用户 / AI 显式消费的产物（如 revert plan）默认走 artifacts，同时保留 CLI 参数（`--plan-file` 等）允许指定外部路径。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/specs/2026-06-21-engineering-doc-refactor-design.md` | 文档重构设计（WORKFLOW 保留决策 D-WF） |
| 关联规则 | `../rules/script-observability.md`（OBS-001） | 脚本维测约束 |
| 关联规则 | `../rules/source-code-modify.md`（SRC-001） | 归档 / 回退约束 |
| 关联配置 | `../config/scope-mapping.yaml` | git-push-to-server 消费 |
| 关联配置 | `../config/doc-sync-mapping.yaml` | sync-patchs-to-doc 消费 |
| 关联配置 | `../config/baseline-status.yaml` | revert-code-from-patchs 消费 |
| 关联 workflow | `../../loop/workflows/` | loop 专属 workflow 见 `../../loop/workflows/README.md` |
