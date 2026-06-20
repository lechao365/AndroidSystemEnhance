# Loop Core 可靠性与规则复用增强设计

> **日期**：2026-06-20
> **状态**：已确认
> **范围**：在 Loop Engineering v2 基线之上，增强 `engineering/loop` 的执行可靠性、结果可信度与规则复用能力；优先修复影响基本功能/性能/可靠性的问题，再建设参数化通用规则库与公共 collector 库，并为 `gen-cases` / `deploy` / `loop_ctrl` 预留稳定契约。
> **前序**：基于 `docs/specs/2026-06-19-loop-engineering-v2-design.md` 与当前已落地的 v2 实现。

---

## 1. 背景

### 1.1 当前 v2 基线的价值

Loop Engineering v2 已完成从“规则盲匹配引擎”向“声明式用例驱动验收器”的主架构迁移，当前主链路已经稳定为：

```text
suite YAML
  -> case_loader
  -> LoopRunner
  -> CaseExecutor
  -> Collector
  -> EvidenceBundle JSON
```

当前方向是正确的，尤其是以下几点不应回退：

1. `LoopRunner` 保持场景无关、保持薄内核；
2. 确定性判断由 `assertion_engine` 承担，而非重新引入复杂规则引擎；
3. 失败后诊断由 collector 承担，而非把所有诊断逻辑塞回主判定链路；
4. 新场景优先通过 YAML 扩展，而不是重新编写 Python workflow。

相关现状代码：
- `engineering/loop/README.md:7`
- `engineering/loop/core/python/loop_core/case_loader.py:59`
- `engineering/loop/core/python/loop_core/executor.py:33`
- `engineering/loop/core/python/loop_core/runner.py:17`

### 1.2 当前主要问题

虽然 v2 主方向正确，但当前实现仍有两类关键缺口：

#### A. 执行可靠性缺口
这些问题会直接影响基本功能正确性与结果可信度：

1. **命令输出采集没有边界/游标语义**
   - `send_line()` 后直接 `capture_window()`，历史缓冲可能污染当前 case。
   - live/fixture 都没有“只读取本命令输出区间”的稳定契约。
2. **运行期异常没有完全收敛**
   - transport、collector、断言引擎异常可能直接打断整次 run。
3. **依赖缺失与 skip 语义过于宽松**
   - `requires` 指向不存在 case 时可能被跳过，suite 仍可能看起来 PASS。
4. **`exit_code_zero` 契约失配**
   - 断言引擎宣称支持，但主链路实际上拿不到 exit code。
5. **EvidenceBundle 只能表达结果，不能表达结果可信度**
   - 缺乏 warning、runtime error、degraded 等结构化信息。

#### B. 规则复用缺口
这些问题会限制后续快速扩展业务规则、沉淀通用系统规则：

1. **case/collector 命名空间模型不足**
   - 当前裸 `id` 合并存在静默覆盖风险。
2. **参数化原子用例未落地**
   - 高复用系统规则只能靠复制 YAML。
3. **collector 仍偏 suite-local**
   - 缺少可跨 suite/跨设备复用的公共 collector 库。
4. **设备差异与通用规则边界仍不够清晰**
   - 若不收敛，`common` 规则会继续绑死具体设备。

### 1.3 本次设计目标

本次设计目标按优先级排序如下：

1. **先保证判定正确与结果可信**；
2. **再提升业务规则扩展速度**；
3. **最大化跨 suite、跨设备复用通用系统规则**；
4. **为 `gen-cases` / `deploy` / `loop_ctrl` 预留稳定契约**。

---

## 2. 设计原则

### 2.1 总原则

1. **判定链路优先于诊断链路**
   - 主判定必须可靠；collector 属于增强诊断，不得篡改主判定语义。
2. **参数化原子用例是主复用轴**
   - 优先建设原子规则模板，不优先建设高级组合模板。
3. **显式命名空间优于隐式覆盖**
   - 所有引用都应当可解析、可校验、可追踪。
4. **fail-fast 优先，但允许诊断能力降级**
   - 配置错误尽量在 load 阶段失败；诊断增强类错误允许降级，但必须显式记录。
5. **transport 保持窄接口，但执行契约必须更硬**
   - 不重新设计庞大 provider 框架，但要收紧命令执行与输出采集语义。
6. **YAML 继续作为主要扩展面**
   - 新业务规则优先通过 YAML 扩展，避免回退到按场景写 Python。

### 2.2 兼容性策略

本次采用 **有限破坏式升级**：

1. 保留 v2 的总体模型：suite / cases / collectors / EvidenceBundle；
2. 允许调整 `id`、`requires`、`collectors`、参数化声明与 suite 默认字段；
3. 通过清晰迁移规则，一次性收紧规则模型，避免长期兼容包袱累积。

---

## 3. 总体架构

建议将 `engineering/loop` 的长期稳定形态收敛为 4 层。

### 3.1 Schema / Composition 层

职责：
- suite 加载；
- include 解析；
- FQN 解析；
- 参数展开；
- 静态校验；
- requires 拓扑排序；
- suite 默认配置解析。

核心模块：
- `engineering/loop/core/python/loop_core/case_loader.py`

设计意图：
- 将“规则如何组织”与“规则如何执行”彻底拆开；
- 运行前尽可能消灭 schema、引用、命名与展开问题。

### 3.2 Execution Kernel 层

职责：
- 执行 case；
- 建立命令输出边界；
- 求值断言；
- 处理依赖短路；
- 触发 collector；
- 收敛运行期异常；
- 汇总 overall 结果。

核心模块：
- `engineering/loop/core/python/loop_core/executor.py`
- `engineering/loop/core/python/loop_core/collector.py`
- `engineering/loop/core/python/loop_core/runner.py`

设计意图：
- 保持一个场景无关的可靠执行内核；
- 不再回退到 per-scenario runner / workflow Python 层。

### 3.3 Evidence 层

职责：
- 统一落盘结构化结果；
- 输出 warning / runtime error / degraded / config issue；
- 生成人类可读 summary 与 AI 可消费 JSON。

核心模块：
- `engineering/loop/core/python/loop_core/models.py`
- `engineering/loop/core/python/loop_core/evidence.py`
- `engineering/loop/core/python/loop_core/report.py`

设计意图：
- EvidenceBundle 作为 LE 与 AI/人工分析之间的唯一可信出口。

### 3.4 Environment Adapter 层

职责：
- transport/provider 接入；
- device profile 读取；
- provider 能力映射；
- 环境差异收口。

核心模块：
- `engineering/loop/core/python/loop_core/transport.py`
- `engineering/loop/core/python/loop_core/config.py`
- `engineering/loop/connection/providers/*`

设计意图：
- 设备差异与连接差异不应渗入规则定义层。

---

## 4. 规则复用模型

### 4.1 FQN 命名模型

当前实现按裸 `case.id` 合并，存在 include 后静默覆盖风险：
- `engineering/loop/core/python/loop_core/case_loader.py:95`

为支持跨 suite 复用，采用如下模型：

#### 作者可写局部 id
例如：
- `shell_ready`
- `zygote_running`
- `boot_completed`

#### 系统内部 FQN
例如：
- `common.shell.shell_ready`
- `common.service.zygote_running`
- `system.boot.boot_completed`

#### 解析规则
1. suite 自身拥有命名空间语义；
2. loader 在加载时将局部 id 解析为 FQN；
3. `requires` / `on_fail.collectors` 支持：
   - 同作用域短名；
   - 跨 suite 必须显式 FQN；
4. collector 采用相同策略；
5. 解析后若 FQN 冲突，load 阶段直接失败。

#### 设计收益
1. 解决静默覆盖；
2. 兼顾 YAML 可读性与系统级唯一性；
3. 为参数化展开与公共 collector 库提供稳定引用模型。

### 4.2 参数化原子用例

参数化原子用例是规则复用主轴。

#### 目标
将高频系统规则沉淀为可实例化的原子模板，而不是复制相似 YAML。

#### 典型模板类型
- `service_running(service_name)`
- `prop_equals(key, value)`
- `path_exists(path)`
- `cmd_contains(command, expected)`
- `not_contains_in_output(command, value)`
- `prompt_visible(marker_group)`

#### 分层策略
- **L1：参数化原子 case** —— 主复用层；
- **L2：少量组合模板** —— 仅服务于高频系统基线；
- **L3：suite 编排层** —— 业务场景拼装与裁剪。

#### 明确限制
1. 不设计复杂 DSL；
2. 不将“组合模板”发展为隐式规则引擎；
3. 参数展开必须发生在 load 阶段，而非 execute 阶段。

### 4.3 公共 collector 库

collector 定位为失败诊断层，不参与主判定。

#### 分类
1. **公共 collector**
   - 可跨 suite、跨设备复用；
2. **suite 局部 collector**
   - 仅保留场景专属诊断。

#### 公共 collector 示例
- `common.collector.boot_log`
- `common.collector.init_log`
- `common.collector.crash_dump`
- `common.collector.audit_tail`
- `common.collector.service_snapshot`

#### 引用规则
1. 本地 collector 可写短名；
2. 公共 collector / 跨 suite collector 必须可解析为 FQN；
3. 引用不存在的 collector 不允许静默忽略；
4. 若某 collector 被标记为“增强型诊断”，允许降级为 warning，但必须记录到 EvidenceBundle。

### 4.4 设备差异与规则差异分离

为实现跨设备复用，必须严格区分：

#### 通用规则表达“验证什么”
例如：
- 某 service 是否 running；
- 某 prop 是否等于目标值；
- 某 prompt 是否可见；
- 某命令输出是否包含/不包含目标文本。

#### profile/参数表达“设备差异是什么”
例如：
- prompt markers；
- 默认 capture timeout / recent limit；
- 某设备特有服务名、路径、属性名；
- provider 连接参数。

设计要求：
- 规则模板不写死设备差异；
- suite 只做业务编排；
- profile/参数层负责跨设备适配。

---

## 5. 执行可靠性与运行契约

本章是本次设计的 **P0**，优先级高于参数化复用层。

### 5.1 输出采集边界

当前主要风险：
- `send_line()` 后直接 `capture_window()`；
- transport 可混入 recent buffer；
- 当前 case 可能读到历史输出。

相关代码：
- `engineering/loop/core/python/loop_core/executor.py:143`
- `engineering/loop/core/python/loop_core/collector.py:39`
- `engineering/loop/core/python/loop_core/transport.py:37`

#### 新契约要求
transport 至少需要提供等价于以下语义的能力：

```text
cursor = mark_output_boundary()
send_line(command)
output = capture_since(cursor, timeout, recent_limit)
```

或者：

```text
result = execute_command(command, timeout, recent_limit)
```

无论采用哪种 API 命名，必须满足以下语义：

1. 每个 case 只读取自己发送后的输出区间；
2. 每个 collector 只读取自己触发后的输出区间；
3. fixture/live transport 共享同一边界语义；
4. 不允许“整段 recent buffer + 新输出”的历史混合视图作为主执行结果。

#### 设计收益
1. `contains/regex/not_contains` 才具备可信语义；
2. `prompt_visible` 不再被历史 prompt 干扰；
3. collector 采到的证据更接近失败现场；
4. 采样范围更小，也有利于性能控制。

### 5.2 异常收敛模型

当前 `execute_suite()` / `run()` 主链路未完整收敛 transport / collector / 断言异常：
- `engineering/loop/core/python/loop_core/executor.py:138`
- `engineering/loop/core/python/loop_core/runner.py:55`
- `engineering/loop/core/python/loop_core/cli.py:115`

#### 错误分类

##### A. 配置错误
示例：
- FQN 冲突；
- `requires` 缺失；
- collector 引用不存在；
- assert 参数非法；
- 参数化展开后生成重复 case。

处理原则：
- load 阶段直接失败；
- 不进入 execute 阶段。

##### B. case 运行错误
示例：
- `send_line()` 失败；
- `capture` 超时 / transport 断开；
- 断言上下文构造失败；
- provider 返回结构不完整。

处理原则：
- 当前 case 标记为 `error`；
- 写入 `failure_reason` / `error_type` / `runtime_error`；
- bundle 仍需生成；
- 后续 case 是否继续由依赖关系决定，而不是让整次 run 崩掉。

##### C. collector 运行错误
示例：
- collector 命令发送失败；
- 采集超时；
- provider 在 collector 阶段断线。

处理原则：
- 不改变主判定；
- collector 结果标记为 `error` / `degraded`；
- bundle 中显式记录 warning / collector_error；
- 已采到的部分证据尽量保留。

### 5.3 dependency / skip / overall 收紧

当前 `requires` 指向不存在 case 时可能在执行期被 skip，且 suite 仍可能 PASS：
- `engineering/loop/core/python/loop_core/case_loader.py:144`
- `engineering/loop/core/python/loop_core/executor.py:116`

#### 新语义

##### load 阶段
1. `requires` 指向不存在 FQN：直接失败；
2. 依赖图存在环：直接失败；
3. critical case 引用链不完整：直接失败。

##### execute 阶段
skip 仅允许由真实运行结果传播，例如：
1. 前置 case fail；
2. 前置 case error；
3. 前置 case skipped。

##### overall 判定
- **critical case 若未正常完成执行，不允许 overall=PASS**。

可选状态表达：
- `PASS`
- `FAIL`
- `DEGRADED`

若当前实现暂不引入 `DEGRADED` 作为 summary 主状态，也必须保证：
- critical case 的 skip/error 至少会使 overall 变为非 PASS。

### 5.4 `prompt_visible / exit_code / timeout` 契约统一

#### `prompt_visible`
当前 executor 通过扫描输出文本判断 prompt：
- `engineering/loop/core/python/loop_core/executor.py:157`

设计要求：
- prompt 应优先成为 transport/provider 语义；
- executor 消费结构化结果，而不是长期依赖文本扫描。

允许的过渡方案：
- 过渡期保留文本扫描 fallback；
- 但主契约应逐步迁移到 transport 能力。

#### `exit_code_zero`
当前存在明确契约失配：
- 断言引擎支持：`engineering/loop/core/python/loop_core/assertion_engine.py:117`
- executor 未注入 exit code：`engineering/loop/core/python/loop_core/executor.py:167`

设计决议：
1. 保留 `exit_code_zero` 断言类型；
2. 仅在 provider 支持 exit code 时启用；
3. 在真正支持前，模板/文档不得将其作为通用规则主能力推荐；
4. EvidenceBundle 需能表达“exit code unavailable”是能力缺失而非系统行为失败。

#### `timeout`
当前 CLI 写死：
- `engineering/loop/core/python/loop_core/cli.py:116`

设计要求：
- `timeout` / `recent_limit` 采用三层优先级：
  1. case 显式覆盖；
  2. suite 默认；
  3. profile / CLI fallback。

这样才能支撑慢设备、长输出、collector 重命令与跨设备运行差异。

### 5.5 FixtureTransport 保真度提升

当前 `FixtureTransport` 的主要问题：
- 每次 `capture_window()` 只按 `t <= timeout_sec` 过滤；
- 缺少消费游标；
- 多 case / 多 collector 场景会重复读同一批数据。

相关代码：
- `engineering/loop/core/python/loop_core/transport.py:47`

设计要求：
1. fixture transport 必须支持与 live transport 等价的边界语义；
2. fixture 至少要支持多命令独立输出区间；
3. fixture 不必完全模拟 live 的所有实时特征，但必须保证命令-输出归属正确。

---

## 6. EvidenceBundle 增强设计

当前 `EvidenceBundle` 模型已有 `device_profile` 字段，但主链路未完整注入：
- `engineering/loop/core/python/loop_core/models.py:86`
- `engineering/loop/core/python/loop_core/executor.py:91`
- `engineering/loop/core/python/loop_core/runner.py:69`

### 6.1 目标

EvidenceBundle 不仅表达“结果是什么”，还表达“这个结果是否可信、是否有降级、上下文是什么”。

### 6.2 建议增强字段

#### 顶层增强字段
- `warnings[]`
- `runtime_errors[]`
- `config_degradations[]`
- `device_profile_summary`
- `execution_config`
  - `capture_timeout`
  - `recent_limit`
  - `provider_type`
  - `suite_defaults`

#### case 级增强字段
- `status: pass | fail | skipped | error`
- `error_type`
- `dependency_status`
- `execution_window` 摘要（可选）
- `assertion_capability`（如 `exit_code` 是否可用）

#### collector 级增强字段
- `status: ok | error | degraded`
- `partial: bool`
- `artifact_paths[]`（大输出外置时）

### 6.3 输出大小控制

为避免大日志把 bundle 本体压得过重：

1. bundle 本体保留 preview 与结构化摘要；
2. 长输出落 artifacts 文件；
3. bundle 中仅引用文件路径与摘要；
4. summary.txt 聚焦关键用例、关键错误、关键 collector 状态。

---

## 7. 静态校验设计

静态校验属于 loader/compile 阶段能力，是本次可靠性增强的重要组成部分。

### 7.1 必须 fail-fast 的错误

1. suite 顶层字段缺失；
2. case 缺失必填字段；
3. assert 类型未知；
4. assert 参数与类型不匹配；
5. FQN 冲突；
6. `requires` 指向不存在 case；
7. `collectors` 引用不存在且未标记为可降级；
8. `severity` 非法；
9. 参数化展开失败；
10. 参数化展开后产生重复 case。

### 7.2 可降级但必须记录的问题

1. 某增强型 collector 不可用；
2. 某 provider 不支持附加能力（如 exit code）；
3. 某可选 profile 参数缺失，已回退到默认值。

### 7.3 校验输出形式

建议 loader 输出“编译后 suite 结构 + 诊断列表”，用于：
- run 阶段直接消费；
- 后续 `gen-cases` / `loop_ctrl` 做前置校验；
- CI 中单独做 schema lint。

---

## 8. 分期实施策略

### 8.1 P0：执行可信度

目标：先让 `le run` 的结论可信。

包含：
1. 静态校验前置；
2. output capture 新契约；
3. 异常收敛；
4. overall 判定收紧；
5. EvidenceBundle 增强；
6. FixtureTransport 保真度增强。

P0 完成标志：
1. critical case 未执行完成时不再出现 overall=PASS；
2. transport/collector 异常时仍能稳定落 bundle；
3. 命令输出边界不再混入历史缓冲；
4. fixture/live 语义趋同。

### 8.2 P1：规则复用层

目标：让新增业务规则主要通过 YAML 组合完成。

包含：
1. FQN 命名模型落地；
2. 参数化原子用例；
3. 公共 collector 库；
4. suite 默认配置能力；
5. profile 参数注入规范。

P1 完成标志：
1. 通用系统规则不再依赖大规模复制 YAML；
2. `common` 规则可跨 suite、跨设备复用；
3. 业务场景主要通过 suite 编排完成。

### 8.3 P2：AI 闭环接口预留

目标：为 `gen-cases` / `deploy` / `loop_ctrl` 提供稳定基座。

包含：
1. `gen-cases` 的 suite schema 契约；
2. `deploy` 的输入输出与人工确认边界；
3. `loop_ctrl` 对 EvidenceBundle 的消费契约；
4. provider 能力矩阵与适配边界。

P2 完成标志：
1. AI 生成用例直接面向稳定 schema；
2. LE core 不再因为闭环接入而反复改底层数据结构；
3. 新 provider 接入不要求改动规则层。

---

## 9. 迁移顺序

建议按以下顺序推进，避免 schema、执行、provider 三件事同时大改：

### 阶段 1：先修执行与校验
- 静态校验；
- output capture 边界；
- 异常收敛；
- overall 语义修正；
- EvidenceBundle 增强。

### 阶段 2：引入 FQN 解析
- 在不大规模改 case 内容的前提下先建立统一引用模型。

### 阶段 3：模板化高频原子规则
- 优先从 `shell`、`service`、`prop`、`path` 等高频公共规则入手。

### 阶段 4：抽取公共 collector 库
- 减少 suite 内重复诊断命令块。

### 阶段 5：补高层组合模板与 AI 接口
- 在 P0/P1 稳定后再引入，避免隐式复杂度前置。

---

## 10. 非目标

本次设计明确不做以下事项：

1. 不重新引入 v1 风格规则引擎；
2. 不恢复 workflows/ 场景 Python 层；
3. 不把 collector 重新做成 Python 插件系统；
4. 不为所有场景设计复杂 DSL；
5. 不将 AI driver 内嵌到 loop_core；
6. 不在本设计中展开具体实现代码细节。

---

## 11. 风险与控制

### 11.1 主要风险

1. **一次同时修改 schema 与 provider，定位困难**；
2. **参数化设计过度，重新长成隐式规则引擎**；
3. **fixture/live 语义继续偏离，测试保真度不足**；
4. **bundle 字段膨胀过快，产物臃肿**；
5. **兼容窗口过长，历史 YAML 与新模型长期并存。**

### 11.2 控制策略

1. P0 先只做可靠性与契约，不急着铺开所有模板；
2. 参数化先做原子层，不先做高级组合层；
3. fixture 与 live 共同围绕输出边界契约演进；
4. bundle 只保留结构化摘要，大日志外置；
5. 通过有限破坏式升级尽快收敛到单一规则模型。

---

## 12. 结论

本次设计的核心不是继续给 `loop_core` 堆功能，而是做两件更关键的事情：

1. **把 loop_core 收敛成可信执行内核**；
2. **把通用系统规则沉淀成可参数化复用资产**。

最终目标形态是：

- 新业务规则主要通过 YAML 扩展；
- 通用系统规则可跨 suite、跨设备稳定复用；
- 执行失败、运行退化、诊断降级都能被结构化表达；
- 后续 AI 生成 / 执行 / 修复闭环无需再重做底层 schema。

这套设计应作为下一份 implementation plan 的输入基线。
