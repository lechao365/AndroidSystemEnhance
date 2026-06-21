# Engineering

> **AI 读取指引**：本 README 采用三层结构。先读「大纲」判断需要哪些章节，
> 再按需精读对应章节，避免全量解析。带 🔖 的章节为高频引用，优先阅读。

## 定位

- **是什么**：工程能力总目录，承载公共工程基础设施（harness）与 loop engineering 专属能力（loop），不承载业务源码
- **职责边界**：做工程控制 / 约束 / 工具链 / 验收闭环；不做业务功能实现（业务源码在 `~/workspace/`）
- **上下游依赖**：被 `AGENTS.md` 引用为工程入口；harness 与 loop 单向依赖（见「边界与依赖」）

## 大纲

| 章节 | 内容摘要 | 何时读取 |
|------|---------|---------|
| [定位](#定位) | engineering 做什么、不做什么 | 首次进入 |
| [目录说明](#目录说明) | 一级子目录清单与职责 | 了解结构时 |
| [使用方式](#使用方式) | 本目录为索引层，无单独入口 | 实际使用时 |
| [边界与依赖](#边界与依赖) 🔖 | harness↔loop 单向依赖、能力归属判定、workflow 归属、README 同步规则 | 判断目录归属、依赖方向时 |
| [关联资源](#关联资源) | 设计文档、规则、配置链接 | 深入理解时 |

## 目录说明

| 子目录/文件 | 职责 | 关键入口/被谁引用 |
|------------|------|------------------|
| [`harness/`](./harness/) | 公共工程能力层：规则 / 模板 / 路径管理 / 日志观测 / 跨工程可复用脚本与 workflow | [`harness/README.md`](./harness/README.md) |
| [`loop/`](./loop/) | loop engineering 专属能力层：cases / connection / core / scripts / controller / workflows / contracts | [`loop/README.md`](./loop/README.md) |
| [`output/`](./output/) | 本地日志与运行产物目录，不承载实现逻辑 | [`output/README.md`](./output/README.md) |

> 子目录自身的细节见其 `README.md`，本表只给一句话索引。

## 使用方式

本目录无可执行入口，仅作为工程能力总索引。按需进入 `harness/` 或 `loop/` 子目录，各自 README 提供入口与快速开始。

## 关联资源

| 类型 | 路径 | 说明 |
|------|------|------|
| 设计文档 | [`docs/specs/2026-06-21-engineering-doc-refactor-design.md`](../docs/specs/2026-06-21-engineering-doc-refactor-design.md) | engineering 文档重构设计 |
| 关联规则 | [`harness/rules/source-code-modify.md`](./harness/rules/source-code-modify.md) | 改 `~/workspace/` 源码前加载 |
| 关联规则 | [`harness/rules/path-management.md`](./harness/rules/path-management.md) | 脚本路径引用约束（PATH-001） |
| 关联配置 | [`harness/config/harness-paths.conf`](./harness/config/harness-paths.conf) | 工程路径 KEY 单一事实源 |

---

## 边界与依赖

> 🔖 **本节是 harness↔loop 边界的单一事实源。**
> `harness/README.md` 与 `loop/README.md` 的边界说明均链接回本节。

### 单向依赖

- **允许**：`engineering/loop/` 依赖 `engineering/harness/`
- **禁止**：`engineering/harness/` 依赖 `engineering/loop/`

### 能力归属判定

**必须放在 `engineering/loop/`：**

- 包含 loop-specific 语义
- 直接服务 case / suite / connection / transport / session / attempt / rerun / LE runs 生命周期
- 当前仅被 loop 使用
- 抽到 harness 会形成过早公共化

**允许放在 `engineering/harness/`：**

- 不含 loop-specific 语义
- 是跨工程基础设施
- 有稳定公共接口
- 不形成 `harness → loop` 反向依赖

### workflow 归属

- 通用工程 workflow → `engineering/harness/workflows/`
- loop 专属 workflow → `engineering/loop/workflows/`

### README 同步规则

目录边界、一级目录、核心入口发生变化时，必须同步检查：

- `engineering/README.md`（本文件）
- `engineering/harness/README.md`
- `engineering/loop/README.md`
