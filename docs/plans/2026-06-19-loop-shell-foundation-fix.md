# loop shell foundation fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 `rp5-serial` provider 与 `boot-failure-debug-loop` 的基础串口交互链路，使 loop 能稳定识别已存在的串口 shell、执行 L1 只读命令并采集输出。

**Architecture:** 采用“host pending prompt 可见性 + automation 双通道协议分离 + workflow 重观察/L1 采样”三段式修复。provider 层负责输出可见性和协议纯化，workflow 层负责 prompt 等待、二次分类和命令输出证据沉淀，不在本轮引入 service parser。

**Tech Stack:** Python 3、pytest、JSON Lines 协议、`rp5_serial` provider、`boot_failure_debug` workflow

---

## Spec

- `docs/specs/2026-06-19-loop-shell-foundation-fix-design.md`

## File Structure

| 类型 | 路径 | 责任 |
|---|---|---|
| 修改 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py` | 让 recent buffer 可附带 pending 半行 prompt |
| 修改 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py` | 建立命令/流双通道，隔离协议响应与串口文本 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py` | 覆盖 automation 双通道、send_line 响应消费、stream.data 读取 |
| 修改 | `engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py` | 覆盖 pending prompt recent 可见性 |
| 修改 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/models.py` | 为动作记录补充输出证据与 metadata |
| 修改 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/actions.py` | 保持动作规划，允许 runner 接管 wait_prompt/L1 证据化执行 |
| 修改 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/runner.py` | 增加 wait_prompt、reobserve、L1 输出采样主闭环 |
| 修改 | `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/report.py` | 在 summary/report 中加入命令输出摘要 |
| 修改 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_models.py` | 校验 ActionRecord 新字段序列化 |
| 修改 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_runner.py` | 覆盖 login_prompt_not_reached -> shell_prompt_available 与 L1 证据采样 |
| 修改 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_report.py` | 覆盖报告中的 L1 输出摘要 |
| 修改 | `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_transport.py` | 保持 transport 合同稳定 |

---

## Task 1: Host recent buffer 暴露 pending prompt

**Files:**
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py`

- [ ] **Step 1: 先写 failing test，锁定 pending prompt 可见性**

```python
# engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py
from rp5_serial.host.serial_runtime import RuntimeState


def test_recent_lines_includes_pending_prompt_text():
    state = RuntimeState(device_id="rp5")
    state._line_buffer = ["Booting Linux"]
    state._rx_buf = b"console:/ $"

    assert state.recent_lines(10) == ["Booting Linux", "console:/ $"]


def test_recent_lines_respects_limit_when_pending_prompt_exists():
    state = RuntimeState(device_id="rp5")
    state._line_buffer = ["line1", "line2", "line3"]
    state._rx_buf = b"console:/ #"

    assert state.recent_lines(2) == ["line3", "console:/ #"]
```

- [ ] **Step 2: 运行 provider monitor 测试，确认当前失败**

Run:
```bash
PYTHONPATH="engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py -q
```

Expected: 至少 `test_recent_lines_includes_pending_prompt_text` 失败，因为当前 `recent_lines()` 不会返回 `_rx_buf` 中的半行文本。

- [ ] **Step 3: 最小实现 pending prompt 拼接逻辑**

```python
# engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py
    def _pending_text(self) -> str | None:
        if not self._rx_buf:
            return None
        text = self._rx_buf.decode("utf-8", errors="replace").rstrip("\r")
        return text or None

    def recent_lines(self, limit: int) -> list[str]:
        """返回最近 N 行缓冲；若存在未换行半行，也作为最后一条观察结果返回。"""
        with self._lock:
            if limit <= 0:
                return []
            lines = list(self._line_buffer[-limit:])
            pending = self._pending_text()
            if pending:
                lines.append(pending)
            if len(lines) > limit:
                lines = lines[-limit:]
            return lines
```

- [ ] **Step 4: 重新运行 provider monitor 测试，确认转绿**

Run:
```bash
PYTHONPATH="engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py -q
```

Expected: `2 passed`。

- [ ] **Step 5: 提交 host 可见性修复**

```bash
git add engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py
git commit -m "fix(loop): expose pending serial prompt in recent buffer"
```

---

## Task 2: AutomationClient 建立命令/流双通道

**Files:**
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py`
- Create: `engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py`

- [ ] **Step 1: 先写 failing test，锁定双通道与响应消费语义**

```python
# engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py
import json
from collections import deque

from rp5_serial.client.automation import AutomationClient
from rp5_serial.shared.codec import encode_message


class FakeRaw:
    def __init__(self, messages: list[dict]):
        self._messages = deque(encode_message(m) for m in messages)

    def readline(self):
        if not self._messages:
            return b""
        return self._messages.popleft()

    def close(self):
        return None


class FakeSocket:
    def __init__(self, raw: FakeRaw):
        self.raw = raw
        self.sent: list[dict] = []
        self.timeout = None
        self.closed = False

    def makefile(self, mode: str):
        return self.raw

    def sendall(self, payload: bytes):
        self.sent.append(json.loads(payload.decode("utf-8")))

    def settimeout(self, value):
        self.timeout = value

    def close(self):
        self.closed = True


def test_connect_subscribes_stream_channel(monkeypatch):
    cmd_sock = FakeSocket(FakeRaw([]))
    stream_sock = FakeSocket(FakeRaw([{"ok": True, "code": "OK", "message": "ok", "data": {}}]))
    sockets = deque([cmd_sock, stream_sock])

    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: sockets.popleft())

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()

    assert stream_sock.sent[0]["op"] == "stream.subscribe"


def test_send_line_consumes_command_response(monkeypatch):
    cmd_sock = FakeSocket(
        FakeRaw([
            {"ok": True, "code": "OK", "message": "ok", "data": {}},
            {"ok": True, "code": "OK", "message": "ok", "data": {}},
        ])
    )
    stream_sock = FakeSocket(FakeRaw([{"ok": True, "code": "OK", "message": "ok", "data": {}}]))
    sockets = deque([cmd_sock, stream_sock])
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: sockets.popleft())

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()
    client.acquire_writer()
    client.send_line("uname -a")

    assert [m["op"] for m in cmd_sock.sent] == ["writer.acquire", "input.send_line"]


def test_read_until_timeout_returns_only_stream_text(monkeypatch):
    cmd_sock = FakeSocket(FakeRaw([]))
    stream_sock = FakeSocket(
        FakeRaw([
            {"ok": True, "code": "OK", "message": "ok", "data": {}},
            {"op": "stream.data", "data": {"text": "console:/ $"}},
        ])
    )
    sockets = deque([cmd_sock, stream_sock])
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: sockets.popleft())

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()

    assert client.read_until_timeout(0.1) == ["console:/ $"]
```

- [ ] **Step 2: 运行 automation client 测试，确认当前失败**

Run:
```bash
PYTHONPATH="engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py -q
```

Expected: 至少 `test_connect_subscribes_stream_channel` 与 `test_send_line_consumes_command_response` 失败，因为当前只有单 socket 且 `send_line()` 不读响应。

- [ ] **Step 3: 以双连接最小实现命令/流分离**

```python
# engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py
class AutomationClient:
    def __init__(self, host: str = "127.0.0.1", port: int = 9700, owner_id: str | None = None):
        self.host = host
        self.port = port
        self.owner_id = owner_id or f"auto-{os.getpid()}"
        self._cmd_sock: socket.socket | None = None
        self._cmd_raw = None
        self._stream_sock: socket.socket | None = None
        self._stream_raw = None

    def connect(self) -> None:
        self._cmd_sock = socket.create_connection((self.host, self.port), timeout=5)
        self._cmd_raw = self._cmd_sock.makefile("rb")
        self._stream_sock = socket.create_connection((self.host, self.port), timeout=5)
        self._stream_raw = self._stream_sock.makefile("rb")
        self._stream_sock.sendall(encode_message({"op": "stream.subscribe", "data": {}}))
        response = self._read_response(self._stream_raw)
        if response is None or response.get("code") != OK:
            raise OSError("stream.subscribe failed")

    def acquire_writer(self) -> bool:
        request = {"op": "writer.acquire", "data": {"owner_type": "workflow", "owner_id": self.owner_id}}
        self._cmd_sock.sendall(encode_message(request))
        response = self._read_response(self._cmd_raw)
        if response is None:
            raise OSError("host 关闭连接")
        return response.get("code") == OK

    def send_line(self, text: str) -> None:
        request = {"op": "input.send_line", "data": {"text": text}}
        self._cmd_sock.sendall(encode_message(request))
        response = self._read_response(self._cmd_raw)
        if response is None:
            raise OSError("host 关闭连接")
        if response.get("code") != OK:
            raise OSError(response.get("message") or "input.send_line failed")

    def read_until_timeout(self, timeout_sec: float) -> list[str]:
        if timeout_sec <= 0:
            return []
        lines: list[str] = []
        deadline = time.monotonic() + timeout_sec
        self._stream_sock.settimeout(0.2)
        try:
            while time.monotonic() < deadline:
                try:
                    payload = self._read_response(self._stream_raw)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if payload is None:
                    break
                if payload.get("op") == "stream.data":
                    text = payload.get("data", {}).get("text")
                    if isinstance(text, str):
                        lines.append(text)
        finally:
            try:
                self._stream_sock.settimeout(None)
            except OSError:
                pass
        return lines

    def capture_recent_lines(self, limit: int) -> list[str]:
        request = {"op": "stream.read_recent", "data": {"limit": limit}}
        self._cmd_sock.sendall(encode_message(request))
        response = self._read_response(self._cmd_raw)
        if response is None:
            raise OSError("host 关闭连接")
        return response.get("data", {}).get("lines", []) or []

    def _read_response(self, raw) -> dict | None:
        line = raw.readline()
        if not line:
            return None
        return decode_message(line)
```

- [ ] **Step 4: 补 release 关闭双通道，并跑 provider 全量测试**

Run:
```bash
PYTHONPATH="engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/connection/providers/rp5-serial/python/tests -q
```

Expected: provider 目录测试全绿，`test_automation_client.py` 通过且既有 lease/session/monitor/interactive 测试不回归。

- [ ] **Step 5: 提交 automation 协议修复**

```bash
git add engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py
git commit -m "fix(loop): separate automation command and stream channels"
```

---

## Task 3: workflow 落地 wait_prompt、重观察与 L1 输出采样

**Files:**
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/models.py`
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/actions.py`
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/runner.py`
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_models.py`
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_runner.py`

- [ ] **Step 1: 先写 failing test，锁定重分类与动作证据**

```python
# engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_runner.py
from boot_failure_debug.models import ObservedLine
from boot_failure_debug.runner import BootFailureRunner


class PromptRecoveringTransport:
    def __init__(self):
        self._capture_calls = 0
        self.sent: list[str] = []

    def acquire_writer(self):
        return True

    def release(self):
        return None

    def send_line(self, text: str):
        self.sent.append(text)

    def capture_window(self, timeout_sec: float, recent_limit: int):
        self._capture_calls += 1
        if self._capture_calls == 1:
            return [ObservedLine(1.0, "init: starting service 'zygote'")]
        if self._capture_calls == 2:
            return [ObservedLine(2.0, "console:/ $")]
        return [ObservedLine(3.0, "uid=0(root) gid=0(root)")]

    def wait_for_pattern(self, patterns: list[str], timeout_sec: float, recent_limit: int):
        return ObservedLine(2.0, "console:/ $")


def test_login_prompt_not_reached_can_reclassify_to_shell_prompt():
    runner = BootFailureRunner(_cfg(), PromptRecoveringTransport())
    attempt = runner.run()

    assert attempt.outcome == "EXIT_SUCCESS"
    assert attempt.final_classification == "shell_prompt_available"
    assert "" in attempt.actions[0].metadata.get("sent_inputs", [""])


def test_l1_actions_capture_output_lines_after_prompt_recovery():
    runner = BootFailureRunner(_cfg(), PromptRecoveringTransport())
    attempt = runner.run()

    dmesg_action = next(a for a in attempt.actions if a.command == "dmesg")
    assert dmesg_action.output_lines
    assert dmesg_action.metadata["captured_line_count"] >= 1
```

```python
# engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_models.py
from boot_failure_debug.models import ActionRecord


def test_action_record_serializes_output_lines_and_metadata():
    record = ActionRecord(
        action_id="a-1",
        level="L1",
        command="dmesg",
        reason="prompt available",
        result="OK",
        output_lines=["[ 1.0 ] init"],
        metadata={"captured_line_count": 1, "pattern_matched": True},
    )
    data = record.to_dict()
    assert data["output_lines"] == ["[ 1.0 ] init"]
    assert data["metadata"]["captured_line_count"] == 1
```

- [ ] **Step 2: 运行 workflow 定向测试，确认当前失败**

Run:
```bash
PYTHONPATH="engineering/loop/workflows/boot-failure-debug-loop/python:engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_models.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_runner.py -q
```

Expected: 至少因 `ActionRecord` 缺少 `output_lines/metadata`、runner 不会在 `login_prompt_not_reached` 后重观察而失败。

- [ ] **Step 3: 最小扩展 ActionRecord 与 runner 私有执行助手**

```python
# engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/models.py
from dataclasses import asdict, dataclass, field

@dataclass
class ActionRecord:
    action_id: str
    level: str
    command: str
    reason: str
    result: str
    evidence_ref: str | None = None
    output_lines: list[str] = field(default_factory=list)
    metadata: dict[str, str | int | bool | list[str]] = field(default_factory=dict)
```

```python
# engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/runner.py
    def _run_wait_prompt(self, action: ActionRecord) -> ActionRecord:
        matched = self.transport.wait_for_pattern(
            self.cfg.prompt_markers,
            self.cfg.prompt_wait_sec,
            self.cfg.recent_lines_limit,
        )
        return ActionRecord(
            action_id=action.action_id,
            level=action.level,
            command=action.command,
            reason=action.reason,
            result="OK" if matched else "FAIL",
            output_lines=[matched.text] if matched else [],
            metadata={"pattern_matched": bool(matched)},
        )

    def _run_l1_capture(self, action: ActionRecord) -> ActionRecord:
        self.transport.send_line(action.command)
        window = self.transport.capture_window(
            timeout_sec=self.cfg.capture_window_sec,
            recent_limit=self.cfg.recent_lines_limit,
        )
        output_lines = [line.text for line in window]
        return ActionRecord(
            action_id=action.action_id,
            level=action.level,
            command=action.command,
            reason=action.reason,
            result="OK",
            output_lines=output_lines,
            metadata={"captured_line_count": len(output_lines)},
        )
```

- [ ] **Step 4: 在 run() 中接入 login_prompt_not_reached -> wait_prompt -> reobserve 闭环**

```python
# engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/runner.py
            elif state == "COLLECT_EVIDENCE":
                if matches:
                    planned = plan_actions(matches)
                    executed: list[ActionRecord] = []
                    for action in planned:
                        if action.command == "wait_prompt":
                            executed.append(self._run_wait_prompt(action))
                        elif action.command in self.cfg.l1_commands:
                            executed.append(self._run_l1_capture(action))
                        else:
                            executed.append(execute_action(action, self.transport))
                    all_actions.extend(executed)

                    wait_prompt_ok = any(
                        a.command == "wait_prompt" and a.result == "OK" for a in executed
                    )
                    if classification == "login_prompt_not_reached" and wait_prompt_ok:
                        snapshot = capture_snapshot(
                            self.transport,
                            self.cfg,
                            timeout_sec=self.cfg.capture_window_sec,
                        )
                        boot_cycle_count = count_boot_cycles(snapshot.lines) if snapshot else 0
                        matches = evaluate_rules(snapshot, self.cfg)
                        classification = classify(matches)
                        if classification == "shell_prompt_available":
                            l1_planned = plan_actions(matches)
                            l1_executed = [
                                self._run_l1_capture(action)
                                for action in l1_planned
                                if action.command in self.cfg.l1_commands
                            ]
                            all_actions.extend(l1_executed)
                state = "REASSESS"
```

- [ ] **Step 5: 运行 workflow 关键测试，确认 prompt 重观察与 L1 证据转绿**

Run:
```bash
PYTHONPATH="engineering/loop/workflows/boot-failure-debug-loop/python:engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_models.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_runner.py -q
```

Expected: 新增用例通过，原有 `normal_boot/kernel_panic/boot_hang/no_output/reboot_loop` 收口不回归。

- [ ] **Step 6: 提交 workflow 闭环修复**

```bash
git add engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/models.py engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/runner.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_models.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_runner.py
git commit -m "fix(loop): reobserve prompt and capture l1 outputs"
```

---

## Task 4: 报告输出与全量回归验证

**Files:**
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/report.py`
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_report.py`
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_transport.py`
- Modify: `engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_actions.py`

- [ ] **Step 1: 先写 failing test，锁定 summary 中的 L1 输出摘要**

```python
# engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_report.py
from boot_failure_debug.models import ActionRecord, LoopAttempt
from boot_failure_debug.report import render_summary


def test_render_summary_contains_l1_output_preview():
    attempt = LoopAttempt(
        attempt_id="att-report",
        device_id="rp5",
        outcome="EXIT_SUCCESS",
        final_classification="shell_prompt_available",
        boot_cycle_count=1,
        matched_rules=[],
        actions=[
            ActionRecord(
                action_id="a-1",
                level="L1",
                command="dmesg",
                reason="prompt available",
                result="OK",
                output_lines=["[ 1.0 ] init started", "[ 2.0 ] servicemanager ready"],
                metadata={"captured_line_count": 2},
            )
        ],
    )

    summary = render_summary(attempt)
    assert "L1采样" in summary
    assert "dmesg" in summary
    assert "init started" in summary
```

- [ ] **Step 2: 运行 report 测试，确认当前失败**

Run:
```bash
PYTHONPATH="engineering/loop/workflows/boot-failure-debug-loop/python:engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_report.py -q
```

Expected: 新增断言失败，因为 `render_summary()` 还不会输出命令采样摘要。

- [ ] **Step 3: 最小实现报告中的 L1 证据预览**

```python
# engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/report.py
    l1_previews: list[str] = []
    for action in attempt.actions:
        if action.level == "L1" and action.output_lines:
            preview = " | ".join(action.output_lines[:2])
            l1_previews.append(f"{action.command}: {preview}")

    lines = [
        f"最终分类: {attempt.final_classification}",
        f"结果: {attempt.outcome}",
        f"boot_cycle: {attempt.boot_cycle_count}",
        f"命中规则: {', '.join(matched_rule_ids) if matched_rule_ids else '(无)'}",
        f"执行动作: {', '.join(action_cmds) if action_cmds else '(无)'}",
        f"关键证据: {', '.join(evidence_summary[:5]) if evidence_summary else '(无)'}",
    ]
    if l1_previews:
        lines.append(f"L1采样: {'; '.join(l1_previews[:4])}")
```

- [ ] **Step 4: 跑 workflow 全量回归**

Run:
```bash
PYTHONPATH="engineering/loop/workflows/boot-failure-debug-loop/python:engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests -q
```

Expected: workflow 全量测试通过，尤其 `test_runner.py`、`test_report.py`、`test_transport.py` 无回归。

- [ ] **Step 5: 跑 provider + workflow 联合回归**

Run:
```bash
PYTHONPATH="engineering/loop/workflows/boot-failure-debug-loop/python:engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/connection/providers/rp5-serial/python/tests engineering/loop/workflows/boot-failure-debug-loop/python/tests -q
```

Expected: provider 与 workflow 测试全集通过。

- [ ] **Step 6: 最小 live 验证**

Run:
```bash
PYTHONPATH="engineering/loop/workflows/boot-failure-debug-loop/python:engineering/loop/connection/providers/rp5-serial/python" python -m boot_failure_debug.cli --host 127.0.0.1 --port 9700 --device-profile engineering/loop/connection/profiles/devices/rp5/default.json --workflow-profile engineering/loop/profiles/boot-failure-debug/default.json --artifacts-dir /tmp/opencode/loop-shell-live
```

Expected:
- 若串口 shell 已存在或回车可唤起，最终分类应为 `shell_prompt_available`
- `summary.txt` 中应出现 `L1采样:` 行
- `report.json` 中 `actions[*].output_lines` 应包含命令输出证据

- [ ] **Step 7: 提交报告与回归完成状态**

```bash
git add engineering/loop/workflows/boot-failure-debug-loop/python/boot_failure_debug/report.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_report.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_actions.py engineering/loop/workflows/boot-failure-debug-loop/python/tests/test_transport.py
git commit -m "feat(loop): report l1 shell evidence"
```

---

## Self-Review Checklist

- Spec coverage:
  - provider pending prompt 可见性 -> Task 1
  - automation 协议/流分离与 send_line 响应消费 -> Task 2
  - wait_prompt / reobserve -> Task 3
  - L1 输出证据与报告 -> Task 3 / Task 4
  - 全量测试与 live 最小验证 -> Task 4
- Placeholder scan:
  - 无 `TODO/TBD/implement later` 字样
  - 所有任务包含明确文件、测试、命令、期望结果
- Type consistency:
  - `ActionRecord.output_lines` / `ActionRecord.metadata` 在 models、runner、report、tests 中保持同名
  - `cfg.prompt_wait_sec`、`cfg.capture_window_sec`、`cfg.recent_lines_limit` 在 runner 统一使用

---

## Verification Commands Summary

```bash
PYTHONPATH="engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/connection/providers/rp5-serial/python/tests -q
PYTHONPATH="engineering/loop/workflows/boot-failure-debug-loop/python:engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/workflows/boot-failure-debug-loop/python/tests -q
PYTHONPATH="engineering/loop/workflows/boot-failure-debug-loop/python:engineering/loop/connection/providers/rp5-serial/python" pytest engineering/loop/connection/providers/rp5-serial/python/tests engineering/loop/workflows/boot-failure-debug-loop/python/tests -q
```
