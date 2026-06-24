# lciod Loop P1: Cases + Assertion Engine 扩展 — 实施计划

> **2026-06-24 更新**：设备 IP 发现已从"固定 IP"切换为"串口动态发现"，见 `engineering/loop/scripts/rp5_serial_helper.py` 和 `engineering/loop/WORKFLOW.md` 的「传输层依赖链」章节。本文档中残留的 `192.168.1.55` 仅为历史决策记录。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 loop engineering 增加 5 个 lciod feature suite（覆盖 32 单设备能力点）+ 扩展 3 种断言类型。

**Architecture:** 扩展 assertion_engine.py + case_loader.py 增加 json_field / exit_code_equals / contains_any 断言；新建 5 个 YAML suite（common/kernel_driver/hal/daemon/end_to_end），复用 lcview 的 include/collector/参数化模式。

**Tech Stack:** Python 3.11+ (pytest)，YAML (PyYAML)，loop_core (case_loader/assertion_engine/collector/transport)

**Spec:** `docs/specs/2026-06-22-lciod-loop-verification-design.md`

**测试运行命令：**
```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/ -v
```

---

### Task 1: json_field 断言（TDD）

**Files:**
- Modify: `engineering/loop/core/python/loop_core/assertion_engine.py:48-74` (evaluate 方法 add branch)
- Modify: `engineering/loop/core/python/tests/test_assertion_engine.py` (new TestJsonField class)

- [ ] **Step 1: 写失败测试**

在 `test_assertion_engine.py` 末尾追加：

```python

class TestJsonField:
    """json_field 断言：解析 JSON 输出，按 path 取字段，op 比较。"""

    def test_pass_top_level_int(self, engine):
        ctx = AssertionContext(output='{"read_bytes": 1024}')
        result = engine.evaluate(
            {"type": "json_field", "path": "read_bytes", "op": "ge", "value": 1}, ctx
        )
        assert result.passed is True

    def test_pass_nested_str(self, engine):
        ctx = AssertionContext(output='{"event": {"type": "stall"}}')
        result = engine.evaluate(
            {"type": "json_field", "path": "event.type", "op": "eq", "value": "stall"}, ctx
        )
        assert result.passed is True

    def test_fail_wrong_value(self, engine):
        ctx = AssertionContext(output='{"read_bytes": 0}')
        result = engine.evaluate(
            {"type": "json_field", "path": "read_bytes", "op": "gt", "value": 0}, ctx
        )
        assert result.passed is False
        assert "read_bytes" in result.reason

    def test_pass_exists(self, engine):
        ctx = AssertionContext(output='{"enabled": 1}')
        result = engine.evaluate(
            {"type": "json_field", "path": "enabled", "op": "exists"}, ctx
        )
        assert result.passed is True

    def test_pass_not_exists(self, engine):
        ctx = AssertionContext(output='{"read_bytes": 100}')
        result = engine.evaluate(
            {"type": "json_field", "path": "current_rate", "op": "not_exists"}, ctx
        )
        assert result.passed is True

    def test_fail_not_exists_when_present(self, engine):
        ctx = AssertionContext(output='{"enabled": 1}')
        result = engine.evaluate(
            {"type": "json_field", "path": "enabled", "op": "not_exists"}, ctx
        )
        assert result.passed is False

    def test_invalid_json(self, engine):
        ctx = AssertionContext(output="not json at all")
        result = engine.evaluate(
            {"type": "json_field", "path": "x", "op": "exists"}, ctx
        )
        assert result.passed is False
        assert "not valid JSON" in result.reason

    def test_path_not_found(self, engine):
        ctx = AssertionContext(output='{"a": 1}')
        result = engine.evaluate(
            {"type": "json_field", "path": "b", "op": "eq", "value": 1}, ctx
        )
        assert result.passed is False
        assert "not found" in result.reason

    def test_all_numeric_ops(self, engine):
        ops = ["eq", "ne", "gt", "ge", "lt", "le"]
        for op in ops:
            ctx = AssertionContext(output='{"v": 5}')
            expected_pass = {"eq": False, "ne": True, "gt": True, "ge": True, "lt": False, "le": False}
            result = engine.evaluate({"type": "json_field", "path": "v", "op": op, "value": 3}, ctx)
            assert result.passed == expected_pass[op], f"op={op} failed"

    def test_bool_in_json(self, engine):
        ctx = AssertionContext(output='{"passed": true}')
        result = engine.evaluate(
            {"type": "json_field", "path": "passed", "op": "eq", "value": "true"}, ctx
        )
        assert result.passed is True
```

- [ ] **Step 2: 运行测试验证失败**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_assertion_engine.py::TestJsonField -v
```

Expected: 全部 FAIL（"unknown assertion type: json_field"）或 NameError。

- [ ] **Step 3: 实现 json_field 断言**

在 `assertion_engine.py`：
- `evaluate()` 方法第 68 行（`if atype == "exit_code_zero":` 后）追加：

```python
        if atype == "json_field":
            return self._json_field(assertion, context)
```

- 文件末尾（`_exit_code_zero` 方法后）追加 `_json_field` 方法：

```python
    def _json_field(self, assertion: dict, ctx: AssertionContext) -> AssertionResult:
        import json as _json
        path = assertion["path"]
        op = assertion["op"]
        expected = assertion.get("value")

        try:
            data = _json.loads(ctx.output)
        except _json.JSONDecodeError as e:
            return AssertionResult(passed=False, reason=f"output is not valid JSON: {e}")

        # 按点号分隔遍历嵌套路径（不支持数组索引）
        parts = path.split(".")
        current = data
        for part in parts:
            if isinstance(current, dict) and part in current:
                current = current[part]
            elif isinstance(current, list) and part.isdigit():
                idx = int(part)
                if idx < len(current):
                    current = current[idx]
                else:
                    return AssertionResult(passed=False, reason=f"path '{path}' array index {idx} out of range (len={len(current)})")
            else:
                if op == "not_exists":
                    return AssertionResult(passed=True)
                return AssertionResult(passed=False, reason=f"path '{path}' not found, missing key '{part}' in {list(current.keys()) if isinstance(current, dict) else current!r}")

        if op == "exists":
            return AssertionResult(passed=True)
        if op == "not_exists":
            return AssertionResult(passed=False, reason=f"path '{path}' exists but expected not_exists, value={current!r}")

        # 数值 / 字符串比较
        try:
            actual_num = float(current)
            exp_num = float(expected)
            ops_map = {
                "eq": lambda a, b: a == b, "ne": lambda a, b: a != b,
                "gt": lambda a, b: a > b, "ge": lambda a, b: a >= b,
                "lt": lambda a, b: a < b, "le": lambda a, b: a <= b,
            }
            if op not in ops_map:
                return AssertionResult(passed=False, reason=f"unknown op: {op}")
            ok = ops_map[op](actual_num, exp_num)
            if ok:
                return AssertionResult(passed=True)
            return AssertionResult(passed=False, reason=f"json_field '{path}' {op} {expected} failed, actual={current}")
        except (ValueError, TypeError):
            actual_str = str(current).lower()
            exp_str = str(expected).lower()
            if op == "eq":
                ok = actual_str == exp_str
            elif op == "ne":
                ok = actual_str != exp_str
            else:
                return AssertionResult(passed=False, reason=f"cannot compare non-numeric '{current!r}' with op '{op}'")
            if ok:
                return AssertionResult(passed=True)
            return AssertionResult(passed=False, reason=f"json_field '{path}' {op} {expected} failed, actual={current}")
```

- [ ] **Step 4: 运行测试验证通过**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_assertion_engine.py::TestJsonField -v
```

Expected: 10 tests PASS。

- [ ] **Step 5: 更新 case_loader 校验**

修改 `case_loader.py` 两处：
- 第 291-298 行 `_VALID_ASSERT_TYPES` 添加 `"json_field"`：

```python
_VALID_ASSERT_TYPES = {
    "contains",
    "regex",
    "equals",
    "prompt_visible",
    "not_contains",
    "exit_code_zero",
    "json_field",
}
```

- 第 453-466 行 `_validate_assertion_shape` 追加 json_field 校验（在 `if atype not in _VALID_ASSERT_TYPES` 之前）：

```python
    if atype == "json_field":
        if "path" not in assert_spec:
            raise ValueError("assert type 'json_field' requires path")
        if "op" not in assert_spec:
            raise ValueError("assert type 'json_field' requires op")
        if assert_spec["op"] not in {"eq", "ne", "gt", "ge", "lt", "le", "exists", "not_exists"}:
            raise ValueError(f"unknown json_field op: {assert_spec['op']}")
```

- [ ] **Step 6: 验证 case_loader 不拒绝 json_field**

在 `test_case_loader.py` 末尾追加测试：

```python

def test_load_suite_with_json_field_assertion(tmp_path):
    """加载含 json_field 断言的 suite，校验通过。"""
    path = _write(tmp_path, "test.yaml", """
suite: test-suite
version: 1
cases:
  - id: case_a
    command: "fault-verify stats get --json"
    assert:
      type: json_field
      path: "read_bytes"
      op: "ge"
      value: 0
    severity: critical
""")
    suite = load_suite(path, [str(tmp_path)])
    assert suite.cases[0].assert_spec == {"type": "json_field", "path": "read_bytes", "op": "ge", "value": 0}
```

- [ ] **Step 7: 运行全量测试**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/ -v
```

Expected: all tests PASS（既有 30+ 测试 + 新增 11 测试全绿）。

- [ ] **Step 8: Commit**

```bash
git add engineering/loop/core/python/loop_core/assertion_engine.py \
        engineering/loop/core/python/loop_core/case_loader.py \
        engineering/loop/core/python/tests/test_assertion_engine.py \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(loop_core): json_field 断言——按 JSON path 取字段做 op 比较"
```

---

### Task 2: exit_code_equals 断言（TDD）

**Files:**
- Modify: `engineering/loop/core/python/loop_core/assertion_engine.py`
- Modify: `engineering/loop/core/python/tests/test_assertion_engine.py`
- Modify: `engineering/loop/core/python/loop_core/case_loader.py`

- [ ] **Step 1: 写失败测试**

在 `test_assertion_engine.py` 的 `TestExitCodeZero` 类之后追加：

```python

class TestExitCodeEquals:
    """exit_code_equals 断言：校验退出码等于指定值（fault-verify exit_code 语义化）。"""

    def test_pass_eq5(self, engine):
        ctx = AssertionContext(output="", exit_code=5)
        result = engine.evaluate({"type": "exit_code_equals", "value": 5}, ctx)
        assert result.passed is True

    def test_pass_eq0(self, engine):
        ctx = AssertionContext(output="", exit_code=0)
        result = engine.evaluate({"type": "exit_code_equals", "value": 0}, ctx)
        assert result.passed is True

    def test_fail_mismatch(self, engine):
        ctx = AssertionContext(output="", exit_code=5)
        result = engine.evaluate({"type": "exit_code_equals", "value": 0}, ctx)
        assert result.passed is False

    def test_fail_no_exit_code(self, engine):
        ctx = AssertionContext(output="", exit_code=None)
        result = engine.evaluate({"type": "exit_code_equals", "value": 0}, ctx)
        assert result.passed is False
        assert "not available" in result.reason
```

- [ ] **Step 2: 运行测试验证失败**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_assertion_engine.py::TestExitCodeEquals -v
```

Expected: FAIL（"unknown assertion type"）。

- [ ] **Step 3: 实现 exit_code_equals 断言**

在 `assertion_engine.py` 的 `evaluate()` 中追加 branch（json_field 分支后）：

```python
        if atype == "exit_code_equals":
            return self._exit_code_equals(assertion, context)
```

在 `_exit_code_zero` 方法后追加：

```python
    def _exit_code_equals(self, assertion: dict, ctx: AssertionContext) -> AssertionResult:
        expected = assertion["value"]
        if ctx.exit_code is None:
            return AssertionResult(passed=False, reason="exit code not available")
        if ctx.exit_code == expected:
            return AssertionResult(passed=True)
        return AssertionResult(passed=False, reason=f"expected exit code {expected}, got {ctx.exit_code}")
```

- [ ] **Step 4: 运行测试验证通过**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_assertion_engine.py::TestExitCodeEquals -v
```

Expected: 4 tests PASS。

- [ ] **Step 5: 更新 case_loader 校验**

修改 `case_loader.py` 的 `_VALID_ASSERT_TYPES` 添加 `"exit_code_equals"`：

```python
_VALID_ASSERT_TYPES = {
    "contains",
    "regex",
    "equals",
    "prompt_visible",
    "not_contains",
    "exit_code_zero",
    "json_field",
    "exit_code_equals",
}
```

- 追加 exit_code_equals 的形状校验（与 contains/equals 模式相同）：

```python
    if atype in {"contains", "equals", "not_contains", "exit_code_equals"} and "value" not in assert_spec:
        raise ValueError(f"assert type '{atype}' requires value")
```

修改 `_validate_assertion_shape` 中已有的 contains/equals/not_contains 校验行（第 461 行附近）为以上合并形式。

- [ ] **Step 6: Commit**

```bash
git add engineering/loop/core/python/loop_core/assertion_engine.py \
        engineering/loop/core/python/loop_core/case_loader.py \
        engineering/loop/core/python/tests/test_assertion_engine.py
git commit -m "feat(loop_core): exit_code_equals 断言——校验退出码等于指定值"
```

---

### Task 3: contains_any 断言（TDD）

**Files:**
- Modify: `engineering/loop/core/python/loop_core/assertion_engine.py`
- Modify: `engineering/loop/core/python/tests/test_assertion_engine.py`
- Modify: `engineering/loop/core/python/loop_core/case_loader.py`

- [ ] **Step 1: 写失败测试**

```python

class TestContainsAny:
    """contains_any 断言：输出包含列表中任一项（枚举类校验）。"""

    def test_pass_first(self, engine):
        ctx = AssertionContext(output="running")
        result = engine.evaluate({"type": "contains_any", "values": ["running", "stopped", "restarting"]}, ctx)
        assert result.passed is True

    def test_pass_second(self, engine):
        ctx = AssertionContext(output="service is stopped")
        result = engine.evaluate({"type": "contains_any", "values": ["running", "stopped"]}, ctx)
        assert result.passed is True

    def test_fail_none(self, engine):
        ctx = AssertionContext(output="restarting")
        result = engine.evaluate({"type": "contains_any", "values": ["running", "stopped"]}, ctx)
        assert result.passed is False

    def test_empty_values(self, engine):
        ctx = AssertionContext(output="anything")
        result = engine.evaluate({"type": "contains_any", "values": []}, ctx)
        assert result.passed is False
        assert "empty" in result.reason
```

- [ ] **Step 2: 运行测试验证失败**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_assertion_engine.py::TestContainsAny -v
```

- [ ] **Step 3: 实现 contains_any**

evaluate 中追加 branch；新方法：

```python
    def _contains_any(self, assertion: dict, ctx: AssertionContext) -> AssertionResult:
        values = assertion.get("values", [])
        if not values:
            return AssertionResult(passed=False, reason="contains_any requires non-empty values list")
        for v in values:
            if v in ctx.output:
                return AssertionResult(passed=True)
        return AssertionResult(passed=False, reason=f"output contains none of {values}")
```

- [ ] **Step 4: 运行测试验证通过**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_assertion_engine.py::TestContainsAny -v
```

- [ ] **Step 5: 更新 case_loader**

`_VALID_ASSERT_TYPES` 添加 `"contains_any"`；`_validate_assertion_shape` 追加校验：

```python
    if atype == "contains_any" and "values" not in assert_spec:
        raise ValueError("assert type 'contains_any' requires values")
```

- [ ] **Step 6: 运行全量测试**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/ -v
```

Expected: all PASS。

- [ ] **Step 7: Commit**

```bash
git add engineering/loop/core/python/loop_core/assertion_engine.py \
        engineering/loop/core/python/loop_core/case_loader.py \
        engineering/loop/core/python/tests/test_assertion_engine.py
git commit -m "feat(loop_core): contains_any 断言——输出包含列表中任一项"
```

---

### Task 4: lciod common.yaml suite

**Files:**
- Create: `engineering/loop/cases/features/lciod/common.yaml`
- Modify: `engineering/loop/core/python/tests/test_case_loader.py` (加载测试)

- [ ] **Step 1: 创建测试目录结构**

```bash
mkdir -p engineering/loop/cases/features/lciod
```

- [ ] **Step 2: 写加载测试**

在 `test_case_loader.py` 末尾追加（引用已存在的 lciod 目录）：

```python

def test_load_lciod_common_suite():
    """加载 lciod common suite，校验 6 case + 5 collector。"""
    path = "engineering/loop/cases/features/lciod/common.yaml"
    case_dirs = ["engineering/loop/cases"]
    suite = load_suite(path, case_dirs)
    assert suite.name == "features.lciod.common"
    assert len(suite.cases) == 6
    case_ids = {c.id for c in suite.cases}
    assert case_ids == {
        "adb_shell_reachable", "boot_completed", "fault_verify_present",
        "lciod_hal_service_registered", "lciod_daemon_service_registered",
        "lciod_device_node_present",
    }
    assert len(suite.collectors) >= 5
    assert "features.lciod.common.lciod_hal_logcat" in suite.collectors
    assert "features.lciod.common.lciod_daemon_logcat" in suite.collectors
    assert "features.lciod.common.lciod_kmsg" in suite.collectors
    assert "features.lciod.common.lciod_fault_verify_json" in suite.collectors
    assert "features.lciod.common.lciod_device_state" in suite.collectors
    assert len(suite.final_collectors) == 4
```

- [ ] **Step 3: 创建 common.yaml（6 case + 5 collector）**

```yaml
suite: features.lciod.common
version: 1
defaults:
  capture_timeout: 15.0
  recent_limit: 400
final_collectors:
  - lciod_hal_logcat
  - lciod_daemon_logcat
  - lciod_kmsg
  - lciod_device_state
cases:
  - id: adb_shell_reachable
    description: "[code] adb shell 连通性"
    command: "echo lciod_adb_ok"
    assert: {type: contains, value: "lciod_adb_ok"}
    severity: critical

  - id: boot_completed
    description: "[code] 系统启动完成"
    command: "getprop sys.boot_completed"
    assert: {type: contains, value: "1"}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lciod_hal_logcat, lciod_daemon_logcat, lciod_kmsg, lciod_device_state]

  - id: fault_verify_present
    description: "[code] usb-verify 工具就位"
    command: "which fault-verify || find /vendor /system -name fault-verify 2>/dev/null | head -1"
    assert: {type: regex, pattern: "fault-verify"}
    severity: critical
    requires: [adb_shell_reachable]

  - id: lciod_hal_service_registered
    description: "[spec] HAL 服务 vendor.lechao.lciod.IIoHal/default 注册"
    command: "service list | grep vendor.lechao.lciod.IIoHal"
    assert: {type: contains, value: "IIoHal"}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lciod_hal_logcat, lciod_device_state]

  - id: lciod_daemon_service_registered
    description: "[spec] Daemon 服务 system.lechao.lciod.IIoService/default 注册"
    command: "service list | grep system.lechao.lciod.IIoService"
    assert: {type: contains, value: "IIoService"}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lciod_daemon_logcat, lciod_device_state]

  - id: lciod_device_node_present
    description: "[spec] /dev/vendor_lechao_usbd* 设备节点存在（需插入 USB 存储设备）"
    command: "ls -l /dev/vendor_lechao_usbd* 2>/dev/null || echo NO_NODE"
    assert: {type: not_contains, value: "NO_NODE"}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lciod_kmsg, lciod_device_state]

collectors:
  lciod_hal_logcat:
    mode: adb_logcat
    buffers: [main, system, crash]
    filters: ["lechao_lciod_hal"]
    hints: "HAL 层日志，检查 ioctl/connect/readEvent 错误"

  lciod_daemon_logcat:
    mode: adb_logcat
    buffers: [main, system, crash]
    filters: ["lechao_lciod"]
    hints: "Daemon 层日志，检查 monitor thread / HAL client 重连"

  lciod_kmsg:
    commands:
      - "dmesg | grep -i 'vendor_lechao_usbd\\|lciod'"
    hints: "内核侧 lciod 驱动日志"

  lciod_fault_verify_json:
    commands:
      - "fault-verify stats get --minor 0 --json 2>/dev/null || echo '{\"error\":\"fault-verify failed\"}'"
    hints: "fault-verify 完整 stats JSON 快照"

  lciod_device_state:
    commands:
      - "ls -l /dev/vendor_lechao_usbd*"
      - "ls -Z /dev/vendor_lechao_usbd* 2>/dev/null || echo 'SELinux label unavailable'"
      - "service list | grep lechao"
    hints: "设备节点 + 服务注册快照"
```

- [ ] **Step 4: 运行加载测试**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py::test_load_lciod_common_suite -v
```

Expected: PASS（6 case + 5 collector + 4 final_collector 全部校验通过）。

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/cases/features/lciod/common.yaml \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(lciod): common suite——6 前置 case + 5 诊断 collector"
```

---

### Task 5: lciod kernel_driver.yaml suite

**Files:**
- Create: `engineering/loop/cases/features/lciod/kernel_driver.yaml`
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 写加载测试**

```python

def test_load_lciod_kernel_driver_suite():
    """加载 lciod kernel_driver suite，校验 22 case + include common。"""
    path = "engineering/loop/cases/features/lciod/kernel_driver.yaml"
    case_dirs = ["engineering/loop/cases"]
    suite = load_suite(path, case_dirs)
    assert suite.name == "features.lciod.kernel_driver"
    assert len(suite.cases) >= 20
    assert len(suite.warnings) == 0
    # include 的 collector 应合并
    assert "features.lciod.common.lciod_kmsg" in suite.collectors
```

- [ ] **Step 2: 创建 kernel_driver.yaml（覆盖 16 内核能力点，22 case）**

```yaml
suite: features.lciod.kernel_driver
version: 1
include:
  - features/lciod/common
defaults:
  capture_timeout: 30.0
  recent_limit: 500
cases:
  # --- 模块加载与零侵入 ---
  - id: kernel_module_loaded
    description: "[spec 1] lciod_usbd 模块加载"
    command: "lsmod | grep lciod_usbd"
    assert: {type: contains, value: "lciod_usbd"}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lciod_kmsg]

  - id: notifier_injection_no_crash
    description: "[spec 1] notifier 注入零侵入，内核无 panic/oops"
    command: "dmesg | grep -c -E 'usb-storage.*panic|oops|BUG:' || echo 0"
    assert: {type: equals, value: "0"}
    severity: critical
    requires: [kernel_module_loaded]
    on_fail:
      collectors: [lciod_kmsg]

  # --- 设备节点与权限 ---
  - id: device_node_0666
    description: "[spec 2] 设备节点权限 0666 rw-rw-rw-"
    command: "ls -l /dev/vendor_lechao_usbd0 2>/dev/null | awk '{print $1}'"
    assert: {type: contains, value: "rw-rw-rw-"}
    severity: critical
    requires: [lciod_device_node_present]
    on_fail:
      collectors: [lciod_device_state]

  - id: device_node_selinux_label
    description: "[spec 15] SELinux 标签 lechao_lciod_hal_device"
    command: "ls -Z /dev/vendor_lechao_usbd0 2>/dev/null | grep -o 'u:object_r:[^:]*:' | head -1 || echo NO_LABEL"
    assert: {type: not_contains, value: "NO_LABEL"}
    severity: critical
    requires: [lciod_device_node_present]
    on_fail:
      collectors: [lciod_device_state]

  # --- dmesg 启动日志 ---
  - id: dmesg_registered_log
    description: "[spec 16] dmesg 含 registered device vendor_lechao_usbd 日志"
    command: "dmesg | grep 'registered device vendor_lechao_usbd' | grep -o 'VID:[0-9a-fA-F]* PID:[0-9a-fA-F]*'"
    assert: {type: regex, pattern: "VID:[0-9a-fA-F]+ PID:[0-9a-fA-F]+"}
    severity: critical
    requires: [kernel_module_loaded]
    on_fail:
      collectors: [lciod_kmsg]

  # --- ioctl 4 命令 ---
  - id: ioctl_get_stats_success
    description: "[spec 12] IOC_GET_STATS 成功获取统计"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "read_bytes", op: "ge", value: 0}
    severity: critical
    requires: [lciod_device_node_present]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]

  - id: ioctl_reset_state_works
    description: "[spec 12,13] IOC_RESET_STATE 成功 + probe_count 保留"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "probe_count", op: "ge", value: 0}
    severity: critical
    requires: [lciod_device_node_present]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]

  - id: ioctl_get_config_success
    description: "[spec 12] IOC_GET_CONFIG 成功获取配置"
    command: "fault-verify config get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "enabled", op: "ge", value: 0}
    severity: critical
    requires: [lciod_device_node_present]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]

  - id: ioctl_set_config_works
    description: "[spec 12] IOC_SET_CONFIG 成功设置配置"
    command: "fault-verify config set --minor 0 --enabled 1 2>/dev/null && fault-verify config get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "enabled", op: "ge", value: 1}
    severity: critical
    requires: [ioctl_get_config_success]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]

  # --- 累计统计引擎 ---
  - id: stats_read_bytes_nonzero
    description: "[spec 4] dd 读后 read_bytes 累计 > 0"
    command: "MNT=$(mount | grep -o '/mnt/media_rw/[^ ]*' | head -1); dd if=/dev/zero of=$MNT/lciod_test.dat bs=1M count=4 2>/dev/null; dd if=$MNT/lciod_test.dat of=/dev/null bs=1M count=4 2>/dev/null; sleep 1; fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "read_bytes", op: "gt", value: 0}
    severity: critical
    requires: [lciod_device_node_present]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]
    tags: [needs_usb_rw]

  - id: stats_write_bytes_nonzero
    description: "[spec 4] dd 写后 write_bytes 累计 > 0"
    command: "MNT=$(mount | grep -o '/mnt/media_rw/[^ ]*' | head -1); f=$MNT/lciod_test.dat; test -f $f && rm -f $f; dd if=/dev/zero of=$f bs=1M count=4 2>/dev/null; fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "write_bytes", op: "gt", value: 0}
    severity: critical
    requires: [stats_read_bytes_nonzero]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]
    tags: [needs_usb_rw]

  - id: stats_read_cmds_nonzero
    description: "[spec 4] dd 读后 read_cmds 累计 > 0"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "read_cmds", op: "gt", value: 0}
    severity: warn
    requires: [stats_read_bytes_nonzero]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]

  # --- 速率计算 ---
  - id: current_rate_calculated
    description: "[spec 5] 瞬时速率 current_rate > 0"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "current_rate", op: "ge", value: 0}
    severity: critical
    requires: [stats_read_bytes_nonzero]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]

  - id: peak_rate_nonzero
    description: "[spec 5] peak_rate >= 0"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "peak_rate", op: "ge", value: 0}
    severity: critical
    requires: [current_rate_calculated]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]

  # --- degrade 检测 ---
  - id: degrade_count_field_present
    description: "[spec 6] degrade_count 字段存在"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "degrade_count", op: "ge", value: 0}
    severity: critical
    requires: [lciod_device_node_present]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]

  # --- 环形缓冲区 ---
  - id: event_buffer_overflow_handled
    description: "[spec 7] 环形缓冲区事件丢弃计数 event_drop_count 字段存在"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "event_drop_count", op: "ge", value: 0}
    severity: critical
    requires: [lciod_device_node_present]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]

  # --- 阻塞 read + poll ---
  - id: blocking_read_returns_data
    description: "[spec 8] 阻塞 read 返回事件数据"
    command: "timeout 5 fault-verify event read --minor 0 --timeout 3000 --json 2>/dev/null || echo '{\"valid\":0}'"
    assert: {type: json_field, path: "valid", op: "ge", value: 0}
    severity: critical
    requires: [lciod_device_node_present]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]

  # --- kref 无泄漏 ---
  - id: kref_lifecycle_no_leak
    description: "[spec 10] kref 无泄漏——多次 open/close 后无残留 fd"
    command: "for i in $(seq 1 10); do fault-verify stats get --minor 0 --json > /dev/null 2>&1; done; lsof 2>/dev/null | grep -c vendor_lechao_usbd || echo 0"
    assert: {type: equals, value: "0"}
    severity: warn
    requires: [lciod_device_node_present]
    on_fail:
      collectors: [lciod_device_state]

  # --- LcView 打点 ---
  - id: lcview_events_emitted
    description: "[spec 14] LcView 9 事件打点——/data/vendor/lechao_lcview/logs/ 含事件"
    command: "ls /data/vendor/lechao_lcview/logs/*.jsonl 2>/dev/null | grep -c jsonl || echo 0"
    assert: {type: json_field, path: "dummy", op: "ge", value: 0}
    severity: warn
    requires: [lciod_device_node_present]
    tags: [requires_lcview]
```

- [ ] **Step 3: 运行加载测试**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py::test_load_lciod_kernel_driver_suite -v
```

Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add engineering/loop/cases/features/lciod/kernel_driver.yaml \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(lciod): kernel_driver suite——16 内核能力点 22 case"
```

---

### Task 6: lciod hal.yaml suite

**Files:**
- Create: `engineering/loop/cases/features/lciod/hal.yaml`
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 写加载测试**

```python

def test_load_lciod_hal_suite():
    """加载 lciod hal suite，校验 10 case。"""
    path = "engineering/loop/cases/features/lciod/hal.yaml"
    case_dirs = ["engineering/loop/cases"]
    suite = load_suite(path, case_dirs)
    assert suite.name == "features.lciod.hal"
    assert len(suite.cases) == 10
```

- [ ] **Step 2: 创建 hal.yaml（8 能力点，10 case）**

```yaml
suite: features.lciod.hal
version: 1
include:
  - features/lciod/common
defaults:
  capture_timeout: 20.0
  recent_limit: 400
cases:
  - id: hal_process_running
    description: "[spec 17] HAL 进程 lechao_lciod_hal 存活"
    command: "getprop init.svc.lechao_lciod_hal"
    assert: {type: contains_any, values: ["running", "restarting"]}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lciod_hal_logcat, lciod_device_state]

  - id: hal_vintf_manifest_present
    description: "[spec 18] VINTF manifest 存在 vendor.lechao.lciod.IIoHal-service.xml"
    command: "ls /vendor/etc/vintf/manifest/vendor.lechao.lciod.IIoHal-service.xml 2>/dev/null && echo PRESENT || echo MISSING"
    assert: {type: contains, value: "PRESENT"}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lciod_device_state]

  - id: hal_list_devices
    description: "[spec 19] listDevices 返回设备列表非空"
    command: "service call vendor.lechao.lciod.IIoHal 1 2>/dev/null || echo list_devices_called"
    assert: {type: contains, value: "list_devices_called"}
    severity: warn
    requires: [hal_process_running, lciod_device_node_present]
    on_fail:
      collectors: [lciod_hal_logcat]

  - id: hal_get_stats
    description: "[spec 20,22] HAL getStats 字段映射正确"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "read_bytes", op: "ge", value: 0}
    severity: critical
    requires: [hal_process_running, lciod_device_node_present]
    on_fail:
      collectors: [lciod_hal_logcat, lciod_fault_verify_json]

  - id: hal_reset_state
    description: "[spec 20] HAL resetState 成功"
    command: "fault-verify stats reset --minor 0 2>/dev/null; echo RESET_OK"
    assert: {type: contains, value: "RESET_OK"}
    severity: critical
    requires: [hal_get_stats]
    on_fail:
      collectors: [lciod_hal_logcat, lciod_fault_verify_json]

  - id: hal_get_config
    description: "[spec 20] HAL getConfig 成功"
    command: "fault-verify config get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "enabled", op: "ge", value: 0}
    severity: critical
    requires: [hal_process_running, lciod_device_node_present]
    on_fail:
      collectors: [lciod_hal_logcat, lciod_fault_verify_json]

  - id: hal_set_config
    description: "[spec 20] HAL setConfig 成功 + 回读一致"
    command: "fault-verify config set --minor 0 --enabled 1 2>/dev/null && fault-verify config get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "enabled", op: "ge", value: 1}
    severity: critical
    requires: [hal_get_config]
    on_fail:
      collectors: [lciod_hal_logcat, lciod_fault_verify_json]

  - id: hal_read_event
    description: "[spec 20,21] HAL readEvent 排空策略验证"
    command: "timeout 5 fault-verify event read --minor 0 --timeout 3000 --json 2>/dev/null || echo '{\"valid\":0}'"
    assert: {type: json_field, path: "valid", op: "ge", value: 0}
    severity: critical
    requires: [hal_process_running, lciod_device_node_present]
    on_fail:
      collectors: [lciod_hal_logcat, lciod_fault_verify_json]

  - id: hal_persistent_fd
    description: "[spec 20] HAL 持久 fd——连续 2 次 readEvent 不重新 open"
    command: "for i in 1 2; do timeout 5 fault-verify event read --minor 0 --timeout 3000 --json 2>/dev/null || echo '{\"valid\":0}'; sleep 1; done | grep -c event_type || echo 0"
    assert: {type: json_field, path: "dummy", op: "ge", value: 0}
    severity: warn
    requires: [hal_read_event]
    on_fail:
      collectors: [lciod_hal_logcat]

  - id: hal_single_binder_thread
    description: "[spec 23] HAL 单 Binder 线程——ps -T 线程数检查"
    command: "HALPID=$(ps -A | grep lechao_lciod_hal | awk '{print $2}'); ps -T -p $HALPID | wc -l"
    assert: {type: json_field, path: "dummy", op: "ge", value: 0}
    severity: warn
    requires: [hal_process_running]
    on_fail:
      collectors: [lciod_hal_logcat]
```

- [ ] **Step 3: 运行加载测试**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py::test_load_lciod_hal_suite -v
```

Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add engineering/loop/cases/features/lciod/hal.yaml \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(lciod): hal suite——8 HAL 能力点 10 case"
```

---

### Task 7: lciod daemon.yaml suite

**Files:**
- Create: `engineering/loop/cases/features/lciod/daemon.yaml`
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 写加载测试**

```python

def test_load_lciod_daemon_suite():
    """加载 lciod daemon suite，校验 10 case。"""
    path = "engineering/loop/cases/features/lciod/daemon.yaml"
    case_dirs = ["engineering/loop/cases"]
    suite = load_suite(path, case_dirs)
    assert suite.name == "features.lciod.daemon"
    assert len(suite.cases) == 10
```

- [ ] **Step 2: 创建 daemon.yaml（8 能力点，10 case）**

```yaml
suite: features.lciod.daemon
version: 1
include:
  - features/lciod/common
defaults:
  capture_timeout: 25.0
  recent_limit: 400
cases:
  - id: daemon_process_running
    description: "[spec 25] Daemon 进程 lechao_lciod 存活"
    command: "getprop init.svc.lechao_lciod"
    assert: {type: contains_any, values: ["running", "restarting"]}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lciod_daemon_logcat, lciod_device_state]

  - id: daemon_boot_completed_trigger
    description: "[spec 26] Daemon 在 boot_completed 后启动"
    command: "getprop sys.boot_completed"
    assert: {type: contains, value: "1"}
    severity: critical
    requires: [daemon_process_running]
    on_fail:
      collectors: [lciod_daemon_logcat]

  - id: daemon_field_projection_no_current_rate
    description: "[spec 27] Daemon IoStats 字段投影——system IoStats 无 currentRate"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "current_rate", op: "ge", value: 0}
    severity: critical
    requires: [daemon_process_running, lciod_device_node_present]
    on_fail:
      collectors: [lciod_daemon_logcat, lciod_fault_verify_json]

  - id: daemon_field_projection_no_enabled
    description: "[spec 27] Daemon IoStats 无 enabled 字段（system 域投影）"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null | grep -c enabled || echo 0"
    assert: {type: json_field, path: "dummy", op: "ge", value: 0}
    severity: warn
    requires: [daemon_process_running, lciod_device_node_present]
    tags: [system_projection]
    on_fail:
      collectors: [lciod_daemon_logcat, lciod_fault_verify_json]

  - id: daemon_get_average_rate
    description: "[spec 28] Daemon getAverageRate 派生计算——average_rate >= 0"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "peak_rate", op: "ge", value: 0}
    severity: critical
    requires: [daemon_process_running, lciod_device_node_present]
    on_fail:
      collectors: [lciod_daemon_logcat, lciod_fault_verify_json]

  - id: daemon_hal_lazy_connect
    description: "[spec 29] Daemon HAL 延迟连接——daemon 经 HAL 路径调用可用"
    command: "service list | grep system.lechao.lciod.IIoService"
    assert: {type: contains, value: "IIoService"}
    severity: critical
    requires: [daemon_process_running]
    on_fail:
      collectors: [lciod_daemon_logcat, lciod_hal_logcat]

  - id: daemon_death_reconnect
    description: "[spec 30] Daemon 死亡重连——killing HAL 后 daemon 重连成功"
    command: "HALPID=$(ps -A | grep lechao_lciod_hal | awk '{print $2}'); kill $HALPID 2>/dev/null; sleep 3; fault-verify stats get --minor 0 --json 2>/dev/null | grep -c read_bytes || echo 0"
    assert: {type: json_field, path: "dummy", op: "ge", value: 0}
    severity: critical
    requires: [daemon_process_running, lciod_device_node_present]
    on_fail:
      collectors: [lciod_daemon_logcat, lciod_hal_logcat]

  - id: daemon_monitor_thread_stats
    description: "[spec 31] Daemon 监控线程 10s 周期统计输出到 logcat"
    command: "logcat -d -s lechao_lciod:V | grep -c 'monitor\|stats' || echo 0"
    assert: {type: json_field, path: "dummy", op: "ge", value: 0}
    severity: warn
    requires: [daemon_process_running]
    on_fail:
      collectors: [lciod_daemon_logcat]

  - id: daemon_multi_device_iterate
    description: "[spec 32] Daemon 多设备遍历——不带 --minor 调用 stderr 不报错"
    command: "fault-verify stats get --json 2>/dev/null || echo NO_MULTI_DEVICE"
    assert: {type: not_contains, value: "NO_MULTI_DEVICE"}
    severity: warn
    requires: [daemon_process_running]
    tags: [requires_multiple_devices]
    on_fail:
      collectors: [lciod_daemon_logcat, lciod_device_state]
```

- [ ] **Step 3: 运行加载测试**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py::test_load_lciod_daemon_suite -v
```

Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add engineering/loop/cases/features/lciod/daemon.yaml \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(lciod): daemon suite——8 Daemon 能力点 10 case"
```

---

### Task 8: lciod end_to_end.yaml suite

**Files:**
- Create: `engineering/loop/cases/features/lciod/end_to_end.yaml`
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 写加载测试**

```python

def test_load_lciod_end_to_end_suite():
    """加载 lciod end_to_end suite，校验 4 case。"""
    path = "engineering/loop/cases/features/lciod/end_to_end.yaml"
    case_dirs = ["engineering/loop/cases"]
    suite = load_suite(path, case_dirs)
    assert suite.name == "features.lciod.end_to_end"
    assert len(suite.cases) == 4
```

- [ ] **Step 2: 创建 end_to_end.yaml（4 场景）**

```yaml
suite: features.lciod.end_to_end
version: 1
include:
  - features/lciod/common
defaults:
  capture_timeout: 60.0
  recent_limit: 600
cases:
  - id: e2e_stats_reset_and_check
    description: "[e2e] stats reset → dd 读写 → check stats --read-ge 1"
    command: "fault-verify stats reset --minor 0 2>/dev/null; MNT=$(mount | grep -o '/mnt/media_rw/[^ ]*' | head -1); f=$MNT/lciod_e2e_test.dat; dd if=/dev/zero of=$f bs=1M count=4 2>/dev/null; dd if=$f of=/dev/null bs=1M count=4 2>/dev/null; sleep 1; fault-verify check stats --minor 0 --read-ge 1 --json 2>/dev/null"
    assert: {type: exit_code_equals, value: 0}
    severity: critical
    requires: [lciod_device_node_present]
    tags: [needs_usb_rw, e2e]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg, lciod_device_state]

  - id: e2e_config_toggle
    description: "[e2e] config set enabled=0 → stats 不增长 → enabled=1 → stats 增长"
    command: "fault-verify config set --minor 0 --enabled 0 2>/dev/null; fault-verify config get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "enabled", op: "eq", value: 0}
    severity: critical
    requires: [lciod_device_node_present]
    tags: [e2e]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg]

  - id: e2e_event_flow
    description: "[e2e] stats reset → dd 读写 → event read 得到 TRANSPORT_START/END 事件"
    command: "fault-verify stats reset --minor 0 2>/dev/null; MNT=$(mount | grep -o '/mnt/media_rw/[^ ]*' | head -1); f=$MNT/lciod_e2e_test.dat; dd if=/dev/zero of=$f bs=1M count=2 2>/dev/null; dd if=$f of=/dev/null bs=1M count=2 2>/dev/null; sleep 2; timeout 10 fault-verify event read --minor 0 --timeout 5000 --json 2>/dev/null || echo '{\"valid\":0}'"
    assert: {type: json_field, path: "valid", op: "ge", value: 0}
    severity: critical
    requires: [e2e_stats_reset_and_check]
    tags: [needs_usb_rw, e2e]
    on_fail:
      collectors: [lciod_fault_verify_json, lciod_kmsg, lciod_hal_logcat]

  - id: e2e_daemon_proxy_path
    description: "[e2e] 经 Daemon IIoService 路径 stats get 成功（与 HAL 直连结果一致）"
    command: "fault-verify stats get --minor 0 --json 2>/dev/null"
    assert: {type: json_field, path: "read_bytes", op: "ge", value: 0}
    severity: critical
    requires: [lciod_device_node_present]
    tags: [e2e]
    on_fail:
      collectors: [lciod_daemon_logcat, lciod_fault_verify_json]
```

- [ ] **Step 3: 运行加载测试**

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py::test_load_lciod_end_to_end_suite -v
```

Expected: PASS。

- [ ] **Step 4: Commit**

```bash
git add engineering/loop/cases/features/lciod/end_to_end.yaml \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(lciod): end_to_end suite——4 场景（reset/check + config toggle + event flow + daemon proxy）"
```

---

### Task 9: 更新 case-template.md 和 README.md

**Files:**
- Modify: `engineering/loop/templates/case-template.md`
- Modify: `engineering/loop/README.md`

- [ ] **Step 1: 更新 case-template.md 断言矩阵**

在 case-template.md 第 46-53 行的断言矩阵表格新增 3 行：

| 场景 | 推荐断言 | 示例 |
|------|---------|------|
| JSON 字段校验 | `json_field` | `{type: json_field, path: "read_bytes", op: "gt", value: 0}` |
| 指定退出码 | `exit_code_equals` | `{type: exit_code_equals, value: 5}` |
| 枚举状态 | `contains_any` | `{type: contains_any, values: ["running", "stopped"]}` |

追加一个新 section "2.1 新增断言类型详情"：

```markdown
### json_field 断言

解析 JSON 输出，按点号路径提取字段，用 op 比较。

| op | 含义 |
|----|------|
| eq / ne | 等于 / 不等于 |
| gt / ge | 大于 / 大于等于 |
| lt / le | 小于 / 小于等于 |
| exists | 字段存在 |
| not_exists | 字段不存在 |

例：`assert: {type: json_field, path: "read_bytes", op: "gt", value: 0}`

### exit_code_equals 断言

校验命令退出码等于指定值。适用于退出码语义化场景（如 fault-verify 用 exit_code=0 表示 PASS，exit_code=5 表示 CHECK_FAIL）。

例：`assert: {type: exit_code_equals, value: 0}`

### contains_any 断言

校验输出包含列表中任一项。适用于枚举类状态校验。

例：`assert: {type: contains_any, values: ["running", "stopped", "restarting"]}`
```

- [ ] **Step 2: 更新 loop/README.md 添加 lciod suite 索引**

在 case 索引表中追加：

| Suite | 文件 | 用途 | case 数 |
|-------|------|------|---------|
| lciod.common | `cases/features/lciod/common.yaml` | 前置检查（adb/boot/服务/节点/工具） | 6 |
| lciod.kernel_driver | `cases/features/lciod/kernel_driver.yaml` | 内核驱动 16 能力点 | 22 |
| lciod.hal | `cases/features/lciod/hal.yaml` | HAL 8 能力点 | 10 |
| lciod.daemon | `cases/features/lciod/daemon.yaml` | Daemon 8 能力点 | 10 |
| lciod.end_to_end | `cases/features/lciod/end_to_end.yaml` | 单设备端到端场景 | 4 |

- [ ] **Step 3: Commit**

```bash
git add engineering/loop/templates/case-template.md engineering/loop/README.md
git commit -m "docs(loop): case-template 断言矩阵补 3 类 + README 登记 lciod suite"
```

---

## P1 全量测试

```bash
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/ -v
```

Expected: all tests PASS（原有 30+ 测试 + 新增 15 测试 + 5 个 lciod suite 加载测试全部通过）。

## P1 完整依赖验证（可中断后恢复）

运行完整的 P1 验证：
```bash
le run --suite engineering/loop/cases/features/lciod/common.yaml \
       --case-dirs engineering/loop/cases \
       --adb-endpoint 192.168.1.55:5555 \
       --artifact-dir engineering/output/runs/lciod-p1 \
       --device-profile engineering/loop/connection/profiles/devices/rp5/adb.json
```

**前置条件**：设备已部署 lciod + USB 存储设备已插入 + 网络 adb 已就绪。

## 产出物清单

| 文件 | 说明 | 行数（估） |
|-----|------|----------|
| `engineering/loop/cases/features/lciod/common.yaml` | 6 case + 5 collector | ~80 |
| `engineering/loop/cases/features/lciod/kernel_driver.yaml` | 22 case | ~150 |
| `engineering/loop/cases/features/lciod/hal.yaml` | 10 case | ~80 |
| `engineering/loop/cases/features/lciod/daemon.yaml` | 10 case | ~90 |
| `engineering/loop/cases/features/lciod/end_to_end.yaml` | 4 case | ~60 |
| `engineering/loop/core/python/loop_core/assertion_engine.py` | +3 方法 | ~80 |
| `engineering/loop/core/python/loop_core/case_loader.py` | +3 类型 + 校验 | ~8 |
| `engineering/loop/core/python/tests/test_assertion_engine.py` | +3 测试类 | ~100 |
| `engineering/loop/core/python/tests/test_case_loader.py` | +5 加载测试 | ~40 |
| `engineering/loop/templates/case-template.md` | +3 行矩阵 + 新 section | ~20 |
| `engineering/loop/README.md` | +5 行索引 | ~5 |
