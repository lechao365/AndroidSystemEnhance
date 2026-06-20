# Config

workflow 依赖的映射配置表——把"目录特征 → scope / 文档归属"的规则抽成独立数据源，新增目录或模块时只改本目录配置，不动 workflow 脚本。

## 双轨结构（Markdown 解释层 + YAML/Schema 机器层）

每份配置同时存在两种形态，**共同描述同一套规则，必须保持一致**：

| 层级 | 文件 | 形态 | 受众 | 角色 |
|------|------|------|------|------|
| 解释层 | `*-mapping.md` | Markdown 表格 | 人（开发者/AI 阅读） | 人类可读说明，含背景、判定方法、备注 |
| 机器层 | `*-mapping.yaml` | YAML 数据 | 程序/校验器 | 结构化规则，可被脚本消费 |
| 机器层 | `schema/*-mapping.schema.json` | JSON Schema (draft-07) | 校验器 | 约束 YAML 的字段类型与取值 |

**一致性约束**：两轨描述同一规则集，任何规则增删改都必须**同步更新两轨**。改 md 表格时同步改 yaml rules/routes，反之亦然。

**消费进度**（渐进机器化）：
- 当前阶段：YAML/schema 为**权威数据源**，但**不强制 workflow 脚本全面消费 YAML**（脚本可继续走原有逻辑或人工解读 md）。
- 校验器（`engineering/harness/scripts/validate_harness_config.sh`，Task 8 落地）负责 YAML ↔ schema 一致性、priority/match 冲突检测。
- 未来阶段：workflow 脚本逐步切换为直接读取 YAML，md 退化为纯解释层。

## 文件说明

### 配置文件（解释层）

| 文件 | 作用 | 被谁引用 |
|------|------|---------|
| [scope-mapping.md](./scope-mapping.md) | Git commit 的 scope 判定规则：按改动行数最多目录映射到 scope 词（如 `kernel-lcview`） | `workflows/git-push-to-server/` |
| [doc-sync-mapping.md](./doc-sync-mapping.md) | patchs → 技术文档的精准分发规则：按路径 glob 匹配分发到 `01-*` / `02-*` 文档目录 | `workflows/sync-patchs-to-doc/` |
| [task-admission-matrix.md](./task-admission-matrix.md) | 任务准入矩阵：AI 进入 harness 相关任务前的统一路由表 | `CONTROL-CHARTER.md`、AGENTS.md |
| [baseline-status.md](./baseline-status.md) | baseline 状态登记表：记录每次同步归档的归档/候选/晋升状态 | `CONTROL-CHARTER.md`、`source-code-modify.md` |
| [baseline-evidence-template.md](./baseline-evidence-template.md) | baseline 证据模板：归档晋升为 promoted baseline 前必须填写的证据字段 | `source-code-modify.md`、`revert-code-from-patchs/` |

### 配置文件（机器层）

| 文件 | 作用 | 校验 schema |
|------|------|-------------|
| [scope-mapping.yaml](./scope-mapping.yaml) | scope 判定规则的机器可读版（`version` + `rules[]`，每条含 `match`/`scope`/`priority`） | [schema/scope-mapping.schema.json](./schema/scope-mapping.schema.json) |
| [doc-sync-mapping.yaml](./doc-sync-mapping.yaml) | patchs→文档分发规则的机器可读版（`version` + `routes[]`，每条含 `match`/`docs[]`/`mode`/`priority`） | [schema/doc-sync-mapping.schema.json](./schema/doc-sync-mapping.schema.json) |

### YAML 字段速查

**scope-mapping.yaml**
- `version`: 整数，配置版本号
- `rules[].match`: glob 路径特征（相对仓库根）
- `rules[].scope`: scope 词（小写字母/数字/连字符）
- `rules[].priority`: 整数，越大越优先；首条命中即归属

**doc-sync-mapping.yaml**
- `version`: 整数，配置版本号
- `routes[].match`: glob 路径特征（相对 `patchs/rpi5/`）
- `routes[].docs`: 目标文档目录数组（`fixed` 为确定目标，`ai-diff` 为候选集合，`ai-pending` 可空）
- `routes[].mode`: `fixed` / `ai-diff` / `ai-pending`
- `routes[].priority`: 整数，越大越优先；首条命中即归属

## 何时更新

- **新增工程目录**：在 `scope-mapping.md` **和** `scope-mapping.yaml` 同步追加 scope 映射行（注意 priority 顺序）
- **新增特性文档目录**（如 `03-*`）：在 `doc-sync-mapping.md` **和** `doc-sync-mapping.yaml` 同步追加 patchs 路径特征 → 文档目录的映射
- 两份配置均采用"按 priority 降序、首条命中即归属"的匹配规则，新增条目注意优先级顺序
- **禁止只改一轨**：md 与 yaml 必须同步，否则校验器（Task 8）将报告不一致
