# Live Reboot 诊断闭环 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 AI 主导的 live reboot 启动诊断半闭环——case 级 `action: reboot` 声明 + transport 跨重启 `reboot_and_wait` + 实测 boot_markers + kmsg collector + AI 诊断报告契约。

**Architecture:** 三层改动——(1) provider 层 `Rp5SerialTransport.reboot_and_wait` 实现"发 reboot→stream 等 boot_markers→getprop 验证"三级渐进判定；(2) core 层 TestCase 加 `action` 字段，executor 对 action case 走特殊分支调 transport.reboot_and_wait；(3) data 层 boot-success.yaml 加 trigger_reboot case + shell.yaml 加 kmsg collector。所有改动向后兼容：旧 case YAML、FixtureTransport、现有 4 collector 不受影响。

**Tech Stack:** Python 3.10+（dataclass / typing），PyYAML，pytest，bash（le.sh wrapper），rp5-serial host-client（TCP JSON Lines 协议）。

**Spec:** `docs/specs/2026-06-20-live-reboot-diagnosis-loop-design.md`

**测试命令**（所有 Python 单测统一入口）：
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib
```

**静态校验命令**：
```bash
bash engineering/harness/scripts/validate_harness_docs.sh
```

---

## Task 1: DeviceProfile 字段语义激活 + 设备 profile 填实测 markers

**Files:**
- Modify: `engineering/loop/connection/profiles/devices/rp5/default.json`（全文重写）
- Test: `engineering/loop/core/python/tests/test_config.py`（已有，追加断言）

- [ ] **Step 1: 写失败测试——DeviceProfile 含 boot_markers/panic_markers 字段**

追加到 `engineering/loop/core/python/tests/test_config.py` 末尾：

```python
def test_device_profile_supports_boot_and_panic_markers():
    """boot_markers / panic_markers 字段可独立配置，默认空 list。"""
    from loop_core.config import DeviceProfile

    profile = DeviceProfile(
        device_id="rp5",
        boot_markers=["Booting Linux on physical CPU", "init: ... started service 'zygote' has pid"],
        panic_markers=["Kernel panic", "BUG:", "Internal error"],
    )
    assert profile.boot_markers == [
        "Booting Linux on physical CPU",
        "init: ... started service 'zygote' has pid",
    ]
    assert profile.panic_markers == ["Kernel panic", "BUG:", "Internal error"]


def test_device_profile_defaults_empty_markers():
    """未配置时 boot_markers / panic_markers 默认空 list。"""
    from loop_core.config import DeviceProfile

    profile = DeviceProfile(device_id="x")
    assert profile.boot_markers == []
    assert profile.panic_markers == []
```

- [ ] **Step 2: 跑测试验证通过（字段已存在，只是未被消费）**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_config.py -v
```
Expected: PASS（`config.py:37-39` 已有字段定义）

- [ ] **Step 3: 改设备 profile 填实测 markers**

全文重写 `engineering/loop/connection/profiles/devices/rp5/default.json`：

```json
{
  "device_id": "rp5",
  "transport": "serial",
  "prompt_markers": ["console:/ $", "console:/ #", "localhost:/ #", "# ", "$ "],
  "boot_markers": [
    "Booting Linux on physical CPU",
    "init: ... started service 'zygote' has pid"
  ],
  "reboot_markers": [
    "reboot: Restarting system",
    "U-Boot",
    "Booting Linux on physical CPU"
  ],
  "panic_markers": ["Kernel panic", "BUG:", "Internal error"],
  "line_ending": "\n"
}
```

- [ ] **Step 4: 跑全量测试确认无回归**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib 2>&1 | tail -20
```
Expected: 全绿（现有用例不消费新字段）

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/connection/profiles/devices/rp5/default.json \
        engineering/loop/core/python/tests/test_config.py
git commit -m "feat(loop): 激活 DeviceProfile boot_markers/panic_markers + 填实测 markers"
```

---

## Task 2: 新增诊断报告模板

**Files:**
- Create: `engineering/harness/templates/diagnosis-report-template.md`
- Modify: `engineering/harness/templates/README.md`（登记新模板）

- [ ] **Step 1: 创建诊断报告模板**

创建 `engineering/harness/templates/diagnosis-report-template.md`：

```markdown
# Boot 诊断报告模板

> 本模板约束 AI（opencode）在收到 EvidenceBundle 后产出的诊断报告格式。
> 报告路径：`engineering/output/runs/<run-id>/diagnosis-report.md`（与 EvidenceBundle 同目录）。

## 报告结构

每份诊断报告必须包含以下 6 节，顺序固定：

```markdown
# Boot 诊断报告 - <run-id>

## 1. 结论
- 整体状态：FAIL/PASS
- 根因假设：<一句话>

## 2. 证据链
| 阶段 | 证据 | 引用 |
|------|------|------|
| reboot | <status, 耗时, stage_reached> | EvidenceBundle.cases[trigger_reboot] |
| zygote | <status, 输出预览> | EvidenceBundle.cases[zygote_running] |
| kmsg | <异常片段> | collector.kmsg output |

## 3. 根因分析
<详细分析，引用证据>

## 4. 修复建议（人工执行）
- 改动点 1：workspace/<路径>:<函数> → <建议>
- 改动点 2：...

## 5. 建议新增 case（人工 review 后加入 boot-success.yaml）
<可选，无建议则写"本次无新 case 建议">
```yaml
- id: <建议的 case id>
  command: "<建议的命令>"
  assert: {type: contains, value: "<期望值>"}
  ...
```

## 6. 循环终止建议
- 已 PASS → 无需继续
- FAIL 根因明确 → 建议范围 B 自动改码（需用户确认）
- FAIL 根因不明确 → 建议人工介入
```

## AI 行为约束

1. AI 读 EvidenceBundle 后**必须**按此模板产出报告，不得自创格式
2. 报告路径**必须**与 EvidenceBundle 同目录
3. 第 4 节修复建议**必须**具体到 workspace 文件路径和函数名，禁止笼统"检查 xx 模块"
4. 第 5 节 YAML 建议**必须**完整可粘贴（含 id/command/assert/severity/on_fail）
5. AI **不自动修改** boot-success.yaml，只给建议（G2 决策）

## 字段说明

| 字段 | 说明 |
|------|------|
| `<run-id>` | EvidenceBundle 的 `bundle_id`（如 `eb-a23b8614`） |
| `stage_reached` | reboot_and_wait 达到的阶段：`l1_boot_start` / `l2_init_ready` / `l3_verified` / `none` |
| `<证据引用>` | 指向 EvidenceBundle JSON 的路径，如 `cases[0].output_preview` |
```

- [ ] **Step 2: 在 templates/README.md 登记新模板**

读取 `engineering/harness/templates/README.md`，在文件清单中追加：

```markdown
- [`diagnosis-report-template.md`](./diagnosis-report-template.md) — Loop boot 诊断报告模板，约束 AI 收到 EvidenceBundle 后产出的 markdown 报告格式（结论/证据链/根因/修复建议/建议新增 case/循环终止建议）。
```

> 注意：具体插入位置需先读 README.md 看现有清单格式，按字母序或逻辑分组插入。

- [ ] **Step 3: 跑静态校验确认 README 一致性**

Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
Expected: PASS（新模板文件已在 README 登记，无漏登记告警）

- [ ] **Step 4: 提交**

```bash
git add engineering/harness/templates/diagnosis-report-template.md \
        engineering/harness/templates/README.md
git commit -m "feat(harness): 新增 boot 诊断报告模板"
```

---

## Task 3: TestCase model 加 action 字段

**Files:**
- Modify: `engineering/loop/core/python/loop_core/case_loader.py:16-43`（TestCase dataclass）
- Modify: `engineering/loop/core/python/loop_core/models.py:29-64`（TestCaseResult 无需改，已有通用字段）
- Test: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 写失败测试——TestCase 含 action 字段**

追加到 `engineering/loop/core/python/tests/test_case_loader.py`：

```python
def test_test_case_has_action_field():
    """TestCase dataclass 含 action 字段，默认空字符串。"""
    from loop_core.case_loader import TestCase

    case = TestCase(id="x", suite="s", command="", assert_spec={})
    assert case.action == ""


def test_test_case_action_can_be_set():
    """TestCase.action 可被显式设置为 'reboot'。"""
    from loop_core.case_loader import TestCase

    case = TestCase(id="x", suite="s", command="", assert_spec={}, action="reboot")
    assert case.action == "reboot"
```

- [ ] **Step 2: 跑测试验证失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py::test_test_case_has_action_field -v
```
Expected: FAIL with `unexpected keyword argument 'action'` 或 AttributeError

- [ ] **Step 3: 改 TestCase dataclass 加 action 字段**

编辑 `engineering/loop/core/python/loop_core/case_loader.py:16-43`，在 TestCase dataclass 加 `action` 字段（放在 `command` 之后，`assert_spec` 之前）：

```python
@dataclass
class TestCase:
    """加载后的用例定义。

    Attributes:
        id: 用例标识（suite 内唯一）
        suite: 所属 suite 名
        fqn: 全限定名 `<suite>.<id>`，作为内部唯一引用键（向后兼容默认空）
        command: 执行的命令（空字符串表示仅探测 prompt；与 action 互斥）
        action: 动作类型（如 "reboot"；与 command 互斥，默认空字符串表示命令模式）
        assert_spec: 断言规格 dict {type, value/pattern}
        severity: critical（fail 阻断）/ warn（仅记录）
        requires: 前置依赖用例 FQN 列表
        on_fail: 失败时动作 {collectors: [FQN,...]}
        tags: 用例标签
        description: 用例描述
    """

    id: str
    suite: str
    command: str
    action: str = ""
    assert_spec: dict
    severity: str = "critical"
    requires: list[str] = field(default_factory=list)
    on_fail: dict = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    description: str = ""
    fqn: str = ""
```

- [ ] **Step 4: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py::test_test_case_has_action_field \
  engineering/loop/core/python/tests/test_case_loader.py::test_test_case_action_can_be_set -v
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/core/python/loop_core/case_loader.py \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(loop-core): TestCase 加 action 字段（默认空，与 command 互斥）"
```

---

## Task 4: case_loader 解析 + 校验 action 字段

**Files:**
- Modify: `engineering/loop/core/python/loop_core/case_loader.py:175-187`（`_parse_case`）+ `:266-274`（`_validate_case_definition`）+ `:253-263`（断言类型常量集）
- Test: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 写失败测试——_parse_case 解析 action 字段**

追加到 `engineering/loop/core/python/tests/test_case_loader.py`：

```python
def test_parse_case_extracts_action():
    """_parse_case 从 YAML 节点提取 action 字段。"""
    from loop_core.case_loader import _parse_case

    defn = {"id": "trigger_reboot", "action": "reboot", "assert": {}}
    case = _parse_case(defn, "system.boot")
    assert case.action == "reboot"
    assert case.command == ""


def test_parse_case_action_defaults_empty():
    """无 action 字段时默认空字符串（命令模式）。"""
    from loop_core.case_loader import _parse_case

    defn = {"id": "x", "command": "ls", "assert": {"type": "contains", "value": "x"}}
    case = _parse_case(defn, "s")
    assert case.action == ""


def test_validate_case_rejects_action_and_command_both_present():
    """action 与 command 互斥：两者都有则报错。"""
    from loop_core.case_loader import _validate_case_definition

    import pytest
    with pytest.raises(ValueError, match="action and command are mutually exclusive"):
        _validate_case_definition({
            "id": "x",
            "action": "reboot",
            "command": "ls",
            "assert": {"type": "contains", "value": "y"},
        })


def test_validate_case_rejects_unknown_action():
    """action 只允许已知值（当前只有 reboot）。"""
    from loop_core.case_loader import _validate_case_definition

    import pytest
    with pytest.raises(ValueError, match="unknown action"):
        _validate_case_definition({
            "id": "x",
            "action": "sleep",
            "assert": {"type": "contains", "value": "y"},
        })


def test_validate_case_action_reboot_does_not_require_assert_value():
    """action: reboot 的 case 不需要 assert value（动作型，不产断言结果）。"""
    from loop_core.case_loader import _validate_case_definition

    # 不抛异常即通过
    _validate_case_definition({"id": "x", "action": "reboot", "assert": {}})


def test_validate_case_still_accepts_command_only():
    """纯命令模式（无 action）仍被接受（向后兼容）。"""
    from loop_core.case_loader import _validate_case_definition

    _validate_case_definition({
        "id": "x",
        "command": "ls",
        "assert": {"type": "contains", "value": "y"},
    })
```

- [ ] **Step 2: 跑测试验证失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py \
  -v -k "action" 2>&1 | tail -20
```
Expected: FAIL（`_parse_case` 未提取 action；`_validate_case_definition` 无互斥校验）

- [ ] **Step 3: 改 _parse_case 提取 action 字段**

编辑 `engineering/loop/core/python/loop_core/case_loader.py:175-187`，`_parse_case` 加 action 提取：

```python
def _parse_case(defn: dict, suite: str) -> TestCase:
    """从 YAML 节点构建 TestCase。fqn 在 load_suite 中统一填充。"""
    return TestCase(
        id=defn["id"],
        suite=suite,
        command=defn.get("command", ""),
        action=defn.get("action", ""),
        assert_spec=defn.get("assert", {}),
        severity=defn.get("severity", "critical"),
        requires=list(defn.get("requires", [])),
        on_fail=dict(defn.get("on_fail", {})),
        tags=list(defn.get("tags", [])),
        description=defn.get("description", ""),
    )
```

- [ ] **Step 4: 改 _validate_case_definition 加 action 校验**

编辑 `engineering/loop/core/python/loop_core/case_loader.py:266-274`，`_validate_case_definition` 加互斥校验 + action 合法值校验。

在文件顶部常量区（约 `:253-263`）加：

```python
# 允许的 action 取值（当前仅 reboot）
_VALID_ACTIONS = {"reboot"}
```

替换 `_validate_case_definition`（`:266-274`）：

```python
def _validate_case_definition(defn: dict) -> None:
    """静态校验单条用例定义：必需键、severity 合法性、action/command 互斥。"""
    required_keys = {"id", "assert"}
    missing = required_keys - set(defn)
    if missing:
        raise ValueError(f"case missing required keys: {sorted(missing)}")
    severity = defn.get("severity", "critical")
    if severity not in _VALID_SEVERITIES:
        raise ValueError(f"invalid severity: {severity}")
    # action 与 command 互斥
    has_action = bool(defn.get("action"))
    has_command = bool(defn.get("command"))
    if has_action and has_command:
        raise ValueError(
            f"case '{defn.get('id')}': action and command are mutually exclusive"
        )
    # action 值合法性
    action = defn.get("action", "")
    if action and action not in _VALID_ACTIONS:
        raise ValueError(f"case '{defn.get('id')}': unknown action: {action}")
```

- [ ] **Step 5: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py -v -k "action" 2>&1 | tail -20
```
Expected: PASS

- [ ] **Step 6: 跑全量 case_loader 测试确认无回归**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py -v 2>&1 | tail -20
```
Expected: 全绿（旧用例无 action 字段，默认空字符串走命令模式）

- [ ] **Step 7: 提交**

```bash
git add engineering/loop/core/python/loop_core/case_loader.py \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(loop-core): case_loader 解析 + 校验 action 字段（与 command 互斥）"
```

---

## Task 5: case_loader 对 action case 跳过断言规格校验

**Files:**
- Modify: `engineering/loop/core/python/loop_core/case_loader.py:141-143`（断言规格校验跳过 action case）
- Modify: `engineering/loop/core/python/loop_core/case_loader.py:338-346`（`_validate_assertion_shape` 兼容 action case）
- Test: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 写失败测试——action case 的空 assert 不报错**

追加到 `engineering/loop/core/python/tests/test_case_loader.py`：

```python
def test_load_suite_accepts_action_case_with_empty_assert(tmp_path):
    """action: reboot 的 case，assert 为空 dict 也能正常加载。"""
    from loop_core.case_loader import load_suite

    suite_file = tmp_path / "boot-test.yaml"
    suite_file.write_text(
        """
suite: system.boot_test
version: 1
cases:
  - id: trigger_reboot
    action: reboot
    description: "触发重启"
    assert: {}
  - id: boot_ok
    command: "getprop sys.boot_completed"
    assert: {type: contains, value: "1"}
    requires: [trigger_reboot]
""",
        encoding="utf-8",
    )
    suite = load_suite(str(suite_file), [str(tmp_path)])
    # 拓扑序：trigger_reboot 在前
    assert suite.cases[0].id == "trigger_reboot"
    assert suite.cases[0].action == "reboot"
    assert suite.cases[1].id == "boot_ok"
    assert suite.cases[1].requires == ["system.boot_test.trigger_reboot"]


def test_load_suite_action_case_no_assert_value_still_validates():
    """action case 的 assert 不含 type，_validate_assertion_shape 应跳过。"""
    from loop_core.case_loader import _validate_assertion_shape

    # action case 的 assert 是空 dict，无 type，不应抛异常
    _validate_assertion_shape({})
```

- [ ] **Step 2: 跑测试验证失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py \
  -v -k "action_case" 2>&1 | tail -20
```
Expected: FAIL（`_validate_assertion_shape` 对空 dict 抛 `unknown assertion type: None`）

- [ ] **Step 3: 改 _validate_assertion_shape 跳过空 assert**

编辑 `engineering/loop/core/python/loop_core/case_loader.py:338-346`：

```python
def _validate_assertion_shape(assert_spec: dict) -> None:
    """校验断言规格结构：type 合法、必填参数齐备。

    action case（如 action: reboot）的 assert 为空 dict，跳过校验。
    """
    if not assert_spec:
        return  # action case 无断言
    atype = assert_spec.get("type")
    if atype in {"contains", "equals", "not_contains"} and "value" not in assert_spec:
        raise ValueError(f"assert type '{atype}' requires value")
    if atype == "regex" and "pattern" not in assert_spec:
        raise ValueError("assert type 'regex' requires pattern")
    if atype not in _VALID_ASSERT_TYPES:
        raise ValueError(f"unknown assertion type: {atype}")
```

- [ ] **Step 4: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py -v 2>&1 | tail -20
```
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/core/python/loop_core/case_loader.py \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(loop-core): case_loader 跳过 action case 的断言规格校验"
```

---

## Task 6: 新增 RebootResult dataclass（models.py）

**Files:**
- Modify: `engineering/loop/core/python/loop_core/models.py`（末尾追加 RebootResult）
- Test: `engineering/loop/core/python/tests/test_models.py`

- [ ] **Step 1: 写失败测试——RebootResult dataclass 可构造**

追加到 `engineering/loop/core/python/tests/test_models.py`：

```python
def test_reboot_result_pass():
    """RebootResult 成功场景。"""
    from loop_core.models import RebootResult

    result = RebootResult(
        status="pass",
        transcript_lines=["Booting Linux", "init: zygote"],
        failure_reason="",
        stage_reached="l3_verified",
        boot_duration_sec=42.5,
    )
    assert result.status == "pass"
    assert result.stage_reached == "l3_verified"
    assert result.boot_duration_sec == 42.5
    d = result.to_dict()
    assert d["status"] == "pass"


def test_reboot_result_fail_timeout():
    """RebootResult 超时失败场景。"""
    from loop_core.models import RebootResult

    result = RebootResult(
        status="fail",
        transcript_lines=["Booting Linux"],
        failure_reason="timeout",
        stage_reached="l2_init_ready",
        boot_duration_sec=90.0,
    )
    assert result.status == "fail"
    assert result.failure_reason == "timeout"
```

- [ ] **Step 2: 跑测试验证失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_models.py -v -k "reboot_result" 2>&1 | tail -10
```
Expected: FAIL with `cannot import name 'RebootResult'`

- [ ] **Step 3: 在 models.py 末尾追加 RebootResult dataclass**

编辑 `engineering/loop/core/python/loop_core/models.py`，在文件末尾（EvidenceBundle 之后）追加：

```python
@dataclass
class RebootResult:
    """reboot_and_wait 的返回值。

    Attributes:
        status: "pass"（设备成功回来）/ "fail"（超时或 panic）
        transcript_lines: 整个 reboot 过程采集的串口行（从 reboot 命令到判定设备回来）
        failure_reason: 失败原因（"" / "timeout" / "panic_detected: <line>" / "writer_busy" / "fixture_no_reboot"）
        stage_reached: 达到的阶段：l1_boot_start / l2_init_ready / l3_verified / none
        boot_duration_sec: 从 reboot 命令到 L3 验证通过的耗时（失败时为到失败点的耗时）
    """

    status: str
    transcript_lines: list[str] = field(default_factory=list)
    failure_reason: str = ""
    stage_reached: str = "none"
    boot_duration_sec: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)
```

- [ ] **Step 4: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_models.py -v -k "reboot_result" 2>&1 | tail -10
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/core/python/loop_core/models.py \
        engineering/loop/core/python/tests/test_models.py
git commit -m "feat(loop-core): 新增 RebootResult dataclass"
```

---

## Task 7: BaseTransport 声明 reboot_and_wait 抽象接口

**Files:**
- Modify: `engineering/loop/core/python/loop_core/transport.py:44-110`（BaseTransport 加抽象方法）
- Test: `engineering/loop/core/python/tests/test_transport.py`

- [ ] **Step 1: 写失败测试——BaseTransport 声明 reboot_and_wait**

追加到 `engineering/loop/core/python/tests/test_transport.py`：

```python
def test_base_transport_has_reboot_and_wait_abstract():
    """BaseTransport 声明 reboot_and_wait 抽象方法，子类必须实现。"""
    import inspect
    from loop_core.transport import BaseTransport

    # reboot_and_wait 在 BaseTransport 上有声明
    assert hasattr(BaseTransport, "reboot_and_wait")
    sig = inspect.signature(BaseTransport.reboot_and_wait)
    # 关键参数存在
    param_names = list(sig.parameters.keys())
    assert "boot_markers" in param_names
    assert "panic_markers" in param_names
```

- [ ] **Step 2: 跑测试验证失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_transport.py -v -k "reboot_and_wait_abstract" 2>&1 | tail -10
```
Expected: FAIL with `hasattr` 返回 False

- [ ] **Step 3: 在 BaseTransport 加 reboot_and_wait 抽象方法**

编辑 `engineering/loop/core/python/loop_core/transport.py`，在 `capture_since` 之后（约 `:110`）加：

```python
    # ------------------------------------------------------------------
    # reboot API（跨重启）
    # ------------------------------------------------------------------

    def reboot_and_wait(
        self,
        boot_markers: list[str],
        panic_markers: list[str],
        boot_complete_timeout: float = 180.0,
        l1_timeout: float = 30.0,
        l2_timeout: float = 90.0,
        l3_timeout: float = 60.0,
        prompt_markers: list[str] | None = None,
    ) -> "RebootResult":
        """发 reboot 并等待设备回来。

        子类必须实现。live 实现走 stream + marker 检测；
        fixture 实现走 fixture 数据消费。

        Args:
            boot_markers: [L1_early, L2_init_ready] 两级 boot 标记
            panic_markers: kernel panic 标记（命中即 fail）
            boot_complete_timeout: 总超时（兜底，默认 180s）
            l1_timeout: 等 boot_markers[0] 的上限
            l2_timeout: 等 boot_markers[1] 的上限
            l3_timeout: 等 getprop sys.boot_completed 返回 1 的上限
            prompt_markers: prompt 标记列表（L3 getprop 响应判定用）

        Returns:
            RebootResult
        """
        raise NotImplementedError(
            "reboot_and_wait not implemented; provider needs reboot support"
        )
```

在文件顶部 import 区（`:24`）加：

```python
from loop_core.models import ObservedLine, RebootResult
```

（替换原来的 `from loop_core.models import ObservedLine`）

同时更新 `capture_since` 的返回类型引用（无需改，已用 CommandCapture）。

- [ ] **Step 4: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_transport.py -v -k "reboot_and_wait_abstract" 2>&1 | tail -10
```
Expected: PASS

- [ ] **Step 5: 跑全量 transport 测试确认无回归**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_transport.py -v 2>&1 | tail -20
```
Expected: 全绿（FixtureTransport 尚未实现 reboot_and_wait，但默认实现抛 NotImplementedError 不影响现有用例——除非有用例实例化 FixtureTransport 后调用，需确认）

> 注意：现有 FixtureTransport 用例不调用 reboot_and_wait，默认 NotImplementedError 不触发。但为安全，下一个 Task 会给 FixtureTransport 加兼容实现。

- [ ] **Step 6: 提交**

```bash
git add engineering/loop/core/python/loop_core/transport.py \
        engineering/loop/core/python/tests/test_transport.py
git commit -m "feat(loop-core): BaseTransport 声明 reboot_and_wait 抽象接口"
```

---

## Task 8: FixtureTransport 实现 reboot_and_wait 兼容方法

**Files:**
- Modify: `engineering/loop/core/python/loop_core/transport.py:113-233`（FixtureTransport）
- Test: `engineering/loop/core/python/tests/test_transport.py`

- [ ] **Step 1: 写失败测试——FixtureTransport.reboot_and_wait 检测 fixture 内 reboot marker**

追加到 `engineering/loop/core/python/tests/test_transport.py`：

```python
def test_fixture_transport_reboot_and_wait_pass_with_reboot_marker():
    """fixture 含 boot marker 时，reboot_and_wait 返回 pass。"""
    from loop_core.transport import FixtureTransport

    rows = [
        {"t": 0.0, "text": "reboot: Restarting system"},
        {"t": 1.0, "text": "Booting Linux on physical CPU 0x0"},
        {"t": 2.0, "text": "Linux version 6.6.116"},
        {"t": 18.0, "text": "init: ... started service 'zygote' has pid 636"},
        {"t": 19.0, "text": "1"},  # getprop sys.boot_completed 响应
    ]
    transport = FixtureTransport(rows)
    result = transport.reboot_and_wait(
        boot_markers=["Booting Linux on physical CPU", "init: ... started service 'zygote' has pid"],
        panic_markers=["Kernel panic"],
        prompt_markers=["console:/ $"],
    )
    assert result.status == "pass"
    assert result.stage_reached == "l3_verified"


def test_fixture_transport_reboot_and_wait_fail_no_reboot_marker():
    """fixture 不含任何 boot marker 时，返回 fail(fixture_no_reboot)。"""
    from loop_core.transport import FixtureTransport

    rows = [
        {"t": 0.0, "text": "unrelated line"},
        {"t": 1.0, "text": "another line"},
    ]
    transport = FixtureTransport(rows)
    result = transport.reboot_and_wait(
        boot_markers=["Booting Linux on physical CPU", "init: ... started service 'zygote' has pid"],
        panic_markers=["Kernel panic"],
    )
    assert result.status == "fail"
    assert "fixture_no_reboot" in result.failure_reason or result.stage_reached == "none"


def test_fixture_transport_reboot_and_wait_detects_panic():
    """fixture 含 panic marker 时立即返回 fail。"""
    from loop_core.transport import FixtureTransport

    rows = [
        {"t": 0.0, "text": "reboot: Restarting system"},
        {"t": 1.0, "text": "Booting Linux on physical CPU"},
        {"t": 2.0, "text": "Kernel panic - not syncing"},
    ]
    transport = FixtureTransport(rows)
    result = transport.reboot_and_wait(
        boot_markers=["Booting Linux on physical CPU", "init: ... started service 'zygote' has pid"],
        panic_markers=["Kernel panic"],
    )
    assert result.status == "fail"
    assert "panic_detected" in result.failure_reason
```

- [ ] **Step 2: 跑测试验证失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_transport.py -v -k "fixture_transport_reboot" 2>&1 | tail -10
```
Expected: FAIL（FixtureTransport 未实现 reboot_and_wait，走 BaseTransport 默认 NotImplementedError）

- [ ] **Step 3: 实现 FixtureTransport.reboot_and_wait**

编辑 `engineering/loop/core/python/loop_core/transport.py`，在 FixtureTransport 的 `capture_since` 之后（文件末尾，约 `:233`）加：

```python
    def reboot_and_wait(
        self,
        boot_markers: list[str],
        panic_markers: list[str],
        boot_complete_timeout: float = 180.0,
        l1_timeout: float = 30.0,
        l2_timeout: float = 90.0,
        l3_timeout: float = 60.0,
        prompt_markers: list[str] | None = None,
    ) -> RebootResult:
        """fixture 模式：在 fixture 数据里检测 boot marker。

        fixture 回放不真实发 reboot，而是扫描 fixture 行：
        - 命中 panic_markers → 立即返回 fail(panic_detected)
        - 命中 boot_markers[0] → L1 达到
        - 命中 boot_markers[1] → L2 达到
        - L2 后模拟发 getprop，扫剩余行找 "1" → L3 达成 pass
        - 无任何 boot marker → fail(fixture_no_reboot)

        Args 参数与 BaseTransport.reboot_and_wait 一致（timeout 参数 fixture 忽略）。
        """
        del boot_complete_timeout, l1_timeout, l2_timeout, l3_timeout  # fixture 不真实等待

        all_lines = [r["text"] for r in self._rows]
        l1_marker = boot_markers[0] if len(boot_markers) > 0 else ""
        l2_marker = boot_markers[1] if len(boot_markers) > 1 else ""

        stage = "none"
        l2_end_idx = len(all_lines)

        for idx, line in enumerate(all_lines):
            # panic 优先
            for p in panic_markers:
                if p in line:
                    return RebootResult(
                        status="fail",
                        transcript_lines=all_lines,
                        failure_reason=f"panic_detected: {line}",
                        stage_reached=stage,
                        boot_duration_sec=0.0,
                    )
            # L1
            if stage == "none" and l1_marker and l1_marker in line:
                stage = "l1_boot_start"
                continue
            # L2
            if stage == "l1_boot_start" and l2_marker and l2_marker in line:
                stage = "l2_init_ready"
                l2_end_idx = idx
                continue

        if stage == "none":
            return RebootResult(
                status="fail",
                transcript_lines=all_lines,
                failure_reason="fixture_no_reboot: no boot marker found in fixture",
                stage_reached="none",
                boot_duration_sec=0.0,
            )

        # L3：扫 L2 之后的行找 getprop 响应 "1"
        # fixture 模式简化：L2 达成后剩余行若含 prompt_markers 或单独 "1" 行，视为 boot_completed
        remaining = all_lines[l2_end_idx + 1:]
        markers = prompt_markers or []
        boot_completed_hit = any(
            line.strip() == "1" or any(m in line for m in markers)
            for line in remaining
        )
        if stage == "l2_init_ready" and boot_completed_hit:
            stage = "l3_verified"
            return RebootResult(
                status="pass",
                transcript_lines=all_lines,
                failure_reason="",
                stage_reached="l3_verified",
                boot_duration_sec=0.0,
            )

        return RebootResult(
            status="fail",
            transcript_lines=all_lines,
            failure_reason=f"timeout at stage {stage}: boot_completed not found in fixture",
            stage_reached=stage,
            boot_duration_sec=0.0,
        )
```

- [ ] **Step 4: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_transport.py -v -k "fixture_transport_reboot" 2>&1 | tail -10
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/core/python/loop_core/transport.py \
        engineering/loop/core/python/tests/test_transport.py
git commit -m "feat(loop-core): FixtureTransport 实现 reboot_and_wait 兼容方法"
```

---

## Task 9: Rp5SerialTransport 实现 reboot_and_wait（live 模式核心）

**Files:**
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py`（末尾追加方法）
- Test: `engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py`

> 注意：live transport 测试用 mock client，不连真实设备。真实设备验证在 Task 15。

- [ ] **Step 1: 写失败测试——mock client 模拟 reboot + boot marker 流**

追加到 `engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py`：

```python
class FakeClient:
    """模拟 AutomationClient，按预设序列返回 read_until_timeout 结果。"""

    def __init__(self, read_sequences: list[list[str]]) -> None:
        self._sequences = read_sequences
        self._seq_idx = 0
        self.sent_lines: list[str] = []
        self._writer_held = False

    def acquire_writer(self) -> bool:
        self._writer_held = True
        return True

    def release(self) -> None:
        self._writer_held = False

    def send_line(self, text: str) -> None:
        self.sent_lines.append(text)
        # 模拟 reboot 命令可能触发瞬间断流
        if text == "reboot":
            raise OSError("simulated reboot disconnect")

    def read_until_timeout(self, timeout_sec: float) -> list[str]:
        if self._seq_idx < len(self._sequences):
            result = self._sequences[self._seq_idx]
            self._seq_idx += 1
            return result
        return []


def test_rp5_transport_reboot_and_wait_pass():
    """L1→L2→L3 全程通过的 happy path。"""
    from rp5_serial.transport import Rp5SerialTransport

    # 模拟：第 1 次读=空（reboot 命令断流），第 2 次=L1 marker，
    # 第 3 次=L2 marker，第 4 次=getprop 响应 "1"
    fake = FakeClient([
        [],  # reboot 后首次读空
        ["Booting Linux on physical CPU 0x0", "Linux version 6.6"],
        ["init: ... started service 'zygote' has pid 636"],
        ["1"],  # getprop sys.boot_completed 响应
    ])
    transport = Rp5SerialTransport(fake)
    result = transport.reboot_and_wait(
        boot_markers=["Booting Linux on physical CPU", "init: ... started service 'zygote' has pid"],
        panic_markers=["Kernel panic"],
        prompt_markers=["console:/ $"],
    )
    assert result.status == "pass"
    assert result.stage_reached == "l3_verified"
    assert "reboot" in fake.sent_lines
    assert "getprop sys.boot_completed" in fake.sent_lines


def test_rp5_transport_reboot_and_wait_panic():
    """L1 前命中 panic marker 立即 fail。"""
    from rp5_serial.transport import Rp5SerialTransport

    fake = FakeClient([
        [],
        ["Booting Linux on physical CPU"],
        ["Kernel panic - not syncing"],
    ])
    transport = Rp5SerialTransport(fake)
    result = transport.reboot_and_wait(
        boot_markers=["Booting Linux on physical CPU", "init: ... started service 'zygote' has pid"],
        panic_markers=["Kernel panic"],
    )
    assert result.status == "fail"
    assert "panic_detected" in result.failure_reason


def test_rp5_transport_reboot_and_wait_l1_timeout():
    """L1 marker 一直不出现 → fail(timeout, none)。"""
    from rp5_serial.transport import Rp5SerialTransport

    fake = FakeClient([[], [], []])  # 永远空
    transport = Rp5SerialTransport(fake)
    result = transport.reboot_and_wait(
        boot_markers=["Booting Linux on physical CPU", "init: ... started service 'zygote' has pid"],
        panic_markers=["Kernel panic"],
        l1_timeout=0.1,  # 极短超时快速触发
    )
    assert result.status == "fail"
    assert "timeout" in result.failure_reason
    assert result.stage_reached == "none"
```

- [ ] **Step 2: 跑测试验证失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py \
  -v -k "rp5_transport_reboot" 2>&1 | tail -15
```
Expected: FAIL（Rp5SerialTransport 未实现 reboot_and_wait）

- [ ] **Step 3: 实现 Rp5SerialTransport.reboot_and_wait**

编辑 `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py`，在文件顶部 import 区（`:11-12`）加：

```python
from loop_core.models import ObservedLine, RebootResult
from loop_core.transport import BaseTransport, CommandCapture
```

（替换原来的 `from loop_core.models import ObservedLine` + `from loop_core.transport import BaseTransport, CommandCapture`）

在 `capture_since` 之后（文件末尾，约 `:329`）加：

```python
    def reboot_and_wait(
        self,
        boot_markers: list[str],
        panic_markers: list[str],
        boot_complete_timeout: float = 180.0,
        l1_timeout: float = 30.0,
        l2_timeout: float = 90.0,
        l3_timeout: float = 60.0,
        prompt_markers: list[str] | None = None,
    ) -> RebootResult:
        """发 reboot 并等待设备回来（live 模式）。

        三级渐进判定：
        L1: 等 boot_markers[0]（boot 开始）
        L2: 等 boot_markers[1]（init 阶段，zygote 起来）
        L3: 发 getprop sys.boot_completed，等响应含 "1"

        任一阶段命中 panic_markers 立即 fail。任一阶段超时即 fail，
        保留已采集 transcript 作为证据。
        """
        import time as _time

        del boot_complete_timeout  # 由 l1+l2+l3 之和兜底
        boot_start = _time.monotonic()
        all_lines: list[str] = []
        stage = "none"

        l1_marker = boot_markers[0] if len(boot_markers) > 0 else ""
        l2_marker = boot_markers[1] if len(boot_markers) > 1 else ""

        # 发 reboot（容忍 OSError——reboot 系统调用可能瞬间断流）
        try:
            self.client.send_line("reboot")
        except OSError:
            pass  # 预期行为，继续等 stream

        def _check_panic(lines: list[str]) -> str | None:
            for line in lines:
                for p in panic_markers:
                    if p in line:
                        return line
            return None

        # L1 等待
        l1_deadline = _time.monotonic() + l1_timeout
        l1_hit = False
        while _time.monotonic() < l1_deadline:
            chunk = self.client.read_until_timeout(2.0)
            if chunk:
                all_lines.extend(chunk)
                panic_line = _check_panic(chunk)
                if panic_line:
                    return RebootResult(
                        status="fail",
                        transcript_lines=all_lines,
                        failure_reason=f"panic_detected: {panic_line}",
                        stage_reached=stage,
                        boot_duration_sec=round(_time.monotonic() - boot_start, 3),
                    )
                if l1_marker and any(l1_marker in line for line in chunk):
                    l1_hit = True
                    stage = "l1_boot_start"
                    break
            if l1_hit:
                break
        if not l1_hit:
            return RebootResult(
                status="fail",
                transcript_lines=all_lines,
                failure_reason="timeout",
                stage_reached="none",
                boot_duration_sec=round(_time.monotonic() - boot_start, 3),
            )

        # L2 等待
        l2_deadline = _time.monotonic() + l2_timeout
        l2_hit = False
        while _time.monotonic() < l2_deadline:
            chunk = self.client.read_until_timeout(2.0)
            if chunk:
                all_lines.extend(chunk)
                panic_line = _check_panic(chunk)
                if panic_line:
                    return RebootResult(
                        status="fail",
                        transcript_lines=all_lines,
                        failure_reason=f"panic_detected: {panic_line}",
                        stage_reached=stage,
                        boot_duration_sec=round(_time.monotonic() - boot_start, 3),
                    )
                if l2_marker and any(l2_marker in line for line in chunk):
                    l2_hit = True
                    stage = "l2_init_ready"
                    break
        if not l2_hit:
            return RebootResult(
                status="fail",
                transcript_lines=all_lines,
                failure_reason="timeout",
                stage_reached=stage,
                boot_duration_sec=round(_time.monotonic() - boot_start, 3),
            )

        # L3 验证：发 getprop sys.boot_completed
        try:
            self.client.send_line("getprop sys.boot_completed")
        except OSError:
            pass  # 容忍，继续等响应

        l3_deadline = _time.monotonic() + l3_timeout
        while _time.monotonic() < l3_deadline:
            chunk = self.client.read_until_timeout(2.0)
            if chunk:
                all_lines.extend(chunk)
                panic_line = _check_panic(chunk)
                if panic_line:
                    return RebootResult(
                        status="fail",
                        transcript_lines=all_lines,
                        failure_reason=f"panic_detected: {panic_line}",
                        stage_reached=stage,
                        boot_duration_sec=round(_time.monotonic() - boot_start, 3),
                    )
                # getprop 响应含 "1"（允许混在 prompt 行里，如 "console:/ $ 1"）
                if any(line.strip() == "1" or line.strip().endswith("1") for line in chunk):
                    return RebootResult(
                        status="pass",
                        transcript_lines=all_lines,
                        failure_reason="",
                        stage_reached="l3_verified",
                        boot_duration_sec=round(_time.monotonic() - boot_start, 3),
                    )

        return RebootResult(
            status="fail",
            transcript_lines=all_lines,
            failure_reason="timeout",
            stage_reached=stage,
            boot_duration_sec=round(_time.monotonic() - boot_start, 3),
        )
```

- [ ] **Step 4: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py \
  -v -k "rp5_transport_reboot" 2>&1 | tail -15
```
Expected: PASS

- [ ] **Step 5: 跑全量 provider 测试确认无回归**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/connection/providers/rp5-serial/python/tests/ -v 2>&1 | tail -20
```
Expected: 全绿

- [ ] **Step 6: 提交**

```bash
git add engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py \
        engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py
git commit -m "feat(rp5-serial): 实现 reboot_and_wait（L1/L2/L3 三级渐进判定）"
```

---

## Task 10: executor 对 action case 走 reboot_and_wait 分支

**Files:**
- Modify: `engineering/loop/core/python/loop_core/executor.py:128-239`（`_execute_case`）+ `:33-51`（execute_suite 传入 boot_markers/panic_markers）
- Test: `engineering/loop/core/python/tests/test_executor.py`

- [ ] **Step 1: 写失败测试——action case 触发 transport.reboot_and_wait**

追加到 `engineering/loop/core/python/tests/test_executor.py`：

```python
class FakeTransportWithReboot:
    """模拟 transport，记录 reboot_and_wait 调用。"""

    def __init__(self) -> None:
        self.reboot_called = False
        self.reboot_args: dict = {}

    def acquire_writer(self) -> bool:
        return True

    def release(self) -> None:
        pass

    def mark_output_boundary(self) -> int:
        return 0

    def send_line(self, text: str) -> None:
        pass

    def capture_since(self, boundary, timeout_sec, recent_limit, prompt_markers=None):
        from loop_core.transport import CommandCapture
        from loop_core.models import ObservedLine
        return CommandCapture(lines=[ObservedLine(t=0, text="1")], prompt_visible=True)

    def reboot_and_wait(self, **kwargs):
        from loop_core.models import RebootResult
        self.reboot_called = True
        self.reboot_args = kwargs
        return RebootResult(
            status="pass",
            transcript_lines=["Booting Linux", "init: zygote", "1"],
            failure_reason="",
            stage_reached="l3_verified",
            boot_duration_sec=42.0,
        )


def test_executor_action_case_calls_reboot_and_wait():
    """action: reboot 的 case 触发 transport.reboot_and_wait，结果转 TestCaseResult。"""
    from loop_core.case_loader import TestCase
    from loop_core.assertion_engine import AssertionEngine
    from loop_core.executor import CaseExecutor

    fake = FakeTransportWithReboot()
    executor = CaseExecutor(fake, AssertionEngine())

    case = TestCase(
        id="trigger_reboot",
        suite="system.boot",
        command="",
        action="reboot",
        assert_spec={},
        severity="critical",
    )
    result = executor._execute_case(
        case,
        results={},
        prompt_markers=["console:/ $"],
        capture_timeout=5.0,
        recent_limit=400,
        boot_markers=["Booting Linux", "init: zygote"],
        panic_markers=["Kernel panic"],
    )

    assert fake.reboot_called is True
    assert result.status == "pass"
    assert result.id == "trigger_reboot"
    assert "Booting Linux" in result.output
    assert result.assertion == {"type": "action", "action": "reboot"}
```

- [ ] **Step 2: 跑测试验证失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_executor.py \
  -v -k "action_case_calls_reboot" 2>&1 | tail -15
```
Expected: FAIL（`_execute_case` 不接受 `boot_markers` / `panic_markers` 参数，且无 action 分支）

- [ ] **Step 3: 改 _execute_case 加 boot_markers/panic_markers 参数 + action 分支**

编辑 `engineering/loop/core/python/loop_core/executor.py`。

3a. 改 `_execute_case` 签名（`:128-135`），加两个可选参数：

```python
    def _execute_case(
        self,
        case: TestCase,
        results: dict[str, TestCaseResult],
        prompt_markers: list[str],
        capture_timeout: float,
        recent_limit: int,
        boot_markers: list[str] | None = None,
        panic_markers: list[str] | None = None,
    ) -> TestCaseResult:
        """执行单个用例（含依赖检查）。

        boot_markers / panic_markers 仅对 action: reboot 的用例生效。
        """
```

3b. 在依赖检查通过后、命令执行前（`:159` 之前），加 action 分支：

```python
        # action case 分支（如 action: reboot）
        if case.action == "reboot":
            reboot_fn = getattr(self.transport, "reboot_and_wait", None)
            if reboot_fn is None or not callable(reboot_fn):
                return TestCaseResult(
                    id=case.id,
                    suite=case.suite,
                    status="error",
                    command=case.command,
                    failure_reason="transport does not support reboot_and_wait",
                    error_type="unsupported_action",
                    tags=case.tags,
                )
            start = time.monotonic()
            try:
                reboot_result = reboot_fn(
                    boot_markers=boot_markers or [],
                    panic_markers=panic_markers or [],
                    prompt_markers=prompt_markers,
                )
            except Exception as exc:
                return TestCaseResult(
                    id=case.id,
                    suite=case.suite,
                    status="error",
                    command=case.command,
                    failure_reason=str(exc),
                    error_type=type(exc).__name__,
                    tags=case.tags,
                )
            duration = round(time.monotonic() - start, 3)
            output_text = "\n".join(reboot_result.transcript_lines)
            preview = " | ".join(reboot_result.transcript_lines[:5]) if reboot_result.transcript_lines else ""
            return TestCaseResult(
                id=case.id,
                suite=case.suite,
                status="pass" if reboot_result.status == "pass" else "fail",
                command=case.command,
                output=output_text,
                output_preview=preview,
                assertion={"type": "action", "action": case.action},
                duration_sec=duration,
                failure_reason=reboot_result.failure_reason,
                triggered_collectors=case.on_fail.get("collectors", []) if reboot_result.status != "pass" else [],
                tags=case.tags,
            )
```

- [ ] **Step 4: 改 execute_suite 把 boot_markers/panic_markers 传给 _execute_case**

编辑 `executor.py:58-66`，execute_suite 循环里加参数透传。先给 execute_suite 加两个可选参数（`:33-40`）：

```python
    def execute_suite(
        self,
        suite: CaseSuite,
        device_id: str = "",
        prompt_markers: list[str] | None = None,
        capture_timeout: float = 5.0,
        recent_limit: int = 400,
        boot_markers: list[str] | None = None,
        panic_markers: list[str] | None = None,
    ) -> EvidenceBundle:
```

然后改循环体（`:58-66`）：

```python
        for case in suite.cases:
            result = self._execute_case(
                case, results, prompt_markers, capture_timeout, recent_limit,
                boot_markers=boot_markers, panic_markers=panic_markers,
            )
```

- [ ] **Step 5: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_executor.py -v 2>&1 | tail -20
```
Expected: 全绿（新测试通过 + 旧测试因新参数是可选的不受影响）

> 注意：旧测试调用 `_execute_case` 时不传 boot_markers/panic_markers，默认 None，action 分支不触发。

- [ ] **Step 6: 跑全量 core 测试确认无回归**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/ -v 2>&1 | tail -20
```
Expected: 全绿

- [ ] **Step 7: 提交**

```bash
git add engineering/loop/core/python/loop_core/executor.py \
        engineering/loop/core/python/tests/test_executor.py
git commit -m "feat(loop-core): executor 对 action: reboot case 走 reboot_and_wait 分支"
```

---

## Task 11: runner 注入 boot_markers/panic_markers 给 executor

**Files:**
- Modify: `engineering/loop/core/python/loop_core/runner.py:33-72`（LoopRunner 加字段 + 透传）
- Test: `engineering/loop/core/python/tests/test_runner.py`

- [ ] **Step 1: 写失败测试——LoopRunner 把 boot_markers/panic_markers 传给 execute_suite**

追加到 `engineering/loop/core/python/tests/test_runner.py`：

```python
def test_runner_passes_boot_markers_to_executor(monkeypatch):
    """LoopRunner.run() 把 boot_markers/panic_markers 透传给 executor.execute_suite。"""
    from loop_core.runner import LoopRunner
    from loop_core.case_loader import CaseSuite

    captured = {}

    class FakeExecutor:
        def __init__(self, *args, **kwargs):
            pass

        def execute_suite(self, suite, **kwargs):
            captured.update(kwargs)
            from loop_core.models import EvidenceBundle
            return EvidenceBundle(
                bundle_id="eb-test",
                device_id="rp5",
                suite=suite.name,
                timestamp="2026-01-01T00:00:00+0800",
                summary={"total": 0, "passed": 0, "failed": 0, "skipped": 0, "overall": "PASS"},
                cases=[],
                evidence={},
            )

    class FakeTransport:
        def acquire_writer(self): return True
        def release(self): pass

    suite = CaseSuite(name="test", version=1, cases=[], collectors={})
    runner = LoopRunner(
        device_id="rp5",
        prompt_markers=["console:/ $"],
        transport=FakeTransport(),
        suite=suite,
        boot_markers=["Booting Linux"],
        panic_markers=["Kernel panic"],
    )
    # 替换 executor 为 fake
    runner.executor = FakeExecutor()
    runner.run()

    assert captured.get("boot_markers") == ["Booting Linux"]
    assert captured.get("panic_markers") == ["Kernel panic"]
```

- [ ] **Step 2: 跑测试验证失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_runner.py -v -k "boot_markers" 2>&1 | tail -10
```
Expected: FAIL（LoopRunner.__init__ 不接受 boot_markers 参数）

- [ ] **Step 3: 改 LoopRunner 加 boot_markers/panic_markers**

编辑 `engineering/loop/core/python/loop_core/runner.py:33-50`，`__init__` 加两个可选参数：

```python
    def __init__(
        self,
        device_id: str,
        prompt_markers: list[str],
        transport,
        suite: CaseSuite,
        capture_timeout: float = 5.0,
        recent_limit: int = 400,
        device_profile: dict | None = None,
        boot_markers: list[str] | None = None,
        panic_markers: list[str] | None = None,
    ) -> None:
        self.device_id = device_id
        self.prompt_markers = prompt_markers
        self.transport = transport
        self.suite = suite
        self.capture_timeout = capture_timeout
        self.recent_limit = recent_limit
        self.device_profile = device_profile or {}
        self.boot_markers = boot_markers or []
        self.panic_markers = panic_markers or []
        self.executor = CaseExecutor(transport, AssertionEngine())
```

然后改 `run()` 方法（`:62-68`），execute_suite 调用透传：

```python
            bundle = self.executor.execute_suite(
                self.suite,
                device_id=self.device_id,
                prompt_markers=self.prompt_markers,
                capture_timeout=self.capture_timeout,
                recent_limit=self.recent_limit,
                boot_markers=self.boot_markers,
                panic_markers=self.panic_markers,
            )
```

- [ ] **Step 4: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_runner.py -v 2>&1 | tail -20
```
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/core/python/loop_core/runner.py \
        engineering/loop/core/python/tests/test_runner.py
git commit -m "feat(loop-core): LoopRunner 注入 boot_markers/panic_markers 给 executor"
```

---

## Task 12: cli.py 把 profile 的 markers 传给 LoopRunner

**Files:**
- Modify: `engineering/loop/core/python/loop_core/cli.py:143-152`（LoopRunner 构造）
- Test: `engineering/loop/core/python/tests/test_cli.py`

- [ ] **Step 1: 写失败测试——CLI 从 profile 提取 markers 传给 runner**

追加到 `engineering/loop/core/python/tests/test_cli.py`：

```python
def test_cli_passes_boot_panic_markers_to_runner(tmp_path, monkeypatch):
    """CLI 从 DeviceProfile 提取 boot_markers/panic_markers 传给 LoopRunner。"""
    from loop_core import cli

    captured = {}

    class FakeRunner:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            from loop_core.models import EvidenceBundle
            return EvidenceBundle(
                bundle_id="eb-test", device_id="rp5", suite="test",
                timestamp="2026-01-01T00:00:00+0800",
                summary={"total": 0, "passed": 0, "failed": 0, "skipped": 0, "overall": "PASS"},
                cases=[], evidence={},
            )

        def build_failure_bundle(self, reason):
            from loop_core.models import EvidenceBundle
            return EvidenceBundle(
                bundle_id="eb-fail", device_id="rp5", suite="test",
                timestamp="2026-01-01T00:00:00+0800",
                summary={"total": 0, "passed": 0, "failed": 0, "skipped": 0, "overall": "FAIL", "error": reason},
                cases=[], evidence={},
            )

    # 创建含 boot_markers 的 profile
    profile_file = tmp_path / "profile.json"
    profile_file.write_text('{"device_id":"rp5","boot_markers":["Booting Linux"],"panic_markers":["Kernel panic"]}', encoding="utf-8")

    monkeypatch.setattr(cli, "LoopRunner", FakeRunner)

    argv = [
        "run",
        "--suite", "dummy.yaml",
        "--fixture", "dummy.jsonl",
        "--device-profile", str(profile_file),
        "--artifacts-dir", str(tmp_path / "out"),
    ]
    # 需要 dummy suite 和 fixture
    (tmp_path / "dummy.yaml").write_text("suite: test\nversion: 1\ncases: []\n", encoding="utf-8")
    (tmp_path / "dummy.jsonl").write_text('{"t":0,"text":"x"}\n', encoding="utf-8")

    cli.main(argv)

    assert captured.get("boot_markers") == ["Booting Linux"]
    assert captured.get("panic_markers") == ["Kernel panic"]
```

- [ ] **Step 2: 跑测试验证失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_cli.py -v -k "boot_panic_markers" 2>&1 | tail -10
```
Expected: FAIL（LoopRunner 构造时未传 boot_markers/panic_markers）

- [ ] **Step 3: 改 cli.py 把 profile markers 传给 LoopRunner**

编辑 `engineering/loop/core/python/loop_core/cli.py:143-152`，LoopRunner 构造加 markers：

```python
    runner = LoopRunner(
        device_id=profile.device_id,
        prompt_markers=profile.prompt_markers,
        transport=transport,
        suite=suite,
        capture_timeout=capture_timeout,
        recent_limit=recent_limit,
        boot_markers=profile.boot_markers,
        panic_markers=profile.panic_markers,
    )
```

- [ ] **Step 4: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_cli.py -v 2>&1 | tail -20
```
Expected: 全绿

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/core/python/loop_core/cli.py \
        engineering/loop/core/python/tests/test_cli.py
git commit -m "feat(loop-core): CLI 从 profile 提取 markers 传给 LoopRunner"
```

---

## Task 13: cases/common/shell.yaml 加 kmsg collector

**Files:**
- Modify: `engineering/loop/cases/common/shell.yaml`（collectors 块末尾加 kmsg）
- Test: 手动验证 load_suite 成功 + 现有测试无回归

- [ ] **Step 1: 改 shell.yaml 加 kmsg collector**

编辑 `engineering/loop/cases/common/shell.yaml`，在 `collectors:` 块的 `crash_dump` 之后（约 `:41`）加：

```yaml
  kmsg:
    commands:
      - "cat /proc/last_kmsg 2>/dev/null || dmesg | head -200"
    hints: "上一次启动的内核日志（诊断 reboot 原因关键，last_kmsg 优先，回退 dmesg）"
```

- [ ] **Step 2: 写测试验证 kmsg collector 能被 include 加载**

追加到 `engineering/loop/core/python/tests/test_case_loader.py`：

```python
def test_common_shell_yaml_has_kmsg_collector():
    """common/shell.yaml 包含 kmsg collector 定义。"""
    from loop_core.case_loader import load_suite

    repo_root = __import__("pathlib").Path(__file__).resolve()
    # 从测试文件位置向上找 cases 目录
    while repo_root.name != "engineering":
        repo_root = repo_root.parent
        if repo_root == repo_root.parent:
            break
    cases_dir = repo_root / "loop" / "cases"
    shell_yaml = cases_dir / "common" / "shell.yaml"

    suite = load_suite(str(shell_yaml), [str(cases_dir)])
    assert "common.shell.kmsg" in suite.collectors
    kmsg_spec = suite.collectors["common.shell.kmsg"]
    assert "cat /proc/last_kmsg" in kmsg_spec["commands"][0]
```

- [ ] **Step 3: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py -v -k "kmsg" 2>&1 | tail -10
```
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add engineering/loop/cases/common/shell.yaml \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(loop-cases): common/shell.yaml 新增 kmsg collector"
```

---

## Task 14: boot-success.yaml 加 trigger_reboot action case

**Files:**
- Modify: `engineering/loop/cases/system/boot-success.yaml`（首条加 trigger_reboot，后续 case 加 requires）
- Test: 验证 load_suite 成功 + 拓扑序正确

- [ ] **Step 1: 写测试验证 boot-success.yaml 含 trigger_reboot 且拓扑序正确**

追加到 `engineering/loop/core/python/tests/test_case_loader.py`：

```python
def test_boot_success_yaml_has_trigger_reboot_first():
    """boot-success.yaml 首条 case 是 trigger_reboot，后续 case requires 它。"""
    from loop_core.case_loader import load_suite

    repo_root = __import__("pathlib").Path(__file__).resolve()
    while repo_root.name != "engineering":
        repo_root = repo_root.parent
        if repo_root == repo_root.parent:
            break
    cases_dir = repo_root / "loop" / "cases"
    boot_yaml = cases_dir / "system" / "boot-success.yaml"

    suite = load_suite(str(boot_yaml), [str(cases_dir)])
    # 拓扑序：trigger_reboot 应在前（被其他 case requires）
    reboot_cases = [c for c in suite.cases if c.action == "reboot"]
    assert len(reboot_cases) == 1
    assert reboot_cases[0].id == "trigger_reboot"

    # shell_reachable 来自 include common/shell，也在 suite 中
    # trigger_reboot 的 fqn
    reboot_fqn = reboot_cases[0].fqn

    # 至少有一条 case requires trigger_reboot
    dependents = [c for c in suite.cases if reboot_fqn in c.requires]
    assert len(dependents) >= 1, "no case requires trigger_reboot"
```

- [ ] **Step 2: 跑测试验证失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py -v -k "trigger_reboot" 2>&1 | tail -10
```
Expected: FAIL（boot-success.yaml 当前无 trigger_reboot case）

- [ ] **Step 3: 改 boot-success.yaml 加 trigger_reboot**

全文重写 `engineering/loop/cases/system/boot-success.yaml`：

```yaml
# 系统级 boot 成功验收用例。
# include common.shell 获取 shell_reachable 用例 + 公共诊断 collector
# （boot_log / init_log / crash_dump / kmsg）。
# FQN 命名空间：system.boot
suite: system.boot
version: 1

include:
  - common/shell

cases:
  - id: trigger_reboot
    action: reboot
    description: "触发设备重启并等待启动完成"
    severity: critical
    assert: {}

  - id: boot_completed
    description: "sys.boot_completed 属性为 1"
    command: "getprop sys.boot_completed"
    assert:
      type: contains
      value: "1"
    severity: critical
    requires: [trigger_reboot]
    on_fail:
      collectors: [boot_log, init_log, kmsg]
    tags: [boot, system]

  - id: zygote_running
    description: "zygote 服务处于 running 状态"
    command: "getprop init.svc.zygote"
    assert:
      type: contains
      value: "running"
    severity: critical
    requires: [trigger_reboot]
    on_fail:
      collectors: [serial_recent, crash_dump, init_log, kmsg]
    tags: [boot, android_core]

  - id: surfaceflinger_running
    description: "surfaceflinger 服务处于 running 状态"
    command: "getprop init.svc.surfaceflinger"
    assert:
      type: contains
      value: "running"
    severity: critical
    requires: [trigger_reboot]
    on_fail:
      collectors: [crash_dump, init_log, kmsg]
    tags: [boot, android_core]
```

- [ ] **Step 4: 跑测试验证通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py -v -k "trigger_reboot" 2>&1 | tail -10
```
Expected: PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/cases/system/boot-success.yaml \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(loop-cases): boot-success.yaml 加 trigger_reboot action case"
```

---

## Task 15: case-template.md 文档化 action 字段

**Files:**
- Modify: `engineering/loop/templates/case-template.md`（schema 段 + 断言矩阵补 action 说明）

- [ ] **Step 1: 改 case-template.md 在 schema 段加 action 字段说明**

编辑 `engineering/loop/templates/case-template.md:16-28`，cases 字段示例加 action：

在 `command:` 字段下方加 `action:` 可选字段说明（保持 command/action 互斥语义清晰）：

```yaml
cases:             # 必填：用例列表
  - id: <用例ID，snake_case，全局唯一>
    description: "<用例描述，一句话说清楚验证什么>"
    command: "<执行的 shell 命令，空字符串表示仅探测 prompt>"
    # 或
    action: <reboot>  # 可选：动作型用例（与 command 互斥）。当前支持 reboot：触发设备重启并等待启动完成
    assert:        # 必填：断言规格（action case 可为空 {}）
      type: <断言类型>
      value: <断言值>
```

- [ ] **Step 2: 在模板末尾加"action 用例"专节**

在 `case-template.md` 末尾（参数化用例之后）追加：

```markdown
## 9. action 动作型用例

当用例需要触发设备状态变迁（如重启）而非执行命令时，用 `action` 字段替代 `command`。

### 支持的 action 值

| action | 行为 | 适用场景 |
|--------|------|---------|
| `reboot` | 触发设备重启并等待启动完成（三级渐进判定：L1 boot 开始 → L2 init 阶段 → L3 boot_completed 验证） | boot 诊断、启动问题复现 |

### 示例

```yaml
cases:
  - id: trigger_reboot
    action: reboot
    description: "触发设备重启并等待启动完成"
    severity: critical
    assert: {}

  - id: boot_ok
    command: "getprop sys.boot_completed"
    assert: {type: contains, value: "1"}
    requires: [trigger_reboot]   # 拓扑保证：reboot 完成后才跑
```

### 规则

1. `action` 与 `command` **互斥**，二选一
2. `action: reboot` 的 case **不需要 assert value**（assert 可为空 `{}`）
3. 后续 case 靠 `requires: [trigger_reboot]` 拓扑保证在 reboot 完成后执行
4. reboot_and_wait 的判定 marker 来自 DeviceProfile（`boot_markers` / `panic_markers`）
5. action case 的 TestCaseResult.assertion 字段为 `{"type": "action", "action": "reboot"}`
```

- [ ] **Step 3: 跑静态校验确认模板合规**

Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
Expected: PASS

- [ ] **Step 4: 提交**

```bash
git add engineering/loop/templates/case-template.md
git commit -m "docs(loop): case-template 文档化 action 字段"
```

---

## Task 16: WORKFLOW.md 加 AI 诊断报告约束 + 全量回归 + live 端到端验证

**Files:**
- Modify: `engineering/loop/WORKFLOW.md`（遗留点更新 + 加 AI 诊断报告约束段）

- [ ] **Step 1: 改 WORKFLOW.md 更新遗留点 + 加 AI 诊断报告约束**

编辑 `engineering/loop/WORKFLOW.md:106-110`（遗留点段），更新为：

```markdown
## 遗留点

1. **gen-cases 未实现**：第二步实现 AI 用例生成（le deploy 已部分实现 reboot 诊断闭环）
2. **deploy 未实现**：第二步实现 binary/image 部署（当前 reboot 诊断闭环属范围 A，不含 deploy）
3. **loop_ctrl 未实现**：第三步实现循环控制（N=5 / 回归检测 / 升级人工）
4. **参数化用例**：case_loader 预留 parameters 字段，第一步未实现展开
```

在 WORKFLOW.md 末尾追加新段：

```markdown
## AI 诊断报告约束（范围 A reboot 诊断闭环）

当 AI（opencode）通过 `/le` 触发 reboot 诊断闭环并收到 EvidenceBundle 后，**必须**按
`engineering/harness/templates/diagnosis-report-template.md` 产出诊断报告：

1. 报告路径：`engineering/output/runs/<run-id>/diagnosis-report.md`（与 EvidenceBundle 同目录）
2. 报告含 6 节：结论 / 证据链 / 根因分析 / 修复建议 / 建议新增 case / 循环终止建议
3. 修复建议必须具体到 workspace 文件路径和函数名
4. YAML 建议（第 5 节）不自动应用，只给人 review（G2 决策）
5. AI 不自动修改 boot-success.yaml

reboot 诊断闭环的数据流：
- `/le run --suite boot-success.yaml --host <ip> --port 9700 ...`
- executor 遇到 `action: reboot` case → 调 transport.reboot_and_wait
- reboot_and_wait 三级渐进判定（L1 boot 开始 / L2 init 阶段 / L3 boot_completed 验证）
- 后续 case（requires: [trigger_reboot]）在设备回来后正常执行
- on_fail 触发 collectors（含新增 kmsg）
- EvidenceBundle 落盘 → AI 读后按模板产出诊断报告
```

- [ ] **Step 2: 跑全量单测确认所有改动无回归**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib 2>&1 | tail -30
```
Expected: 全绿

- [ ] **Step 3: 跑静态校验**

Run: `bash engineering/harness/scripts/validate_harness_docs.sh`
Expected: PASS（warns=0）

- [ ] **Step 4: live 端到端验证（手动，需真实设备）**

> 此步骤需设备在线 + host 运行。若环境不可用，标记为后续验证项。

```bash
# 前置：Windows 启动 start_rp5_serial_host.bat，确认 host 监听 9700
# 清理残留 writer lease（若上次中断）

bash engineering/harness/scripts/le.sh run \
  --suite engineering/loop/cases/system/boot-success.yaml \
  --host 127.0.0.1 --port 9700 \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir engineering/output/runs/boot-success-live-$(date +%Y%m%d-%H%M%S)
```
Expected:
- trigger_reboot case status=pass，stage_reached=l3_verified
- boot_completed / zygote_running / surfaceflinger_running 全 pass
- EvidenceBundle JSON 落盘
- summary.txt 显示 Overall: PASS

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/WORKFLOW.md
git commit -m "docs(loop): WORKFLOW 更新遗留点 + 加 AI 诊断报告约束"
```

---

## 验证清单（全部 Task 完成后）

- [ ] **全量单测绿**：`PYTHONPATH=... python3 -m pytest ...` 全绿
- [ ] **静态校验绿**：`validate_harness_docs.sh` warns=0
- [ ] **fixture 回归**：现有 boot-success.yaml 在 fixture 模式（不含 action case 的旧用例）仍可跑（向后兼容验证）
- [ ] **live 端到端**：真实 reboot 后 boot-success 全 pass，诊断报告生成且根因明确
- [ ] **向后兼容**：旧 case YAML（无 action 字段）能正常加载执行
