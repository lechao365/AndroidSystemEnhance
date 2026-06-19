# boot-failure-debug-loop v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 `rp5-serial` provider 之上完成 `boot-failure-debug-loop` v1 的剩余全部实现：状态机、boot cycle 检测、规则分类、L1/L2 采样、报告、bash 入口，并优先构建 AI 可在 WSL2 自行完成的离线验证链路，最后只保留最小真机验证。

**Architecture:** 不先抽大而全 `core/`。本轮采用 workflow-local 实现：基于现有 `AutomationClient.capture_recent_lines()`（`engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py:128`）与 host `stream.read_recent`（`engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/handler.py:231`）构建轮询式 observer；通过 transcript fixture + fake transport 完成大部分离线验证；仅在最终阶段接入真实 Windows Host 与树莓派串口。

**Tech Stack:** Bash、Python 3、pytest、标准库 `json/pathlib/dataclasses/re`、现有 `rp5_serial` provider、`engineering/harness/lib/harness_bootstrap.sh`、`engineering/harness/lib/harness_observability.sh`

**Spec:** `docs/specs/2026-06-19-loop-engineering-design.md`

**Design summary:**

- **范围全量**：本轮 plan 覆盖 V1 剩余全部内容
- **实现分段**：编码可连续推进，但验证按阶段切开
- **验证前置自动化**：先把能在 WSL/本地模拟完成的全做完
- **真机验证后置最小化**：只保留"必须依赖真实串口/真实启动行为"的步骤给人工
- **5 个里程碑**：配置与契约层 → 观察器与状态机 → 规则与采样 → 报告与入口 → 分段集成验证
- **测试基础设施**：recorded transcript fixtures + fake transport，让 workflow 在 WSL 中完整跑通，真机只做最后短闭环确认

---

## File Structure

| 类型 | 路径 | 责任 |
|---|---|---|
| 修改 | `engineering/loop/README.md` | 顶层说明更新，标记 workflow/profiles 已进入实现 |
| 修改 | `engineering/loop/WORKFLOW.md` | 当前阶段从 provider-only 更新为 workflow 实施中 |
| 修改 | `engineering/loop/connection/profiles/README.md` | profile 结构与覆盖优先级说明 |
| 修改 | `engineering/loop/connection/profiles/devices/rp5/README.md` | RPi5 profile 字段说明 |
| 修改 | `engineering/loop/connection/protocol/rp5_serial_protocol.md` | 补充 `stream.read_recent` 与 client-side `wait_for_pattern/capture_window` 约定 |
| 新增 | `engineering/loop/profiles/README.md` | loop 级 profile 说明 |
| 新增 | `engineering/loop/profiles/boot-failure-debug/default.json` | workflow 默认阈值与动作配置 |
| 新增 | `engineering/loop/connection/profiles/devices/rp5/default.json` | RPi5 设备 profile |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/README.md` | workflow 功能说明 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/WORKFLOW.md` | workflow 使用流程 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/bin/loop_boot_failure_debug.sh` | WSL2 bash 入口 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/__init__.py` | package 入口 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/config.py` | profile 加载与合并 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/models.py` | attempt / rule / action / report / boot-cycle 模型 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/transport.py` | live transport + fixture transport 抽象 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/observer.py` | recent-buffer 轮询观察器 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/boot_cycles.py` | boot cycle 识别 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/rules.py` | 6 条 V1 规则 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/actions.py` | L1/L2 动作与只读采样 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/report.py` | human/json 报告渲染 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/runner.py` | workflow 状态机主编排 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/cli.py` | CLI 入口 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/conftest.py` | 测试公共 fixture |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/no_output.jsonl` | 无输出 fixture |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/normal_boot.jsonl` | 正常启动 fixture |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/kernel_panic.jsonl` | kernel panic fixture |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/boot_hang.jsonl` | boot hang fixture |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/reboot_loop.jsonl` | reboot loop fixture |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_config.py` | 配置测试 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_models.py` | 模型测试 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_transport.py` | fake/live transport 合同测试 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_observer.py` | observer 测试 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_boot_cycles.py` | boot cycle 测试 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_rules.py` | 规则测试 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_actions.py` | 采样动作测试 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_runner.py` | 状态机/流程测试 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_report.py` | 报告测试 |
| 新增 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_cli.py` | CLI 测试 |

---

## Task 0: 执行前规则加载与环境基线

**Files:**
- Read: `engineering/harness/rules/script-observability.md`
- Read: `engineering/harness/lib/harness_bootstrap.sh`
- Read: `engineering/harness/lib/harness_observability.sh`

- [ ] **Step 1: 读取 engineering bash 规则**

Run:
```bash
python - <<'PY'
from pathlib import Path
for p in [
    "engineering/harness/rules/script-observability.md",
    "engineering/harness/lib/harness_bootstrap.sh",
    "engineering/harness/lib/harness_observability.sh",
]:
    print(f"===== {p} =====")
    print(Path(p).read_text())
PY
```

Expected: 读到 observability 规则与两个公共库内容，明确 bash 入口必须复用公共库。

- [ ] **Step 2: 固定后续测试环境变量**

Run:
```bash
export PYTHONPATH="engineering/loop/workflows/boot-failure-debug-loop/python:engineering/loop/connection/providers/rp5-serial/python"
```

Expected: 后续 pytest / CLI 可同时导入 `boot_failure_debug` 与 `rp5_serial`。

- [ ] **Step 3: 记录当前 provider 能力基线**

重点依赖：
```python
# engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py
AutomationClient.capture_recent_lines(limit)
AutomationClient.send_line(text)
AutomationClient.acquire_writer()
```

Expected: 明确 workflow 本轮不改 host 协议，只在 client 之上封装观察与等待能力。

- [ ] **Step 4: 基线回归**

Run:
```bash
PYTHONPATH="engineering/loop/connection/providers/rp5-serial/python" python -m pytest engineering/loop/connection/providers/rp5-serial/python/tests -q
```

Expected: 现有 provider 测试全部通过。

- [ ] **Step 5: 提交基线确认**

```bash
git add -A
git commit -m "chore(loop): capture boot-failure workflow baseline"
```

**AI 可自验证:** 全部
**人工/真机:** 无

---

## Task 1: 建立 workflow / profiles / fixture 骨架

**Files:**
- Create: `engineering/loop/profiles/README.md`
- Create: `engineering/loop/profiles/boot-failure-debug/default.json`
- Create: `engineering/loop/connection/profiles/devices/rp5/default.json`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/README.md`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/WORKFLOW.md`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/no_output.jsonl`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/normal_boot.jsonl`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/kernel_panic.jsonl`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/boot_hang.jsonl`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/reboot_loop.jsonl`
- Modify: `engineering/loop/README.md`
- Modify: `engineering/loop/WORKFLOW.md`
- Modify: `engineering/loop/connection/profiles/README.md`
- Modify: `engineering/loop/connection/profiles/devices/rp5/README.md`

- [ ] **Step 1: 先写 fixture 与 profile 合同测试**

```python
# engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_config.py
from pathlib import Path
import json

def test_rp5_default_profile_contains_required_markers():
    profile = json.loads(Path("engineering/loop/connection/profiles/devices/rp5/default.json").read_text())
    assert profile["device_id"] == "rp5"
    assert profile["prompt_markers"]
    assert profile["boot_markers"]
    assert profile["reboot_markers"]

def test_boot_failure_workflow_profile_contains_v1_thresholds():
    profile = json.loads(Path("engineering/loop/profiles/boot-failure-debug/default.json").read_text())
    assert profile["observe_timeout_sec"] > 0
    assert profile["reboot_loop_threshold"] >= 2
    assert profile["recent_lines_limit"] >= 100

def test_reboot_loop_fixture_contains_multiple_boot_cycles():
    lines = Path("engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/reboot_loop.jsonl").read_text().strip().splitlines()
    assert len(lines) >= 4
```

- [ ] **Step 2: 运行测试，确认当前失败**

Run:
```bash
export PYTHONPATH="engineering/loop/workflows/boot-failure-debug-loop/python:engineering/loop/connection/providers/rp5-serial/python" && python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_config.py -q
```

Expected: FAIL，提示文件不存在。

- [ ] **Step 3: 写最小配置/fixture 内容**

```json
// engineering/loop/connection/profiles/devices/rp5/default.json
{
  "device_id": "rp5",
  "transport": "serial",
  "prompt_markers": ["console:/ $", "localhost:/ #", "# ", "$ "],
  "boot_markers": ["Booting Linux", "Linux version", "U-Boot", "starting kernel"],
  "reboot_markers": ["reboot: Restarting system", "Booting Linux", "U-Boot"],
  "panic_markers": ["Kernel panic", "Unable to handle kernel", "Call trace:"],
  "hang_markers": ["Freeing unused kernel memory", "init: starting service"],
  "line_ending": "\n"
}
```

```json
// engineering/loop/profiles/boot-failure-debug/default.json
{
  "observe_timeout_sec": 90,
  "quiet_window_sec": 8,
  "prompt_wait_sec": 12,
  "capture_window_sec": 5,
  "recent_lines_limit": 400,
  "reboot_loop_threshold": 2,
  "max_reassess_rounds": 1,
  "l1_commands": ["dmesg", "getprop", "mount", "ps"],
  "l2_actions": ["send_enter", "wait_prompt", "retry_read_only_once", "extend_observe_window"]
}
```

```json
{"t": 0.0, "text": "Booting Linux on physical CPU 0x0"}
{"t": 0.8, "text": "Linux version 6.1.0-android14"}
{"t": 4.2, "text": "init: starting service 'zygote'"}
{"t": 10.0, "text": "console:/ $"}
```

- [ ] **Step 4: 复跑测试并补 README/WORKFLOW 文档骨架**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_config.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交骨架**

```bash
git add engineering/loop
git commit -m "feat(loop): add boot-failure workflow skeleton and profiles"
```

**AI 可自验证:** 全部
**人工/真机:** 无

---

## Task 2: 配置加载、数据模型、artifact 命名

**Files:**
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/__init__.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/config.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/models.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_models.py`

- [ ] **Step 1: 先写失败测试**

```python
from boot_failure_debug.config import load_profiles
from boot_failure_debug.models import LoopAttempt, RuleMatch, ActionRecord

def test_load_profiles_merges_device_and_workflow_override(tmp_path):
    merged = load_profiles(
        device_profile_path="engineering/loop/connection/profiles/devices/rp5/default.json",
        workflow_profile_path="engineering/loop/profiles/boot-failure-debug/default.json",
        override={"observe_timeout_sec": 30},
    )
    assert merged.observe_timeout_sec == 30
    assert "console:/ $" in merged.prompt_markers

def test_loop_attempt_to_dict_contains_report_refs():
    attempt = LoopAttempt(
        attempt_id="a-1",
        device_id="rp5",
        outcome="EXIT_FAILURE",
        final_classification="kernel_panic_detected",
        boot_cycle_count=1,
        matched_rules=[],
        actions=[],
        artifacts_dir="artifacts/a-1"
    )
    data = attempt.to_dict()
    assert data["final_classification"] == "kernel_panic_detected"
    assert data["artifacts_dir"] == "artifacts/a-1"
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_models.py -q
```

Expected: FAIL，模块不存在。

- [ ] **Step 3: 实现配置与模型**

```python
# config.py
from dataclasses import dataclass
import json
from pathlib import Path

@dataclass
class WorkflowConfig:
    device_id: str
    prompt_markers: list[str]
    boot_markers: list[str]
    reboot_markers: list[str]
    panic_markers: list[str]
    hang_markers: list[str]
    line_ending: str
    observe_timeout_sec: int
    quiet_window_sec: int
    prompt_wait_sec: int
    capture_window_sec: int
    recent_lines_limit: int
    reboot_loop_threshold: int
    max_reassess_rounds: int
    l1_commands: list[str]
    l2_actions: list[str]

def load_profiles(device_profile_path: str, workflow_profile_path: str, override: dict | None = None) -> WorkflowConfig:
    device = json.loads(Path(device_profile_path).read_text())
    workflow = json.loads(Path(workflow_profile_path).read_text())
    merged = {**device, **workflow, **(override or {})}
    return WorkflowConfig(**merged)
```

```python
# models.py
from dataclasses import asdict, dataclass, field

@dataclass
class ObservedLine:
    t: float
    text: str
    boot_cycle_id: int = 0

@dataclass
class RuleMatch:
    rule_id: str
    matched: bool
    confidence: float
    severity: str
    evidence: list[str]
    phase: str
    suggested_actions: list[str]

@dataclass
class ActionRecord:
    action_id: str
    level: str
    command: str
    reason: str
    result: str
    evidence_ref: str | None = None

@dataclass
class LoopAttempt:
    attempt_id: str
    device_id: str
    outcome: str
    final_classification: str
    boot_cycle_count: int
    matched_rules: list[RuleMatch] = field(default_factory=list)
    actions: list[ActionRecord] = field(default_factory=list)
    artifacts_dir: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
```

- [ ] **Step 4: 复跑并增加 profile 边界测试**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_models.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_config.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/workflows/boot-failure-debug-loop/python engineering/loop/profiles engineering/loop/connection/profiles/devices/rp5/default.json
git commit -m "feat(loop): add boot-failure config and models"
```

**AI 可自验证:** 全部
**人工/真机:** 无

---

## Task 3: transport 抽象与离线 fixture transport

**Files:**
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/transport.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_transport.py`

- [ ] **Step 1: 先写 transport 合同测试**

```python
from boot_failure_debug.transport import FixtureTransport

def test_fixture_transport_replays_timeline_in_order():
    transport = FixtureTransport.from_jsonl(
        "engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/normal_boot.jsonl"
    )
    lines = transport.capture_window(timeout_sec=15, recent_limit=100)
    assert lines[0].text.startswith("Booting Linux")
    assert lines[-1].text == "console:/ $"

def test_fixture_transport_wait_for_pattern_matches_prompt():
    transport = FixtureTransport.from_jsonl(
        "engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/normal_boot.jsonl"
    )
    matched = transport.wait_for_pattern(["console:/ $"], timeout_sec=15, recent_limit=100)
    assert matched is not None
    assert matched.text == "console:/ $"
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_transport.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现 transport 抽象**

```python
from dataclasses import dataclass
import json
import time
from pathlib import Path
from rp5_serial.client.automation import AutomationClient
from boot_failure_debug.models import ObservedLine

class BaseTransport:
    def acquire_writer(self) -> bool: ...
    def release(self) -> None: ...
    def send_line(self, text: str) -> None: ...
    def capture_window(self, timeout_sec: float, recent_limit: int) -> list[ObservedLine]: ...
    def wait_for_pattern(self, patterns: list[str], timeout_sec: float, recent_limit: int) -> ObservedLine | None: ...

class Rp5SerialTransport(BaseTransport):
    def __init__(self, client: AutomationClient):
        self.client = client

class FixtureTransport(BaseTransport):
    @classmethod
    def from_jsonl(cls, path: str) -> "FixtureTransport":
        rows = [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]
        return cls(rows)
```

实现要求：
- `FixtureTransport.capture_window()` 返回按 `t` 排序的 `ObservedLine`
- `FixtureTransport.wait_for_pattern()` 在 timeline 中扫描 pattern
- `Rp5SerialTransport.capture_window()` 轮询 `capture_recent_lines()`，将字符串包装成 `ObservedLine`

- [ ] **Step 4: 复跑测试并增加 live transport mock 测试**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_transport.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/workflows/boot-failure-debug-loop/python
git commit -m "feat(loop): add boot-failure transport adapters"
```

**AI 可自验证:** 全部
**人工/真机:** 无

---

## Task 4: observer 与 boot cycle 检测

**Files:**
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/observer.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/boot_cycles.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_observer.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_boot_cycles.py`

- [ ] **Step 1: 先写失败测试**

```python
from boot_failure_debug.boot_cycles import assign_boot_cycles
from boot_failure_debug.config import load_profiles
from boot_failure_debug.models import ObservedLine

def test_assign_boot_cycles_splits_on_reboot_markers():
    cfg = load_profiles(
        "engineering/loop/connection/profiles/devices/rp5/default.json",
        "engineering/loop/profiles/boot-failure-debug/default.json",
    )
    lines = [
        ObservedLine(t=0.0, text="Booting Linux"),
        ObservedLine(t=4.0, text="console:/ $"),
        ObservedLine(t=10.0, text="reboot: Restarting system"),
        ObservedLine(t=12.0, text="Booting Linux"),
    ]
    result = assign_boot_cycles(lines, cfg)
    assert result[0].boot_cycle_id == 1
    assert result[-1].boot_cycle_id == 2

def test_observer_detects_no_output_window():
    ...
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_observer.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_boot_cycles.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现 observer 与 cycle 识别**

```python
# boot_cycles.py
def assign_boot_cycles(lines, cfg):
    cycle = 1
    out = []
    for line in lines:
        if any(marker in line.text for marker in cfg.reboot_markers) and out:
            out.append(type(line)(t=line.t, text=line.text, boot_cycle_id=cycle))
            cycle += 1
            continue
        out.append(type(line)(t=line.t, text=line.text, boot_cycle_id=cycle))
    return out
```

```python
# observer.py
class ObservationSnapshot:
    def __init__(self, lines, quiet_for_sec, prompt_line):
        self.lines = lines
        self.quiet_for_sec = quiet_for_sec
        self.prompt_line = prompt_line

def capture_snapshot(transport, cfg, timeout_sec):
    lines = transport.capture_window(timeout_sec=timeout_sec, recent_limit=cfg.recent_lines_limit)
    ...
```

实现要求：
- observer 提供：
  - `capture_snapshot(timeout_sec)`
  - `wait_for_prompt(timeout_sec)`
  - `recent_context(limit)`
- 对 recent-buffer 轮询去重，避免重复行被反复累计

- [ ] **Step 4: 复跑测试**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_observer.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_boot_cycles.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/workflows/boot-failure-debug-loop/python
git commit -m "feat(loop): add observer and boot cycle detection"
```

**AI 可自验证:** 全部
**人工/真机:** 无

---

## Task 5: 6 条规则与分类逻辑

**Files:**
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/rules.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_rules.py`

- [ ] **Step 1: 先写 6 条规则失败测试**

```python
from boot_failure_debug.rules import evaluate_rules
from boot_failure_debug.transport import FixtureTransport
from boot_failure_debug.observer import capture_snapshot
from boot_failure_debug.config import load_profiles

def test_kernel_panic_fixture_matches_kernel_panic_detected():
    cfg = load_profiles(...)
    transport = FixtureTransport.from_jsonl("engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/kernel_panic.jsonl")
    snapshot = capture_snapshot(transport, cfg, timeout_sec=20)
    matches = evaluate_rules(snapshot, cfg)
    assert any(m.rule_id == "kernel_panic_detected" and m.matched for m in matches)

def test_no_output_fixture_matches_no_output_after_attach():
    ...
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_rules.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现规则**

```python
def evaluate_rules(snapshot, cfg):
    lines = [line.text for line in snapshot.lines]

    return [
        match_no_output_after_attach(snapshot, cfg),
        match_kernel_panic_detected(lines),
        match_kernel_boot_hang(snapshot, cfg),
        match_login_prompt_not_reached(snapshot, cfg),
        match_shell_prompt_available(snapshot, cfg),
        match_reboot_loop_detected(snapshot, cfg),
    ]
```

每条规则至少输出：
```python
RuleMatch(
    rule_id="kernel_panic_detected",
    matched=True,
    confidence=0.95,
    severity="high",
    evidence=["Kernel panic - not syncing"],
    phase="CLASSIFY_FAILURE",
    suggested_actions=["capture_recent_context", "collect_read_only_if_prompt"]
)
```

- [ ] **Step 4: 复跑并补分类优先级测试**

优先级建议：
1. `kernel_panic_detected`
2. `reboot_loop_detected`
3. `shell_prompt_available`
4. `kernel_boot_hang`
5. `login_prompt_not_reached`
6. `no_output_after_attach`

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_rules.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/workflows/boot-failure-debug-loop/python
git commit -m "feat(loop): add boot-failure rules"
```

**AI 可自验证:** 全部
**人工/真机:** 无

---

## Task 6: L1/L2 动作与只读采样

**Files:**
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/actions.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_actions.py`

- [ ] **Step 1: 先写动作测试**

```python
from boot_failure_debug.actions import plan_actions, execute_actions
from boot_failure_debug.models import RuleMatch
from boot_failure_debug.transport import FixtureTransport

def test_shell_prompt_available_plans_read_only_sampling():
    matches = [
        RuleMatch(
            rule_id="shell_prompt_available",
            matched=True,
            confidence=0.9,
            severity="low",
            evidence=["console:/ $"],
            phase="CLASSIFY_FAILURE",
            suggested_actions=[]
        )
    ]
    actions = plan_actions(matches)
    assert [a.command for a in actions][:2] == ["dmesg", "getprop"]

def test_no_prompt_only_uses_l2_safe_actions():
    ...
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_actions.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现动作规划与执行**

```python
READ_ONLY_COMMANDS = ("dmesg", "getprop", "mount", "ps")
SAFE_L2_ACTIONS = ("send_enter", "wait_prompt", "retry_read_only_once", "extend_observe_window")

def plan_actions(matches):
    if any(m.rule_id == "shell_prompt_available" and m.matched for m in matches):
        return [ActionRecord(action_id="a1", level="L1", command=cmd, reason="prompt available", result="PLANNED") for cmd in READ_ONLY_COMMANDS]
    return [
        ActionRecord(action_id="a1", level="L2", command="send_enter", reason="prompt not visible", result="PLANNED"),
        ActionRecord(action_id="a2", level="L2", command="wait_prompt", reason="prompt not visible", result="PLANNED"),
    ]
```

执行要求：
- `dmesg/getprop/mount/ps` 通过 `transport.send_line()` 发送
- `send_enter` 发送空字符串
- `wait_prompt` 调 `transport.wait_for_pattern()`
- `logcat` 先不默认纳入自动采样，避免输出过大；只在 workflow profile 显式开启时执行

- [ ] **Step 4: 复跑并校验不会产生 L3/L4**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_actions.py -q
```

Expected: PASS，且没有任何高风险动作名。

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/workflows/boot-failure-debug-loop/python
git commit -m "feat(loop): add boot-failure L1 L2 actions"
```

**AI 可自验证:** 全部
**人工/真机:** 真实 Android 环境是否支持全部只读命令，需要最终真机抽检

---

## Task 7: 状态机主编排、REASSESS、attempt 结果收口

**Files:**
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/runner.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_runner.py`

- [ ] **Step 1: 先写状态机测试**

```python
from boot_failure_debug.runner import BootFailureRunner
from boot_failure_debug.transport import FixtureTransport
from boot_failure_debug.config import load_profiles

def test_runner_returns_exit_success_when_prompt_available():
    cfg = load_profiles(...)
    transport = FixtureTransport.from_jsonl("engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/normal_boot.jsonl")
    runner = BootFailureRunner(cfg, transport)
    attempt = runner.run()
    assert attempt.outcome == "EXIT_SUCCESS"
    assert attempt.final_classification == "shell_prompt_available"

def test_runner_returns_exit_failure_on_kernel_panic():
    ...
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_runner.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现状态机**

```python
class BootFailureRunner:
    def run(self):
        state = "PREPARE"
        reassess_round = 0
        while True:
            if state == "PREPARE":
                ...
                state = "ATTACH_SERIAL"
            elif state == "ATTACH_SERIAL":
                ...
                state = "OBSERVE_BOOT"
            elif state == "OBSERVE_BOOT":
                ...
                state = "CLASSIFY_FAILURE"
            elif state == "CLASSIFY_FAILURE":
                ...
                state = "COLLECT_EVIDENCE"
            elif state == "COLLECT_EVIDENCE":
                ...
                state = "REASSESS"
            elif state == "REASSESS":
                ...
```

收口规则：
- 命中 `shell_prompt_available` -> `EXIT_SUCCESS`
- 命中 panic / reboot loop / boot hang / no output -> `EXIT_FAILURE`
- `REASSESS` 最多 1 轮
- 每轮都更新 `boot_cycle_count`

- [ ] **Step 4: 复跑并加全 fixture 回归**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_runner.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_rules.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_actions.py -q
```

Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/workflows/boot-failure-debug-loop/python
git commit -m "feat(loop): add boot-failure state machine runner"
```

**AI 可自验证:** 全部
**人工/真机:** 无

---

## Task 8: 报告、artifacts、CLI

**Files:**
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/report.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/cli.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_report.py`
- Create: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_cli.py`

- [ ] **Step 1: 先写报告/CLI 测试**

```python
from boot_failure_debug.report import render_summary, write_report_bundle
from boot_failure_debug.runner import BootFailureRunner

def test_render_summary_contains_classification_and_boot_cycles(tmp_path):
    ...
    summary = render_summary(attempt)
    assert "最终分类" in summary
    assert "boot cycle" in summary

def test_cli_fixture_mode_writes_json_and_summary(tmp_path):
    ...
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_report.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_cli.py -q
```

Expected: FAIL。

- [ ] **Step 3: 实现报告与 CLI**

```python
def render_summary(attempt) -> str:
    return f"""最终分类: {attempt.final_classification}
结果: {attempt.outcome}
boot cycle: {attempt.boot_cycle_count}
命中规则: {", ".join(m.rule_id for m in attempt.matched_rules if m.matched)}
执行动作: {", ".join(a.command for a in attempt.actions)}
"""

def write_report_bundle(attempt, output_dir: str) -> dict[str, str]:
    ...
```

```python
# cli.py
# 支持两种模式
# 1) --fixture <jsonl>
# 2) --host 127.0.0.1 --port 9700
```

CLI 参数至少包括：
- `--device-profile`
- `--workflow-profile`
- `--override-json`
- `--fixture`
- `--host`
- `--port`
- `--artifacts-dir`

- [ ] **Step 4: 复跑并做离线端到端**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_report.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_cli.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_runner.py -q
python -m boot_failure_debug.cli \
  --fixture engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/kernel_panic.jsonl \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --workflow-profile engineering/loop/profiles/boot-failure-debug/default.json \
  --artifacts-dir /tmp/opencode/boot-failure-artifacts
```

Expected:
- pytest PASS
- 生成 `report.json`、`summary.txt`、`captured_lines.txt`

- [ ] **Step 5: 提交**

```bash
git add engineering/loop/workflows/boot-failure-debug-loop/python
git commit -m "feat(loop): add boot-failure report and cli"
```

**AI 可自验证:** 全部
**人工/真机:** 无

---

## Task 9: bash 入口与文档同步

**Files:**
- Create: `engineering/loop/workflows/boot-failure-debug-loop/bin/loop_boot_failure_debug.sh`
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/README.md`
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/WORKFLOW.md`
- Modify: `engineering/loop/README.md`
- Modify: `engineering/loop/WORKFLOW.md`
- Modify: `engineering/loop/connection/protocol/rp5_serial_protocol.md`

- [ ] **Step 1: 先写 bash/文档验证测试**

```python
from pathlib import Path

def test_workflow_bash_entrypoint_sources_harness_bootstrap():
    content = Path("engineering/loop/workflows/boot-failure-debug-loop/bin/loop_boot_failure_debug.sh").read_text()
    assert "harness_bootstrap.sh" in content
    assert 'harness_init "loop-boot-failure-debug"' in content
    assert "python -m boot_failure_debug.cli" in content
```

- [ ] **Step 2: 运行测试，确认失败**

Run:
```bash
python -m pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_cli.py -q
```

Expected: FAIL 或缺文件。

- [ ] **Step 3: 实现 bash 入口**

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../../harness/lib/harness_bootstrap.sh
source "$SCRIPT_DIR/../../../../harness/lib/harness_bootstrap.sh"

harness_init "loop-boot-failure-debug"
PYTHON_ROOT="$SCRIPT_DIR/../python"
export PYTHONPATH="$PYTHON_ROOT:$SCRIPT_DIR/../../../connection/providers/rp5-serial/python${PYTHONPATH:+:$PYTHONPATH}"

python -m boot_failure_debug.cli "$@"
rc=$?
harness_exit "$rc"
```

- [ ] **Step 4: bash 语法检查 + fixture mode 检查**

Run:
```bash
bash -n engineering/loop/workflows/boot-failure-debug-loop/bin/loop_boot_failure_debug.sh
engineering/loop/workflows/boot-failure-debug-loop/bin/loop_boot_failure_debug.sh \
  --fixture engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/normal_boot.jsonl \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --workflow-profile engineering/loop/profiles/boot-failure-debug/default.json \
  --artifacts-dir /tmp/opencode/boot-failure-script-artifacts
```

Expected:
- `bash -n` 无输出
- script 成功执行并生成 artifacts

- [ ] **Step 5: 提交**

```bash
git add engineering/loop
git commit -m "feat(loop): add boot-failure workflow entrypoint and docs"
```

**AI 可自验证:** 全部
**人工/真机:** 无

---

## Task 10: AI 自验证总装（WSL 优先）与最小人工验证清单

**Files:**
- Test: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/*.py`
- Test: `engineering/loop/connection/providers/rp5-serial/python/tests/*.py`

- [ ] **Step 1: 跑全部离线测试**

Run:
```bash
export PYTHONPATH="engineering/loop/workflows/boot-failure-debug-loop/python:engineering/loop/connection/providers/rp5-serial/python" && \
python -m pytest \
  engineering/loop/connection/providers/rp5-serial/python/tests \
  engineering/loop/workflows/boot-failure-debug-loop/python/tests -q
```

Expected: 全 PASS。

- [ ] **Step 2: 跑全 fixture 离线回放**

Run:
```bash
for f in \
  engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/no_output.jsonl \
  engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/normal_boot.jsonl \
  engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/kernel_panic.jsonl \
  engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/boot_hang.jsonl \
  engineering/loop/workflows/boot-failure-debug-loop/python/tests/fixtures/reboot_loop.jsonl
do
  python -m boot_failure_debug.cli \
    --fixture "$f" \
    --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
    --workflow-profile engineering/loop/profiles/boot-failure-debug/default.json \
    --artifacts-dir /tmp/opencode/boot-failure-batch
done
```

Expected:
- 每个 fixture 都输出 `summary.txt` 与 `report.json`
- 分类分别符合预期

- [ ] **Step 3: AI 可做的 Windows/Host 回归（若环境可访问 live host）**

Run:
```bash
python -m rp5_serial.client.status --host 127.0.0.1 --port 9700
python -m boot_failure_debug.cli \
  --host 127.0.0.1 \
  --port 9700 \
  --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
  --workflow-profile engineering/loop/profiles/boot-failure-debug/default.json \
  --artifacts-dir /tmp/opencode/boot-failure-live
```

Expected:
- status 能连通 host
- workflow 能完成一次 live 观察并输出报告
若当前会话无法访问 Windows Host，则跳过该步，不视为失败。

- [ ] **Step 4: 最小人工/真机验证**

必须人工执行，仅保留以下 3 组：
1. **Windows Host + 真实 COM**
   - PowerShell:
   ```powershell
   $env:PYTHONPATH="$PWD\engineering\loop\connection\providers\rp5-serial\python"
   python -m rp5_serial.host.server --port COM5 --baudrate 115200 --listen-port 9700
   ```
   期望：Host 成功独占 COM，日志正常滚动。

2. **WSL2 live workflow**
   ```bash
   engineering/loop/workflows/boot-failure-debug-loop/bin/loop_boot_failure_debug.sh \
     --host 127.0.0.1 \
     --port 9700 \
     --device-profile engineering/loop/connection/profiles/devices/rp5/default.json \
     --workflow-profile engineering/loop/profiles/boot-failure-debug/default.json \
     --artifacts-dir /tmp/opencode/boot-failure-live
   ```
   期望：能接管、观察、输出报告。

3. **真机场景最小集合**
   - 正常启动到 prompt -> 应分类为 `shell_prompt_available`
   - 故障启动 transcript -> 应分类为 panic/hang/no-output/reboot-loop 之一
   - prompt 可达时只读命令采样成功

- [ ] **Step 5: 提交验证收敛结果**

```bash
git add engineering/loop
git commit -m "test(loop): verify boot-failure workflow offline and live"
```

**AI 可自验证:**
- provider/workflow 全部 pytest
- fixture replay 全部 CLI
- 若能连 Windows Host，则可做 live host 连通回归

**必须人工/真机:**
- 真实 COM 打开
- 真实树莓派启动行为
- 真实 shell 下只读命令可执行性
- 长时间串口稳定性

---

## 验证矩阵

| 验证项 | AI 在 WSL 可完成 | AI 在 host 可达时完成 | 必须人工/真机 |
|---|---:|---:|---:|
| profile 合并、阈值覆盖 | 是 | 是 | 否 |
| 数据模型、report schema | 是 | 是 | 否 |
| fixture replay 全链路 | 是 | 是 | 否 |
| no-output / panic / hang / reboot-loop 分类 | 是 | 是 | 否 |
| boot cycle 检测 | 是 | 是 | 否 |
| L1/L2 动作规划 | 是 | 是 | 否 |
| bash 入口与 artifacts 生成 | 是 | 是 | 否 |
| live host TCP 连通 | 否 | 是 | 否 |
| 真实 COM 独占 | 否 | 否 | 是 |
| 真实树莓派日志接入 | 否 | 否 | 是 |
| 真实 prompt 后只读命令可执行性 | 否 | 否 | 是 |
| 长时间串口稳定性 | 否 | 否 | 是 |

---

## 实施顺序建议

1. Task 0-3：先把 **AI 离线验证底座** 做好
2. Task 4-8：再完成 workflow 主体（observer / rules / actions / runner / report）
3. Task 9：补 bash 入口与文档同步
4. Task 10：AI 自验证总装，最后保留最小人工/真机验证

---

## 与 V1 设计规格的对齐说明

本 plan 严格对齐 `docs/specs/2026-06-19-loop-engineering-design.md` 第 10 节：

- **10.1 业务目标**：覆盖无输出 / kernel panic / boot hang / login prompt 不可达 / 反复重启 → Task 1 的 5 个 fixture + Task 5 的 6 条规则
- **10.2 状态机**：`PREPARE → ATTACH_SERIAL → OBSERVE_BOOT → CLASSIFY_FAILURE → COLLECT_EVIDENCE → REASSESS → EXIT_SUCCESS/EXIT_FAILURE` → Task 7
- **10.3 boot cycle 检测**：`boot_cycle_id` 识别 boot 起点、reboot 边界、按 cycle 归档 → Task 4
- **10.4 V1 规则集**：6 条规则，文本特征 + 时间窗口 + 阶段推进失败 → Task 5
- **10.5 V1 动作边界**：L1 只读采样（dmesg/logcat/getprop/mount/ps）、L2 低风险探测（回车/等待 prompt/温和重试/延长观察）、不做 L3/L4 → Task 6
- **10.6 V1 报告输出**：最终分类 + 启动推进阶段 + boot cycle 次数 + 命中规则 + 执行动作 + 关键证据 + 建议下一步，human-readable + machine-readable → Task 8
