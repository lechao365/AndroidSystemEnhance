# Templates

技术文档模板——只读契约，设计文档（`docs/01-*/`、`docs/02-*/`）必须遵循其章节结构。

## 文件说明

| 文件 | 用途 | 适用对象 |
|------|------|---------|
| [module-readme-template.md](./module-readme-template.md) | 模块级 README 模板，4+1 视图（用例 / 逻辑 / 过程 / 开发 / 部署）组织，用于特性的顶层 README（如 `01-打点增强/README.md`） | 特性目录的入口文档 |
| [module-template.md](./module-template.md) | 模块详细设计文档模板，覆盖用例 / 逻辑 / 过程 / 开发 / 部署 / 关键设计 / 接口参考等完整章节 | 特性下的单个子模块文档（如 `01.01-内核态增强.md`） |
| [diagnosis-report-template.md](./diagnosis-report-template.md) | Loop boot 诊断报告模板，约束 AI 收到 EvidenceBundle 后产出的 markdown 报告格式（结论 / 证据链 / 根因 / 修复建议 / 建议新增 case / 循环终止建议） | Loop boot 诊断报告产出 |

## 约束

- **只读**：`sync-patchs-to-doc` workflow 将本目录视为只读契约，AI 不得擅改。
- diff 引入的内容无法归入现有模板章节时，标记 `TEMPLATE-CONFLICT`，由用户确认后才可调整模板。
- 文档结构与模板章节不一致时，`sync-patchs-to-doc` 的自检环节会标记缺失 / 多余章节。
