# Templates

技术文档模板——只读契约，设计文档（`docs/01-*/`、`docs/02-*/`）必须遵循其章节结构。

## 文件说明

| 文件 | 用途 | 适用对象 |
|------|------|---------|
| [module-readme-template.md](./module-readme-template.md) | 模块级 README 模板，4+1 视图（用例 / 逻辑 / 过程 / 开发 / 部署）组织，用于特性的顶层 README（如 `01-打点增强/README.md`） | 特性目录的入口文档 |
| [module-template.md](./module-template.md) | 模块详细设计文档模板，覆盖用例 / 逻辑 / 过程 / 开发 / 部署 / 关键设计 / 接口参考等完整章节 | 特性下的单个子模块文档（如 `01.01-内核态增强.md`） |
| [diagnosis-report-template.md](./diagnosis-report-template.md) | Loop boot 诊断报告模板，约束 AI 在 FAIL 后基于 EvidenceBundle 产出结论 / 证据链 / 现象归类与不确定性 / 调查线索 / 候选修复方向 / case 建议 / 循环终止建议 | Loop boot 诊断报告产出 |
| [engineering-readme-template.md](./engineering-readme-template.md) | Engineering 层 README 模板，核心5节+扩展块+AI三层读取机制 | harness 子目录 README |
| [rules-template.md](./rules-template.md) | 规则文件模板，5节核心+附录+现有规则校准指引 | harness/rules/ 下的规则文件 |

## 约束

- **只读**：`sync-patchs-to-doc` workflow 将本目录视为只读契约，AI 不得擅改。
- diff 引入的内容无法归入现有模板章节时，标记 `TEMPLATE-CONFLICT`，由用户确认后才可调整模板。
- 文档结构与模板章节不一致时，`sync-patchs-to-doc` 的自检环节会标记缺失 / 多余章节。
