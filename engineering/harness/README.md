# Engineering Harness

本项目工程化基础设施——规则、工作流、模板、配置的统一承载层，约束 AI 与人在源码 / 归档 / 文档 / 提交各环节的行为。

## 快速导航（按意图查找）

| 我要做的事 | 先读哪里 |
|-----------|---------|
| 改 `~/workspace/` 源码 | [rules/source-code-modify.md](./rules/source-code-modify.md) |
| 提交并推送 | [workflows/git-push-to-server/](./workflows/git-push-to-server/) |
| 归档源码到 patchs | [workflows/sync-code-to-patchs/](./workflows/sync-code-to-patchs/) |
| workspace 坏了要回退 | [workflows/revert-code-from-patchs/](./workflows/revert-code-from-patchs/) |
| patchs 变了更新技术文档 | [workflows/sync-patchs-to-doc/](./workflows/sync-patchs-to-doc/) |
| 写 / 改技术文档 | [templates/](./templates/) + [rules/doc-paths.md](./rules/doc-paths.md) |
| 画 PlantUML 图 | [rules/plantuml.md](./rules/plantuml.md) |
| 多任务并行处理 | [rules/parallel-strategy.md](./rules/parallel-strategy.md) |
| 改 harness 下的 bash 脚本 | [rules/script-observability.md](./rules/script-observability.md) |
| 查 commit scope 映射 | [config/scope-mapping.md](./config/scope-mapping.md) |
| 查 patchs→文档分发规则 | [config/doc-sync-mapping.md](./config/doc-sync-mapping.md) |

## 目录说明

| 目录 | 作用 |
|------|------|
| [config/](./config/) | workflow 依赖的映射配置表（scope、文档分发），新增目录只改配置不动脚本 |
| [lib/](./lib/) | bash 公共库：`harness_bootstrap.sh`（统一入口）+ `harness_observability.sh`（日志/step/artifact/tmp/status/upstream） |
| [log/](./log/) | 脚本运行时日志产物（不归档、随时覆盖，勿手动修改）；临时文件也落入此处 artifacts/ |
| [rules/](./rules/) | 全局约束规则，AI 与人都必须遵守的硬性约定 |
| [scripts/](./scripts/) | 独立一次性脚本（非工作流），如全量镜像构建 |
| [templates/](./templates/) | 技术文档模板（只读契约），设计文档必须遵循 |
| [workflows/](./workflows/) | 多步闭环工作流，每个子目录 = 一个完整流程（脚本 + WORKFLOW.md） |

## lib 公共能力速查

所有 `engineering/` 下 bash 脚本通过 `lib/harness_bootstrap.sh` 统一入口加载：

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../lib/harness_bootstrap.sh"   # 自动定位 REPO_ROOT + source observability
harness_init "<script-name>"
```

提供的关键能力（详见 [rules/script-observability.md](./rules/script-observability.md)）：

- **日志/步骤**：`log_info/warn/error`、`log_result`、`step_begin/end`
- **状态输出**：`harness_status_emit <OK|MISS|SKIP|STALE|PRUNE> <label>`
- **临时产物**：`harness_tmp_file` / `harness_tmp_dir`（自动落入 artifacts，参与轮转）
- **错误捕获**：`on_err`、模式 A/B
- **upstream 基线**：`harness_find_upstream_base`、`harness_report_no_upstream`（显式策略，禁止猜测）
- **EXIT 回调**：`harness_on_exit_add "<cmd>"`（替代手写 trap）
- **退出收尾**：`harness_exit [code]`

> **API 边界**：业务脚本只能使用不带下划线前缀的公共 API；`_H_*` / `_h_*` 为库内部私有，禁止直接依赖。

## 约定

- **rules/** 是强制约束，被 `AGENTS.md` 引用为加载规则，改动直接影响 AI 行为。
- **config/** 是 rules/workflows 的数据源，新增目录或模块只改配置不动脚本。
- **templates/** 为只读契约，改模板需用户确认（见 `sync-patchs-to-doc` 的 `TEMPLATE-CONFLICT`）。
- **lib/** 是 scripts/workflows 的公共依赖，改动需同步影响范围内的脚本。
