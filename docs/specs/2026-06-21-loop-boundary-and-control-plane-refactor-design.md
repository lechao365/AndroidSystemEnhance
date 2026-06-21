# Loop Engineering 边界收敛与控制面重构设计

> **日期**：2026-06-21
> **状态**：已确认，待实施计划
> **范围**：收敛 `engineering/` 顶层目录边界，明确 `harness` 与 `loop` 的职责与单向依赖；将误放在 `engineering/harness/` 中的 loop-specific 组件严格回迁到 `engineering/loop/`；在 `engineering/loop/` 内建立 `scripts/`、`controller/`、`workflows/`、`contracts/` 四类新结构，为 1-7 自动化闭环补齐控制面骨架。第一阶段优先实现 terminate / retry / regression 控制策略，不追求一次做厚 diagnosis / deploy / verify。
> **前序**：基于现有 `engineering/harness/`、`engineering/loop/`、`docs/specs/2026-06-19-loop-engineering-v2-design.md`、`docs/specs/2026-06-20-loop-core-reliability-and-reuse-design.md`、`docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md` 的已落地实现与文档约束。

---

## 1. 背景

### 1.1 当前 `loop` 的真实能力边界

当前 `engineering/loop/` 已经具备稳定的**单次 attempt 执行内核**，可完成：

- suite / case 加载
- transport 执行
- collector 采证
- `EvidenceBundle` 输出
- FAIL 后进入 `/le` 诊断契约

其主链路本质上仍然是：

```text
suite YAML
  -> case_loader
  -> LoopRunner
  -> CaseExecutor
  -> Collector
  -> EvidenceBundle JSON
```

也就是说，当前 `loop_core` 的核心价值是“**单次 run 执行与证据输出**”，而不是“多轮自动闭环控制器”。这一本质不能在后续改造中被模糊或破坏。

### 1.2 当前 1-7 闭环的缺口

`engineering/loop/WORKFLOW.md` 已定义了 1-7 的目标流程：

1. AI 生成 YAML 用例
2. `le run` 执行用例
3. 全 pass -> 功能 OK
4. 失败后 AI 读取证据进行诊断
5. AI 生成候选补丁草案
6. 编译部署
7. goto 2 直到 PASS 或 N 次失败后升级人工

但现状中真正稳定落地的主要还是第 2-5 步的部分能力：

- `loop_core` 只负责单次执行
- `/le` 诊断与候选补丁草案更多是流程契约，而非真正的控制面实现
- `deploy` 与 `loop_ctrl` 仍未落地为框架能力

因此，要补齐 1-7，不是继续向 `loop_core` 堆逻辑，而是需要在 `loop` 内部新增独立控制面分层。

### 1.3 当前存在目录边界污染

目前 `engineering/harness/` 中混入了若干 loop-specific 组件，典型包括：

- `engineering/harness/scripts/le.sh`
- `engineering/harness/scripts/le_runs_cleanup.sh`
- `engineering/harness/scripts/rp5_serial_helper.py`
- `engineering/harness/scripts/start_rp5_serial_host.bat`
- `engineering/harness/workflows/lcview-adb-run/`

这些对象虽然复用了 harness 的 bootstrap、路径管理与 observability 基础设施，但其**语义归属**属于 loop engineering，而不是通用 harness engineering。继续留在 `harness/` 会导致：

1. loop 能力不内聚
2. harness 被迫承载 loop-specific 语义
3. 后续 controller / workflow / session state 容易继续错误落位
4. `harness -> loop` 的潜在反向依赖风险持续积累

### 1.4 本次设计的核心目标

本次设计并不试图一次性完成完整 1-7 自动化，而是先完成下面四件基础但关键的事情：

1. 收敛 `engineering/` 顶层边界
2. 严格回迁误归属的 loop-specific 组件
3. 在 `engineering/loop/` 内建立控制面三层骨架
4. 以 terminate / retry / regression 为第一阶段优先策略，给后续自动闭环提供稳定控制入口

---

## 2. 目标

### 2.1 建立 `engineering/` 顶层总纲

新增 `engineering/README.md`，明确：

- `harness` 是公共 harness engineering 能力层
- `loop` 是 loop engineering 专属能力层
- `output` 是执行产物层，不承载实现逻辑
- 依赖方向固定为 `loop -> harness`
- `harness` 禁止依赖 `loop`
- 能力归属按“语义边界”判定，而不是按脚本、workflow、目录形式判定

### 2.2 严格回迁误归属组件

将当前位于 `engineering/harness/` 中、但语义属于 loop engineering 的组件迁回 `engineering/loop/`，并同步更新所有引用、文档与测试路径。

### 2.3 在 `engineering/loop/` 中新增控制面分层

在保留 `core/`、`connection/`、`cases/`、`templates/` 的前提下，新增：

- `scripts/`
- `controller/`
- `workflows/`
- `contracts/`

其中：

- `scripts/` 承载 loop CLI 与 loop 专属脚本入口
- `controller/` 承载 loop session / 状态机 / 停止策略
- `workflows/` 承载 loop 专属 phase plan / verify / fallback / rerun 流程
- `contracts/` 承载 controller 与 workflow 之间的 machine-readable contract

### 2.4 第一阶段只做控制面骨架，不做厚实现

本次第一阶段优先：

- loop session 生命周期
- attempt 历史
- 状态机骨架
- terminate / retry / regression policy
- workflow 调度骨架
- 机器可消费的最小结果契约

不要求本轮就实现：

- 完整自动 patch apply
- 完整自动 binary / image deploy
- 全部 loop workflow 场景迁移和重写
- 厚 diagnosis / deploy / verify 引擎

---

## 3. 非目标

本次设计明确不包含以下目标：

1. 不把 `loop_core` 改造成多轮 orchestration 内核
2. 不在第一阶段实现完整自动补丁应用链路
3. 不在第一阶段实现完整 binary / image deploy 执行面
4. 不在第一阶段重写所有现有 loop suite
5. 不在第一阶段把所有 human-readable 文档契约一次替换成完整机器契约
6. 不将 loop-specific 组件留在 harness 中作为“暂时过渡”

这里的重点是：**本次先解决边界、归属、控制面骨架与严格回迁问题**。

---

## 4. `engineering/` 顶层边界设计

## 4.1 顶层目录职责

后续 `engineering/` 顶层目录语义固定如下：

- `engineering/harness/`：公共工程控制面、规则、模板、通用脚本基础设施、路径管理、日志与 observability 支撑、跨工程可复用 workflow
- `engineering/loop/`：loop engineering 专属能力，包括执行内核、用例、连接、控制器、loop workflow、loop 脚本与控制面契约
- `engineering/output/`：日志、artifacts、运行结果输出目录，仅为产物承载层

## 4.2 单向依赖规则

必须固定以下依赖方向：

```text
engineering/loop  -> engineering/harness
engineering/harness -X-> engineering/loop
```

允许：

- `engineering/loop/scripts/*` 使用 `engineering/harness/lib/*`
- `engineering/loop/*` 复用 harness 的路径管理、观测、日志、规则与通用脚本基础设施

禁止：

- `engineering/harness/scripts/*` 直接承载 loop CLI
- `engineering/harness/workflows/*` 承载 loop-specific phase plan
- `engineering/harness/` 引入 loop 的 case / connection / controller / workflow / session / attempt / deploy decision 语义

## 4.3 能力归属判定规则

### 属于 `engineering/loop/` 的能力

若能力满足任一条件，则必须归入 `engineering/loop/`：

- 直接服务 loop engineering
- 包含 case / suite / connection / transport / session / attempt / rerun / LE runs 生命周期等 loop-specific 语义
- 当前仅被 loop 使用
- 上提到 harness 会构成过早抽象或过早公共化

### 属于 `engineering/harness/` 的能力

仅当能力同时满足以下条件时，才允许归入 `engineering/harness/`：

- 不包含 loop-specific 领域语义
- 是跨工程通用基础设施
- 有稳定公共接口
- 不引入 `harness -> loop` 反向依赖

## 4.4 `workflow` 的归属规则

`workflow` 的归属不应按“它是不是 workflow 文件”判断，而应按“它是否服务 loop-specific 语义”判断：

- 通用工程 workflow -> `engineering/harness/workflows/`
- loop 专属 workflow -> `engineering/loop/workflows/`

因此，loop 中的 bootstrap / verify / fallback / rerun / multi-transport 协调流程不得继续放在 harness workflow 目录下。

---

## 5. `engineering/loop/` 目标分层

## 5.1 目标目录结构

本次重构后，`engineering/loop/` 目标结构为：

```text
engineering/loop/
├── README.md
├── WORKFLOW.md
├── core/
├── connection/
├── cases/
├── templates/
├── scripts/
├── controller/
├── workflows/
└── contracts/
```

## 5.2 `core/`：单次 attempt 执行内核

`core/` 的定位应被收敛为：

- 单次 suite/case 执行
- assertions
- collectors
- evidence 输出
- transport 调用

明确不负责：

- session
- terminate / retry / regression
- multi-attempt orchestration
- deploy 决策
- workflow phase 组织

后续所有文档和 README 都应把 `loop_core` 明确描述为 **single-attempt execution kernel**。

## 5.3 `scripts/`：loop 专属脚本入口层

`engineering/loop/scripts/` 负责承载 loop-specific 脚本入口，包括：

- `le.sh`：loop CLI wrapper
- `le_runs_cleanup.sh`：loop runs 生命周期清理
- `rp5_serial_helper.py`：loop host-side serial helper
- `start_rp5_serial_host.bat`：loop 专属 host daemon 启动器

这些脚本仍可依赖 harness bootstrap / path util / observability，但其**归属**必须在 loop。

## 5.4 `controller/`：loop 控制面

`engineering/loop/controller/` 负责：

- loop session 生命周期
- attempt 历史与递进
- stage machine
- terminate / retry / regression policy
- 继续/停止决策
- workflow 调度
- session state 持久化

明确不负责：

- case 执行细节
- transport 细节
- diagnosis 内部推理
- deploy 命令细节
- workflow 内部 phase 实现

## 5.5 `workflows/`：loop 专属流程面

`engineering/loop/workflows/` 负责：

- phase plan
- bootstrap / verify / fallback / rerun 路径
- serial / adb / host 多阶段切换
- 基于 workflow 输出标准化 `StageResult`

明确不负责：

- 全局 session 总状态
- N 次终止控制
- 全局回归策略
- 跨 attempt 聚合

也就是说：

- workflow 决定“这一轮怎么跑”
- controller 决定“下一轮还要不要继续跑”

## 5.6 `contracts/`：控制面契约层

`engineering/loop/contracts/` 负责定义：

- `SessionState` schema
- `AttemptState` schema
- `StageResult` schema
- `TerminationDecision` schema
- `FailureCode` taxonomy
- `DiagnosisResult` / `DeployResult` / `VerifyResult` 的最小结构

其目标是让 controller 消费**机器契约**，而不是直接依赖：

- `summary.txt`
- markdown 报告
- workflow 内部实现细节

---

## 6. 严格回迁范围

本次回迁采用**严格回迁**原则：只要对象当前属于 loop-specific，就在本次任务中迁回 `engineering/loop/`，而不是仅做标记或延后处理。

## 6.1 回迁到 `engineering/loop/scripts/`

以下对象明确迁入 `engineering/loop/scripts/`：

### 1. `le.sh`

原位置：
- `engineering/harness/scripts/le.sh`

回迁理由：
- 是 Loop Engineering CLI wrapper
- 直接调用 `loop_core.cli`
- 是 loop 的统一入口，不是 harness 通用脚本

### 2. `le_runs_cleanup.sh`

原位置：
- `engineering/harness/scripts/le_runs_cleanup.sh`

回迁理由：
- 仅清理 LE 的 runs 产物
- 变量与语义直接绑定 `LE_RUNS_KEEP`
- 属于 loop 运行产物生命周期管理，而不是 harness 通用清理脚本

### 3. `rp5_serial_helper.py`

原位置：
- `engineering/harness/scripts/rp5_serial_helper.py`

回迁理由：
- 当前仅被 loop 使用
- 文件自身语义即“供 Loop Engineering host case 调用”
- 抽到 harness 属于过早提取公共能力

### 4. `start_rp5_serial_host.bat`

原位置：
- `engineering/harness/scripts/start_rp5_serial_host.bat`

回迁理由：
- 当前用于 loop engineering 中串口转发特有 host daemon
- 不是跨工程通用 host 启动器
- 属于 loop 环境启动器

## 6.2 回迁到 `engineering/loop/workflows/`

以下对象明确迁入 `engineering/loop/workflows/`：

### 1. `lcview-adb-run/`

原位置：
- `engineering/harness/workflows/lcview-adb-run/`

回迁理由：
- 直接面向 loop suite / transport / fallback evidence
- 本质是 loop 多阶段验证 workflow
- 不属于 harness 通用 workflow

## 6.3 回迁后的原则

回迁后：

- harness 不再承载 loop CLI
- harness 不再承载 loop runs 生命周期脚本
- harness 不再承载 loop-specific serial helper
- harness 不再承载 loop 专属 workflow
- loop 脚本与 workflow 继续可以依赖 harness 基础设施，但其归属必须保持在 loop 目录下

---

## 7. controller 第一阶段设计

## 7.1 第一阶段目标

controller 第一阶段只解决：

- session 管理
- attempt 编号与历史
- 最小状态机
- terminate / retry / regression 决策
- workflow 调度入口
- machine-readable state 聚合

不解决：

- 厚 deploy 实现
- 厚 diagnosis 实现
- 全部场景 workflow 落地

## 7.2 最小状态机

建议第一阶段支持以下阶段：

- `run`
- `diagnose`
- `patch_draft`
- `deploy`
- `verify`
- `terminate`

其中：

- `run` / `verify` 主要通过 `core + workflows` 驱动
- `diagnose` / `patch_draft` / `deploy` 第一阶段先对接最小结果契约
- controller 不承担这些阶段的内部实现细节

## 7.3 terminate policy

第一阶段 terminate policy 采用硬规则优先：

- `PASS` -> stop
- `attempt_count > max_attempts` -> stop
- `deploy` 致命失败 -> stop
- session state 缺关键结果 -> stop

## 7.4 repeated-failure stop policy

- 连续两轮同一 `failure_code` -> stop
- 连续两轮 diagnosis 没有新结论 -> stop
- 连续两轮 patch/deploy 后无改善 -> stop

## 7.5 retry policy

- transport 短暂失败 -> 允许有限重试
- evidence 不足 -> 允许一次补采
- ready check 短暂失败 -> 允许一次重试

## 7.6 regression stop policy

- 新出现严重 failure -> stop
- verify 引入新的基线破坏 -> stop

controller 第一阶段不做复杂概率策略或动态权重策略，以避免控制器在首版就过厚。

---

## 8. workflows 第一阶段设计

## 8.1 基本原则

workflow 是 loop 专属 phase plan，不是 harness 通用流程，也不是 `loop_core` 的替代执行器。

workflow 只负责：

- 定义阶段顺序
- 组织 transport / profile / suite 切换
- 执行 bootstrap / verify / fallback / rerun 流程
- 输出标准化 `StageResult`

workflow 不负责：

- 全局 session 状态
- terminate / retry 总决策
- case 执行细节实现

## 8.2 第一阶段只做薄 workflow 骨架

建议第一阶段先建立两类最小 workflow：

### `single_run_verify`

职责：
- 执行单一 suite
- 返回标准 `StageResult`
- 供 controller 驱动最小闭环

### `multi_phase_verify`

职责：
- 预留 serial -> adb -> fallback 的 phase plan
- 第一阶段只建立接口和骨架
- 不要求本轮一次性把全部场景做厚

## 8.3 `lcview-adb-run` 的定位

回迁到 `engineering/loop/workflows/` 后，`lcview-adb-run/` 的定位应被明确为：

- loop workflow 目录下的现有样板
- 多 transport phase plan 的具体实例
- controller 后续调度的 workflow 候选之一

它不应再被描述为 harness 通用 workflow。

---

## 9. contracts 第一阶段设计

## 9.1 原则

controller 必须消费 machine-readable contract，而不是依赖：

- `summary.txt`
- markdown 报告
- workflow 的内部实现细节

因此，`contracts/` 必须作为独立层存在，而不是混入 `controller/`。

## 9.2 第一阶段最小对象模型

### `SessionState`

建议至少包含：

- `session_id`
- `workflow_id`
- `target`
- `max_attempts`
- `current_attempt`
- `status`
- `termination_reason`
- `attempts[]`

### `AttemptState`

建议至少包含：

- `attempt_index`
- `stage_results`
- `run_result_ref`
- `diagnosis_result_ref`
- `patch_result_ref`
- `deploy_result_ref`
- `verify_result_ref`
- `attempt_decision`

### `StageResult`

建议至少包含：

- `stage_name`
- `status`
- `failure_code`
- `summary`
- `artifacts`
- `next_action_hint`

### `TerminationDecision`

建议至少包含：

- `decision`
- `reason_code`
- `reason_summary`
- `can_retry`
- `should_escalate`

### `FailureCode`

第一阶段只定义最小集合，优先覆盖：

- 执行失败
- 证据不足
- 重复失败
- 回归失败
- deploy 致命失败
- session 状态错误

后续按 workflow 与诊断面扩展。

---

## 10. 文档与说明同步要求

## 10.1 新增文档

必须新增：

- `engineering/README.md`

该文件应成为 engineering 顶层边界总纲。

## 10.2 需同步更新的文档

至少包括：

- `engineering/loop/README.md`
- `engineering/harness/README.md`
- `engineering/harness/workflows/README.md`
- `engineering/harness/scripts/README.md`
- `engineering/output/README.md`

必要时还包括受迁移影响的测试与说明文件。

## 10.3 文档更新重点

文档更新必须同步反映：

- harness / loop / output 的职责边界
- 单向依赖规则
- CLI 入口路径变更
- loop workflow 路径变更
- runs cleanup 路径变更
- Windows host launcher 路径变更
- loop core 被重新界定为 single-attempt execution kernel

---

## 11. 风险与约束

## 11.1 controller 与 workflow 职责串层

风险：
- controller 直接写入具体 workflow phase 细节
- workflow 反过来承担 session 总状态

规避方式：
- controller 只做 session/决策
- workflow 只做 phase plan

## 11.2 workflow 与 `loop_core` 重叠

风险：
- workflow 重写 case 执行逻辑
- workflow 重新实现 evidence 输出

规避方式：
- workflow 仅组织 transport / suite / phase
- 统一复用 `loop_core`

## 11.3 再次把 loop helper 提前抽回 harness

风险：
- 为了“看起来更通用”而再次把 loop-only 能力迁回 harness

规避方式：
- 当前只要仍是 loop-only，就留在 loop
- 上提到 harness 必须满足公共能力判定规则

## 11.4 文档更新不完整导致路径失真

风险：
- 回迁后 README、测试、脚本说明未同步更新，造成路径失真和认知混乱

规避方式：
- 将 README / 测试 / 路径引用更新列为显式实施项
- 回迁完成后统一扫引用

---

## 12. 验收标准

当本次设计对应的实施完成后，应满足：

1. `engineering/README.md` 明确解释 harness / loop / output 的关系与规则
2. `loop -> harness` 依赖方向被文档化并落实到目录布局
3. loop-specific 组件已从 harness 严格回迁到 loop
4. `engineering/loop/` 中存在清晰的：
   - `scripts/`
   - `controller/`
   - `workflows/`
   - `contracts/`
5. `loop_core` 的定位被收敛为 single-attempt execution kernel
6. 第一阶段 controller 能表达 terminate / retry / regression 的最小控制面
7. 相关 README / 测试 / 引用路径同步更新，不再把回迁组件描述为 harness 能力

---

## 13. 建议实施顺序

后续 implementation plan 建议按以下顺序展开：

1. 先新增 `engineering/README.md`，固定顶层边界
2. 再创建 `engineering/loop/scripts/`、`controller/`、`workflows/`、`contracts/` 骨架
3. 严格回迁现有误归属组件
4. 同步修正文档、测试与路径引用
5. 最后补最小 controller / workflow / contract 骨架与 terminate/retry/regression 策略入口

顺序上的关键原则是：

- 先立边界，再迁组件
- 先迁组件，再补控制面
- 先最小 contracts，再 controller，再 workflow 薄骨架

---

## 14. 结论

本次设计建议明确采用以下路线：

- 以 `engineering/README.md` 建立 engineering 顶层边界总纲
- 在 `engineering/loop/` 中一次建立 `scripts/ + controller/ + workflows/ + contracts/` 四类结构
- 对误放到 `engineering/harness/` 的 loop-specific 组件执行**严格回迁**
- 第一阶段优先完成 terminate / retry / regression 控制面骨架
- 暂不追求一次做厚 diagnosis / deploy / verify 引擎

该路线兼顾：

- 目录边界清晰
- loop 能力内聚
- harness 不被 loop-specific 语义污染
- 后续 1-7 自动化闭环具备可持续演进的控制面基础
