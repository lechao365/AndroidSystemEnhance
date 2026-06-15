# AndroidSystemEnhance 项目约束

## Patchs 归档规则

执行 `scripts/sync_code_to_patchs.sh` 完成一键同步，规则详见 [rules/sync_code_to_patchs.md](rules/sync_code_to_patchs.md)。

## 文档同步规则

当 patchs/rpi5/ 发生变动后，执行 `scripts/sync_patchs_to_doc.sh` 生成变动报告，
AI 根据报告内容给出技术文档的更新方案，经确认后落盘。
规则详见 [rules/sync_patchs_to_doc.md](rules/sync_patchs_to_doc.md)。

## 源码改动优先级

`~/workspace/` 是编译源码树（唯一参与编译），`patchs/` 是单向归档目录，改动必须从源头开始。
详细规则详见 [rules/source-code-priority.md](rules/source-code-priority.md)。

## 并行策略

优先使用子 agent 并行处理独立任务，提升效率并减少主会话上下文污染。
具体策略详见 [rules/parallel-strategy.md](rules/parallel-strategy.md)。

## PlantUML 画图约束

所有 PlantUML 图表编写前，必须参考 [rules/plantuml.md](rules/plantuml.md) 中的规则，防止渲染失败。

## 权限规则

继承全局权限规则。项目额外自动放行：`~/workspace/` 目录下所有文件的增删改查。

## 文档归档路径

所有设计规格和实施计划保存到 `docs/specs/` 和 `docs/plans/`，禁止使用 `docs/superpowers/`。
详细规则详见 [rules/doc-paths.md](rules/doc-paths.md)。
