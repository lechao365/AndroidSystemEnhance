# Network ADB Success Implementation Plan

> **2026-06-24 更新**：设备 IP 发现已从"固定 IP"切换为"串口动态发现"，见 `engineering/loop/scripts/rp5_serial_helper.py` 和 `engineering/loop/WORKFLOW.md` 的「传输层依赖链」章节。本文档中残留的 `192.168.1.55` 仅为历史决策记录。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Loop Engineering 增加 `system.network_adbd` 验收场景，并以最小框架改动支持 `run_on: host|device` 双执行平面，从串口主链完成 reboot→Wi‑Fi→adbd→host `adb connect 192.168.1.55:5555` 的自动验收闭环。

**Architecture:** 继续以 `rp5-serial` 作为主 transport，不引入完整 ADB provider。通过在 `case_loader` / `executor` / `collector` 上增加 `run_on` 语义与 host subprocess runner，把 host 侧命令统一纳入现有 `TestCaseResult` / `CollectorResult` 模型；在场景层新增 `network-adbd-success.yaml` 与本地 Wi‑Fi / adbd / host adb collector，最大化复用 `common/shell`、`boot-success`、`action: reboot` 与现有断言引擎。

**Tech Stack:** Python 3.10+（dataclass / subprocess / pytest）、YAML suite 定义、现有 `rp5-serial` provider、bash / adb host 命令。

**Spec:** `docs/specs/2026-06-21-network-adbd-success-design.md`

---

## File Structure

- Modify: `engineering/loop/core/python/loop_core/case_loader.py`
  - 为 `TestCase` 增加 `run_on` 字段，补充 host/device 静态校验，并让 include / defaults / collector 继续向后兼容。
- Modify: `engineering/loop/core/python/loop_core/executor.py`
  - 为 case 执行路径增加 host 分支；保持 `reboot` 与 device transport 逻辑不变。
- Modify: `engineering/loop/core/python/loop_core/collector.py`
  - 为 collector 增加 host 分支；device collector 与 `serial_context` 逻辑继续保留。
- Create: `engineering/loop/core/python/loop_core/host_exec.py`
  - 提供统一 host subprocess runner，供 executor / collector 复用，避免复制命令执行与错误处理代码。
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`
  - 补 `run_on` 字段、非法组合、collector host 校验测试。
- Modify: `engineering/loop/core/python/tests/test_executor.py`
  - 补 host case 执行、断言失败、异常映射测试。
- Modify: `engineering/loop/core/python/tests/test_collector.py`
  - 补 host collector `ok / degraded / error` 测试。
- Create: `engineering/loop/core/python/tests/test_host_exec.py`
  - 为 host runner 建独立单元测试，锁定 stdout/stderr/exit code/异常语义。
- Create: `engineering/loop/cases/system/network-adbd-success.yaml`
  - 定义 `system.network_adbd` suite、network-adbd 本地 collector 与 host `adb connect` 终态验证。
- Modify: `engineering/loop/README.md`
  - 记录 `run_on` 语义与 `system.network_adbd` 场景用途，避免文档与用例脱节。

---

### Task 1: 给 `case_loader` 增加 `run_on` 字段与静态校验

**Files:**
- Modify: `engineering/loop/core/python/loop_core/case_loader.py`
- Test: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 先写失败测试，锁定 `run_on` 基本语义**

在 `engineering/loop/core/python/tests/test_case_loader.py` 末尾追加：

```python
def test_case_run_on_defaults_to_device(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: hostless
    command: "echo ok"
    assert: {type: contains, value: "ok"}
""")
    suite = load_suite(path, [str(tmp_path)])
    assert suite.cases[0].run_on == "device"


def test_case_run_on_host_is_parsed(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: host_case
    run_on: host
    command: "python3 -c 'print(\"ok\")'"
    assert: {type: contains, value: "ok"}
""")
    suite = load_suite(path, [str(tmp_path)])
    assert suite.cases[0].run_on == "host"


def test_invalid_case_run_on_raises(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: bad
    run_on: cloud
    command: "echo ok"
    assert: {type: contains, value: "ok"}
""")
    with pytest.raises(ValueError, match="invalid run_on"):
        load_suite(path, [str(tmp_path)])


def test_host_reboot_action_is_rejected(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: bad_reboot
    run_on: host
    action: reboot
    assert: {}
""")
    with pytest.raises(ValueError, match="reboot action requires run_on=device"):
        load_suite(path, [str(tmp_path)])


def test_prompt_visible_host_case_is_rejected(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: bad_prompt
    run_on: host
    command: "python3 -c 'print(\"ok\")'"
    assert: {type: prompt_visible}
""")
    with pytest.raises(ValueError, match="prompt_visible requires run_on=device"):
        load_suite(path, [str(tmp_path)])


def test_empty_host_command_is_rejected(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: bad_empty
    run_on: host
    command: ""
    assert: {type: contains, value: "ok"}
""")
    with pytest.raises(ValueError, match="host case requires non-empty command"):
        load_suite(path, [str(tmp_path)])


def test_host_collector_is_preserved(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: "echo fail"
    assert: {type: contains, value: "ok"}
    on_fail:
      collectors: [host_debug]
collectors:
  host_debug:
    run_on: host
    commands: ["python3 -c 'print(\"dbg\")'"]
    hints: "host side"
""")
    suite = load_suite(path, [str(tmp_path)])
    assert suite.collectors["t.host_debug"]["run_on"] == "host"


def test_host_serial_context_collector_is_rejected(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: "echo fail"
    assert: {type: contains, value: "ok"}
collectors:
  bad_ctx:
    run_on: host
    mode: serial_context
    commands: []
""")
    with pytest.raises(ValueError, match="serial_context collector requires run_on=device"):
        load_suite(path, [str(tmp_path)])


def test_host_collector_requires_commands(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: a
    command: "echo fail"
    assert: {type: contains, value: "ok"}
collectors:
  bad_host:
    run_on: host
    commands: []
""")
    with pytest.raises(ValueError, match="host collector requires at least one command"):
        load_suite(path, [str(tmp_path)])
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py -v
```

Expected: FAIL，至少会出现：
- `TestCase` 没有 `run_on`
- `invalid run_on` / `host case requires non-empty command` 等校验尚未实现

- [ ] **Step 3: 修改 `TestCase` 与解析逻辑，新增 `run_on` 字段**

在 `engineering/loop/core/python/loop_core/case_loader.py` 做如下修改：

1. 给 `TestCase` dataclass 增加字段：
```python
run_on: str = "device"
```

2. 在 `_parse_case()` 中透传：
```python
run_on=defn.get("run_on", "device"),
```

3. 新增允许值集合：
```python
_VALID_RUN_ON = {"device", "host"}
```

4. 在 `_validate_case_definition()` 中加入：
```python
run_on = defn.get("run_on", "device")
if run_on not in _VALID_RUN_ON:
    raise ValueError(f"invalid run_on: {run_on}")
if action == "reboot" and run_on != "device":
    raise ValueError("reboot action requires run_on=device")
if defn.get("assert", {}).get("type") == "prompt_visible" and run_on != "device":
    raise ValueError("prompt_visible requires run_on=device")
if run_on == "host" and not command:
    raise ValueError("host case requires non-empty command")
```

5. 在合并 collector 后、`_resolve_case_links()` 前新增 collector 校验函数，例如：
```python
def _validate_collectors(collectors: dict[str, dict]) -> None:
    for fqn, spec in collectors.items():
        run_on = spec.get("run_on", "device")
        if run_on not in _VALID_RUN_ON:
            raise ValueError(f"invalid run_on in collector '{fqn}': {run_on}")
        mode = spec.get("mode", "commands")
        commands = spec.get("commands", [])
        if mode == "serial_context" and run_on != "device":
            raise ValueError("serial_context collector requires run_on=device")
        if run_on == "host" and not commands:
            raise ValueError("host collector requires at least one command")
```

6. 在 `load_suite()` 中合并完 `all_collectors` 后调用：
```python
_validate_collectors(all_collectors)
```

- [ ] **Step 4: 重新运行 `test_case_loader.py`，确认全部通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py -v
```

Expected: PASS

- [ ] **Step 5: 提交本任务改动**

```bash
git add engineering/loop/core/python/loop_core/case_loader.py \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(loop-core): add host run_on schema"
```

---

### Task 2: 新增可复用 host subprocess runner

**Files:**
- Create: `engineering/loop/core/python/loop_core/host_exec.py`
- Test: `engineering/loop/core/python/tests/test_host_exec.py`

- [ ] **Step 1: 写失败测试，锁定 host runner 语义**

创建 `engineering/loop/core/python/tests/test_host_exec.py`：

```python
import pytest

from loop_core.host_exec import HostCommandError, run_host_command


def test_run_host_command_returns_stdout_and_exit_code():
    result = run_host_command("python3 -c 'print(\"ok\")'", timeout_sec=5.0)
    assert result.exit_code == 0
    assert result.output.strip() == "ok"
    assert result.error == ""


def test_run_host_command_merges_stderr_into_output():
    result = run_host_command(
        "python3 -c 'import sys; sys.stderr.write(\"err\\n\")'",
        timeout_sec=5.0,
    )
    assert result.exit_code == 0
    assert "err" in result.output


def test_run_host_command_preserves_nonzero_exit_code():
    result = run_host_command(
        "python3 -c 'import sys; print(\"bad\"); sys.exit(7)'",
        timeout_sec=5.0,
    )
    assert result.exit_code == 7
    assert "bad" in result.output


def test_run_host_command_timeout_raises_host_command_error():
    with pytest.raises(HostCommandError, match="timed out"):
        run_host_command("python3 -c 'import time; time.sleep(2)'", timeout_sec=0.2)
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/test_host_exec.py -v
```

Expected: FAIL，提示 `loop_core.host_exec` 不存在

- [ ] **Step 3: 实现 `host_exec.py` 最小运行器**

创建 `engineering/loop/core/python/loop_core/host_exec.py`：

```python
"""host 侧命令执行器。"""
from __future__ import annotations

from dataclasses import dataclass
import subprocess


class HostCommandError(RuntimeError):
    """host 命令执行阶段的不可恢复错误。"""


@dataclass
class HostCommandResult:
    command: str
    output: str
    exit_code: int
    error: str = ""


def run_host_command(command: str, timeout_sec: float) -> HostCommandResult:
    """在 host 本机执行一条 shell 命令并返回统一结果。"""
    try:
        completed = subprocess.run(
            ["bash", "-lc", command],
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise HostCommandError(f"host command timed out after {timeout_sec}s: {command}") from exc
    except OSError as exc:
        raise HostCommandError(f"failed to execute host command: {exc}") from exc

    output = (completed.stdout or "") + (completed.stderr or "")
    return HostCommandResult(
        command=command,
        output=output,
        exit_code=completed.returncode,
        error="" if completed.returncode == 0 else f"exit code {completed.returncode}",
    )
```

- [ ] **Step 4: 运行 `test_host_exec.py`，确认通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/test_host_exec.py -v
```

Expected: PASS

- [ ] **Step 5: 提交本任务改动**

```bash
git add engineering/loop/core/python/loop_core/host_exec.py \
        engineering/loop/core/python/tests/test_host_exec.py
git commit -m "feat(loop-core): add host subprocess runner"
```

---

### Task 3: 在 executor 中接入 `run_on: host` case 执行分支

**Files:**
- Modify: `engineering/loop/core/python/loop_core/executor.py`
- Modify: `engineering/loop/core/python/tests/test_executor.py`

- [ ] **Step 1: 写失败测试，锁定 host case 的 pass / fail / error 映射**

在 `engineering/loop/core/python/tests/test_executor.py` 末尾追加：

```python
def test_host_case_passes_with_contains_assertion(tmp_path):
    suite_yaml = """
suite: t
version: 1
cases:
  - id: host_ok
    run_on: host
    command: "python3 -c 'print(\"connected to 192.168.1.55:5555\")'"
    assert: {type: contains, value: "connected to"}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite,
        device_id="rp5",
        prompt_markers=["console:/ $"],
    )
    assert bundle.cases[0].status == "pass"
    assert "connected to" in bundle.cases[0].output


def test_host_case_fails_when_assertion_not_met(tmp_path):
    suite_yaml = """
suite: t
version: 1
cases:
  - id: host_fail
    run_on: host
    command: "python3 -c 'print(\"offline\")'"
    assert: {type: contains, value: "connected to"}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite,
        device_id="rp5",
        prompt_markers=["console:/ $"],
    )
    assert bundle.cases[0].status == "fail"
    assert "expected output to contain 'connected to'" in bundle.cases[0].failure_reason


def test_host_case_supports_exit_code_zero_assertion(tmp_path):
    suite_yaml = """
suite: t
version: 1
cases:
  - id: host_exit
    run_on: host
    command: "python3 -c 'import sys; sys.exit(0)'"
    assert: {type: exit_code_zero}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite,
        device_id="rp5",
        prompt_markers=["console:/ $"],
    )
    assert bundle.cases[0].status == "pass"


def test_host_case_runtime_error_maps_to_error_status(tmp_path):
    suite_yaml = """
suite: t
version: 1
cases:
  - id: host_err
    run_on: host
    command: "python3 -c 'import time; time.sleep(2)'"
    assert: {type: contains, value: "ok"}
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])
    transport = _make_transport([])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite,
        device_id="rp5",
        prompt_markers=["console:/ $"],
        capture_timeout=0.2,
    )
    assert bundle.cases[0].status == "error"
    assert bundle.cases[0].error_type in {"host_error", "HostCommandError"}
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/test_executor.py -v
```

Expected: FAIL，因为 executor 还没有 host 分支

- [ ] **Step 3: 在 `executor.py` 增加 host case 分支**

在 `engineering/loop/core/python/loop_core/executor.py` 中：

1. 新增导入：
```python
from loop_core.host_exec import HostCommandError, run_host_command
from loop_core.transport import CommandCapture
```

2. 在命令执行分支前增加一个小辅助方法（放在 `CaseExecutor` 内部或私有函数均可）：
```python
def _capture_host_command(self, command: str, timeout_sec: float) -> CommandCapture:
    result = run_host_command(command, timeout_sec)
    lines = [ObservedLine(t=0.0, text=line) for line in result.output.splitlines()]
    return CommandCapture(lines=lines, prompt_visible=False, exit_code=result.exit_code)
```

3. 在现有 `if case.command:` 之前按 `case.run_on` 分流：
```python
if case.command and getattr(case, "run_on", "device") == "host":
    start = time.monotonic()
    capture = self._capture_host_command(case.command, capture_timeout)
    output_lines = [line.text for line in capture.lines]
    prompt_visible = capture.prompt_visible
    output_text = "\n".join(output_lines)
    duration = round(time.monotonic() - start, 3)
    ctx = AssertionContext(
        output=output_text,
        prompt_visible=prompt_visible,
        exit_code=capture.exit_code,
    )
    result = self.engine.evaluate(case.assert_spec, ctx)
elif case.command:
    ...  # 保持现有 device transport 分支不变
```

4. 在异常分支中把 `HostCommandError` 映射到 `error`：
```python
except HostCommandError as exc:
    return TestCaseResult(
        id=case.id,
        suite=case.suite,
        status="error",
        command=case.command,
        failure_reason=str(exc),
        error_type="host_error",
        tags=case.tags,
    )
```

5. 保持 `action: reboot` 与现有 device transport 逻辑不动。

- [ ] **Step 4: 运行 `test_executor.py`，确认 host case 相关测试通过且旧测试不回归**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/test_executor.py -v
```

Expected: PASS

- [ ] **Step 5: 提交本任务改动**

```bash
git add engineering/loop/core/python/loop_core/executor.py \
        engineering/loop/core/python/tests/test_executor.py
git commit -m "feat(loop-core): execute host cases via subprocess"
```

---

### Task 4: 在 collector 中接入 `run_on: host` 采证分支

**Files:**
- Modify: `engineering/loop/core/python/loop_core/collector.py`
- Modify: `engineering/loop/core/python/tests/test_collector.py`

- [ ] **Step 1: 写失败测试，锁定 host collector 行为**

在 `engineering/loop/core/python/tests/test_collector.py` 末尾追加：

```python
def test_host_collector_runs_commands_locally():
    transport = FixtureTransport([])
    transport.acquire_writer()
    result = Collector(transport).run(
        "host_debug",
        {
            "run_on": "host",
            "commands": ["python3 -c 'print(\"host dbg\")'"],
            "hints": "host side",
        },
        capture_timeout=5.0,
        recent_limit=100,
    )
    assert result.status == "ok"
    assert result.outputs[0]["lines"] == ["host dbg"]


def test_host_collector_partial_failure_becomes_degraded():
    transport = FixtureTransport([])
    transport.acquire_writer()
    result = Collector(transport).run(
        "host_mix",
        {
            "run_on": "host",
            "commands": [
                "python3 -c 'print(\"ok\")'",
                "python3 -c 'import time; time.sleep(2)'",
            ],
            "hints": "host side",
        },
        capture_timeout=0.2,
        recent_limit=100,
    )
    assert result.status == "degraded"
    assert result.partial is True
    assert result.outputs[0]["lines"] == ["ok"]
    assert "error" in result.outputs[1]


def test_host_collector_all_failures_become_error():
    transport = FixtureTransport([])
    transport.acquire_writer()
    result = Collector(transport).run(
        "host_bad",
        {
            "run_on": "host",
            "commands": [
                "python3 -c 'import time; time.sleep(2)'",
                "python3 -c 'import time; time.sleep(2)'",
            ],
            "hints": "host side",
        },
        capture_timeout=0.2,
        recent_limit=100,
    )
    assert result.status == "error"
    assert result.partial is False
    assert all("error" in out for out in result.outputs)
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/test_collector.py -v
```

Expected: FAIL，因为 collector 还没有 host 分支

- [ ] **Step 3: 在 `collector.py` 增加 host collector 分支**

在 `engineering/loop/core/python/loop_core/collector.py` 中：

1. 增加导入：
```python
from loop_core.host_exec import HostCommandError, run_host_command
```

2. 在 `mode == "serial_context"` 之后、device 命令循环之前增加：
```python
run_on = spec.get("run_on", "device")
if run_on == "host":
    outputs: list[dict] = []
    error_msg = ""
    for cmd in commands:
        start = time.monotonic()
        try:
            result = run_host_command(cmd, capture_timeout)
            outputs.append({
                "command": cmd,
                "lines": result.output.splitlines(),
                "duration_sec": round(time.monotonic() - start, 3),
            })
        except HostCommandError as exc:
            outputs.append({
                "command": cmd,
                "lines": [],
                "duration_sec": round(time.monotonic() - start, 3),
                "error": str(exc),
            })
            if not error_msg:
                error_msg = str(exc)
    failed_count = sum(1 for out in outputs if "error" in out)
    succeeded_count = len(outputs) - failed_count
    if failed_count == 0:
        status = "ok"
        partial = False
    elif succeeded_count > 0:
        status = "degraded"
        partial = True
    else:
        status = "error"
        partial = False
    return CollectorResult(
        name=name,
        commands=commands,
        outputs=outputs,
        hints=hints,
        status=status,
        partial=partial,
        error=error_msg,
    )
```

3. 保持现有 `device` 与 `serial_context` 分支不动。

- [ ] **Step 4: 运行 `test_collector.py`，确认全部通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/test_collector.py -v
```

Expected: PASS

- [ ] **Step 5: 提交本任务改动**

```bash
git add engineering/loop/core/python/loop_core/collector.py \
        engineering/loop/core/python/tests/test_collector.py
git commit -m "feat(loop-core): support host collectors"
```

---

### Task 5: 新增 `system.network_adbd` suite 与本地 collector

**Files:**
- Create: `engineering/loop/cases/system/network-adbd-success.yaml`
- Test: `engineering/loop/core/python/tests/test_case_loader.py`

- [ ] **Step 1: 写失败测试，锁定 suite 可加载且 host case / 本地 collectors 解析正确**

在 `engineering/loop/core/python/tests/test_case_loader.py` 末尾追加：

```python
def test_network_adbd_suite_loads_with_host_case_and_local_collectors():
    suite = load_suite(
        "engineering/loop/cases/system/network-adbd-success.yaml",
        ["engineering/loop/cases"],
    )
    ids = [case.id for case in suite.cases]
    assert ids[0] == "trigger_reboot"
    assert "shell_reachable" in ids
    assert ids[-1] == "host_adb_connect_success"

    host_case = next(case for case in suite.cases if case.id == "host_adb_connect_success")
    assert host_case.run_on == "host"

    assert "system.network_adbd.wifi_state" in suite.collectors
    assert "system.network_adbd.wifi_script_log" in suite.collectors
    assert "system.network_adbd.adbd_tcp_state" in suite.collectors
    assert suite.collectors["system.network_adbd.host_adb_state"]["run_on"] == "host"
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py -v
```

Expected: FAIL，因为 suite 文件尚不存在

- [ ] **Step 3: 创建 `network-adbd-success.yaml`**

创建 `engineering/loop/cases/system/network-adbd-success.yaml`：

```yaml
suite: system.network_adbd
version: 1

defaults:
  capture_timeout: 20
  recent_limit: 400

include:
  - common/shell

cases:
  - id: trigger_reboot
    action: reboot
    description: "触发设备重启并等待启动完成"
    severity: critical
    assert: {}
    on_fail:
      collectors: [serial_recent, init_log, crash_dump, kmsg]

  - id: boot_completed
    description: "sys.boot_completed 属性为 1"
    command: "getprop sys.boot_completed"
    assert:
      type: contains
      value: "1"
    severity: critical
    requires: [trigger_reboot, shell_reachable]
    on_fail:
      collectors: [boot_log, init_log, kmsg]

  - id: wifi_service_executed
    description: "rpi5_wifi_connect 服务已进入有效执行态"
    command: "for i in $(seq 1 20); do s=$(getprop init.svc.rpi5_wifi_connect); case \"$s\" in running|stopped) echo WIFI_SERVICE_OK:$s; exit 0 ;; esac; sleep 1; done; echo WIFI_SERVICE_BAD:$(getprop init.svc.rpi5_wifi_connect)"
    assert:
      type: contains
      value: "WIFI_SERVICE_OK:"
    severity: critical
    requires: [boot_completed]
    on_fail:
      collectors: [serial_recent, init_log, wifi_script_log, kmsg]

  - id: wifi_conf_present
    description: "/data/boot/wifi.conf 存在"
    command: "if [ -f /data/boot/wifi.conf ]; then echo PRESENT; else echo MISSING; fi"
    assert:
      type: equals
      value: "PRESENT"
    severity: critical
    requires: [wifi_service_executed]
    on_fail:
      collectors: [serial_recent, wifi_state, wifi_script_log]

  - id: wifi_conf_not_default
    description: "wifi.conf 已配置真实 ssid/psk"
    command: "ssid=$(grep -iE '^[[:space:]]*ssid[[:space:]]*=' /data/boot/wifi.conf | head -1 | cut -d= -f2 | tr -d '[:space:]'); psk=$(grep -iE '^[[:space:]]*psk[[:space:]]*=' /data/boot/wifi.conf | head -1 | cut -d= -f2 | tr -d '[:space:]'); if [ -n \"$ssid\" ] && [ -n \"$psk\" ] && [ \"$ssid\" != default ] && [ \"$psk\" != default ]; then echo CONFIG_OK; else echo DEFAULT_VALUE; fi"
    assert:
      type: equals
      value: "CONFIG_OK"
    severity: critical
    requires: [wifi_conf_present]
    on_fail:
      collectors: [wifi_state, wifi_script_log]

  - id: wifi_connected_ssid
    description: "设备已连上目标 SSID"
    command: "for i in $(seq 1 30); do cmd wifi status 2>/dev/null | grep -F 'SSID: HUAWEI-BE7P' >/dev/null && { echo SSID_OK; exit 0; }; sleep 2; done; cmd wifi status 2>/dev/null || true"
    assert:
      type: contains
      value: "SSID_OK"
    severity: critical
    requires: [wifi_conf_not_default]
    on_fail:
      collectors: [serial_recent, wifi_state, wifi_script_log, kmsg]

  - id: wlan_ip_ready
    description: "wlan0 已获得静态 IP 192.168.1.55"
    command: "for i in $(seq 1 20); do ip addr show wlan0 2>/dev/null | grep -F '192.168.1.55/' >/dev/null && { echo IP_READY; exit 0; }; sleep 2; done; ip addr show wlan0 2>/dev/null || true"
    assert:
      type: contains
      value: "IP_READY"
    severity: critical
    requires: [wifi_connected_ssid]
    on_fail:
      collectors: [serial_recent, wifi_state, wifi_script_log, adbd_tcp_state]

  - id: adb_tcp_port_persist_ready
    description: "persist.adb.tcp.port 为 5555"
    command: "getprop persist.adb.tcp.port"
    assert:
      type: contains
      value: "5555"
    severity: critical
    requires: [wlan_ip_ready]
    on_fail:
      collectors: [init_log, adbd_tcp_state]

  - id: adb_tcp_port_service_ready
    description: "service.adb.tcp.port 为 5555"
    command: "getprop service.adb.tcp.port"
    assert:
      type: contains
      value: "5555"
    severity: critical
    requires: [adb_tcp_port_persist_ready]
    on_fail:
      collectors: [init_log, adbd_tcp_state]

  - id: adbd_running
    description: "adbd 服务处于 running 状态"
    command: "for i in $(seq 1 20); do [ \"$(getprop init.svc.adbd)\" = running ] && { echo ADBD_OK; exit 0; }; sleep 1; done; echo ADBD_BAD:$(getprop init.svc.adbd)"
    assert:
      type: contains
      value: "ADBD_OK"
    severity: critical
    requires: [adb_tcp_port_service_ready]
    on_fail:
      collectors: [serial_recent, init_log, adbd_tcp_state, kmsg]

  - id: host_adb_connect_success
    run_on: host
    description: "host 侧 adb connect 192.168.1.55:5555 成功"
    command: "adb connect 192.168.1.55:5555"
    assert:
      type: regex
      pattern: "(connected to|already connected to)"
    severity: critical
    requires: [adbd_running]
    on_fail:
      collectors: [host_adb_state, wifi_state, wifi_script_log, adbd_tcp_state, serial_recent]

collectors:
  wifi_state:
    commands:
      - "getprop init.svc.rpi5_wifi_connect"
      - "cmd wifi status"
      - "ip addr show wlan0"
      - "ip route"
    hints: "关注 WiFi 服务状态、SSID、wlan0 地址与默认路由"

  wifi_script_log:
    commands:
      - "logcat -d | grep rpi5_wifi"
    hints: "关注 rpi5_wifi 脚本日志中的挂载、配置、连接、静态 IP 设置与重试信息"

  adbd_tcp_state:
    commands:
      - "getprop persist.adb.tcp.port"
      - "getprop service.adb.tcp.port"
      - "getprop init.svc.adbd"
    hints: "关注 adbd TCP 属性是否正确以及 adbd 服务是否 running"

  host_adb_state:
    run_on: host
    commands:
      - "adb devices"
      - "adb connect 192.168.1.55:5555"
    hints: "关注 host 侧 adb server 视角下的设备状态与 connect 错误信息"
```

- [ ] **Step 4: 运行 `test_case_loader.py`，确认 suite 解析通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/test_case_loader.py -v
```

Expected: PASS

- [ ] **Step 5: 提交本任务改动**

```bash
git add engineering/loop/cases/system/network-adbd-success.yaml \
        engineering/loop/core/python/tests/test_case_loader.py
git commit -m "feat(loop): add network adbd success suite"
```

---

### Task 6: 更新 README 并跑全量测试

**Files:**
- Modify: `engineering/loop/README.md`
- Test: `engineering/loop/core/python/tests/`

- [ ] **Step 1: 更新 `engineering/loop/README.md`，补 `run_on` 与 network-adbd 场景说明**

在 `engineering/loop/README.md` 的 suite / YAML 说明附近新增：

```markdown
## `run_on` 执行平面

Loop case 与 collector 默认在 `device` 执行，即通过当前 transport（fixture / rp5-serial）向设备发送命令并采集输出。

当场景需要 host 侧动作（例如 `adb connect 192.168.1.55:5555`）时，可在 case 或 collector 上显式声明：

```yaml
- id: host_adb_connect_success
  run_on: host
  command: "adb connect 192.168.1.55:5555"
  assert:
    type: regex
    pattern: "(connected to|already connected to)"
```

约束：

- `run_on` 只允许 `device` / `host`
- `action: reboot` 仅允许 `run_on: device`
- `prompt_visible` 与 `serial_context` 仅适用于 `device`

## `system.network_adbd` 场景

`engineering/loop/cases/system/network-adbd-success.yaml` 用于验证 RPi5 的开机自动联网与网络 adb 闭环：

1. `trigger_reboot` + `shell_reachable`
2. `boot_completed`
3. `rpi5_wifi_connect` 服务已进入有效执行态
4. `/data/boot/wifi.conf` 存在且非默认值
5. 已连接目标 SSID
6. `wlan0` 获得 `192.168.1.55`
7. adbd TCP 属性正确，`adbd` 为 `running`
8. host `adb connect 192.168.1.55:5555` 成功

该场景继续以串口作为主执行与主取证通道；host adb 仅作为最终成功判据，而不是主 transport。
```

- [ ] **Step 2: 运行 loop_core 相关全量测试**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/ -v
```

Expected: PASS

- [ ] **Step 3: 提交本任务改动**

```bash
git add engineering/loop/README.md
git commit -m "docs(loop): document host run_on and network adbd suite"
```

---

### Task 7: 做一次 live 验收演练命令编排

**Files:**
- Modify: `engineering/loop/README.md`

- [ ] **Step 1: 在 README 中补 live 运行示例命令**

在 `engineering/loop/README.md` 的 network-adbd 小节后追加：

```markdown
### Live 运行示例

```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
python3 -m loop_core.cli run \
  --suite engineering/loop/cases/system/network-adbd-success.yaml \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir engineering/output/runs/network-adbd-live \
  --host 127.0.0.1 \
  --port 9700
```

运行前要求：

- host 环境可直接调用 `adb`
- 设备端 `wifi.conf` 已配置真实 `ssid/psk/static_ip`
- 当前静态 IP 设计假定为 `192.168.1.55`
```
```

- [ ] **Step 2: 跑文档与测试最终校验**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/ -v && \
bash engineering/harness/scripts/validate_harness_docs.sh
```

Expected:
- loop_core 单测全绿
- harness 文档校验通过

- [ ] **Step 3: 提交最终文档收尾改动**

```bash
git add engineering/loop/README.md
git commit -m "docs(loop): add network adbd live run example"
```

---

## Self-Review

- Spec coverage: 已覆盖 `run_on` 双执行平面、host `adb connect` 终态判据、串口主链复用、network-adbd suite、专项 collector、静态 IP `192.168.1.55`、旧 suite 向后兼容与非法组合 fail-fast。
- Placeholder scan: 本计划未使用 TBD/TODO/“后续补充细节”等占位描述；每个任务都给了明确文件路径、测试与代码草案。
- Type consistency: `run_on` 字段在 case 与 collector 中统一使用 `device|host`；host 运行器统一命名为 `run_host_command()`；最终场景 ID 固定为 `host_adb_connect_success`。
