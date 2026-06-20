# AndroidSystemEnhance 项目约束

## 源码改动优先级
**改动 `~/workspace/` 下任何源码前，必须先加载** [engineering/harness/rules/source-code-modify.md](engineering/harness/rules/source-code-modify.md)（含验证流程、归档纪律、禁止行为）。
`~/workspace/` 是编译源码树（唯一参与编译），`patchs/` 是单向归档目录，改动必须从源头开始。

## 并行策略
优先使用子 agent 并行处理独立任务，提升效率并减少主会话上下文污染。
具体策略详见 [engineering/harness/rules/parallel-strategy.md](engineering/harness/rules/parallel-strategy.md)。

## PlantUML 画图约束
所有 PlantUML 图表编写前，必须参考 [engineering/harness/rules/plantuml.md](engineering/harness/rules/plantuml.md) 中的规则，防止渲染失败。

## 脚本维测规则（observability）
改动 `engineering/` 下任何 bash 脚本（含 workflows/、scripts/、未来 loop/ 等）前，必须先加载 [engineering/harness/rules/script-observability.md](engineering/harness/rules/script-observability.md)。
该规则强制要求：source 公共库、接入文件日志、结构化 step、错误现场捕获、统一退出码、中间产物归档。`engineering/output/log/` 为本地维测产物，不归档。

## 文档归档路径
所有设计规格和实施计划保存到 `docs/specs/` 和 `docs/plans/`，禁止使用 `docs/superpowers/`。
详细规则详见 [engineering/harness/rules/doc-paths.md](engineering/harness/rules/doc-paths.md)。

## 文档索引一致性
改动 `engineering/harness/` 下任何文件（脚本、规则、配置、文档、lib）后，必须检查相关 README.md 是否需要同步更新（新增/删除/重命名文件时尤其关键）。
详细检查清单见 [engineering/harness/README.md](engineering/harness/README.md) 的「README 同步」章节。

## 路径管理
`engineering/` 下所有脚本（shell / python / bat）禁止硬编码工程内路径，统一通过 `engineering/harness/config/harness-paths.conf`（单一事实源）+ 三方路径工具获取。
改动任何脚本的路径引用前，必须先加载 [engineering/harness/rules/path-management.md](engineering/harness/rules/path-management.md)（PATH-001）。
目录调整时仅修改 `paths.conf`，无需改动脚本。
