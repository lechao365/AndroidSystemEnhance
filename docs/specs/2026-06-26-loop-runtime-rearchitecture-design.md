# Loop Runtime 重构设计

> **状态**：待用户审阅
> **日期**：2026-06-26
> **目标**：以零三方依赖、自研轻量状态图 runtime 的方式，彻底替换当前 loop 的旧编排架构；迁移期允许旧架构短暂存在，仅用于对照验证与风险兜底，最终统一删除。
> **范围**：`engineering/loop/`、`docs/specs/`、`docs/plans/`
> **相关现状**：`engineering/loop/WORKFLOW.md`、`engineering/loop/controller/`、`engineering/loop/core/python/loop_core/`、`engineering/loop/deploy/`、`engineering/loop/connection/`

---

## 1. 背景与目标

当前 loop 已具备较强的能力层基础：
- `connection/` 已完成 RPi5 串口接入、serial→adb 依赖链与 host/client 拓扑。
- `cases/` 已沉淀 system / feature / common 三类 YAML 场景资产。
- `loop_core/` 已具备 suite 加载、断言、collector、evidence 输出等单轮验证能力。
- `deploy/` 已具备 compile / deploy / rollback / image_verify / decider 能力。

但当前主问题不在“能力缺失”，而在“编排架构过旧”：
1. 流程知识主要存放在 `WORKFLOW.md` 与离散 CLI 中，仍偏文本 SOP 驱动。
2. AI 需要记忆并手工串联 `init -> run-verify -> decide -> analyze-request -> apply-patch -> compile -> deploy -> rerun`，流程知识没有内化为 runtime。
3. 旧 `controller`/`workflow` 编排层与新诉求冲突：用户目标不是长期兼容，而是**彻底推翻旧架构**。

因此本设计的核心目标是：

### 1.1 目标

1. **最终态只有一套新架构**：自研零依赖轻量状态图 runtime，成为 loop 唯一正式编排层。
2. **旧架构只作为迁移脚手架**：存在于迁移期，用于对照验证与故障兜底；切换完成后必须删除。
3. **最大化复用能力层**：`connection`、`cases`、`loop_core`、`deploy`、`contracts` 保留并增强，避免重复造轮子。
4. **最小化切换风险**：先提纯可复用层，待全部验证 PASS 后，再一把切换到新 runtime。
5. **尽量全自动**：正常闭环下自动执行，只有以下两类终态：
   - 全 PASS：自动成功结束。
   - 达到人工门槛：自动退人工。

### 1.2 非目标

1. 不引入 LangGraph、Temporal、OpenHands、SWE-agent 等三方框架或 SDK。
2. 不长期保留旧 `le control` 主闭环模式。
3. 第一阶段不升级现有 YAML case schema，只做**兼容现有 schema 的 runtime 适配**。
4. 不在当前阶段引入多 agent 编排；保留 roadmap 可能性，但本期不落地。

---

## 2. 设计原则

### 2.1 借鉴业界思想，但完全自实现

本方案借鉴以下成熟实践的“思想”，不引入其实现：
- **Anthropic agent patterns**：优先简单、可组合、可验证工作流，而不是过早引入复杂框架。
- **LangGraph**：状态显式化、checkpoint、interrupt/resume、human-in-the-loop。
- **OpenHands**：控制面与能力执行面分层。
- **SWE-agent**：以测试/验证反馈驱动循环收敛。

### 2.2 彻底替换旧编排，而非长期并存

最终态中：
- 旧 `controller` 主编排逻辑删除。
- 旧 `workflows` 编排层删除。
- 旧 `WORKFLOW.md` 执行型 SOP 删除并重写。
- 旧 `le control` 主闭环入口删除。

### 2.3 渐进迁移，但不追加旧架构投资

迁移期允许旧架构存在，但只有两个目的：
1. 作为对照验证基准。
2. 作为切换前风险兜底。

除阻断性缺陷外，不再对旧编排层做长期增强。

### 2.4 能力层与编排层彻底分离

- `connection` / `loop_core` / `deploy` / `patch_*` 负责“做事”。
- `runtime` 负责“决定什么时候做、下一步去哪、什么时候停”。

### 2.5 高风险不可恢复场景立即退人工

除 `FAIL >= 5` 外，下列场景允许立即退人工：
1. `kernel_dead_no_shell`
2. `PATCH_REJECTED` 且属于越权/越界修改
3. `SESSION_STATE_ERROR` / checkpoint 损坏
4. deploy 回退失败
5. 关键连接基础设施异常且无法恢复（如 rp5-serial host 不可用）

---

## 3. 模块去留策略

### 3.1 保留并增强的模块

| 模块 | 处理 | 原因 | 最终定位 |
|---|---|---|---|
| `engineering/loop/connection/` | 保留 + 增强 | 是 RPi5 接入能力层，不是旧编排层 | 新 runtime 的 transport capability |
| `engineering/loop/cases/` | 保留 + 增强 | 是业务场景与验收真相源 | 新 runtime 的 suite 资产 |
| `engineering/loop/core/python/loop_core/` | 保留 + 重构增强 | 是 verify/evidence 能力层 | 新 runtime 的验证执行引擎 |
| `engineering/loop/deploy/` | 保留 + 增强 | 是 compile/deploy/rollback 能力层 | 新 runtime 的部署执行引擎 |
| `engineering/loop/contracts/` | 保留 + 重构增强 | 是纯数据契约层，适合作为共享模型基座 | 新 runtime 契约中心 |

### 3.2 抽取能力后删除旧形态的模块

| 模块 | 处理 | 原因 | 最终结果 |
|---|---|---|---|
| `engineering/loop/controller/` 旧主控制流 | 抽取 `patch_applier` / `patch_guard` 等能力后删除旧编排逻辑 | 当前是旧架构重灾区 | 最终重建为 runtime 控制中心 |
| `engineering/loop/workflows/` 旧编排层 | 保留业务 phase 知识，删除脚本式编排层 | 正是被 runtime 替代的对象 | 最终删除旧 workflow 编排 |
| 旧 `le control` 主闭环模式 | 迁移期短暂存在，对照后删除 | 与“唯一正式架构”目标冲突 | 最终删除 |
| `WORKFLOW.md` 旧执行型 SOP | 重写 | 不能再承担 AI 文本驱动执行职责 | 最终只描述新 runtime |

### 3.3 case schema 策略

本期采用 **A 方案**：
- 第一阶段**尽量兼容现有 YAML schema**。
- 新 runtime 适配现有 `suite/include/requires/assert/run_on/collector` 体系。

**Roadmap（B 方案）**：
- 后续可在 schema 中引入更清晰的 `phase/risk/recovery/agent-hint` 等 runtime metadata。
- 本期不做 schema 升级，不阻塞 runtime 重构。

---

## 4. 最终目标架构

### 4.1 三层最终架构

#### A. Runtime 编排层（唯一正式控制层）

新增逻辑归属到 `engineering/loop/controller/` 内部的新 runtime 包：
- `runtime.py`
- `runtime_state.py`
- `nodes.py`
- `guards.py`
- `transitions.py`
- `checkpoint_store.py`
- `event_log.py`

职责：
- 管理状态机
- 执行 node handler
- 进行 guard 判定与 transition
- 记录 checkpoint
- 支持 interrupt/resume
- 产出 terminal state（成功/人工/失败）

#### B. Capability 执行层

保留既有能力，并统一为 runtime node handler 的调用对象：
- `connection/`：transport / session / transcript / serial→adb 辅助
- `loop_core/`：verify / collector / evidence
- `deploy/`：compile / deploy / rollback / image_verify
- `patch_applier.py` / `patch_guard.py`
- `contracts/`

#### C. Documentation / Spec 层

文档只负责：
- 描述 runtime 状态机
- 描述 guard 与人工 gate
- 描述 transport / deploy 硬规则
- 描述恢复与清场策略

文档不再承担“AI 记住流程并手工串命令”的职责。

---

## 5. Runtime 核心模型

### 5.1 最小职责

Runtime 只承担 5 个职责：
1. 加载并持久化运行状态。
2. 执行当前节点。
3. 根据 guard 判定下一跳。
4. 记录 checkpoint / event log。
5. 处理中断与恢复。

### 5.2 不属于 runtime 的职责

以下能力不应内嵌在 runtime 中：
- suite 具体执行逻辑（`loop_core`）
- patch 具体写入逻辑（`patch_applier`）
- compile/deploy 具体实现（`loop_deploy`）
- AI 推理本身（analyzer 只是边界对象）

### 5.3 状态模型

建议 contracts 升级为两层状态：

#### 业务会话层 `LoopSession`
关注闭环业务语义：
- `session_id`
- `target`
- `suite`
- `current_attempt`
- `max_attempts`
- `attempts[]`
- `artifacts_dir`
- `latest_failure_code`
- `status`

#### 运行时层 `RuntimeState`
关注引擎执行状态：
- `current_node`
- `previous_node`
- `node_status`
- `transition_reason`
- `pending_human_gate`
- `interrupted`
- `resume_token`
- `last_checkpoint_at`
- `terminal_state`

### 5.4 节点模型

首批正式节点建议固定为：
1. `INIT_SESSION`
2. `RUN_VERIFY`
3. `DECIDE_NEXT`
4. `BUILD_ANALYSIS_REQUEST`
5. `WAIT_ANALYZER_PATCH`
6. `APPLY_PATCH`
7. `COMPILE_PATCH`
8. `DEPLOY_PATCH`
9. `REVERT_PATCH`
10. `ESCALATE_HUMAN`
11. `DONE_SUCCESS`
12. `DONE_FAILURE`

### 5.5 Guard 模型

Guard 必须从旧文档/旧 policy 中提升为 runtime 一等公民。

#### 成功类
- `all_cases_passed`
- `deploy_success_and_verify_passed`

#### 重试类
- `attempts_below_limit`
- `patch_applied_successfully`
- `compile_failed_but_recoverable`
- `deploy_failed_but_recoverable`

#### 终止/人工类
- `attempt_limit_reached`
- `repeated_failure_code`
- `duplicate_patch_hash`
- `patch_rejected`
- `kernel_dead_no_shell`
- `boot_timeout_no_recovery`
- `session_state_corrupted`
- `transport_unrecoverable`
- `rollback_failed`

### 5.6 Terminal State

最终状态必须显式区分：
- `DONE_SUCCESS`
- `ESCALATE_HUMAN`
- `DONE_FAILURE`

禁止继续使用语义模糊的单一 `STOP`。

### 5.7 Checkpoint

每个节点执行后必须至少写出一个 checkpoint，记录：
- `checkpoint_id`
- `session_id`
- `attempt_index`
- `current_node`
- `input_summary`
- `output_summary`
- `failure_code`
- `matched_guards`
- `next_node`
- `timestamp`

Checkpoint 是 runtime 的恢复、审计、对照验证基础。

---

## 6. 目标状态机

最终最小闭环为：

```text
INIT_SESSION
  -> RUN_VERIFY
  -> DECIDE_NEXT
     -> DONE_SUCCESS
     -> BUILD_ANALYSIS_REQUEST
     -> ESCALATE_HUMAN

BUILD_ANALYSIS_REQUEST
  -> WAIT_ANALYZER_PATCH
  -> APPLY_PATCH
     -> COMPILE_PATCH
        -> DEPLOY_PATCH
           -> RUN_VERIFY
        -> REVERT_PATCH
           -> DECIDE_NEXT
     -> DECIDE_NEXT

任何时刻命中终止 guard
  -> ESCALATE_HUMAN / DONE_FAILURE
```

### 6.1 与旧流程的关系

该状态机语义上覆盖现有 8 步闭环，但流程知识内嵌进 runtime，而非继续留在 `WORKFLOW.md` 文本 SOP 中。

### 6.2 WAIT_ANALYZER_PATCH 的意义

`WAIT_ANALYZER_PATCH` 必须独立成节点，原因：
- 当前 analyzer 仍可由主会话 AI 产出 patch。
- 未来可替换为更内聚的 analyzer worker，而不改变主状态机。

---

## 7. 连接层、case、core 的最终定位

### 7.1 connection 层：保留

`connection/` 是新架构正式组成部分，特别是 RPi5 相关部分必须保留：
- rp5-serial host/client 拓扑
- writer lease
- transcript 落盘
- serial→adb 依赖链
- 动态 IP 发现

#### 增强方向
- 将 provider API 统一为 runtime 可直接消费的 transport capability。
- 明确 provider 只负责连接与数据转发，不承担业务编排。

### 7.2 cases：保留

`cases/` 是新 runtime 的长期资产：
- 现有 YAML schema 本期继续兼容。
- `common/shell`、`system/*`、`features/lcview/*`、`features/lciod/*` 全部保留。

#### 增强方向
- 加强 schema 校验与 case 质量门槛。
- 后续 roadmap 再考虑扩展 metadata。

### 7.3 loop_core：保留

`loop_core` 本质是验证执行引擎，不是旧编排层，必须保留：
- `case_loader`
- `assertion_engine`
- `collector`
- `executor`
- `runner`
- `evidence`
- `host_exec`
- `provider_loader`

#### 需要调整的边界
- `loop_core.cli` 不再承担未来主闭环编排职责。
- `runner/executor/evidence` 输出要稳定为 runtime node handler 的标准接口。

---

## 8. controller / workflows / scripts / WORKFLOW.md 的新结论

### 8.1 controller：旧控制流删除，重建为 runtime 控制中心

#### 保留
- `patch_applier.py`
- `patch_guard.py`
- 可复用的 analyzer 契约定义（若边界清晰）

#### 删除
- 旧 `control_cli` 主闭环模式
- 旧编排 glue
- 旧围绕 `SessionState` 组织的主流程逻辑

#### 重建
- 新 runtime 包归入 `controller/`，让 controller 成为真正的控制面。

### 8.2 workflows：旧编排层删除，知识迁移

`workflows/` 当前承载 phase plan / bootstrap / fallback / rerun 逻辑，这部分正是新 runtime 应替代的对象。

#### 迁移策略
- 保留业务 phase 知识。
- 删除旧脚本式 workflow 编排层。
- 将有效知识迁移至 runtime state machine、node handlers 或 case/runtime metadata。

### 8.3 scripts：部分保留、部分重写、部分删除

#### 保留
- `start_rp5_serial_host.bat`
- `rp5_serial_helper.py`（若仍承担 device-ip 发现等基础能力）

#### 重写
- `le.sh`：保留为新 runtime CLI wrapper 的可能壳层，但内部彻底改造。

#### 删除
- 只为旧编排模型服务的脚本命令体系。

### 8.4 WORKFLOW.md：文件保留，内容重写

最终只描述：
- 新 runtime 架构
- 状态机
- guard
- human gate
- deploy 硬规则
- transport 约束

删除旧的“AI 按步骤串命令执行”的 SOP 表述。

---

## 9. 渐进迁移与最终清场

### 9.1 Wave 1：复用能力提纯

目标：
- 提纯 `connection`、`cases`、`loop_core`、`deploy`、`contracts` 的接口与契约。
- 停止对旧编排层做战略增强。

产出：
- 稳定的 stage result / failure_code / artifacts 契约。
- 能被新 runtime 消费的 capability API。

### 9.2 Wave 2：新 runtime 骨架落地

目标：
- 实现 state / node / guard / transition / checkpoint / terminal state。
- 先跑只读与低风险节点。

### 9.3 Wave 3：新 runtime 全链路接管

目标：
- 打通 `verify -> decide -> analyze -> patch -> compile -> deploy -> rerun`。
- 使用旧架构进行对照验证。

原则：
- 旧架构只用于验证，不继续演进。

### 9.4 Wave 4：切换验收

必须验证：
1. `DONE_SUCCESS` 闭环。
2. `FAIL >= 5 -> ESCALATE_HUMAN`。
3. compile fail -> revert。
4. deploy fail -> rollback / escalate。
5. kernel dead -> immediate human escalation。
6. interrupt/resume。

### 9.5 Wave 5：Legacy Removal

这是正式里程碑，必须单列执行：
1. 删除旧 `controller` 主编排逻辑。
2. 删除旧 `workflows` 编排层。
3. 删除旧 `le control` 主闭环模式。
4. 删除旧架构专属测试与适配层。
5. 重写 `WORKFLOW.md` / `README.md` / `controller/README.md`。
6. 让新 runtime 成为唯一正式架构真相源。

---

## 10. 验证与切换门禁

### 10.1 验证分层

1. **L1 单测**：guard / transition / state mapping / patch_hash / attempt threshold / checkpoint。
2. **L2 契约测试**：capability handler 输入输出契约稳定。
3. **L3 双跑对照**：新 runtime 与旧架构对相同输入产出相同或可解释终态。
4. **L4 金丝雀**：低风险→中风险→高风险逐段验证。
5. **L5 全链路验收**：真实 case 下的成功、失败、回退、恢复路径全部验证。

### 10.2 切换门槛

切换默认入口前必须全部满足：
1. 至少 1 个真实业务闭环在新 runtime 下稳定 PASS。
2. 至少 1 个故障闭环触发 `FAIL >= 5 -> ESCALATE_HUMAN`。
3. 至少 1 个 compile fail 场景正确 revert。
4. 至少 1 个 deploy fail 场景正确 rollback 或 escalate。
5. 至少 1 次 interrupt/resume 成功恢复。
6. Legacy Removal 计划与删除清单已准备完毕。

### 10.3 不允许的切换方式

1. 不允许默认入口切换后仍长期依赖旧 control 主闭环。
2. 不允许 runtime 上线后保留两套同等级正式架构。
3. 不允许未完成双跑/金丝雀验证就直接删旧架构。
4. 不允许切换后再“以后再删旧代码”。清场必须作为正式阶段完成。

---

## 11. Roadmap

### 11.1 本期（必须完成）
- 新 runtime 架构落地。
- 复用层提纯。
- 全链路接管。
- 切换验收。
- Legacy Removal。

### 11.2 后续（Roadmap）
1. **Case schema 升级（B 方案）**：引入 `phase/risk/recovery` 等更清晰的 runtime metadata。
2. **Analyzer 组件化**：将 `WAIT_ANALYZER_PATCH` 后面的 analyzer 边界抽成更标准化的实现。
3. **更强的 transport/runtime 统一抽象**：减少 provider 差异暴露。
4. **多 agent / evaluator-optimizer 扩展**：仅在单 runtime 稳定后再评估。

---

## 12. 最终结论

本方案的核心不是“重写全部 loop 代码”，而是：
- **保留并强化正确的能力层资产**：`connection`、`cases`、`loop_core`、`deploy`、`contracts`
- **彻底删除错误的旧编排架构**：旧 `controller` 主控制流、旧 `workflows` 编排层、旧文本 SOP 驱动模式
- **用自研零依赖轻量状态图 runtime 替代旧 loop 架构**
- **通过渐进验证降低风险，但绝不让迁移中间态永久化**

最终交付标准是：
> 新 runtime 成为 loop 唯一正式架构；旧架构在验证完成后从项目中彻底删除。
