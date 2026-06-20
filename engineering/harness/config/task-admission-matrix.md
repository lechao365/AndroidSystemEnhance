# Task Admission Matrix

> **用途**：为 AI 与人工在进入 `engineering/harness/` 相关任务前提供统一路由表，回答“当前任务是否允许直接改、必须先读哪些规则、是否必须经 workflow、是否需要计划/确认/evidence”。

## 准入矩阵

| 任务类型 | 允许直接修改 | 必读规则 | 必经 workflow | 是否先出 plan | 是否需用户确认 | 是否需 evidence |
|----------|--------------|----------|---------------|---------------|----------------|-----------------|
| `~/workspace/` 源码修改 | 否 | `rules/source-code-modify.md` | 视任务而定 | 否 | 视任务而定 | 是 |
| `patchs/` 归档（workspace → patchs） | 否 | `rules/source-code-modify.md` | `workflows/sync-code-to-patchs/` | 否 | README/附加说明按 workflow 约束 | 是 |
| `patchs/` 回退（patchs → workspace） | 否 | `rules/source-code-modify.md` | `workflows/revert-code-from-patchs/` | 是 | 是 | 是 |
| patchs → 技术文档同步 | 否 | `rules/doc-paths.md`、`rules/plantuml.md` | `workflows/sync-patchs-to-doc/` | 是 | 是 | 是 |
| commit / push | 否 | workflow 契约 + commit scope 配置 | `workflows/git-push-to-server/` | 否 | 是 | 是 |
| harness bash 脚本改造 | 是 | `rules/script-observability.md` | 视脚本而定 | 否 | 否 | 是 |
| harness 规则文档改造 | 是 | `CONTROL-CHARTER.md` + 对应 `rules/*.md` | 无 | 视范围而定 | 视风险而定 | 建议保留 |
| harness 模板改造 | 是 | `rules/plantuml.md` + `templates/README.md` | 无 | 建议先出方案 | 是 | 建议保留 |
| harness 配置映射改造 | 是 | `CONTROL-CHARTER.md` + `config/README.md` | 无 | 视范围而定 | 视风险而定 | 建议保留 |
| validator / 测试夹具改造 | 是 | `rules/script-observability.md`（脚本类）+ 本矩阵 | 无 | 否 | 否 | 是 |

## 使用规则

1. 无法命中矩阵的任务，不得直接执行，应先补充任务分类或更新本矩阵。
2. 若任务同时命中多个类别，优先选择副作用更强、确认门更多的那一类。
3. “允许直接修改”仅表示可直接编辑相关受控文件，不代表可以绕过验证与 evidence 要求。
4. 任何涉及 `patchs` 真相源语义切换、模板结构变更、批量回退的任务，都应提高到“需要确认”的处理级别。
