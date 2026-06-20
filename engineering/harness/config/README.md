# Config

workflow 依赖的映射配置表——把"目录特征 → scope / 文档归属"的规则抽成独立数据源，新增目录或模块时只改本目录配置，不动 workflow 脚本。

## 设计说明

所有配置以 **YAML 为单一数据源**，解释说明通过 YAML 注释和 `description`/`note` 字段内嵌。不存在双轨同步问题。

## 文件说明

### 配置文件

| 文件 | 作用 | 被谁引用 |
|------|------|---------|
| [scope-mapping.yaml](./scope-mapping.yaml) | Git commit 的 scope 判定规则：按改动行数最多目录映射到 scope 词（如 `kernel-lcview`） | `workflows/git-push-to-server/` |
| [doc-sync-mapping.yaml](./doc-sync-mapping.yaml) | patchs → 技术文档的精准分发规则：按路径 glob 匹配分发到 `01-*` / `02-*` 文档目录 | `workflows/sync-patchs-to-doc/` |
| [baseline-status.yaml](./baseline-status.yaml) | baseline 状态登记表：记录每次同步归档的归档/候选/晋升状态 | `CONTROL-CHARTER.md`、`source-code-modify.md` |
| [baseline-evidence-template.yaml](./baseline-evidence-template.yaml) | baseline 证据模板：归档晋升为 promoted baseline 前必须填写的证据字段 | `source-code-modify.md`、`revert-code-from-patchs/` |

### 其他配置

| 文件 | 作用 | 校验方式 |
|------|------|---------|
| [paths.conf](./paths.conf) | 统一路径配置（shell / python / bat 三方共用的单一事实源），定义工程内所有路径 KEY | 规则 [rules/path-management.md](../rules/path-management.md) (PATH-001) |

### YAML 字段速查

**scope-mapping.yaml**
- `version`: 整数，配置版本号
- `rules[].match`: glob 路径特征（相对仓库根）
- `rules[].scope`: scope 词（小写字母/数字/连字符）
- `rules[].priority`: 整数，越大越优先；首条命中即归属
- `rules[].description`: 字符串，人类可读的模块/场景说明

**doc-sync-mapping.yaml**
- `version`: 整数，配置版本号
- `routes[].match`: glob 路径特征（相对 `patchs/rpi5/`）
- `routes[].docs`: 目标文档目录数组（`fixed` 为确定目标，`ai-diff` 为候选集合，`ai-pending` 可空）
- `routes[].mode`: `fixed` / `ai-diff` / `ai-pending`
- `routes[].priority`: 整数，越大越优先；首条命中即归属
- `routes[].note`: 字符串，AI 读 diff 时的分发判断指导（仅 `ai-diff` 模式）

## 任务准入矩阵

> **用途**：为 AI 与人工在进入 `engineering/harness/` 相关任务前提供统一路由表，回答"当前任务是否允许直接改、必须先读哪些规则、是否必须经 workflow、是否需要计划/确认/evidence"。

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

使用规则：

1. 无法命中矩阵的任务，不得直接执行，应先补充任务分类或更新本矩阵。
2. 若任务同时命中多个类别，优先选择副作用更强、确认门更多的那一类。
3. "允许直接修改"仅表示可直接编辑相关受控文件，不代表可以绕过验证与 evidence 要求。
4. 任何涉及 `patchs` 真相源语义切换、模板结构变更、批量回退的任务，都应提高到"需要确认"的处理级别。

## 何时更新

- **新增工程目录**：在 `scope-mapping.yaml` 追加 scope 映射行（注意 priority 顺序），同步更新 `description`
- **新增特性文档目录**（如 `03-*`）：在 `doc-sync-mapping.yaml` 追加 patchs 路径特征 → 文档目录的映射
- 两份配置均采用"按 priority 降序、首条命中即归属"的匹配规则，新增条目注意优先级顺序
