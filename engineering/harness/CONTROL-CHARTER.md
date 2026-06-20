# Harness Control Charter

`engineering/harness/` 是本项目的工程控制面与执行保障面，负责约束 AI、脚本与人工在源码、归档、文档、工作流、日志证据等环节的行为边界。

---

## 1. 目标边界

Harness **负责**：

1. 约束 `~/workspace/`、`patchs/`、`docs/`、`engineering/harness/` 之间的受控关系
2. 为关键 workflow 提供统一的流程契约、确认门、失败语义与证据要求
3. 为 harness/bash 脚本提供统一的 observability、日志、artifact、退出码与错误现场捕获机制
4. 为 AI 任务提供准入矩阵、规则入口与文档/模板/配置约束

Harness **不负责**：

1. 定义业务功能本身的产品需求
2. 替代 `~/workspace/` 作为编译真相源
3. 将所有人工判断完全自动化
4. 为未受控目录提供默认恢复或归档语义

---

## 2. 对象模型

### 2.1 核心对象

- **workspace**：`~/workspace/` 下的编译源码树，唯一参与编译，是日常开发的源码真相源
- **patchs**：项目历史命名沿用的归档容器，承载由 workspace 受控同步得到的 patch 资产
- **archive**：一次从 workspace 到 `patchs/` 的同步结果，表示“已归档”，不自动表示“已验证可恢复”
- **candidate baseline**：已完成部分验证、具备晋升条件但尚未完成最终确认的归档状态
- **promoted baseline**：证据完整、允许被 revert workflow 当作恢复真相源使用的 patch 基线
- **promotion**：archive / candidate baseline 晋升为 promoted baseline 的受控过程
- **docs**：项目文档体系，分为过程型文档（`docs/specs/`、`docs/plans/`）与长期技术资产文档（如 `docs/01-*`、`docs/02-*`）
- **workflow contract**：位于 `engineering/harness/workflows/*/WORKFLOW.md` 的流程契约，定义触发条件、前置条件、确认门、输出与失败恢复
- **template**：位于 `engineering/harness/templates/` 的文档结构契约，默认只读
- **artifact**：脚本执行过程中产生的计划、校验结果、日志、临时文件、报告等中间与证据产物

### 2.2 文档分层

- **过程型文档**：`docs/specs/`、`docs/plans/`，服务当前任务的设计与实施
- **长期资产型文档**：`docs/01-*`、`docs/02-*` 等，服务长期知识沉淀与技术设计说明

### 2.3 状态模型与证据要求

patch 资产的生命周期遵循单向晋升链：

```
archive -> candidate baseline -> promoted baseline
```

每个状态的边界：

| 状态 | 来源 | 最低证据要求 | 是否允许作为 revert 真相源 |
|------|------|-------------|-------------------------|
| archive | `sync-code-to-patchs` 完成同步 | sync manifest（含 `source_branch`、`source_commit`） | ❌ 否 |
| candidate baseline | archive 基础上补充部分验证 | sync manifest + `build_result` + `package_result` | ❌ 否 |
| promoted baseline | candidate 完成全部验证并人工批准 | sync manifest + `build_result` + `package_result` + `board_verify` + `approved_by` + `approved_at` | ✅ 是 |

规则：

1. **archive ≠ 验证通过**。archive 仅表示归档动作完成，不代表可恢复。
2. **candidate baseline ≠ promoted baseline**。candidate 表示验证进行中，未获最终批准。
3. **只有 promoted baseline 可作为 revert workflow 的真相源**。revert 前必须核对证据登记项。
4. 证据字段以 `engineering/harness/config/baseline-evidence-template.yaml` 为模板，状态登记维护在 `engineering/harness/config/baseline-status.yaml`。

---

## 3. 真相源矩阵

| 场景 | 真相源 | 说明 |
|------|--------|------|
| 日常源码开发 | `~/workspace/` | 唯一编译真相源 |
| patch 归档 | workspace 受控同步结果 | `patchs/` 只承接同步结果，不自行成为源码源头 |
| 文档同步 | patchs diff + manifest + workspace 上下文 | 由 workflow 与模板约束共同决定 |
| 灾难恢复 | promoted baseline | 只有完成晋升的 baseline 才能宣称为恢复真相源 |
| 脚本执行证据 | `engineering/output/log/` artifacts + log | 所有关键运行证据都必须可回溯 |

---

## 4. Human / AI / Script 职责边界

### Human

负责：

1. 给出任务目标与约束
2. 对关键确认门作出最终决定
3. 对 baseline promotion、模板变更、破坏性回退等高风险动作承担批准责任

### AI

负责：

1. 依据准入矩阵选择正确 workflow 或规则入口
2. 进行语义理解、方案生成、文档编排、差异分析与规则解释
3. 不越过确认门，不绕过 workflow，不臆造路径、状态或证据

### Script

负责：

1. 执行机械动作：扫描、复制、git 操作、生成计划、校验、归档、日志记录
2. 对失败显式报错，不做未经声明的猜测性 fallback
3. 通过 observability 保留运行现场与中间证据

---

## 5. 规则优先级

当多个约束同时出现时，优先级如下：

1. 用户明确指令
2. 本总纲（Control Charter）
3. `engineering/harness/rules/*.md`
4. `engineering/harness/workflows/*/WORKFLOW.md`
5. `engineering/harness/README.md` 及各子目录 README

说明：README 负责导航与解释，不应成为高于 rules / workflow contract 的唯一权威源。

---

## 6. 受控例外

以下场景属于受控例外，必须在对应 workflow 或规则中显式声明：

1. **patchs→workspace 回退**：仅在 workspace 坏状态恢复时允许，且仅以 promoted baseline 为真相源
2. **`patchs/others/` 直接维护**：因无 workspace 对应源码树，可作为独立受控区域直接维护
3. **模板冲突**：当新增内容无法纳入现有模板章节时，必须标记 `TEMPLATE-CONFLICT` 并等待人工确认
4. **upstream 缺失**：脚本必须显式失败并给出修复建议，禁止猜测 remote/base
5. **非 repo 目录**：必须由 workflow 明确定义其归档、恢复与校验语义，禁止隐式类比 repo 项目

---

## 7. 术语表

| 术语 | 定义 |
|------|------|
| workspace | 编译源码树，唯一编译真相源 |
| patchs | 历史命名保留的归档容器，不等同于默认 baseline |
| archive | 一次已完成同步的 patch 结果 |
| candidate baseline | 已进入晋升流程、待最终确认的归档状态 |
| promoted baseline | 证据完整、允许被当作恢复真相源的基线 |
| promotion | 将 archive / candidate baseline 晋升为 promoted baseline 的受控动作 |
| workflow contract | 描述脚本、AI、人工分工与边界的流程契约 |
| artifact | 可归档、可回溯的中间或证据产物 |
| confirmation gate | 需要人工明确确认后才能继续的流程关卡 |
