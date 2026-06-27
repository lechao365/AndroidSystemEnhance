# Engineering Harness

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：本项目工程控制面与执行保障面，通过控制总纲、准入矩阵、规则、工作流、模板、配置与日志证据，约束 AI、人工与脚本在源码 / 归档 / 文档 / 提交各环节的行为边界
- **职责边界**：只承载公共 harness engineering 能力（规则 / workflow / 公共脚本 / 模板 / 配置），不承载 loop-specific case / workflow / controller / session / LE CLI
- **上下游依赖**：被 `AGENTS.md`（项目根）引用为强制加载规则源；单向依赖 `loop/ → harness/`，禁止 `harness/ → loop/`

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | harness 做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 子目录清单与职责索引 | 了解结构时 |
| [快速导航](#快速导航) | "我要做的事 → 先读哪里" | 🔖 找入口时 |
| [使用方式](#使用方式) | harness 无单一入口，各子目录独立入口 | 实际使用时 |
| [关联资源](#关联资源) | 设计文档 / 规则 / workflow / 配置链接 | 深入理解时 |
| [控制总纲](#控制总纲) 🔖 | 目标边界 / 对象模型 / 真相源矩阵 / 职责边界 / 优先级链 | 涉及优先级裁决、真相源判定时 |
| [lib 公共能力速查](#lib-公共能力速查) | bootstrap 加载示例 + 公共 API 清单 | 改 / 写 harness bash 脚本时 |
| [README 同步约定](#readme-同步约定) | 文件变更 → README 更新清单 | 改动 harness 下文件后 |

## 目录说明

| 子目录/文件 | 职责 | 关键入口/被谁引用 |
|------------|------|------------------|
| [config/](./config/) | 控制配置与映射层：任务准入矩阵、scope 映射、文档分发映射 | [config/README.md](./config/README.md) |
| [lib/](./lib/) | bash/python/bat 公共库：路径工具 + bootstrap + observability | [lib/README.md](./lib/README.md) |
| [rules/](./rules/) | 全局约束规则（AI 与人都必须遵守的硬性约定），被 `AGENTS.md` 强制加载 | [rules/README.md](./rules/README.md) |
| [scripts/](./scripts/) | 独立脚本与静态校验入口（validator） | [scripts/README.md](./scripts/README.md) |
| [templates/](./templates/) | 技术文档模板（只读契约），被 `lc-sync-patchs-to-doc` 消费 | [templates/README.md](./templates/README.md) |
| [reference/](./reference/) | 参考文档承载层（命令模板、操作指南等非约束性参考） | [reference/README.md](./reference/README.md) |
| [workflows/](./workflows/) | 多步闭环工作流，每个子目录 = 一个完整流程（脚本 + WORKFLOW.md） | [workflows/README.md](./workflows/README.md) |
| [tests/](./tests/) | harness 自测脚本与 fixtures（observability / workflow 测试） | — |

> 子目录自身的细节见其 `README.md`，本表只给一句话索引。

## 快速导航

> 🔖 按意图查找入口。优先级裁决与真相源判定请直接读 [控制总纲](#控制总纲)。

| 我要做的事 | 先读哪里 |
|-----------|---------|
| 先判断任务能不能直接做 | [config/README.md#任务准入矩阵](./config/README.md#任务准入矩阵) |
| 理解 harness 总体边界与真相源 | [#控制总纲](#控制总纲) |
| 改 `~/workspace/` 源码 | [rules/source-code-modify.md](./rules/source-code-modify.md) |
| 提交并推送 | [workflows/lc-git-push-to-server/](./workflows/lc-git-push-to-server/) |
| 归档源码到 patchs | [workflows/lc-sync-code-to-patchs/](./workflows/lc-sync-code-to-patchs/) |
| workspace 坏了要回退 | [workflows/lc-revert-code-from-patchs/](./workflows/lc-revert-code-from-patchs/) |
| patchs 变了更新技术文档 | [workflows/lc-sync-patchs-to-doc/](./workflows/lc-sync-patchs-to-doc/) |
| 跑 loop runtime 全自动验收 | [../loop/controller/README.md](../loop/controller/README.md) |
| 写 / 改技术文档 | [templates/](./templates/) + [rules/doc-paths.md](./rules/doc-paths.md) |
| 画 PlantUML 图 | [rules/plantuml.md](./rules/plantuml.md) |
| 多任务并行处理 | [rules/parallel-strategy.md](./rules/parallel-strategy.md) |
| 改 harness 下的 bash 脚本 | [rules/script-observability.md](./rules/script-observability.md) |
| 获取工程路径 / 改路径配置 | [rules/path-management.md](./rules/path-management.md) |
| 查 config 机器层 / 映射层说明 | [config/README.md](./config/README.md) |
| 查 commit scope 映射 | [config/scope-mapping.yaml](./config/scope-mapping.yaml) |
| 查 patchs→文档分发规则 | [config/doc-sync-mapping.yaml](./config/doc-sync-mapping.yaml) |
| 查 RPI5 编译命令参考 | [reference/build-reference.md](./reference/build-reference.md) |
| 做 harness 静态校验 | [scripts/validate_harness_docs.sh](./scripts/validate_harness_docs.sh) |

## 使用方式

harness 无统一可执行入口，各子目录有独立入口。常见入口：`validate_harness_docs.sh`（文档校验）、`workflows/*/bin/*.sh`（工作流脚本）。详细入口清单见各子目录 README。

### 入口清单

| 入口 | 作用 | 调用方式 |
|------|------|---------|
| [validate_harness_docs.sh](./scripts/validate_harness_docs.sh) | 文档/契约层静态校验（README 链接、文件清单、PlantUML、workflow front matter） | `bash engineering/harness/scripts/validate_harness_docs.sh` |
| [validate_harness_scripts.sh](./scripts/validate_harness_scripts.sh) | bash 脚本合规校验（bootstrap / `harness_init` / 裸 exit / 私有符号） | `bash engineering/harness/scripts/validate_harness_scripts.sh` |
| [validate_harness_config.sh](./scripts/validate_harness_config.sh) | 配置层校验（YAML 可解析性 / 字段域） | `bash engineering/harness/scripts/validate_harness_config.sh` |

> 工作流脚本入口见 [workflows/README.md](./workflows/README.md)；编译脚本见 [scripts/README.md](./scripts/README.md)。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | [docs/specs/2026-06-21-engineering-doc-refactor-design.md](../../docs/specs/2026-06-21-engineering-doc-refactor-design.md) | 本轮文档重构设计（含 harness README 结构定稿） |
| 关联规则 | [rules/source-code-modify.md](./rules/source-code-modify.md)（SRC-001~004） | 改 `~/workspace/` 源码前加载 |
| 关联规则 | [rules/script-observability.md](./rules/script-observability.md)（OBS-001~002） | 改 harness bash 脚本前加载 |
| 关联规则 | [rules/path-management.md](./rules/path-management.md)（PATH-001） | 改脚本路径引用前加载 |
| 关联 workflow | [workflows/lc-git-push-to-server/](./workflows/lc-git-push-to-server/) | 收集 diff → commit → push |
| 关联 workflow | [workflows/lc-sync-code-to-patchs/](./workflows/lc-sync-code-to-patchs/) | workspace → patchs 受控归档 |
| 关联 workflow | [workflows/lc-revert-code-from-patchs/](./workflows/lc-revert-code-from-patchs/) | patchs promoted baseline → workspace 回退 |
| 关联 workflow | [workflows/lc-sync-patchs-to-doc/](./workflows/lc-sync-patchs-to-doc/) | patchs diff → 技术文档同步 |
| 关联配置 | [config/scope-mapping.yaml](./config/scope-mapping.yaml) | commit scope 判定 |
| 关联配置 | [config/harness-paths.conf](./config/harness-paths.conf) | 工程路径单一事实源 |

---

## 控制总纲

`engineering/harness/` 是本项目的工程控制面与执行保障面，负责约束 AI、脚本与人工在源码、归档、文档、工作流、日志证据等环节的行为边界。

### 目标边界

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

### 对象模型

#### 核心对象

- **workspace**：`~/workspace/` 下的编译源码树，唯一参与编译，是日常开发的源码真相源
- **patchs**：项目历史命名沿用的归档容器，承载由 workspace 受控同步得到的 patch 资产
- **archive**：一次从 workspace 到 `patchs/` 的同步结果，表示"已归档"，不自动表示"已验证可恢复"
- **candidate baseline**：已完成部分验证、具备晋升条件但尚未完成最终确认的归档状态
- **promoted baseline**：证据完整、允许被 revert workflow 当作恢复真相源使用的 patch 基线
- **promotion**：archive / candidate baseline 晋升为 promoted baseline 的受控过程
- **docs**：项目文档体系，分为过程型文档（`docs/specs/`、`docs/plans/`）与长期技术资产文档（如 `docs/01-*`、`docs/02-*`）
- **workflow contract**：位于 `engineering/harness/workflows/*/WORKFLOW.md` 的流程契约，定义触发条件、前置条件、确认门、输出与失败恢复
- **template**：位于 `engineering/harness/templates/` 的文档结构契约，默认只读
- **artifact**：脚本执行过程中产生的计划、校验结果、日志、临时文件、报告等中间与证据产物

#### 文档分层

- **过程型文档**：`docs/specs/`、`docs/plans/`，服务当前任务的设计与实施
- **长期资产型文档**：`docs/01-*`、`docs/02-*` 等，服务长期知识沉淀与技术设计说明

#### 状态模型与证据要求

patch 资产的生命周期遵循单向晋升链：

```
archive -> candidate baseline -> promoted baseline
```

每个状态的边界：

| 状态 | 来源 | 最低证据要求 | 是否允许作为 revert 真相源 |
|------|------|-------------|-------------------------|
| archive | `lc-sync-code-to-patchs` 完成同步 | sync manifest（含 `source_branch`、`source_commit`） | ❌ 否 |
| candidate baseline | archive 基础上补充部分验证 | sync manifest + `build_result` + `package_result` | ❌ 否 |
| promoted baseline | candidate 完成全部验证并人工批准 | sync manifest + `build_result` + `package_result` + `board_verify` + `approved_by` + `approved_at` | ✅ 是 |

规则：

1. **archive ≠ 验证通过**。archive 仅表示归档动作完成，不代表可恢复。
2. **candidate baseline ≠ promoted baseline**。candidate 表示验证进行中，未获最终批准。
3. **只有 promoted baseline 可作为 revert workflow 的真相源**。revert 前必须核对证据登记项。
4. 证据字段以 `engineering/harness/config/baseline-evidence-template.yaml` 为模板，状态登记维护在 `engineering/harness/config/baseline-status.yaml`。

### 真相源矩阵

| 场景 | 真相源 | 说明 |
|------|--------|------|
| 日常源码开发 | `~/workspace/` | 唯一编译真相源 |
| patch 归档 | workspace 受控同步结果 | `patchs/` 只承接同步结果，不自行成为源码源头 |
| 文档同步 | patchs diff + manifest + workspace 上下文 | 由 workflow 与模板约束共同决定 |
| 灾难恢复 | promoted baseline | 只有完成晋升的 baseline 才能宣称为恢复真相源 |
| 脚本执行证据 | `engineering/output/log/` artifacts + log | 所有关键运行证据都必须可回溯 |

### 职责边界

#### Human

负责：

1. 给出任务目标与约束
2. 对关键确认门作出最终决定
3. 对 baseline promotion、模板变更、破坏性回退等高风险动作承担批准责任

#### AI

负责：

1. 依据准入矩阵选择正确 workflow 或规则入口
2. 进行语义理解、方案生成、文档编排、差异分析与规则解释
3. 不越过确认门，不绕过 workflow，不臆造路径、状态或证据

#### Script

负责：

1. 执行机械动作：扫描、复制、git 操作、生成计划、校验、归档、日志记录
2. 对失败显式报错，不做未经声明的猜测性 fallback
3. 通过 observability 保留运行现场与中间证据

### 规则优先级

当多个约束同时出现时，优先级如下：

1. 用户明确指令
2. 本控制总纲（[`#控制总纲`](#控制总纲)）
3. `engineering/harness/rules/*.md`
4. `engineering/harness/workflows/*/WORKFLOW.md`
5. `engineering/harness/README.md` 及各子目录 README

说明：README 负责导航与解释，不应成为高于 rules / workflow contract 的唯一权威源。

### 受控例外

以下场景属于受控例外，必须在对应 workflow 或规则中显式声明：

1. **patchs→workspace 回退**：仅在 workspace 坏状态恢复时允许，且仅以 promoted baseline 为真相源
2. **`patchs/others/` 直接维护**：因无 workspace 对应源码树，可作为独立受控区域直接维护
3. **模板冲突**：当新增内容无法纳入现有模板章节时，必须标记 `TEMPLATE-CONFLICT` 并等待人工确认
4. **upstream 缺失**：脚本必须显式失败并给出修复建议，禁止猜测 remote/base
5. **非 repo 目录**：必须由 workflow 明确定义其归档、恢复与校验语义，禁止隐式类比 repo 项目

### 术语表

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

---

## lib 公共能力速查

所有 `engineering/` 下 bash 脚本通过 `lib/shell/harness_bootstrap.sh` 统一入口加载：

```bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"   # 自动定位 REPO_ROOT + source observability
harness_init "<script-name>"
```

提供的关键能力（详见 [rules/script-observability.md](./rules/script-observability.md)）：

- **路径解析**：`harness_path <KEY>` / `harness_env_path` / `harness_pythonpath`（shell）；`path(key)` / `ensure_dir(key)`（python）
- **日志/步骤**：`log_info/warn/error`、`log_result`、`step_begin/end`
- **状态输出**：`harness_status_emit <OK|MISS|SKIP|STALE|PRUNE> <label>`
- **临时产物**：`harness_tmp_file` / `harness_tmp_dir`（自动落入 artifacts，参与轮转）
- **错误捕获**：`on_err`、模式 A/B
- **upstream 基线**：`harness_find_upstream_base`、`harness_report_no_upstream`（显式策略，禁止猜测）
- **EXIT 回调**：`harness_on_exit_add "<cmd>"`（替代手写 trap）
- **退出收尾**：`harness_exit [code]`

> **API 边界**：业务脚本只能使用不带下划线前缀的公共 API；`_H_*` / `_h_*` 为库内部私有，禁止直接依赖。

---

## README 同步约定

改动本目录下文件后，按以下清单检查 README 是否需更新：

- 新增/删除/重命名 `lib/*.sh`、`scripts/*.sh`、`workflows/*/`、`rules/*.md`、`config/*.yaml`、`config/*.json`、`templates/*` → 更新对应子目录 README.md 的文件清单
- 公共 API 变动（`lib/*.sh` 新增/删除函数对外暴露）→ 额外更新本 README 的「lib 公共能力速查」章节
- 新增/删除 `rules/*.md` → 同步更新 `rules/README.md` 文件说明表 + 本 README 快速导航表
- 新增/删除/重命名 `reference/*.md` → 更新 `reference/README.md` 文件清单 + 本 README 快速导航表
- 仅修改文件内容（文件名/结构不变）→ 无需更新 README
