# Engineering Harness

本项目工程控制面与执行保障面：通过控制总纲、准入矩阵、规则、工作流、模板、配置与日志证据，约束 AI、人工与脚本在源码 / 归档 / 文档 / 提交各环节的行为边界。

## 快速导航（按意图查找）

| 我要做的事 | 先读哪里 |
|-----------|---------|
| 先判断任务能不能直接做 | [config/task-admission-matrix.md](./config/task-admission-matrix.md) |
| 理解 harness 总体边界与真相源 | [CONTROL-CHARTER.md](./CONTROL-CHARTER.md) |
| 改 `~/workspace/` 源码 | [rules/source-code-modify.md](./rules/source-code-modify.md) |
| 提交并推送 | [workflows/git-push-to-server/](./workflows/git-push-to-server/) |
| 归档源码到 patchs | [workflows/sync-code-to-patchs/](./workflows/sync-code-to-patchs/) |
| workspace 坏了要回退 | [workflows/revert-code-from-patchs/](./workflows/revert-code-from-patchs/) |
| patchs 变了更新技术文档 | [workflows/sync-patchs-to-doc/](./workflows/sync-patchs-to-doc/) |
| 写 / 改技术文档 | [templates/](./templates/) + [rules/doc-paths.md](./rules/doc-paths.md) |
| 画 PlantUML 图 | [rules/plantuml.md](./rules/plantuml.md) |
| 多任务并行处理 | [rules/parallel-strategy.md](./rules/parallel-strategy.md) |
| 改 harness 下的 bash 脚本 | [rules/script-observability.md](./rules/script-observability.md) |
| 查 config 机器层 / 映射层说明 | [config/README.md](./config/README.md) |
| 查 commit scope 映射 | [config/scope-mapping.md](./config/scope-mapping.md) |
| 查 patchs→文档分发规则 | [config/doc-sync-mapping.md](./config/doc-sync-mapping.md) |
| 做 harness 静态校验 | `engineering/harness/scripts/validate_harness_*.sh`（validator 落地后启用） |

## 控制入口

- **控制总纲**：[CONTROL-CHARTER.md](./CONTROL-CHARTER.md) —— 定义目标边界、对象模型、真相源矩阵、Human / AI / Script 职责边界与规则优先级。
- **任务准入矩阵**：[config/task-admission-matrix.md](./config/task-admission-matrix.md) —— 进入任务前先判断是否允许直接修改、必须先读哪些规则、是否需要 workflow / plan / 用户确认 / evidence。
- **规则索引**：[rules/README.md](./rules/README.md) —— 规则 ID、触发时机、适用范围。
- **工作流索引**：[workflows/README.md](./workflows/README.md) —— 需要 workflow contract 的任务从这里进入。

## 目录说明

| 目录 | 作用 |
|------|------|
| [config/](./config/) | 控制配置与映射层；包含任务准入矩阵、scope 映射、文档分发映射，供 README / rules / workflows 协同消费 |
| [lib/](./lib/) | bash 公共库：`harness_bootstrap.sh`（统一入口）+ `harness_observability.sh`（日志/step/artifact/tmp/status/upstream） |
| [rules/](./rules/) | 全局约束规则，AI 与人都必须遵守的硬性约定 |
| [scripts/](./scripts/) | 独立脚本与静态校验入口；后续 validator 默认从本目录进入 |
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

- **优先级**：用户指令 > [CONTROL-CHARTER.md](./CONTROL-CHARTER.md) > `rules/*.md` > `workflows/*/WORKFLOW.md` > README 导航说明。
- **rules/** 是强制约束，被 `AGENTS.md` 引用为加载规则，改动直接影响 AI 行为。
- **config/** 是控制配置层；任务路由先看准入矩阵，目录映射再看具体 mapping。
- **templates/** 为只读契约，改模板需用户确认（见 `sync-patchs-to-doc` 的 `TEMPLATE-CONFLICT`）。
- **lib/** 是 scripts/workflows 的公共依赖，改动需同步影响范围内的脚本。
- **README 同步**：改动本目录下文件后，按以下清单检查 README 是否需更新：
  - 新增/删除/重命名 `lib/*.sh`、`scripts/*.sh`、`workflows/*/`、`rules/*.md`、`config/*.md`、`templates/*` → 更新对应子目录 README.md 的文件清单
  - 公共 API 变动（`lib/*.sh` 新增/删除函数对外暴露）→ 额外更新本 README 的「lib 公共能力速查」章节
  - 新增/删除 `rules/*.md` → 同步更新 `rules/README.md` 文件说明表 + 本 README 快速导航表
  - 仅修改文件内容（文件名/结构不变）→ 无需更新 README
