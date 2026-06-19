# loop core 抽取实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 boot_failure_debug 中已被验证的通用框架层抽到 loop_core，Rp5SerialTransport 迁到 provider，业务层瘦身，全程自动化测试保障。

**Architecture:** 三批次连续执行：批次1 纯新增 core（不破坏现状）→ 批次2 切换消费 + provider 迁移 → 批次3 清理 + 文档。

**Tech Stack:** Python 3、pytest、dataclasses、typing.Protocol

**Spec:** `docs/specs/2026-06-19-loop-core-extraction-design.md`

---

## 关键约束

1. 三批次连续执行，遇到错误自行修复
2. 全程自动化测试，无需人工/live 验证
3. PYTHONPATH 三入口：`core/python` + `connection/providers/rp5-serial/python` + `workflows/boot-failure-debug-loop/python`
4. 联合回归命令：
   ```
   PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/workflows/boot-failure-debug-loop/python" pytest engineering/loop/core/python/tests engineering/loop/connection/providers/rp5-serial/python/tests engineering/loop/workflows/boot-failure-debug-loop/python/tests -q
   ```

---

## 批次 1：纯新增 loop_core（不破坏现状）

**Files:**
- Create: `engineering/loop/core/python/loop_core/__init__.py`
- Create: `engineering/loop/core/python/loop_core/models.py`
- Create: `engineering/loop/core/python/loop_core/transport.py`
- Create: `engineering/loop/core/python/loop_core/config.py`
- Create: `engineering/loop/core/python/loop_core/observer.py`
- Create: `engineering/loop/core/python/loop_core/cycles.py`
- Create: `engineering/loop/core/python/loop_core/rules.py`
- Create: `engineering/loop/core/python/loop_core/actions.py`
- Create: `engineering/loop/core/python/loop_core/report.py`
- Create: `engineering/loop/core/python/tests/__init__.py`（pkgutil.extend_path 命名空间包）
- Create: `engineering/loop/core/python/tests/test_models.py`
- Create: `engineering/loop/core/python/tests/test_transport.py`
- Create: `engineering/loop/core/python/tests/test_config.py`
- Create: `engineering/loop/core/python/tests/test_observer.py`
- Create: `engineering/loop/core/python/tests/test_cycles.py`
- Create: `engineering/loop/core/python/tests/test_rules.py`
- Create: `engineering/loop/core/python/tests/test_actions.py`
- Create: `engineering/loop/core/python/tests/test_report.py`

**核心规则：** boot_failure_debug 不动，只是新增 core 包。现有测试必须保持全绿。

### 核心模块设计

#### models.py
- `ObservedLine`：`t: float / text: str / cycle_id: int = 0`（注意：boot_cycle_id → cycle_id）
- `RuleMatch`：原样
- `ActionRecord`：原样（含 output_lines / metadata）
- `LoopAttempt`：新增 `extra_summary_lines: list[str] = field(default_factory=list)`

#### transport.py
- `BaseTransport(ABC)`：原样
- `FixtureTransport`：原样

#### config.py
- `DeviceProfile` dataclass
- `BaseWorkflowConfig` dataclass
- `merge_profiles(device_path, workflow_path, override=None) -> dict`

#### observer.py
- `ObservationSnapshot` dataclass
- `capture_snapshot(transport, timeout_sec, prompt_markers, recent_limit, quiet_window_sec=0.0, cycle_markers=None)`
- `detect_prompt(texts, markers) -> str | None`

#### cycles.py
- `assign_cycles(lines, cycle_markers, field_name="cycle_id") -> list[ObservedLine]`
- `count_cycles(lines, field_name="cycle_id") -> int`

#### rules.py
- `Rule` Protocol：`name: str` / `match(snapshot: ObservationSnapshot) -> RuleMatch`
- `evaluate_rules(snapshot, rules: list[Rule], phase: str) -> list[RuleMatch]`
- `classify(matches, priority: list[str]) -> str`

#### actions.py
- `ExecuteFn = Callable[[ActionRecord, BaseTransport], ActionRecord]`
- `execute_actions(actions, transport, execute_fn) -> list[ActionRecord]`

#### report.py
- `write_report_bundle(attempt, output_dir, snapshot_lines=None) -> dict`
- `render_summary(attempt, advice_map=None) -> str`（不假设 boot_cycle，从 attempt.extra_summary_lines 追加）

- [ ] **Step 1: 创建 loop_core 包结构 + __init__.py**
- [ ] **Step 2: 实现 models.py + test_models.py，TDD**
- [ ] **Step 3: 实现 transport.py + test_transport.py，TDD**
- [ ] **Step 4: 实现 config.py + test_config.py，TDD**
- [ ] **Step 5: 实现 cycles.py + test_cycles.py，TDD**
- [ ] **Step 6: 实现 observer.py + test_observer.py，TDD**
- [ ] **Step 7: 实现 rules.py + test_rules.py，TDD**
- [ ] **Step 8: 实现 actions.py + test_actions.py，TDD**
- [ ] **Step 9: 实现 report.py + test_report.py，TDD**
- [ ] **Step 10: 跑 core 全量测试，确认全绿**
- [ ] **Step 11: 跑现有 boot_failure_debug 测试，确认不回归**
- [ ] **Step 12: 跑 provider 测试，确认不回归**
- [ ] **Step 13: 提交批次 1**

---

## 批次 2：切换消费 + provider 迁移

**Files:**
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py`（Rp5SerialTransport 迁入）
- Create: `engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py`（从 workflow 迁入合同测试）
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/runner.py`（import loop_core）
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/cli.py`（import loop_core + provider transport）
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/rules.py`（实现 Rule 协议 + import loop_core）
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/actions.py`（import loop_core models）
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/config.py`（继承 BaseWorkflowConfig）
- Modify: 所有 workflow tests 的 import
- Delete: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/models.py`
- Delete: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/transport.py`
- Delete: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/observer.py`
- Delete: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/boot_cycles.py`
- Delete: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/report.py`

- [ ] **Step 1: Rp5SerialTransport 迁到 provider 侧 + 合同测试**
- [ ] **Step 2: boot_failure_debug/config.py 改为继承 BaseWorkflowConfig**
- [ ] **Step 3: boot_failure_debug/rules.py 改为实现 Rule 协议 + import loop_core**
- [ ] **Step 4: boot_failure_debug/actions.py 改为 import loop_core.models**
- [ ] **Step 5: boot_failure_debug/runner.py 改为 import loop_core**
- [ ] **Step 6: boot_failure_debug/cli.py 改为 import loop_core + provider transport**
- [ ] **Step 7: 全量调整 workflow tests 的 import**
- [ ] **Step 8: 删除已迁移的业务层文件**
- [ ] **Step 9: 联合回归（provider + core + workflow）全绿**
- [ ] **Step 10: 提交批次 2**

---

## 批次 3：清理 + 文档

**Files:**
- Modify: `engineering/loop/WORKFLOW.md`
- Modify: `engineering/loop/README.md`（如需要）
- Modify: `engineering/loop/core/python/loop_core/__init__.py`（如需 public API）
- 任何残留废弃文件清理

- [ ] **Step 1: 更新 WORKFLOW.md（标记 core 已实现 + 遗留点）**
- [ ] **Step 2: 更新 README（如需要）**
- [ ] **Step 3: 最终联合回归**
- [ ] **Step 4: 提交批次 3**

---

## Self-Review Checklist

- Spec coverage:
  - core 9 个模块全部创建 → 批次 1
  - boot_failure_debug 瘦身 → 批次 2
  - Rp5SerialTransport 迁 provider → 批次 2
  - WORKFLOW.md 遗留点 → 批次 3
- 类型一致性：
  - ObservedLine.cycle_id（不是 boot_cycle_id）
  - LoopAttempt.extra_summary_lines（新增字段）
  - Rule Protocol / evaluate_rules / classify 签名
  - capture_snapshot 参数化签名
