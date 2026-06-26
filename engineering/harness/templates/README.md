# Templates

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：文档结构模板集——engineering 下 README 与 rules 的章节契约，以及技术文档（`docs/01-*`、`docs/02-*`）的设计模板
- **职责边界**：做结构约束模板；不做内容撰写（模板只读，由 `lc-sync-patchs-to-doc` workflow 消费）
- **上下游依赖**：被 `lc-sync-patchs-to-doc` workflow 作为只读契约消费；engineering 下所有 README 遵循 `engineering-readme-template.md`，所有 rules 遵循 `rules-template.md`

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | 本目录做什么、不做什么 | 首次进入 |
| [大纲](#大纲) | 本 README 章节索引 | 判断需要读哪些段 |
| [目录说明](#目录说明) | 5 份模板清单与适用对象 | 了解结构时 |
| [使用方式](#使用方式) | 模板使用约定（只读 / TEMPLATE-CONFLICT / 自检） | 新增/修改 README 或 rules 时 🔖 |
| [关联资源](#关联资源) | 设计文档、规则、workflow 链接 | 深入理解时 |

## 目录说明

| 文件 | 用途 | 适用对象 |
|------|------|---------|
| [`engineering-readme-template.md`](./engineering-readme-template.md) | Engineering 层 README 模板，核心 5 节 + 扩展块选配清单 + AI 三层读取机制 | engineering/*/README.md |
| [`rules-template.md`](./rules-template.md) | 规则文档模板，核心 5 节（规则 ID / 适用范围 / MUST / MUST NOT / 例外清单）+ 附录可选 | harness/rules/*.md |
| [`module-readme-template.md`](./module-readme-template.md) | 模块级 README 模板，4+1 视图（用例 / 逻辑 / 过程 / 开发 / 部署） | 特性目录入口文档（docs/01-*/README.md） |
| [`module-template.md`](./module-template.md) | 模块详细设计文档模板，覆盖用例 / 逻辑 / 过程 / 开发 / 部署 / 关键设计 / 接口参考等完整章节 | 特性下子模块文档（docs/01.01-*.md） |
| [`diagnosis-report-template.md`](./diagnosis-report-template.md) | Loop boot 诊断报告模板（7 节：结论 / 证据链 / 现象归类与不确定性 / 调查线索 / 候选修复方向 / case 建议 / 循环终止建议） | Loop 诊断报告产出 |

## 使用方式

本目录无可执行入口，作为只读契约承载层。

- **新增/修改 engineering 下 README**：遵循 [`engineering-readme-template.md`](./engineering-readme-template.md) 的核心 5 节骨架与扩展块选配清单。
- **新增/修改 rules**：遵循 [`rules-template.md`](./rules-template.md) 的核心 5 节（规则 ID / 适用范围 / MUST / MUST NOT / 例外清单）+ 附录。
- **技术文档同步**：由 `lc-sync-patchs-to-doc` workflow 按模板校验，diff 无法归入现有章节时标记 `TEMPLATE-CONFLICT`，由用户确认后才可调整模板。
- **文档结构与模板章节不一致**：`lc-sync-patchs-to-doc` 的自检环节会标记缺失 / 多余章节。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | `docs/specs/2026-06-21-engineering-doc-refactor-design.md` §四 | 模板定义（§4.1 engineering-readme-template、§4.2 rules-template） |
| 关联规则 | `../rules/doc-paths.md`（DOC-001） | 文档路径约束 |
| 关联规则 | `../rules/plantuml.md`（DOC-002） | 模板内 PlantUML 约束 |
| 关联 workflow | `../workflows/lc-sync-patchs-to-doc/` | 消费本目录模板为只读契约 |
