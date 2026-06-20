# Workflows

多步闭环工作流——每个子目录是一个完整流程，含可执行脚本 + `WORKFLOW.md`（流程契约，定义脚本与 AI 的分工）。

## 工作流清单

| 工作流 | 触发场景 | 核心语义 | 入口 |
|--------|---------|---------|------|
| [git-push-to-server](./git-push-to-server/) | 收集 diff → 生成 commit → 推送 | 脚本做机械工作，AI 做语义工作（生成 message） | `collect_diff.sh` → AI → `commit_and_push.sh` |
| [sync-code-to-patchs](./sync-code-to-patchs/) | workspace 源码归档到 patchs 镜像 | 将 workspace 变更受控归档到 patchs；archive 不自动等同于 promoted baseline | `sync_code_to_patchs.sh` |
| [revert-code-from-patchs](./revert-code-from-patchs/) | workspace 坏了，从 patchs 基线回退 | 仅允许以 promoted baseline 执行恢复；用于 workspace 坏状态回退 | `revert_code_from_patchs.sh` |
| [sync-patchs-to-doc](./sync-patchs-to-doc/) | patchs 变动后同步更新技术文档 | 方案先行（动作清单），确认后落盘，模板只读 | `sync_patchs_to_doc.sh` |

## 结构约定

每个工作流子目录包含：
- **脚本**（`*.sh`）：承担机械工作（diff 收集、git 操作、文件拷贝、归档）。统一通过 `lib/harness_bootstrap.sh` 接入维测库，遵循 `rules/script-observability.md`。
- **`WORKFLOW.md`**：流程契约，定义步骤、AI 与脚本的分工、参数、边界处理、异常处理
- **`README.md`**：极简入口（一句话定位 + 指向 WORKFLOW.md），不重复流程内容

> 详细流程、参数清单、边界处理请阅读对应子目录的 `WORKFLOW.md`。

## 脚本默认产物位置

- 日志：`engineering/output/log/<script-name>/latest.log`
- 中间产物（manifest、plan、verify、build-report 等）：`engineering/output/log/<script-name>/artifacts/<ts>-<name>`
- 临时文件：同 artifacts 目录下 `<ts>-tmp-<name>`（不再裸写 `/tmp/`）
- 需要让用户/AI 显式消费的产物（如 revert plan）默认走 artifacts，同时保留 CLI 参数（`--plan-file` 等）允许指定外部路径
