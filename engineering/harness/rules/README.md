# Rules

全局约束规则——AI 与人都必须遵守的硬性约定，被项目根 `AGENTS.md` 引用为“改动前必须加载”的规则源。

## 规则 ID 说明

- 规则采用 **RID（Rule ID）** 标识，格式为 `<主题>-<序号>`（如 `SRC-001`、`OBS-002`）。
- 主题前缀：`SRC`（source-code-modify）、`DOC`（doc-paths / plantuml）、`OBS`（script-observability）、`PAR`（parallel-strategy）。
- RID 用于在 README、workflow contract、plan、review 与 validator 输出中做稳定引用，避免仅靠文件名沟通。
- 同一规则文件可包含多个 RID；README 负责给出入口与摘要，不替代规则正文。
- 每个规则文件顶部均有 `> **规则 ID**：` 块标注其声明的 RID，作为权威来源。

## 文件说明

| 规则 ID | 文件 | 约束内容 | 触发时机 |
|---------|------|---------|---------|
| `SRC-001` / `SRC-002` / `SRC-003` / `SRC-004` | [source-code-modify.md](./source-code-modify.md) | `SRC-001` workspace 是唯一编译真相源；`SRC-002` patchs 单向受控归档（仅 sync-code-to-patchs）；`SRC-003` patchs/others 可独立维护；`SRC-004` 未证据化 baseline 不得宣称为恢复真相源 | 改动 `~/workspace/` 下任何源码前 |
| `DOC-001` | [doc-paths.md](./doc-paths.md) | 文档分层与归档路径：覆盖 superpowers 默认的 `docs/superpowers/`，统一到 `docs/specs/` 与 `docs/plans/` | 使用 brainstorming / writing-plans skill 时 |
| `DOC-002` | [plantuml.md](./plantuml.md) | PlantUML 编写约束：禁止空图块、禁止 UML 块内花括号占位符、必须显式闭合、条件块内禁止 fork、活动图颜色新语法 | 编写任何 PlantUML 图表前 |
| `PAR-001` | [parallel-strategy.md](./parallel-strategy.md) | 子 agent 并行策略：独立优先、文件不重叠、粒度上限（≤5 文件/agent） | 多任务并行处理时 |
| `OBS-001` / `OBS-002` | [script-observability.md](./script-observability.md) | `OBS-001` 必须通过 bootstrap 接入 `harness_init`；`OBS-002` 统一退出码、禁裸 exit、禁裸 `/tmp/`、产物归档 | 改动 `engineering/` 下任何 bash 脚本前 |

> 本目录文件均被 `AGENTS.md` 声明为强制加载规则，改动会直接影响 AI 在对应场景的行为。
> 规则优先级遵循 [CONTROL-CHARTER.md](../CONTROL-CHARTER.md)：用户指令 > Control Charter > `rules/*.md` > `workflows/*/WORKFLOW.md` > README。
