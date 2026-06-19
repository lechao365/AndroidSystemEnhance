# Engineering Harness README 体系设计

## 背景与目标

`engineering/harness/` 是本项目工程化基础设施的承载层，包含规则、工作流、模板、配置等。
随着内容增长，缺少分层索引，人类与大模型都需要多次 `ls` + 读文件才能定位目标。

本设计在 `harness/` 目录下建立**两级 README 体系**：
- **顶层 README**：子目录级总览 + 按意图查找的导航表
- **子目录 README**：文件级说明（每个文件/子目录的作用）

目标：人类可读、AI 可快速定位、职责分层不重叠。

## 范围

### 包含

在 `engineering/harness/` 下创建 11 个 README：

```
engineering/harness/
├── README.md                                      ← 顶层入口 + 意图导航表
├── config/README.md
├── lib/README.md
├── rules/README.md
├── scripts/README.md
├── templates/README.md
├── workflows/README.md                            ← workflows 总览
├── workflows/git-push-to-server/README.md         ← 极简（指向 WORKFLOW.md）
├── workflows/revert-code-from-patchs/README.md
├── workflows/sync-code-to-patchs/README.md
└── workflows/sync-patchs-to-doc/README.md
```

### 排除

- `log/` 不建 README：运行时产物目录，内容动态变化、不归档，顶层 README 一句话说明即可。
- 不在 `harness/` 以外（如 `engineering/`、`patchs/`）建 README（超出本次范围）。

## 设计原则

1. **分层不重叠**：顶层讲子目录"是什么"，子层讲文件"做什么"，禁止跨层重复。
2. **AI 友好**：顶层用"按意图查找"导航表（表格式），大模型可一眼定位到具体文件。
3. **表格优先**：所有文件清单用表格呈现，便于结构化解析。
4. **极简**：单文件目录（lib、scripts）允许用段落替代表格；workflow 子目录 README 控制在 3 行内。
5. **全中文**：与现有 rules/config/templates 文档风格一致。
6. **相对路径链接**：所有交叉引用用相对路径（`./WORKFLOW.md`、`../../rules/xxx.md`）。
7. **不重复内容**：README 只索引不展开，详情由被索引文件自身承载。

## 详细设计

### 1. 顶层 `harness/README.md`

**职责**：harness 全局入口，提供两条路径——意图导航表（做什么→读哪里）+ 目录总览（每个子目录一句话）。

**结构**：
- 一句话定位 harness 的角色
- **快速导航（按意图查找）**表：典型任务 → 对应文件/目录
- **目录说明**表：每个一级子目录一句话（含 log/，标注不归档）
- 约定段落：rules/config/templates 之间的强制关系

**内容边界**：不展开各文件细节（归子目录 README），不列 workflow 子目录文件（归 workflows/README.md）。

### 2. 子目录 README（config/lib/rules/scripts/templates）

**职责**：说明本目录下每个文件的功能，用表格列出。

**统一结构**：
```markdown
# {目录名}

一句话定位本目录职责。

## 文件说明
| 文件 | 作用 | 关键约束/备注 |
|------|------|--------------|
| xxx | 一句话 | 备注 |
```

单文件目录（lib、scripts）可用段落替代表格，保持轻量。

### 3. workflows/README.md

**职责**：workflows 总览，列出 4 个工作流的触发场景与入口。

**结构**：
- 一句话定位
- 工作流清单表（工作流 | 触发场景 | 入口）
- 说明：每个子目录有极简 README + WORKFLOW.md（完整契约）

### 4. workflow 子目录极简 README（4 个）

**职责**：一句话定位 + 指向 WORKFLOW.md，不重复流程内容。

**统一格式**：
```markdown
# {workflow 名}

{一句话定位}。完整流程见 [WORKFLOW.md](./WORKFLOW.md)。
```

## 已确认的边界决策

| 问题 | 决策 | 理由 |
|------|------|------|
| workflow 子目录是否建 README | 建极简 README | 作为入口，指向 WORKFLOW.md，不重复 |
| log/ 是否建 README | 不建 | 运行时产物，内容动态变化 |
| 单文件目录是否建 README | 都建 | 一致性，未来扩展不需重新判断 |
| 顶层是否加意图导航表 | 加 | 提升大模型定位效率 |

## 验收标准

1. 11 个 README 文件全部落盘到指定路径
2. 顶层 README 的意图导航表覆盖所有典型任务（改源码/提交/归档/回退/同步文档/写文档/画图/并行/改脚本）
3. 每个子目录 README 准确描述其下文件的实际职责（不臆造）
4. 所有交叉引用链接用相对路径且可达
5. 全中文，无客套话，表格优先
6. workflow 子目录 README 不重复 WORKFLOW.md 内容

## 不做的事（YAGNI）

- 不为 harness/ 以外的目录建 README
- 不在 README 中展开文件内部细节（如函数签名、章节列表）
- 不重写或修改现有 WORKFLOW.md / rules / config 文件
- 不增加新的 rules 或约束
