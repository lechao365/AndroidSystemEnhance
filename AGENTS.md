# AndroidSystemEnhance 项目约束

## 源码改动优先级

**改动 `~/workspace/` 下任何源码前，必须先加载** [engineering/harness/rules/source-code-modify.md](engineering/harness/rules/source-code-modify.md)（含验证流程、归档纪律、禁止行为）。
`~/workspace/` 是编译源码树（唯一参与编译），`patchs/` 是单向归档目录，改动必须从源头开始。

## 并行策略

优先使用子 agent 并行处理独立任务，提升效率并减少主会话上下文污染。
具体策略详见 [engineering/harness/rules/parallel-strategy.md](engineering/harness/rules/parallel-strategy.md)。

## PlantUML 画图约束

所有 PlantUML 图表编写前，必须参考 [engineering/harness/rules/plantuml.md](engineering/harness/rules/plantuml.md) 中的规则，防止渲染失败。

## 权限规则

继承全局权限规则。项目额外自动放行：`~/workspace/` 目录下所有文件的增删改查。

## 文档归档路径

所有设计规格和实施计划保存到 `docs/specs/` 和 `docs/plans/`，禁止使用 `docs/superpowers/`。
详细规则详见 [engineering/harness/rules/doc-paths.md](engineering/harness/rules/doc-paths.md)。
