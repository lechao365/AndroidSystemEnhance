# Config

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：workflow 依赖的映射配置层——把「目录特征 → scope / 文档归属 / baseline 状态」的规则抽成独立 YAML 数据源
- **职责边界**：做机器可读的映射数据；不做解释性文档（解释在 YAML 注释与 `description`/`note` 字段内嵌）
- **上下游依赖**：被 `workflows/lc-git-push-to-server/`（scope-mapping）、`workflows/lc-sync-patchs-to-doc/`（doc-sync-mapping）、`source-code-modify.md`（baseline-*）、`../README.md#控制总纲`（优先级链）引用

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | config 做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 配置文件清单与引用关系 | 了解结构时 |
| [使用方式](#使用方式) | 无可执行入口，仅作为配置数据承载层 | 实际使用时 |
| [字段速查](#字段速查) | YAML 字段说明 + harness-paths.conf KEY 清单 | 改配置时 |
| [任务准入矩阵](#任务准入矩阵) | 任务类型 → 允许/必读/必经/确认/evidence | 🔖 接入新任务时 |
| [何时更新](#何时更新) | 触发条件 + 操作清单 | 目录/模块变动时 |
| [关联资源](#关联资源) | 设计文档、规则、workflow 链接 | 深入理解时 |

## 目录说明

### 配置文件

| 文件 | 作用 | 被谁引用 |
|------|------|---------|
| [`scope-mapping.yaml`](./scope-mapping.yaml) | Git commit 的 scope 判定规则：按改动行数最多目录映射到 scope 词（如 `kernel-lcview`） | `../workflows/lc-git-push-to-server/` |
| [`doc-sync-mapping.yaml`](./doc-sync-mapping.yaml) | patchs → 技术文档的精准分发规则：按路径 glob 匹配分发到 `01-*` / `02-*` 文档目录 | `../workflows/lc-sync-patchs-to-doc/` |
| [`baseline-status.yaml`](./baseline-status.yaml) | baseline 状态登记表：记录每次同步归档的 archive / candidate / promoted 状态 | [`../README.md#控制总纲`](../README.md#控制总纲)、`../rules/source-code-modify.md` |
| [`baseline-evidence-template.yaml`](./baseline-evidence-template.yaml) | baseline 证据模板：晋升为 promoted baseline 前必须填写的证据字段 | `../rules/source-code-modify.md`、`../workflows/lc-revert-code-from-patchs/` |
| [`lcharness-layer-map.yaml`](./lcharness-layer-map.yaml) | `LcHarness` Phase 1 层次映射：当前目录/入口到 `core / pack / profile / adapter / control-plane` 的机器可读映射 | `Phase 1` 计划、`validate_lcharness_layer_map.sh`、`validate_harness_config.sh` |

### 其他配置

| 文件 | 作用 | 校验方式 |
|------|------|---------|
| [`harness-paths.conf`](./harness-paths.conf) | 统一路径配置（shell / python / bat 三方共用的单一事实源），定义 `harness/`、`loop/`、`output/` 等工程路径 KEY | 规则 [`../rules/path-management.md`](../rules/path-management.md)（PATH-001） |

## 使用方式

本目录无可执行入口，仅作为配置数据承载层。新增目录或模块时只改本目录 YAML，不动 workflow 脚本；校验通过 `validate_harness_config.sh`。

## 字段速查

### scope-mapping.yaml

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | 整数 | 配置版本号 |
| `rules[].match` | glob | 路径特征（相对仓库根） |
| `rules[].scope` | 字符串 | scope 词（小写字母/数字/连字符） |
| `rules[].priority` | 整数 | 越大越优先；首条命中即归属 |
| `rules[].description` | 字符串 | 人类可读的模块/场景说明 |

### doc-sync-mapping.yaml

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | 整数 | 配置版本号 |
| `routes[].match` | glob | 路径特征（相对 `patchs/rpi5/`） |
| `routes[].docs` | 数组 | 目标文档目录（`fixed` 为确定目标，`ai-diff` 为候选集合，`ai-pending` 可空） |
| `routes[].mode` | 枚举 | `fixed` / `ai-diff` / `ai-pending` |
| `routes[].priority` | 整数 | 越大越优先；首条命中即归属 |
| `routes[].note` | 字符串 | AI 读 diff 时的分发判断指导（仅 `ai-diff` 模式） |

### harness-paths.conf KEY 速查

| KEY | 说明 |
|-----|------|
| `ENGINEERING_DIR` | engineering/ 根目录 |
| `HARNESS_DIR` | harness/ 目录 |
| `LOOP_DIR` | loop/ 目录 |
| `LOOP_SCRIPTS_DIR` | loop/scripts/ |
| `LOOP_WORKFLOWS_DIR` | loop/workflows/ |
| `LOOP_CASES_DIR` | loop/cases/ |
| `SHELL_LIB_DIR` / `PYTHON_LIB_DIR` / `BAT_LIB_DIR` | 三方公共库目录 |
| `OUTPUT_DIR` / `LOG_DIR` / `HOST_LOG_DIR` / `RUNS_DIR` | 产物与日志目录 |
| `PATCHS_DIR` | 归档目录 |
| `PYTHON_PATH_ROOTS` | PYTHONPATH 冒号分隔包根 |
| `ENV_KERNEL_WS` / `ENV_AOSP_WS` / `ENV_KERNEL_OUT` / `ENV_CLANG_BIN` / `ENV_WINDOWS_IMG_DIR` | 环境可覆盖的源码/构建路径 |
| `TEST_SANDBOX_DIR` | 测试沙箱 |

> 路径 KEY 详细语义与脚本引用约束见 [`../rules/path-management.md`](../rules/path-management.md)（PATH-001）。

### lcharness-layer-map.yaml

| 字段 | 类型 | 说明 |
|------|------|------|
| `version` | 整数 | 配置版本号 |
| `entries[].path` | 字符串 | 当前仓中的目录/文件/虚拟对象标识 |
| `entries[].kind` | 枚举 | `directory` / `file` / `virtual` |
| `entries[].layer` | 枚举 | `core` / `pack` / `profile` / `adapter` / `control-plane` |
| `entries[].component` | 字符串 | 稳定组件名 |
| `entries[].target` | 字符串 | 未来 `LcHarness` 中的目标位置 |
| `entries[].rationale` | 字符串 | 为什么归属该层 |
| `entries[].pack_type` | 枚举，可选 | 当 `layer=pack` 时使用：`platform` / `domain` / `solution` |

## 任务准入矩阵

> **用途**：为 AI 与人工在进入 `engineering/harness/` 相关任务前提供统一路由表，回答"当前任务是否允许直接改、必须先读哪些规则、是否必须经 workflow、是否需要计划/确认/evidence"。

| 任务类型 | 允许直接修改 | 必读规则 | 必经 workflow | 是否先出 plan | 是否需用户确认 | 是否需 evidence |
|----------|--------------|----------|---------------|---------------|----------------|-----------------|
| `~/workspace/` 源码修改 | 否 | `rules/source-code-modify.md` | 视任务而定 | 否 | 视任务而定 | 是 |
| `patchs/` 归档（workspace → patchs） | 否 | `rules/source-code-modify.md` | `workflows/lc-sync-code-to-patchs/` | 否 | README/附加说明按 workflow 约束 | 是 |
| `patchs/` 回退（patchs → workspace） | 否 | `rules/source-code-modify.md` | `workflows/lc-revert-code-from-patchs/` | 是 | 是 | 是 |
| patchs → 技术文档同步 | 否 | `rules/doc-paths.md`、`rules/plantuml.md` | `workflows/lc-sync-patchs-to-doc/` | 是 | 是 | 是 |
| commit / push | 否 | workflow 契约 + commit scope 配置 | `workflows/lc-git-push-to-server/` | 否 | 是 | 是 |
| harness bash 脚本改造 | 是 | `rules/script-observability.md` | 视脚本而定 | 否 | 否 | 是 |
| harness 规则文档改造 | 是 | [`../README.md#控制总纲`](../README.md#控制总纲) + 对应 `rules/*.md` | 无 | 视范围而定 | 视风险而定 | 建议保留 |
| harness 模板改造 | 是 | `rules/plantuml.md` + `templates/README.md` | 无 | 建议先出方案 | 是 | 建议保留 |
| harness 配置映射改造 | 是 | [`../README.md#控制总纲`](../README.md#控制总纲) + `config/README.md` | 无 | 视范围而定 | 视风险而定 | 建议保留 |
| validator / 测试夹具改造 | 是 | `rules/script-observability.md`（脚本类）+ 本矩阵 | 无 | 否 | 否 | 是 |

使用规则：

1. 无法命中矩阵的任务，不得直接执行，应先补充任务分类或更新本矩阵。
2. 若任务同时命中多个类别，优先选择副作用更强、确认门更多的那一类。
3. "允许直接修改"仅表示可直接编辑相关受控文件，不代表可以绕过验证与 evidence 要求。
4. 任何涉及 `patchs` 真相源语义切换、模板结构变更、批量回退的任务，都应提高到"需要确认"的处理级别。

## 何时更新

- **新增工程目录**：在 `scope-mapping.yaml` 追加 scope 映射行（注意 priority 顺序），同步更新 `description`
- **新增特性文档目录**（如 `03-*`）：在 `doc-sync-mapping.yaml` 追加 patchs 路径特征 → 文档目录的映射
- **新增 loop / harness 目录入口**：若脚本需要新的工程路径 KEY（如 `LOOP_SCRIPTS_DIR`、`LOOP_WORKFLOWS_DIR`），必须先更新 `harness-paths.conf`，再修改脚本引用
- **baseline 状态变更**（promoted baseline 晋升）：在 `baseline-status.yaml` 新增/更新登记行，核对 `baseline-evidence-template.yaml` 要求的字段已填齐
- 两份映射配置均采用"按 priority 降序、首条命中即归属"的匹配规则，新增条目注意优先级顺序

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | [`docs/specs/2026-06-21-engineering-doc-refactor-design.md`](../../../docs/specs/2026-06-21-engineering-doc-refactor-design.md) | 文档重构设计 |
| 关联规则 | [`../rules/path-management.md`](../rules/path-management.md)（PATH-001） | harness-paths.conf 校验 |
| 关联规则 | [`../rules/source-code-modify.md`](../rules/source-code-modify.md) | baseline 证据 |
| 关联 workflow | [`../workflows/lc-git-push-to-server/`](../workflows/lc-git-push-to-server/) | scope-mapping 消费 |
| 关联 workflow | [`../workflows/lc-sync-patchs-to-doc/`](../workflows/lc-sync-patchs-to-doc/) | doc-sync-mapping 消费 |
| 关联 workflow | [`../workflows/lc-revert-code-from-patchs/`](../workflows/lc-revert-code-from-patchs/) | baseline-evidence 消费 |
