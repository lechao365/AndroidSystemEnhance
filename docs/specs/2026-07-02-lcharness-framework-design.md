# LcHarness 本地注入式通用 Harness 框架设计

> **状态**：已批准的总体设计基线
> **范围**：定义独立 `LcHarness` 仓的目标架构、边界、注入模型与演进策略；不直接展开各阶段实现细节。
> **当前结论**：`LcHarness` 采用“单仓全集成 + 中央控制面 + 业务仓零 tracked 污染 + 链接/映射优先 + stale/reconcile”路线。

## 1. 背景与问题定义

当前仓库的 `engineering/` 已经具备较强的工程能力分层意识：`engineering/README.md:8` 将其定义为工程能力总目录，承载公共工程基础设施与 loop engineering 专属能力；`engineering/README.md:54` 明确允许 `engineering/loop/` 依赖 `engineering/harness/`，同时 `engineering/README.md:55` 禁止 `engineering/harness/` 反向依赖 `engineering/loop/`。这说明现有结构已经具备向独立框架演化的基础边界。

与此同时，当前 `engineering/harness/README.md:8` 将 harness 定位为工程控制面与执行保障面，强调规则、工作流、模板、配置与日志证据；`engineering/harness/README.md:9` 明确不承载 loop-specific 语义。`engineering/loop/README.md:8` 与 `engineering/loop/README.md:9` 则把 loop 定位为 AI 驱动的设备验收闭环与专属能力层。该分层已经接近“框架内核 + 高层能力包”的形态，但它仍是当前业务仓的一部分，尚不是一个独立、可多仓复用、且对业务仓零污染的通用框架。

本设计要解决的问题不是继续细化 `engineering/harness/reference/harness-optimization-blueprint.md` 中的项目内优化清单，而是把当前 `engineering/` 演进为一个独立的、由个人控制和使用的通用框架 `LcHarness`。该框架应只在 `LcHarness` 仓中维护全部能力，并通过本地注入的方式为多个业务仓提供工程增强能力，同时不向业务仓引入任何 tracked 文件、公开入口或团队级依赖。

## 2. 设计目标

### 2.1 目标

1. **独立仓**：`LcHarness` 成为唯一维护 `core + packs + profiles + adapters` 的仓库。
2. **业务仓零污染**：目标业务仓不提交任何与 `LcHarness` 相关的 tracked 文件。
3. **严格单向依赖**：只允许 `LcHarness -> 业务仓` 注入；业务仓不得成为控制面或真相源。
4. **能力最小投影**：业务仓只获得白名单能力，不暴露 `LcHarness` 自管理能力。
5. **单一事实真相**：框架能力主体只在 `LcHarness` 中存在，业务仓 overlay 只是投影视图。
6. **多业务仓复用**：同一套 `LcHarness` 可同时绑定多个业务仓，并为每个业务仓投影不同 profile 与 pack 组合。
7. **集中控制面**：attach / inject / reconcile / validate / detach / status 统一在 `LcHarness` 发起。
8. **可演进升级**：接受业务仓投影视图短暂 stale，通过 reconcile 恢复与 `LcHarness` 当前版本一致。
9. **最小干扰项**：不让业务仓暴露多余命令、框架内部开发能力或跨仓信息。

### 2.2 非目标

1. 当前阶段**不是团队共享平台**，不要求团队成员安装或使用 `LcHarness`。
2. 当前阶段**不是业务仓官方依赖**，业务仓 CI 与 tracked 结构不应默认依赖 `LcHarness`。
3. 当前阶段**不是多仓拆分平台**，不将 core、packs、profiles、adapters 拆成多个物理仓。
4. 当前阶段**不是零维护自动同步系统**，允许 stale 并通过显式 reconcile 处理结构变化。
5. 当前阶段**不是全量能力暴露系统**，业务仓仅应看到被授权的最小能力集合。
6. 当前阶段**不是把 loop 拉平为 core**，loop 继续被视为高层 solution pack。

## 3. 核心约束

本设计受以下硬约束约束：

1. `LcHarness` 是你个人独占使用的独立能力仓，不与业务团队共用。
2. 业务仓必须零 tracked 污染，不允许提交任何锚点文件、bootstrap 文件或 profile 声明。
3. 所有注入、检查、升级、解注入只能由 `LcHarness` 控制面发起。
4. repo binding state 的真相源只存放在 `LcHarness` 本地，而不存放在业务仓。
5. 业务仓只承载本地隐藏 overlay 与最小运行态，不承载控制面入口。
6. overlay 采用“链接/映射优先”模型，而不是全量复制模型。
7. 接受 stale/reconcile：`LcHarness` 升级或 profile/pack 改变后，业务仓 overlay 可以先进入 stale 状态，再由 reconcile 恢复一致。
8. `loop engineering` 定位为 solution pack，而不是 `LcHarness` core。
9. 现有项目边界 `engineering/README.md:54-76` 仍然成立：公共基础设施与 loop 专属能力必须保持单向依赖与清晰归属。

## 4. 概念模型

### 4.1 Core

`Core` 指 `LcHarness` 中业务无关、仓库无关、相对宿主平台无关的稳定基础设施能力，负责提供规则系统、工作流契约、配置 Schema、observability/evidence、validator runtime、binding/reconcile engine 与 trust/permission 基础模型。Core 不应包含 Android/AOSP 特有语义、loop-specific 生命周期语义，且不得依赖任何业务 profile。

### 4.2 Pack

`Pack` 指在 core 之上按能力域可插拔装配的能力包。Pack 可以是：

- **Platform Pack**：通用工程增强，例如 docs 治理、git/workflow 治理、validator 增强。
- **Domain Pack**：某类技术域常见能力，例如 Android/AOSP 相关构建、归档、回退、基线治理。
- **Solution Pack**：高层组合能力，例如 loop engineering。

Pack 只能消费 core 暴露的公共接口，不允许通过隐式方式要求 core 理解自己的业务语义。

### 4.3 Profile

`Profile` 指面向某个目标业务仓的装配声明。Profile 负责选择 packs、裁剪能力可见性、绑定路径模型与 adapter 组合，但不承载复杂核心逻辑。Profile 的职责是“组装”，不是“再造一个框架”。

### 4.4 Adapter

`Adapter` 指宿主环境与投影执行层的适配组件，例如 OpenCode adapter、shell/python/bat adapter、overlay projection adapter、repo binding adapter。Adapter 负责把 core / pack / profile 的逻辑接到具体运行环境，不改变能力本体的语义。

### 4.5 Overlay

`Overlay` 指注入到业务仓中的本地隐藏目录，是 `LcHarness` 对该业务仓的最小能力投影视图。Overlay 不是事实源，只是运行态视图与局部状态容器。

### 4.6 Repo Registry

`Repo Registry` 指 `LcHarness` 本地保存的目标业务仓注册表，记录目标 repo 路径、profile 绑定、已启用 packs、overlay 位置、状态、最近 reconcile 与健康检查信息。它是多 repo 管理与集中控制面的关键状态真相源。

## 5. 总体架构

### 5.1 控制面与业务面分离

`LcHarness` 是唯一控制面，业务仓是被投影目标。控制面与业务面必须严格分离：

- **控制面动作**：attach、inject、reconcile、validate、detach、status、health、profile/pack 解析。
- **业务面内容**：本地隐藏 overlay、repo-local 最小状态、被授权能力的投影视图。

业务仓不得拥有控制面入口，也不得直接执行框架治理动作。这样才能同时满足单向依赖、零污染与最小干扰项三个目标。

### 5.2 单仓全集成架构

`LcHarness` 当前阶段采用单仓全集成路线。所有 core、packs、profiles、adapters 都放在一个独立仓中统一维护，以减少版本协调与治理复杂度。但逻辑上必须显式分层，以防止单仓演化为无边界的大单体。

推荐的逻辑分层如下：

1. **core/**：稳定基础设施层
2. **packs/**：可插拔能力包层
3. **profiles/**：目标业务仓装配层
4. **adapters/**：宿主与注入适配层
5. **control-plane/**：repo registry、状态机、控制命令与健康检查

### 5.3 当前工程到未来架构的映射原则

当前 `engineering/harness/` 目录中的很多能力已经符合 core 候选特征，例如规则系统、路径工具、observability、workflow contract 与 validator 入口；而 `engineering/loop/` 则根据 `engineering/loop/README.md:8-10` 与 `engineering/README.md:59-71` 的边界说明，更适合被重新表述为 solution pack。当前 `.opencode/commands/le.md:5` 与 `.opencode/commands/lc-sync-code-to-patchs.md:5-8` 这类入口则说明业务侧命令仍直接绑在仓内目录上，未来需要改造成由 `LcHarness` 控制面决定哪些能力被投影到目标 repo。

## 6. Core / Pack / Profile / Adapter 分层设计

### 6.1 Core

Core 负责以下通用能力：

- rule / policy engine
- workflow contract engine
- config schema 与静态校验基座
- observability / evidence 模型
- trust / permission 基础语义
- binding & reconcile engine
- repo registry 管理
- adapter 接口与 pack/profile 装配接口

Core 不得直接包含：

- loop-specific 生命周期
- Android/AOSP 强绑定目录语义
- 业务仓专有 path 规则
- 任何只属于单一 repo 的 profile 逻辑

### 6.2 Packs

Packs 是能力复用与隔离的基本单元。

- **Platform Pack** 适合承载通用工程实践，例如 docs 管理、规则扩展、workflow 通用治理。
- **Domain Pack** 适合承载 Android/AOSP 类特定领域能力，例如 patch 归档、baseline 晋升、revert workflow。
- **Solution Pack** 适合承载更高层的闭环系统，例如 loop engineering。

`loop engineering` 被定义为 solution pack 的原因在于：它有强烈的 run/session/attempt/patch/verify 闭环语义，而这些语义已经在当前项目的边界约束中被明确归属于 loop 专属能力，而非 harness core（`engineering/README.md:59-76`、`engineering/loop/README.md:8-10`）。

### 6.3 Profiles

Profile 只做目标 repo 的装配与投影策略声明，典型职责包括：

- 选择要启用的 packs
- 限定暴露的 skills / workflows / runtime 能力
- 绑定该 repo 的路径模型与 adapter 组合
- 约束业务仓不可见的框架内部能力

Profile 不应保存框架控制面真相源，不应成为控制面入口，也不应承载与其他 repo 共享的核心逻辑。

### 6.4 Adapters

Adapters 负责把框架逻辑接到具体宿主上。当前阶段至少包括：

- **OpenCode adapter**：承接技能、命令、规则上下文注入模型
- **shell/python/bat adapters**：承接现有多语言工具链
- **overlay projection adapter**：把 profile 解析结果映射为业务仓本地 overlay 视图
- **repo binding adapter**：面向目标 repo 的 attach/detach 与健康检查

## 7. 注入与投影模型

### 7.1 注入原则

本设计采用“链接/映射优先”的注入模型。注入到业务仓的不是整套 `LcHarness`，而是某个 profile 解析结果的最小运行时投影视图。这样可以同时满足：

- `LcHarness` 作为单一事实源
- 业务仓零 tracked 污染
- 解注入可彻底清理
- 多业务仓复用时不产生复制漂移

### 7.2 能力白名单投影

业务仓只获得自己被授权的能力集合。对于每个目标 repo，控制面必须能决定：

- 哪些 skills 可见
- 哪些 workflows 可见
- 哪些 runtime 能力可见
- 哪些 pack 内容必须隐藏
- 哪些控制面能力绝不允许出现在业务仓侧

这意味着业务仓看到的是一个 **capability-scoped projection view**，而不是 `LcHarness` 的镜像。

### 7.3 Overlay 目录边界

每个业务仓只应存在一个本地隐藏 overlay 根目录。该目录必须满足：

1. 仅本地存在，不被 git 跟踪
2. 不向业务仓 tracked 文件散落内容
3. 可整体创建、整体校验、整体删除
4. 可表达健康 / stale / broken 等局部状态
5. 除最小 repo-local 状态外，尽量通过链接/映射引用 `LcHarness`

本设计先抽象 overlay 目录模型，而不在总体设计阶段绑定最终目录名。

## 8. Repo Registry 与 Binding State 模型

由于业务仓必须零 tracked 污染，repo binding state 不能存放在业务仓中，而必须由 `LcHarness` 统一管理。Repo Registry 至少需要记录：

- repo 标识与本地绝对路径
- 绑定的 profile
- 已启用的 pack 解析结果
- overlay 根目录位置
- 当前状态（attached / healthy / stale / broken 等）
- 最近 reconcile 信息
- 最近 validate / health check 结果

采用中央 registry 的好处在于：

1. 同一套 `LcHarness` 可以统一管理多个 repo
2. profile 和 pack 的升级影响可以被集中识别
3. detach / cleanup 不依赖业务仓内的真相源文件
4. 业务仓不会泄露其他 repo 的 profile 或 pack 信息

## 9. 状态模型与生命周期

### 9.1 状态集合

每个绑定 repo 至少应支持以下状态：

- **detached**：未绑定到 `LcHarness`
- **attached**：已注册到 repo registry，但未完成 overlay 投影
- **injected**：overlay 已建立，但尚未完成完整健康验证
- **healthy**：overlay 与当前 `LcHarness + profile + packs` 保持一致
- **stale**：`LcHarness` 升级或装配变化后，业务仓投影视图落后
- **broken**：链接损坏、路径失效、关键依赖缺失或投影视图不完整
- **detached-clean**：已完成解注入且无残留

### 9.2 状态迁移

核心状态迁移如下：

```text
attach -> injected -> healthy
healthy -> stale
stale -> reconcile -> healthy
any attached state -> broken
attached/injected/healthy/stale/broken -> detach -> detached-clean
```

该模型把 stale/reconcile 视为正常生命周期的一部分，而不是异常兜底逻辑。

## 10. 真相源模型

### 10.1 `LcHarness` 是唯一事实源

以下内容只能在 `LcHarness` 中维护：

- core、packs、profiles、adapters 的定义
- repo registry 与 binding state
- 注入策略与 reconcile 规则
- 允许暴露给业务仓的能力白名单
- 控制面命令与错误语义

### 10.2 业务仓 overlay 不是事实源

业务仓 overlay 只是：

- 能力投影视图
- 局部运行时容器
- 可重建的本地状态副本

因此不得在业务仓 overlay 中手工维护业务定制并反向影响 `LcHarness`。如果出现需要长期保留的差异，应回到 `LcHarness` 的 profile 或 pack 中定义。

## 11. 当前 `engineering/` 向 LcHarness 的映射

### 11.1 现有结构优势

当前 `engineering/` 已具备几个适合演进为 `LcHarness` 的优势：

1. `engineering/README.md:54-55` 已有单向依赖硬边界。
2. `engineering/harness/README.md:29-36` 已对 config/lib/rules/scripts/templates/reference/workflows/tests 做了清晰分类。
3. `engineering/harness/rules/manifest.yaml:3-109` 已经体现出 context + rules + access 的声明式模型。
4. `.opencode/commands/le.md:5` 与 `.opencode/commands/lc-sync-code-to-patchs.md:5-8` 已有工作流/命令注入入口雏形。

### 11.2 现有结构不足

现有结构仍存在以下不足：

1. 核心能力、领域能力、业务 profile 仍混在项目内目录语义中。
2. 业务仓入口仍直接指向仓内 `engineering/` 路径，尚非集中控制面模式。
3. `loop` 仍以当前项目内专属目录形式存在，尚未被抽象为 solution pack。
4. repo binding、stale/reconcile、overlay projection 等关键概念尚未在架构中显式化。

### 11.3 映射原则

Phase 1 不追求立即把全部实现移动到最终目录，而是优先完成：

- 逻辑层次显式化
- 机器可读的 layer map
- README 与边界契约重构
- loop/AndroidSystemEnhance 从“项目内特殊目录”升级为 pack/profile 概念

## 12. 演进策略

本设计采用四阶段演进策略：

### Phase 1：当前 `engineering/` 向 LcHarness 架构重构

目标是在当前仓内显式建立 core / packs / profiles / adapters 逻辑层次，沉淀机器可读 layer map，并把 `loop` 明确为 solution pack。

### Phase 2：LcHarness 中央控制面与 Repo Registry

目标是建立 attach / inject / reconcile / validate / detach / status 的最小闭环，以及统一的 repo registry 与状态机。

### Phase 3：Overlay Projection 与能力白名单注入

目标是把 profile 解析结果投影为业务仓本地 overlay，落地零污染、链接优先和能力白名单可见性。

### Phase 4：AndroidSystemEnhance 首个 Profile 与多 Repo 扩展

目标是使用当前项目作为第一个 target repo 验证单租户效果，并为未来多 repo、多 profile 扩展提供稳定基础。

## 13. 风险与开放问题

### 13.1 路径与链接稳定性

链接/映射优先模型依赖稳定路径与宿主环境兼容性。后续实现需特别评估 Windows / WSL2 / Linux 混合环境下的链接行为与权限边界。

### 13.2 stale 判定粒度

如果 stale 判定过粗，会导致频繁 reconcile；如果过细，则会增加 registry 与健康检查复杂度。需要在 Phase 2 中明确“结构性变化”和“内容性变化”的判定策略。

### 13.3 Profile 粒度控制

Profile 过粗会导致 pack 隔离不彻底；Profile 过细则会提升维护成本。需要在 AndroidSystemEnhance 首个 profile 落地时找到合适粒度。

### 13.4 Pack 边界稳定性

当前项目中的很多能力同时带有“通用工程治理”和“AndroidSystemEnhance 语义”两类特征。Phase 1 需要先明确边界，不宜在第一轮就做大规模物理移动。

### 13.5 控制面与业务面误耦合风险

如果后续为图省事而在业务仓侧暴露控制入口、写入 tracked 锚点文件、或把 repo binding state 放回业务仓，本设计将失去其最核心的竞争力：零污染、单向依赖与集中控制。

## 14. 决策摘要

本设计已确认以下关键决策：

1. `LcHarness` 采用**单仓全集成**起步，而非多仓拆分。
2. 业务仓采用**零 tracked 污染**模式，不保留任何已提交锚点文件。
3. **所有控制面动作只在 `LcHarness` 仓发起**。
4. 业务仓 overlay 采用**链接/映射优先**模型。
5. 允许业务仓进入 **stale** 状态，并通过 **reconcile** 恢复一致。
6. repo binding state 统一保存在 **`LcHarness` 本地 repo registry**。
7. `loop engineering` 被定位为 **solution pack**，而不是 core。
8. AndroidSystemEnhance 将作为首个 profile 和首个 target repo 验证整套模式。

---

本文件是 `LcHarness` 总体设计基线。后续实施计划应以本设计为上位约束，先落实 Phase 1 的分层重构与边界显式化，再进入控制面、overlay 与首个 profile 的编码实现。
