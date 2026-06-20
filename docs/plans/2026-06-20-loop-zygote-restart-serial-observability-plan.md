# Loop 串口观测补强与 Zygote 重启定位能力 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 `engineering/loop` 补齐持续串口 transcript、真实时间戳、重启周期切分与证据归档链路，使 LE 在 shell 可达场景下能提供完整 zygote 重启定位证据，同时以 transcript 底座覆盖 shell 不可达场景的根证据保留。

**Architecture:** 方案分三层推进。第一层在 rp5-serial host 侧补齐“持续落盘 transcript + 真实时间戳 + recent 元数据”，把串口正文从易丢失的内存缓冲提升为可归档产物。第二层在 loop_core / provider transport / collector / runner 中把 transcript 元数据、serial snippet、reboot cycle 摘要写入 `EvidenceBundle`，并以 `serial_context` collector 为各场景提供串口根证据入口。第三层补系统用例、provider bash 入口与文档，使 monitor / run / evidence / README/WORKFLOW 全部围绕“串口是第一现场”收敛。

**Tech Stack:** Python 3、pytest、Bash、JSON artifacts、YAML、rp5-serial provider、harness observability

**设计依据:** `docs/specs/2026-06-20-loop-zygote-restart-serial-observability-design.md`

---

## 文件结构与职责映射

### Host / Provider
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py`
  - 为 recent buffer 增加结构化行模型（文本 + 实际时间戳 + 来源序号），并持续写入 transcript 文件。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py`
  - 增加 CLI 参数，把 transcript 路径/最近 transcript 路径输出到日志与状态接口。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/logging_utils.py`
  - 保持 host lifecycle 日志，同时为 transcript 目录准备统一创建逻辑。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/handler.py`
  - 扩展 `session.status` / `stream.read_recent` 响应，返回 transcript 元数据与 recent 行时间戳。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py`
  - 增加读取结构化 recent 行 / host 状态的接口，供 transport 获取 transcript 元数据。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/status.py`
  - 打印 transcript 路径与 serial observability 摘要。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py`
  - live transport 改用真实时间戳构造 `ObservedLine`，并提供 transcript 引用、snippet、reboot cycle 摘要提取。

### Loop Core
- Modify: `engineering/loop/core/python/loop_core/models.py`
  - 扩展 `CollectorResult` / `EvidenceBundle` 字段，承载 serial artifacts、reboot summary、warnings。
- Modify: `engineering/loop/core/python/loop_core/collector.py`
  - 支持 collector 返回结构化 artifact paths / metadata，保留串口 snippet 引用。
- Modify: `engineering/loop/core/python/loop_core/executor.py`
  - 失败时合并 transport 暴露的串口诊断元数据。
- Modify: `engineering/loop/core/python/loop_core/runner.py`
  - 把 provider transcript context、device profile、execution config 统一写入 bundle。
- Modify: `engineering/loop/core/python/loop_core/evidence.py`
  - 把 transcript / reboot cycles / serial snippet 路径落到 JSON 与 summary。
- Modify: `engineering/loop/core/python/loop_core/config.py`
  - 明确 boot/reboot marker 与 serial diagnosis 参数默认值。

### Cases / Scripts / Docs
- Modify: `engineering/loop/cases/common/shell.yaml`
  - 新增串口保底 collector（如 `serial_recent` / `service_snapshot`），并让 `shell_reachable` 失败时也触发串口取证。
- Modify: `engineering/loop/cases/system/boot-success.yaml`
  - 为 `zygote_running` 增加串口 collector 与 restart 线索 collector 组合。
- Modify: `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_monitor.sh`
  - 增加 transcript/artifacts 参数透传与 harness artifact 归档。
- Modify: `engineering/loop/scripts/le.sh`
  - 透传 transcript/artifacts 相关参数，保持 CLI 入口与 harness 日志一致。
- Modify: `engineering/loop/README.md`
  - 更新 transcript、EvidenceBundle、新 collector 用法。
- Modify: `engineering/loop/WORKFLOW.md`
  - 更新“串口第一现场”的执行链路与故障定位方式。
- Modify: `engineering/loop/connection/providers/rp5-serial/WORKFLOW.md`
  - 让文档与 host 实现保持一致：raw stream / transcript / status / artifacts。

### 测试文件
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py`
  - recent 结构化时间戳、transcript 状态、pending prompt 行为。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py`
  - live transport 真实时间戳、serial snippet、reboot cycle 摘要。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py`
  - 新增 recent 结构化读取与 status transcript 元数据读取测试。
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py`
  - `RuntimeState.status()` 新字段测试。
- Modify: `engineering/loop/core/python/tests/test_collector.py`
  - collector artifact paths / snippet metadata。
- Modify: `engineering/loop/core/python/tests/test_runner.py`
  - runner 把 serial context 写入 bundle。
- Modify: `engineering/loop/core/python/tests/test_evidence.py`
  - JSON / summary 输出 transcript 路径、snippet、reboot cycle。
- Modify: `engineering/loop/core/python/tests/test_executor.py`
  - `shell_reachable` fail 时串口 collector 保底触发。

### 回归命令
- Provider 单测：
  ```bash
  PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
    python3 -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/ -v --import-mode=importlib
  ```
- Core 单测：
  ```bash
  PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
    python3 -m pytest engineering/loop/core/python/tests/ -v --import-mode=importlib
  ```
- 全量回归：
  ```bash
  PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
    python3 -m pytest \
    engineering/loop/core/python/tests/ \
    engineering/loop/connection/providers/rp5-serial/python/tests/ \
    -v --import-mode=importlib
  ```

---

### Task 1: Host transcript 与 recent 结构化观测

**Files:**
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/logging_utils.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/handler.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py`
- Test: `engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py`
- Test: `engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py`

- [ ] **Step 1: 先写失败测试，锁定 transcript 与 recent 元数据契约**

```python
from pathlib import Path

from rp5_serial.host.serial_runtime import RuntimeState


def test_recent_entries_include_timestamp_and_text(tmp_path):
    state = RuntimeState(device_id="rp5", transcript_dir=str(tmp_path))
    state._line_buffer = [
        {"text": "Booting Linux", "ts": "2026-06-20T12:00:00+0800"},
        {"text": "init: starting service 'zygote'", "ts": "2026-06-20T12:00:01+0800"},
    ]
    state._rx_buf = b"console:/ $"

    recent = state.recent_entries(3)

    assert recent[0]["text"] == "Booting Linux"
    assert recent[0]["ts"] == "2026-06-20T12:00:00+0800"
    assert recent[-1]["text"] == "console:/ $"
    assert recent[-1]["pending"] is True


def test_read_lines_appends_transcript_file(tmp_path):
    class FakeSerial:
        in_waiting = 33
        is_open = True

        def read(self, waiting):
            return b"line1\nline2\n"

    state = RuntimeState(device_id="rp5", transcript_dir=str(tmp_path))
    state._serial = FakeSerial()

    lines = state.read_lines()

    assert lines == ["line1", "line2"]
    transcript = Path(state.transcript_path)
    assert transcript.exists()
    text = transcript.read_text(encoding="utf-8")
    assert "line1" in text
    assert "line2" in text


def test_status_contains_transcript_metadata(tmp_path):
    state = RuntimeState(device_id="rp5", transcript_dir=str(tmp_path))
    status = state.status().to_dict()

    assert status["transcript_path"].endswith("rp5-serial-transcript.log")
    assert status["recent_buffer_limit"] >= 500
```

- [ ] **Step 2: 运行失败测试，确认当前实现尚不支持 transcript 元数据**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py \
  -v --import-mode=importlib
```

Expected: FAIL，报 `RuntimeState` 缺少 `transcript_dir/recent_entries/transcript_path` 或 `status` 中缺少 transcript 字段。

- [ ] **Step 3: 最小实现 host transcript 能力**

```python
# serial_runtime.py
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

TZ = timezone(timedelta(hours=8))
MAX_LINE_BUFFER = 2000


def now_iso() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass
class RuntimeState:
    device_id: str
    transcript_dir: str | None = None
    serial_port: str | None = None
    baudrate: int = 115200
    _line_buffer: list[dict] = field(default_factory=list)
    _rx_buf: bytes = b""

    def __post_init__(self) -> None:
        base = Path(self.transcript_dir or "output/host-log")
        base.mkdir(parents=True, exist_ok=True)
        self.transcript_path = str(base / "rp5-serial-transcript.log")

    def _append_entry(self, text: str, pending: bool = False) -> dict:
        entry = {"text": text, "ts": now_iso(), "pending": pending}
        if not pending:
            self._line_buffer.append(entry)
            if len(self._line_buffer) > MAX_LINE_BUFFER:
                del self._line_buffer[: len(self._line_buffer) - MAX_LINE_BUFFER]
            with Path(self.transcript_path).open("a", encoding="utf-8") as fp:
                fp.write(f"{entry['ts']} {text}\n")
        return entry

    def recent_entries(self, limit: int) -> list[dict]:
        if limit <= 0:
            return []
        entries = list(self._line_buffer[-limit:])
        pending = self._pending_text()
        if pending:
            entries.append({"text": pending, "ts": now_iso(), "pending": True})
        if len(entries) > limit:
            entries = entries[-limit:]
        return entries

    def recent_lines(self, limit: int) -> list[str]:
        return [entry["text"] for entry in self.recent_entries(limit)]
```

- [ ] **Step 4: 扩展 status / server / handler 输出 transcript 元信息**

```python
# serial_runtime.py
    def status(self) -> StatusResponse:
        with self._lock:
            payload = StatusResponse(
                host_state="READY",
                serial_state=self._serial_state(),
                active_session=self.active_session.to_dict() if self.active_session else None,
                active_writer=self.active_writer.to_dict() if self.active_writer else None,
                subscriber_count=self.subscriber_count,
            )
            data = payload.to_dict()
            data["transcript_path"] = self.transcript_path
            data["recent_buffer_limit"] = MAX_LINE_BUFFER
            data["recent_line_count"] = len(self._line_buffer)
            return StatusResponse(**payload.to_dict())

# handler.py
    def _op_session_status(self, data: dict[str, Any]) -> None:
        status = self._state.status().to_dict()
        status["transcript_path"] = self._state.transcript_path
        status["recent_buffer_limit"] = MAX_LINE_BUFFER
        status["recent_line_count"] = len(self._state._line_buffer)
        self._send(make_ok(status))

    def _op_stream_read_recent(self, data: dict[str, Any]) -> None:
        try:
            limit = int(data.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        entries = self._state.recent_entries(limit)
        self._send(make_ok({"lines": [entry["text"] for entry in entries], "entries": entries}))
```

- [ ] **Step 5: 运行测试，确认 host 结构化观测契约通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py \
  -v --import-mode=importlib
```

Expected: PASS。

- [ ] **Step 6: 提交这一小步**

```bash
git add \
  engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py \
  engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py \
  engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/logging_utils.py \
  engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/handler.py \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py

git commit -m "feat(loop): persist serial transcript metadata"
```

---

### Task 2: AutomationClient 与 live transport 暴露真实时间戳 / transcript 上下文

**Files:**
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/status.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py`
- Test: `engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py`
- Test: `engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py`

- [ ] **Step 1: 先写失败测试，锁定 recent entries / host status / transport 真实时间戳**

```python
from unittest.mock import MagicMock

from rp5_serial.transport import Rp5SerialTransport


def test_capture_recent_entries_returns_structured_rows(monkeypatch):
    cmd_sock = FakeCmdSocket(FakeRaw([
        {"ok": True, "code": "OK", "message": "ok", "data": {
            "lines": ["line1"],
            "entries": [{"text": "line1", "ts": "2026-06-20T12:00:00+0800", "pending": False}],
        }}
    ]))
    stream_sock = FakeStreamSocket(_ok())
    _patch_connections(monkeypatch, cmd_sock, stream_sock)

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()

    entries = client.capture_recent_entries(1)
    assert entries[0]["text"] == "line1"
    assert entries[0]["ts"] == "2026-06-20T12:00:00+0800"


def test_transport_capture_since_uses_host_timestamps():
    client = MagicMock()
    client.capture_recent_entries.return_value = [
        {"text": "Booting Linux", "ts": "2026-06-20T12:00:00+0800", "pending": False},
        {"text": "console:/ $", "ts": "2026-06-20T12:00:02+0800", "pending": False},
    ]
    client.read_until_timeout.return_value = []
    client.fetch_status.return_value = {
        "data": {"transcript_path": "/tmp/rp5-serial-transcript.log", "recent_line_count": 2}
    }
    transport = Rp5SerialTransport(client)

    capture = transport.capture_since(transport.mark_output_boundary(), 5, 50, ["console:/ $"])

    assert capture.lines[0].text == "Booting Linux"
    assert capture.lines[0].t == 0.0
    assert capture.lines[1].t == 2.0
    assert capture.warnings == []
    assert transport.describe_runtime_context()["transcript_path"] == "/tmp/rp5-serial-transcript.log"
```

- [ ] **Step 2: 运行失败测试，确认 client/transport 还没有新接口**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py \
  -v --import-mode=importlib
```

Expected: FAIL，报 `capture_recent_entries` / `fetch_status` / `describe_runtime_context` 不存在，或 transport 仍用伪时间戳。

- [ ] **Step 3: 在 AutomationClient 中新增结构化 recent/status 读取接口**

```python
# automation.py
    def capture_recent_entries(self, limit: int) -> list[dict]:
        request = {"op": "stream.read_recent", "data": {"limit": limit}}
        self._cmd_sock.sendall(encode_message(request))
        response = self._read_response(self._cmd_raw)
        if response is None:
            raise OSError("host 关闭连接")
        if response.get("code") != OK:
            raise OSError(response.get("message") or "stream.read_recent failed")
        entries = response.get("data", {}).get("entries", [])
        if not isinstance(entries, list):
            return []
        return [entry for entry in entries if isinstance(entry, dict) and isinstance(entry.get("text"), str)]

    def fetch_status(self) -> dict:
        request = {"op": "session.status", "data": {}}
        self._cmd_sock.sendall(encode_message(request))
        response = self._read_response(self._cmd_raw)
        if response is None:
            raise OSError("host 关闭连接")
        return response
```

- [ ] **Step 4: 在 Rp5SerialTransport 中改用 host 时间戳并暴露运行时上下文**

```python
# transport.py
from datetime import datetime


def _parse_ts(ts: str) -> float:
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S%z").timestamp()


class Rp5SerialTransport(BaseTransport):
    def __init__(self, client) -> None:
        self.client = client
        self._runtime_context: dict[str, object] = {}
        self._capture_generation = 0

    def describe_runtime_context(self) -> dict[str, object]:
        try:
            status = self.client.fetch_status()
        except OSError as exc:
            return {"warnings": [f"fetch_status failed: {exc}"]}
        data = status.get("data", {}) if isinstance(status, dict) else {}
        self._runtime_context = {
            "transcript_path": data.get("transcript_path", ""),
            "recent_line_count": data.get("recent_line_count", 0),
            "recent_buffer_limit": data.get("recent_buffer_limit", 0),
        }
        return dict(self._runtime_context)

    def _build_lines_from_entries(self, entries: list[dict]) -> list[ObservedLine]:
        if not entries:
            return []
        base = _parse_ts(entries[0]["ts"])
        lines: list[ObservedLine] = []
        for entry in entries:
            current = _parse_ts(entry["ts"])
            lines.append(ObservedLine(t=round(current - base, 3), text=entry["text"]))
        return lines

    def capture_since(self, boundary, timeout_sec, recent_limit, prompt_markers=None):
        del boundary
        entries = self.client.capture_recent_entries(recent_limit)
        pushed_raw = self.client.read_until_timeout(timeout_sec)
        merged_entries = list(entries)
        for text in pushed_raw:
            merged_entries.append({"text": text, "ts": entries[-1]["ts"] if entries else datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z"), "pending": False})
        lines = self._build_lines_from_entries(merged_entries)
        markers = prompt_markers or []
        prompt_visible = self._detect_prompt_visible(lines, markers)
        return CommandCapture(lines=lines, prompt_visible=prompt_visible)
```

- [ ] **Step 5: 运行测试，确认 transport 不再依赖伪时间戳**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py \
  -v --import-mode=importlib
```

Expected: PASS。

- [ ] **Step 6: 提交这一小步**

```bash
git add \
  engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py \
  engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/status.py \
  engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_automation_client.py \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py

git commit -m "feat(loop): expose live serial runtime context"
```

---

### Task 3: EvidenceBundle 承载 transcript / reboot cycle / serial snippet

**Files:**
- Modify: `engineering/loop/core/python/loop_core/models.py`
- Modify: `engineering/loop/core/python/loop_core/runner.py`
- Modify: `engineering/loop/core/python/loop_core/evidence.py`
- Modify: `engineering/loop/core/python/tests/test_runner.py`
- Modify: `engineering/loop/core/python/tests/test_evidence.py`
- Test: `engineering/loop/core/python/tests/test_runner.py`
- Test: `engineering/loop/core/python/tests/test_evidence.py`

- [ ] **Step 1: 先写失败测试，锁定 bundle 的 serial context 字段**

```python
from loop_core.models import EvidenceBundle


def test_runner_bundle_contains_serial_runtime_context(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: c1
    command: ""
    assert: {type: prompt_visible}
""")
    suite = load_suite(path, [str(tmp_path)])

    class TransportWithContext(FixtureTransport):
        def describe_runtime_context(self):
            return {
                "transcript_path": "/tmp/serial.log",
                "serial_snippet": ["line1", "line2"],
                "reboot_cycles": 2,
            }

    runner = LoopRunner(
        device_id="rp5",
        prompt_markers=["console:/ $"],
        transport=TransportWithContext([{"t": 1.0, "text": "console:/ $"}]),
        suite=suite,
    )
    bundle = runner.run()

    assert bundle.serial_context["transcript_path"] == "/tmp/serial.log"
    assert bundle.serial_context["reboot_cycles"] == 2


def test_summary_renders_transcript_and_reboot_cycles(tmp_path):
    bundle = EvidenceBundle(
        bundle_id="eb-1",
        device_id="rp5",
        suite="boot-success",
        timestamp="2026-06-20T12:00:00+08:00",
        summary={"total": 1, "passed": 0, "failed": 1, "skipped": 0, "overall": "FAIL"},
        cases=[],
        evidence={},
        serial_context={"transcript_path": "/tmp/serial.log", "reboot_cycles": 3, "serial_snippet": ["line1", "line2"]},
    )
    paths = write_evidence_bundle(bundle, str(tmp_path))
    text = Path(paths["summary_txt"]).read_text(encoding="utf-8")
    assert "/tmp/serial.log" in text
    assert "reboot cycles: 3" in text
    assert "line1" in text
```

- [ ] **Step 2: 运行失败测试，确认 bundle 结构尚未扩展**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/test_runner.py \
  engineering/loop/core/python/tests/test_evidence.py \
  -v --import-mode=importlib
```

Expected: FAIL，报 `EvidenceBundle` 缺少 `serial_context` 或 summary 未渲染 transcript 内容。

- [ ] **Step 3: 最小扩展 models / runner，把 transport context 注入 bundle**

```python
# models.py
@dataclass
class EvidenceBundle:
    bundle_id: str
    device_id: str
    suite: str
    timestamp: str
    summary: dict
    cases: list[TestCaseResult]
    evidence: dict[str, CollectorResult]
    device_profile: dict = field(default_factory=dict)
    execution_config: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    serial_context: dict = field(default_factory=dict)

# runner.py
    def _enrich_bundle(self, bundle: EvidenceBundle) -> None:
        bundle.device_profile = {
            "device_id": self.device_id,
            "prompt_markers": self.prompt_markers,
            **self.device_profile,
        }
        bundle.execution_config = {
            "capture_timeout": self.capture_timeout,
            "recent_limit": self.recent_limit,
            "provider_type": type(self.transport).__name__,
        }
        describe = getattr(self.transport, "describe_runtime_context", None)
        if callable(describe):
            bundle.serial_context = describe() or {}
```

- [ ] **Step 4: 更新 evidence 输出，把 transcript / reboot cycles / snippet 写进 JSON 和 summary**

```python
# evidence.py
    if bundle.serial_context:
        lines.extend([
            "",
            "=== 串口上下文 ===",
            f"transcript: {bundle.serial_context.get('transcript_path', '')}",
            f"reboot cycles: {bundle.serial_context.get('reboot_cycles', 0)}",
        ])
        snippet = bundle.serial_context.get("serial_snippet", [])
        if snippet:
            lines.append("serial snippet:")
            for item in snippet[:20]:
                lines.append(f"  {item}")
```

- [ ] **Step 5: 运行测试，确认 bundle 已成为可信串口证据出口**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/test_runner.py \
  engineering/loop/core/python/tests/test_evidence.py \
  -v --import-mode=importlib
```

Expected: PASS。

- [ ] **Step 6: 提交这一小步**

```bash
git add \
  engineering/loop/core/python/loop_core/models.py \
  engineering/loop/core/python/loop_core/runner.py \
  engineering/loop/core/python/loop_core/evidence.py \
  engineering/loop/core/python/tests/test_runner.py \
  engineering/loop/core/python/tests/test_evidence.py

git commit -m "feat(loop): attach serial context to evidence bundle"
```

---

### Task 4: Collector `serial_context` 模式与 zygote 重启诊断增强

**Files:**
- Modify: `engineering/loop/core/python/loop_core/collector.py`
- Modify: `engineering/loop/core/python/loop_core/executor.py`
- Modify: `engineering/loop/cases/common/shell.yaml`
- Modify: `engineering/loop/cases/system/boot-success.yaml`
- Modify: `engineering/loop/core/python/tests/test_collector.py`
- Modify: `engineering/loop/core/python/tests/test_executor.py`
- Test: `engineering/loop/core/python/tests/test_collector.py`
- Test: `engineering/loop/core/python/tests/test_executor.py`

- [ ] **Step 1: 先写失败测试，锁定 shell fail 时串口 collector 保底触发**

```python
def test_shell_reachable_fail_still_triggers_serial_collector(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: common.shell
version: 1
cases:
  - id: shell_reachable
    command: ""
    assert: {type: prompt_visible}
    severity: critical
    on_fail:
      collectors: [serial_recent]
collectors:
  serial_recent:
    commands: []
    mode: serial_context
    hints: "capture serial transcript context"
""")
    suite = load_suite(path, [str(tmp_path)])

    class SilentTransport(FixtureTransport):
        def describe_runtime_context(self):
            return {
                "transcript_path": "/tmp/serial.log",
                "serial_snippet": ["boot line", "reboot: Restarting system"],
                "reboot_cycles": 2,
            }

    bundle = CaseExecutor(SilentTransport([]), AssertionEngine()).execute_suite(
        suite,
        device_id="rp5",
        prompt_markers=["console:/ $"],
        capture_timeout=1.0,
        recent_limit=20,
    )

    assert bundle.cases[0].status == "fail"
    assert "common.shell.serial_recent" in bundle.evidence
    assert bundle.evidence["common.shell.serial_recent"].artifact_paths == ["/tmp/serial.log"]
```

- [ ] **Step 2: 运行失败测试，确认 collector 还不会消费 transport serial context**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/test_collector.py \
  engineering/loop/core/python/tests/test_executor.py \
  -v --import-mode=importlib
```

Expected: FAIL，报 `artifact_paths` 为空或 `shell_reachable` fail 时没有证据条目。

- [ ] **Step 3: 在 collector 中新增 `serial_context` 模式，直接消费 transport.describe_runtime_context()**

```python
# collector.py
    def run(self, name: str, spec: dict, capture_timeout: float = 5.0,
            recent_limit: int = 400,
            prompt_markers: list[str] | None = None) -> CollectorResult:
        commands = spec.get("commands", [])
        hints = spec.get("hints", "")
        mode = spec.get("mode", "commands")
        if mode == "serial_context":
            describe = getattr(self.transport, "describe_runtime_context", None)
            context = describe() if callable(describe) else {}
            outputs = [{
                "command": "serial_context",
                "lines": context.get("serial_snippet", []),
                "duration_sec": 0.0,
                "metadata": {
                    "reboot_cycles": context.get("reboot_cycles", 0),
                    "recent_line_count": context.get("recent_line_count", 0),
                },
            }]
            return CollectorResult(
                name=name,
                commands=[],
                outputs=outputs,
                hints=hints,
                status="ok",
                partial=False,
                artifact_paths=[context.get("transcript_path", "")] if context.get("transcript_path") else [],
            )
```

- [ ] **Step 4: 扩展 YAML，用串口 collector 作为所有 zygote 排查场景的保底证据**

```yaml
# common/shell.yaml
cases:
  - id: shell_reachable
    description: "shell prompt 可见，设备串口可达"
    command: ""
    assert:
      type: prompt_visible
    severity: critical
    on_fail:
      collectors: [serial_recent]
    tags: [shell, reachable]

collectors:
  serial_recent:
    commands: []
    mode: serial_context
    hints: "关注 reboot marker、zygote 重启前后串口片段、shell 不可达时的首现场日志"
  boot_log:
    commands:
      - "dmesg"
```

```yaml
# boot-success.yaml
  - id: zygote_running
    description: "zygote 服务处于 running 状态"
    command: "getprop init.svc.zygote"
    assert:
      type: contains
      value: "running"
    severity: critical
    requires: [shell_reachable]
    on_fail:
      collectors: [serial_recent, crash_dump, init_log]
    tags: [boot, android_core]
```

- [ ] **Step 5: 运行测试，确认 shell 不可达时依然保留根证据**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/test_collector.py \
  engineering/loop/core/python/tests/test_executor.py \
  -v --import-mode=importlib
```

Expected: PASS。

- [ ] **Step 6: 提交这一小步**

```bash
git add \
  engineering/loop/core/python/loop_core/collector.py \
  engineering/loop/core/python/loop_core/executor.py \
  engineering/loop/cases/common/shell.yaml \
  engineering/loop/cases/system/boot-success.yaml \
  engineering/loop/core/python/tests/test_collector.py \
  engineering/loop/core/python/tests/test_executor.py

git commit -m "feat(loop): preserve serial evidence when shell unreachable"
```

---

### Task 5: reboot cycle 摘要与 zygote 重启定位辅助元数据

**Files:**
- Modify: `engineering/loop/core/python/loop_core/config.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py`
- Modify: `engineering/loop/connection/profiles/devices/rp5/default.json`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py`
- Test: `engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py`

- [ ] **Step 1: 先写失败测试，锁定 reboot marker 切分摘要**

```python
def test_transport_runtime_context_counts_reboot_cycles():
    client = MagicMock()
    client.capture_recent_entries.return_value = [
        {"text": "Booting Linux", "ts": "2026-06-20T12:00:00+0800", "pending": False},
        {"text": "reboot: Restarting system", "ts": "2026-06-20T12:00:03+0800", "pending": False},
        {"text": "Booting Linux", "ts": "2026-06-20T12:00:05+0800", "pending": False},
    ]
    client.read_until_timeout.return_value = []
    client.fetch_status.return_value = {
        "data": {
            "transcript_path": "/tmp/serial.log",
            "recent_line_count": 3,
            "recent_buffer_limit": 2000,
        }
    }
    transport = Rp5SerialTransport(client)
    transport.set_cycle_markers(["reboot: Restarting system", "U-Boot"])

    context = transport.describe_runtime_context()

    assert context["reboot_cycles"] == 2
    assert context["serial_snippet"][1] == "reboot: Restarting system"
```

- [ ] **Step 2: 运行失败测试，确认 transport 尚未输出 cycle 摘要**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py -v --import-mode=importlib
```

Expected: FAIL，报 `set_cycle_markers` / `reboot_cycles` 不存在。

- [ ] **Step 3: 在 config / transport 中加入 cycle marker 配置与摘要逻辑**

```python
# config.py
@dataclass
class DeviceProfile:
    device_id: str = ""
    transport: str = "serial"
    prompt_markers: list[str] = field(default_factory=list)
    boot_markers: list[str] = field(default_factory=list)
    reboot_markers: list[str] = field(default_factory=list)
    panic_markers: list[str] = field(default_factory=list)
    hang_markers: list[str] = field(default_factory=list)
    line_ending: str = "\n"
    default_capture_timeout: float = 5.0
    default_recent_limit: int = 400
    serial_snippet_limit: int = 40
```

```python
# transport.py
class Rp5SerialTransport(BaseTransport):
    def __init__(self, client) -> None:
        self.client = client
        self._cycle_markers: list[str] = []
        ...

    def set_cycle_markers(self, markers: list[str]) -> None:
        self._cycle_markers = list(markers)

    def describe_runtime_context(self) -> dict[str, object]:
        ...
        entries = self.client.capture_recent_entries(200)
        snippet = [entry["text"] for entry in entries[-40:]]
        reboot_cycles = 1 if snippet else 0
        for line in snippet:
            if any(marker in line for marker in self._cycle_markers):
                reboot_cycles += 1
        self._runtime_context = {
            "transcript_path": data.get("transcript_path", ""),
            "recent_line_count": data.get("recent_line_count", 0),
            "recent_buffer_limit": data.get("recent_buffer_limit", 0),
            "serial_snippet": snippet,
            "reboot_cycles": reboot_cycles,
        }
        return dict(self._runtime_context)
```

- [ ] **Step 4: 在 runner/CLI 初始化 transport 时注入 profile reboot markers**

```python
# cli.py / runner construction
runner = LoopRunner(
    device_id=profile.device_id,
    prompt_markers=profile.prompt_markers,
    transport=transport,
    suite=suite,
    capture_timeout=capture_timeout,
    recent_limit=recent_limit,
    device_profile=device_raw,
)
if hasattr(transport, "set_cycle_markers"):
    transport.set_cycle_markers(profile.reboot_markers)
```

- [ ] **Step 5: 运行测试，确认 reboot cycle 摘要可用于 zygote 重启定位**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py -v --import-mode=importlib
```

Expected: PASS。

- [ ] **Step 6: 提交这一小步**

```bash
git add \
  engineering/loop/core/python/loop_core/config.py \
  engineering/loop/connection/providers/rp5-serial/python/rp5_serial/transport.py \
  engineering/loop/connection/profiles/devices/rp5/default.json \
  engineering/loop/connection/providers/rp5-serial/python/tests/test_transport.py \
  engineering/loop/core/python/loop_core/cli.py

git commit -m "feat(loop): summarize reboot cycles from serial transcript"
```

---

### Task 6: Bash 入口与文档同步 transcript / artifacts / 故障定位说明

**Files:**
- Modify: `engineering/loop/scripts/le.sh`
- Modify: `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_monitor.sh`
- Modify: `engineering/loop/README.md`
- Modify: `engineering/loop/WORKFLOW.md`
- Modify: `engineering/loop/connection/providers/rp5-serial/WORKFLOW.md`
- Test: `engineering/loop/core/python/tests/test_cli.py`

- [ ] **Step 1: 先写失败测试，锁定 CLI 输出 transcript 证据路径**

```python
def test_cli_bundle_contains_transcript_context(tmp_path, monkeypatch):
    suite = tmp_path / "t.yaml"
    suite.write_text("""
suite: t
version: 1
cases:
  - id: shell_check
    command: ""
    assert: {type: prompt_visible}
""", encoding="utf-8")

    fixture = tmp_path / "fixture.jsonl"
    fixture.write_text('{"t": 1.0, "text": "console:/ $"}\n', encoding="utf-8")

    profile = tmp_path / "profile.json"
    profile.write_text('{"device_id":"rp5","prompt_markers":["console:/ $"],"reboot_markers":["reboot: Restarting system"]}', encoding="utf-8")

    rc = main([
        "run",
        "--suite", str(suite),
        "--fixture", str(fixture),
        "--device-profile", str(profile),
        "--case-dirs", str(tmp_path),
        "--artifacts-dir", str(tmp_path / "artifacts"),
    ])

    bundle = json.loads((tmp_path / "artifacts" / "evidence_bundle.json").read_text(encoding="utf-8"))
    assert rc == 0
    assert "serial_context" in bundle
```

- [ ] **Step 2: 运行失败测试，确认 CLI 还未把 transcript context 文档化/贯通**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_cli.py -v --import-mode=importlib
```

Expected: FAIL 或缺少 `serial_context` 字段。

- [ ] **Step 3: 最小修改 bash 入口，保持 transcript / artifacts 贯通**

```bash
# le.sh
harness_init "le"
log_info "启动 LE CLI: $*"
python3 -m loop_core.cli "$@"
rc=$?
log_result "LE 运行结果" "exit_code=$rc"
harness_exit "$rc"
```

```bash
# loop_rp5_serial_monitor.sh
harness_init "loop-rp5-serial-monitor"
log_info "启动串口 monitor: $*"
PYTHON_ROOT="$SCRIPT_DIR/../python"
PYTHONPATH="$PYTHON_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -m rp5_serial.client.monitor "$@"
rc=$?
log_result "monitor 结果" "exit_code=$rc"
harness_exit "$rc"
```

- [ ] **Step 4: 更新文档，明确“串口第一现场”与 EvidenceBundle 新字段**

```md
# README.md 新增要点
- `serial_context.transcript_path`：host 持续落盘的串口 transcript 文件。
- `serial_context.serial_snippet`：最近一段串口关键片段，供 AI/人工快速浏览。
- `serial_context.reboot_cycles`：基于 `reboot_markers` 估算的最近重启周期数。
- `shell_reachable` 失败并不意味着无证据；`serial_recent` 会保底输出 transcript 引用。
```

```md
# WORKFLOW.md 新增要点
1. Host 持续写 serial transcript。
2. LE run 读取 transcript 上下文并写入 EvidenceBundle。
3. shell 不可达时优先分析 `serial_context`；shell 可达时再结合 `init_log/crash_dump`。
```

- [ ] **Step 5: 运行 CLI 测试与全量回归，确认代码与文档一致**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest engineering/loop/core/python/tests/test_cli.py -v --import-mode=importlib

PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python" \
  python3 -m pytest \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  -v --import-mode=importlib
```

Expected: 全部 PASS。

- [ ] **Step 6: 提交这一小步**

```bash
git add \
  engineering/loop/scripts/le.sh \
  engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_monitor.sh \
  engineering/loop/README.md \
  engineering/loop/WORKFLOW.md \
  engineering/loop/connection/providers/rp5-serial/WORKFLOW.md \
  engineering/loop/core/python/tests/test_cli.py

git commit -m "docs(loop): document serial-first restart diagnostics"
```

---

## 自检

- 覆盖 spec：host transcript、真实时间戳、reboot cycle、shell 不可达保底证据、EvidenceBundle 挂载、脚本与文档同步，均已有对应任务。
- 占位检查：无 `TODO/TBD`，每个任务都给出具体文件、测试与命令。
- 类型一致性：统一使用 `serial_context`、`transcript_path`、`serial_snippet`、`reboot_cycles` 这组字段名。
