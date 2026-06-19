# Rules

全局约束规则——AI 与人都必须遵守的硬性约定，被项目根 `AGENTS.md` 引用为"改动前必须加载"的规则源。

## 文件说明

| 文件 | 约束内容 | 触发时机 |
|------|---------|---------|
| [source-code-modify.md](./source-code-modify.md) | 源码改动优先级：`~/workspace/` 是源头，`patchs/` 单向归档，未验证禁止归档 | 改动 `~/workspace/` 下任何源码前 |
| [doc-paths.md](./doc-paths.md) | 文档归档路径：覆盖 superpowers 默认的 `docs/superpowers/`，统一到 `docs/specs/` 与 `docs/plans/` | 使用 brainstorming / writing-plans skill 时 |
| [plantuml.md](./plantuml.md) | PlantUML 编写约束：禁止空图块等实际遇到的渲染失败问题及修复 | 编写任何 PlantUML 图表前 |
| [parallel-strategy.md](./parallel-strategy.md) | 子 agent 并行策略：拆分原则、文件不重叠、粒度上限（≤5 文件/agent） | 多任务并行处理时 |
| [script-observability.md](./script-observability.md) | bash 脚本维测规范：source 公共库、结构化 step、错误现场捕获、统一退出码、产物归档 | 改动 `engineering/` 下任何 bash 脚本前 |

> 本目录文件均被 `AGENTS.md` 声明为强制加载规则，改动会直接影响 AI 在对应场景的行为。
