# LcHarness Architecture Reference

> 非约束性参考文档：说明当前 `engineering/` 如何映射到未来独立 `LcHarness` 单仓全集成架构。

## 定位

- 本文描述未来 `LcHarness` 的逻辑分层：`core / packs / profiles / adapters / control-plane`
- 本文不替代 `rules/*.md`、`WORKFLOW.md` 与 `docs/specs/2026-07-02-lcharness-framework-design.md`
- 当前仓仍以 `engineering/harness`、`engineering/loop` 为实现载体；本文只定义 Phase 1 的目标映射

## 逻辑分层

### Core

业务无关、仓库无关、相对宿主无关的稳定基础设施，包括：
- rules / policy
- workflow contract
- config schema
- observability / evidence
- validator runtime
- binding / reconcile engine（未来实现）

### Packs

可插拔能力包，分为：
- platform packs
- domain packs
- solution packs

### Profiles

面向目标业务仓的装配层，只负责选择 packs、裁剪可见能力、绑定路径与 adapter 组合。

### Adapters

连接 OpenCode、shell/python/bat 与未来业务仓 overlay 的适配层。

### Control Plane

未来 `LcHarness` 中的集中控制层，负责 repo registry、attach/inject/reconcile/detach/status。

## 能力归属判定

### 可进入 core
- 不含 loop-specific 语义
- 不依赖 AndroidSystemEnhance 单仓目录假设
- 能作为跨仓公共基础设施复用
- 对外接口稳定、可被 pack/profile 消费

### 必须下沉为 pack
- 强绑定 Android/AOSP 语义
- 强绑定 patch archive / baseline / revert 语义
- 仅服务 loop runtime 或闭环诊断流程

### 属于 profile
- 只做 repo-specific 装配
- 只决定暴露哪些 skills/workflows/runtime
- 不承载框架核心逻辑

## 当前目录到未来层次的映射

| 当前路径 | 未来层次 | 说明 |
|---------|---------|------|
| `engineering/harness/config/` | core | 机器可读配置与映射层 |
| `engineering/harness/lib/` | core | 公共路径工具、bootstrap、observability |
| `engineering/harness/rules/` | core | 约束规则与 manifest 入口 |
| `engineering/harness/scripts/` | core / control-plane support | 公共校验器与未来控制面支撑脚本 |
| `engineering/harness/templates/` | core | 文档结构契约 |
| `engineering/harness/tests/` | core | harness 公共层测试 |
| `engineering/harness/workflows/` | core seed / domain-pack split pending | 当前 workflow 容器，后续按通用与领域能力拆分 |
| `engineering/loop/` | solution pack | AI 驱动验收闭环，不进入 core |
| `.opencode/commands/le.md` | adapter projection candidate | 当前业务侧入口雏形 |
| `.opencode/commands/lc-sync-code-to-patchs.md` | adapter projection candidate | 当前 workflow 暴露雏形 |

## 未来独立仓目录蓝图

```text
lcharness/
  core/
  packs/
  profiles/
  adapters/
  control-plane/
```

## Phase 1 结论

- 当前仓先做逻辑分层显式化，不做大规模物理搬迁。
- `loop engineering` 在 Phase 1 即被视为 solution pack。
- AndroidSystemEnhance 当前只作为未来首个 profile 的承载上下文，不进入 core。
