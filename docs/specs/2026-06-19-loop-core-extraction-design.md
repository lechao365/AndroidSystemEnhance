# loop core 抽取设计

> **日期**：2026-06-19
> **状态**：已确认
> **范围**：把 `boot_failure_debug/` 中已被验证的通用框架层抽到 `engineering/loop/core/python/loop_core/`，实现"框架做厚、业务做薄"，让后续新 workflow 能快速接入。

---

## 1. 背景

`WORKFLOW.md` 规划的 `core/` 通用抽象层此前为占位骨架，所有框架代码都塞在 `workflows/boot-failure-debug-loop/python/boot_failure_debug/` 下。随着 boot-failure workflow 复杂度增长，框架与业务的耦合开始变严重：数据模型、transport 抽象、observer、规则引擎、报告渲染等通用能力全部绑定在单一业务包里。后续要新增第二个 workflow（如 service restart diagnosis）时，这些通用层要么复制粘贴，要么不得不做 core 抽取。

本次在 boot-failure workflow 已验证的通用模式基础上，把确定会被复用的框架层一次性抽到 `core/`，为后续场景扩展打基础。

---

## 2. 目标

1. 创建 `engineering/loop/core/python/loop_core/` 作为 loop 通用框架包
2. 把已被 boot-failure 验证的通用层上提到 core
3. `Rp5SerialTransport` 迁到 provider 侧，让 connection 域自包含
4. boot_failure_debug 瘦身为纯业务层（规则定义 + 状态机 + 动作编排）
5. 全程自动化测试保障，无需人工/live 验证

---

## 3. 非目标

1. 不抽状态机框架（当前只有一个消费者，未验证通用性）
2. 不引入规则注册器/规则类型族（B1 弱约束即可）
3. 不新增业务功能，不改运行时行为
4. 不改造 harness 框架

---

## 4. 已确认决策清单

| # | 议题 | 决策 | 说明 |
|---|---|---|---|
| 1 | core 厚度 | B | 数据契约层 + 规则引擎框架；状态机框架留作遗留点 |
| 2 | 规则引擎粒度 | B1 | Protocol 弱约束；不引入注册器/规则类型族 |
| 3 | transport 归属 | T1 | core 放 BaseTransport + FixtureTransport；Rp5SerialTransport 迁到 provider |
| 4 | core 位置 | P1 | `engineering/loop/core/python/loop_core/`，与 connection/workflows 平级 |
| 5 | 配置层拆分 | C1 | core 提供 DeviceProfile + BaseWorkflowConfig + merge_profiles |
| 6 | 报告层泛化 | R1 | core 提供 write_report_bundle + render_summary(advice_map)，不假设 boot_cycle |
| 7 | observer 归属 | 进 core | capture_snapshot 参数化，不绑定具体 Config |
| 8 | actions 归属 | A1 | core 只提供 execute_actions 批量执行器；plan/execute 留业务层 |
| 9 | cycles 归属 | BC2 | 泛化为通用 cycle 工具；ObservedLine.boot_cycle_id → cycle_id |
| 10 | 迁移策略 | M2 | 分 3 批次：纯新增 → 切换消费 → 清理文档 |
| 11 | 测试策略 | 自动化优先 | 人工测试需给出明确理由 |

---

## 5. 设计原则

1. **框架做厚、业务做薄**：确定会被复用的层全部进 core，业务层只剩"规则定义 + 状态机 + 动作编排 + 配置"
2. **B1 弱约束优先**：core 提供 Protocol 和数据契约，不猜测业务实现方式
3. **core 不绑定 provider**：core 只有抽象 transport 和 fixture，具体 provider transport 留在 connection 域
4. **参数化优于 Config 依赖**：core 函数接收显式参数而非整个 Config 对象，避免反向依赖业务层
5. **自动化测试全量覆盖**：每个 core 模块都有独立单元测试，迁移后联合回归必须全绿

---

## 6. 重构后目录结构

```
engineering/loop/
    core/python/loop_core/              # 新增：通用框架
        __init__.py
        models.py                       # ObservedLine(cycle_id) / RuleMatch / ActionRecord / LoopAttempt
        transport.py                    # BaseTransport + FixtureTransport
        config.py                       # DeviceProfile / BaseWorkflowConfig / merge_profiles
        observer.py                     # ObservationSnapshot / capture_snapshot(参数化) / detect_prompt
        cycles.py                       # assign_cycles / count_cycles (泛化自 boot_cycles)
        rules.py                        # Rule Protocol / evaluate_rules / classify(priority)
        actions.py                      # execute_actions 批量执行器
        report.py                       # write_report_bundle / render_summary(advice_map)
        tests/                          # core 独立测试
            __init__.py
            test_models.py
            test_transport.py
            test_config.py
            test_observer.py
            test_cycles.py
            test_rules.py
            test_actions.py
            test_report.py
    connection/
        providers/rp5-serial/python/
            rp5_serial/
                client/automation.py
                transport.py            # 新增：Rp5SerialTransport (从 boot_failure_debug 迁入)
            tests/
                test_transport.py       # 新增：Rp5SerialTransport 合同测试（从 workflow 迁入）
        profiles/devices/rp5/default.json
        protocol/
    workflows/boot-failure-debug-loop/python/
        boot_failure_debug/
            __init__.py
            config.py                   # BootFailureConfig(BaseWorkflowConfig) + 专属阈值
            rules.py                    # 6 条 boot-failure 特有规则定义（实现 Rule 协议）
            actions.py                  # plan_actions / execute_action (业务特有分派)
            runner.py                   # BootFailureRunner (状态机，消费 loop_core)
            cli.py                      # CLI 入口
            # 不再有 models.py / transport.py / observer.py / boot_cycles.py / report.py
        tests/
            # 原有测试保留，调整 import
    profiles/boot-failure-debug/default.json
```

---

## 7. 各模块详细边界

### 7.1 loop_core/models.py

迁移并泛化：

- `ObservedLine`：`boot_cycle_id` → `cycle_id`
- `RuleMatch`：原样迁移
- `ActionRecord`：原样迁移（含 `output_lines` / `metadata`）
- `LoopAttempt`：新增 `extra_summary_lines: list[str]` 字段，供业务注入额外摘要行

### 7.2 loop_core/transport.py

- `BaseTransport`：原样迁移
- `FixtureTransport`：原样迁移

### 7.3 loop_core/config.py

拆分自现有 `boot_failure_debug/config.py`：

- `DeviceProfile` dataclass：`device_id / transport / prompt_markers / boot_markers / reboot_markers / panic_markers / hang_markers / line_ending`
- `BaseWorkflowConfig` dataclass：`observe_timeout_sec / capture_window_sec / recent_lines_limit / max_reassess_rounds`
- `merge_profiles(device_path, workflow_path, override) -> dict`：通用合并工具，只合并 key，不绑定具体类型

### 7.4 loop_core/observer.py

迁移并参数化：

- `ObservationSnapshot`：原样迁移
- `capture_snapshot(transport, timeout_sec, prompt_markers, recent_limit, quiet_window_sec=0.0) -> ObservationSnapshot`
- `detect_prompt(lines, markers) -> bool`

### 7.5 loop_core/cycles.py

泛化自 `boot_cycles.py`：

- `assign_cycles(lines, cycle_markers, field_name="cycle_id") -> list[ObservedLine]`
- `count_cycles(lines, field_name="cycle_id") -> int`

### 7.6 loop_core/rules.py

新增规则引擎框架（B1 弱约束）：

- `Rule` Protocol：`name: str` / `match(snapshot: ObservationSnapshot) -> RuleMatch`（规则在构造时自行持有所需配置，core 不假设 cfg 类型）
- `evaluate_rules(snapshot, rules: list[Rule], phase: str) -> list[RuleMatch]`
- `classify(matches: list[RuleMatch], priority: list[str]) -> str`

### 7.7 loop_core/actions.py

- `execute_actions(actions, transport, execute_fn) -> list[ActionRecord]`：纯循环执行器，逐个调用 `execute_fn(action, transport) -> ActionRecord`
- `execute_fn` 为 `Callable[[ActionRecord, BaseTransport], ActionRecord]`，由业务层注入具体分派逻辑，core 不硬编码分派表

### 7.8 loop_core/report.py

- `write_report_bundle(attempt, output_dir, snapshot_lines) -> dict`：原样迁移
- `render_summary(attempt, advice_map=None) -> str`：渲染通用字段（最终分类 / 结果 / 命中规则 / 执行动作 / 关键证据 / L1采样），不假设 boot_cycle；通过 `attempt.extra_summary_lines` 追加业务特有行

### 7.9 rp5_serial/transport.py（provider 侧新增）

- `Rp5SerialTransport(BaseTransport)`：从 `boot_failure_debug/transport.py` 迁入
- 实现 `BaseTransport` 接口，包装 `AutomationClient`

### 7.10 boot_failure_debug/（业务层瘦身）

保留：

- `config.py`：`BootFailureConfig(BaseWorkflowConfig)` + boot-failure 专属阈值（`quiet_window_sec / prompt_wait_sec / reboot_loop_threshold / l1_commands / l2_actions`）+ `load_profiles` 调用 `loop_core.merge_profiles`
- `rules.py`：6 条 boot-failure 特有规则，实现 `loop_core.rules.Rule` 协议
- `actions.py`：`plan_actions` / `execute_action`（业务特有分派逻辑）
- `runner.py`：`BootFailureRunner`，消费 loop_core，保留状态机
- `cli.py`：CLI 入口

删除（已迁移到 core 或 provider）：

- `models.py`
- `transport.py`
- `observer.py`
- `boot_cycles.py`
- `report.py`

---

## 8. 迁移批次

### 批次 1：纯新增 core（不破坏现状）

- 创建 `loop_core/` 包及全部模块
- 迁移通用代码（含 `ObservedLine.boot_cycle_id` → `cycle_id` 的字段改名，但 core 内部自洽）
- 为每个 core 模块编写独立单元测试
- boot_failure_debug 暂时不动，保持现有测试全绿
- **自动化测试**：core 每个模块都有独立测试

### 批次 2：切换消费 + provider 迁移

- boot_failure_debug 改为 `from loop_core import ...`
- 删除业务层中已迁移的重复代码
- `Rp5SerialTransport` 迁到 `rp5_serial/transport.py`
- `boot_failure_debug/transport.py` 的 Rp5SerialTransport 合同测试迁到 provider 侧
- 全量切换 import 路径
- **自动化测试**：联合回归（provider + core + workflow）必须全绿

### 批次 3：清理 + 文档

- 更新 `WORKFLOW.md`：标记 core 已实现 + 记录遗留点
- 更新 README / 协议文档（如需要）
- 删除残留废弃文件
- **自动化测试**：最终联合回归确认

---

## 9. 测试策略

### 9.1 自动化测试（覆盖全部）

- core 每个模块的独立单元测试
- boot_failure_debug 切换 import 后的回归测试（现有测试全部通过）
- provider 侧 Rp5SerialTransport 迁移后的合同测试
- 联合回归（provider + core + workflow）

### 9.2 人工测试

**本次重构不需要人工/live 真机测试。** 理由：

1. 本次是纯结构性重构，不新增业务功能，不改运行时行为
2. transport 合同接口不变，live 行为由现有 mock 测试充分覆盖
3. live 真机验证已在上一轮 shell foundation fix 中完成，本次不涉及 provider 协议变更
4. 唯一需要人工确认的是"实际 live 运行仍正常"，但这属于回归保障，不阻塞重构合入

---

## 10. 成功标准

1. `loop_core/` 包存在且包含全部通用模块
2. 每个 core 模块有独立单元测试且通过
3. boot_failure_debug 只保留 config/rules/actions/runner/cli
4. `Rp5SerialTransport` 位于 `rp5_serial/transport.py`
5. 联合回归（provider + core + workflow）全绿
6. `WORKFLOW.md` 更新，标记 core 已实现 + 记录遗留点

---

## 11. 遗留点（写入 WORKFLOW.md）

1. **状态机框架（C/D 方案）**：当前 runner 状态机留在业务层。当第二个 workflow 出现时，评估是否将 `PREPARE → ATTACH → OBSERVE → CLASSIFY → COLLECT_EVIDENCE → REASSESS → EXIT` 抽为 `BaseWorkflowRunner`。
2. **规则引擎升级（B2/B3）**：当前为 B1 弱约束。若多个 workflow 出现共性规则模式（如 MarkerRule），可升级为声明式规则类型族。
3. **L1 采样执行器（A3）**：当前 L1 采样逻辑在 runner 私有方法中。若第二个 workflow 也需要类似采样，可上提到 core。
4. **quiet_for_sec live 计算**：上一轮 review 发现 live transport 下 quiet_for_sec 恒为 0.0，影响 kernel_boot_hang 分类准确性。
