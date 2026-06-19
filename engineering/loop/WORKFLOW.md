---
name: loop-engineering
description: loop engineering 总体工作流
---

# Loop Engineering Workflow

## 目标

由 AI 接管开发板，稳定执行「观察 → 分类 → 采样 → 诊断 → 报告」的调试闭环：

1. **观察**：通过连接层稳定接管设备输出（串口/ADB）。
2. **分类**：基于规则识别故障类型（无输出 / kernel panic / boot hang / 反复重启 等）。
3. **采样**：在只读或低风险动作范围内采集证据。
4. **诊断**：汇总证据给出分类与建议。
5. **报告**：输出人类可读与机器可读的诊断报告。

## 当前阶段

- `core/`：**已实现**（通用框架层，221 个自动化测试覆盖）
- `connection/providers/rp5-serial/`：已实现（Windows Host + WSL2 Client + AutomationClient 双通道）
- `workflows/boot-failure-debug-loop/`：已实现 v1（消费 loop_core，状态机闭环）
- `profiles/`：已实现（device profile + workflow profile + override 合并）

## 分层职责

- **core/**：loop 通用抽象，不绑定具体 provider。提供数据模型、transport 抽象、fixture 回放、观察器、cycle 切分、规则引擎框架、动作批量执行器、报告渲染。
- **connection/**：连接域，承载协议契约、provider profile、具体 provider 实现。provider 自包含 transport 适配层。
- **workflows/**：业务闭环流程，消费 core 通用框架 + connection provider。只保留业务特有的规则定义、状态机、动作编排、配置。
- **profiles/**：设备级/场景级配置。

## core 模块清单

| 模块 | 职责 |
|------|------|
| `models.py` | ObservedLine(cycle_id) / RuleMatch / ActionRecord / LoopAttempt |
| `transport.py` | BaseTransport 抽象 + FixtureTransport 回放 |
| `config.py` | DeviceProfile / BaseWorkflowConfig / merge_profiles |
| `observer.py` | capture_snapshot(参数化) / detect_prompt |
| `cycles.py` | assign_cycles / count_cycles |
| `rules.py` | Rule Protocol / evaluate_rules / classify(priority) |
| `actions.py` | execute_actions(批量执行器，execute_fn 注入) |
| `report.py` | write_report_bundle / render_summary(advice_map) |

## 扩展新 workflow 的步骤

1. 在 `workflows/` 下创建新 workflow 目录
2. 继承 `loop_core.config.BaseWorkflowConfig` 添加专属阈值
3. 实现规则类（实现 `loop_core.rules.Rule` 协议）
4. 编写状态机 runner（消费 loop_core observer/cycles/rules/report）
5. 编写动作规划与执行（注入 execute_fn）
6. 选择 provider transport（如 `rp5_serial.transport.Rp5SerialTransport`）

## 遗留点

以下能力当前未抽到 core，待第二个 workflow 出现时评估：

1. **状态机框架（C/D 方案）**：当前 runner 状态机留在各业务层。当多个 workflow 共享相同状态流转模式时，评估是否将 `PREPARE → ATTACH → OBSERVE → CLASSIFY → COLLECT_EVIDENCE → REASSESS → EXIT` 抽为 `BaseWorkflowRunner`。
2. **规则引擎升级（B2/B3）**：当前为 B1 弱约束（Rule Protocol）。若多个 workflow 出现共性规则模式（如 MarkerRule / SilenceRule / CycleRule），可升级为声明式规则类型族。
3. **L1 采样执行器（A3）**：当前 L1 采样逻辑在 runner 私有方法中。若第二个 workflow 也需要类似采样，可上提到 core。
4. **quiet_for_sec live 计算**：live transport 下 ObservedLine.t 使用 monotonic 基准，导致 observer 的 quiet_for_sec 计算恒为 0.0，影响 kernel_boot_hang 分类准确性。需将 live transport 的时间戳改为相对偏移。
5. **transport 合同扩展**：当前 wait_for_pattern 在 live 模式下轮询 recent buffer 可能产生较高 TCP 请求频率（~10 req/s），后续可优化为单次请求。

## 与 harness 的关系

loop bash 入口复用 harness observability，但不依赖 harness 业务 workflow。详见 `engineering/loop/README.md`。
