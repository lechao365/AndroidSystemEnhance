# rp5-serial Host/Client MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `engineering/loop/connection/providers/rp5-serial/` 下建立 Windows Host + WSL2 Client 的最小可用串口接管基础设施，使 WSL2 可以通过 monitor / interactive / automation 三种模式接入树莓派 5 串口，并支持单 writer 控制。

**Architecture:** 采用 `connection/providers/rp5-serial/` 同仓结构，Windows Host 独占物理 COM 并通过最小协议向 WSL2 暴露逻辑会话；WSL2 侧采用 bash 入口 + Python package 核心，复用 harness observability 规范，但 Host 本身仅提供轻量维测。MVP 仅支持 `send_line` 与单 writer、无排队机制，不包含 boot-failure workflow 与 ADB。

**Tech Stack:** Bash、Python 3、pytest、pyserial；WSL2 bash 入口复用 `engineering/harness/lib/harness_bootstrap.sh` 与 `engineering/harness/lib/harness_observability.sh`；Windows Host 先前台运行。

**Spec:** `docs/specs/2026-06-19-loop-engineering-design.md`

---

## File Structure

| 类型 | 路径 | 责任 |
|---|---|---|
| 新增 | `engineering/loop/README.md` | loop engineering 顶层入口说明 |
| 新增 | `engineering/loop/WORKFLOW.md` | loop engineering 总体工作流说明 |
| 新增 | `engineering/loop/connection/README.md` | connection 域说明 |
| 新增 | `engineering/loop/connection/protocol/README.md` | 协议目录说明 |
| 新增 | `engineering/loop/connection/protocol/rp5_serial_protocol.md` | rp5-serial host/client 协议定义 |
| 新增 | `engineering/loop/connection/profiles/README.md` | connection profiles 说明 |
| 新增 | `engineering/loop/connection/profiles/devices/rp5/README.md` | RPi5 provider profile 说明 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/README.md` | provider 顶层说明 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/WORKFLOW.md` | provider 工作流与运行方式 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_status.sh` | WSL2 状态查询入口 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_monitor.sh` | WSL2 monitor 入口 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_interactive.sh` | WSL2 interactive 入口 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_automation.sh` | WSL2 automation 入口 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/__init__.py` | Python package 入口 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/shared/models.py` | session / lease / event / status 数据模型 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/shared/errors.py` | 协议错误码 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/shared/codec.py` | 请求/响应编解码 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py` | Host 服务主入口 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py` | 串口读写主循环与 writer/session 状态 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/logging_utils.py` | Host 轻量日志 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/status.py` | status Python 核心 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/monitor.py` | monitor Python 核心 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/interactive.py` | interactive Python 核心 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py` | automation Python 核心 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/tests/test_codec.py` | codec 单测 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py` | session 状态单测 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/tests/test_lease.py` | writer lease 单测 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py` | monitor 流程测试 |
| 新增 | `engineering/loop/connection/providers/rp5-serial/python/tests/test_interactive_flow.py` | interactive 流程测试 |

---

## Task 1: 建立 loop 与 rp5-serial 目录骨架

**Files:**
- Create: `engineering/loop/README.md`
- Create: `engineering/loop/WORKFLOW.md`
- Create: `engineering/loop/connection/README.md`
- Create: `engineering/loop/connection/protocol/README.md`
- Create: `engineering/loop/connection/protocol/rp5_serial_protocol.md`
- Create: `engineering/loop/connection/profiles/README.md`
- Create: `engineering/loop/connection/profiles/devices/rp5/README.md`
- Create: `engineering/loop/connection/providers/rp5-serial/README.md`
- Create: `engineering/loop/connection/providers/rp5-serial/WORKFLOW.md`

- [ ] **Step 1: 创建目录骨架**

Run:
```bash
mkdir -p engineering/loop/connection/protocol && mkdir -p engineering/loop/connection/profiles/devices/rp5 && mkdir -p engineering/loop/connection/providers/rp5-serial
```

Expected: 目录创建成功。

- [ ] **Step 2: 写 `engineering/loop/README.md`**

内容至少包含：
```md
# Loop Engineering

`engineering/loop/` 只承载 loop engineering 本身，不重构 `engineering/harness/`。

## 当前范围
- `connection/`：连接域
- `workflows/`：业务闭环
- `profiles/`：设备/场景配置

## 首期目标
- `rp5-serial` provider
- `boot-failure-debug-loop` v1（后续计划实现）
```

- [ ] **Step 3: 写 `engineering/loop/connection/providers/rp5-serial/README.md` 与 `WORKFLOW.md`**

内容至少包含：
```md
# rp5-serial Provider

## 目标
- Windows Host 独占物理串口
- WSL2 Client 通过 monitor / interactive / automation 接入
- 单 writer，无排队
- 仅支持 `send_line`

## 运行边界
- Windows Host 先前台运行
- WSL2 bash 入口复用 harness observability
```

- [ ] **Step 4: 写协议与 profile 文档骨架**

`rp5_serial_protocol.md` 至少先写出操作列表：
```md
- `session.open`
- `session.close`
- `session.status`
- `stream.subscribe`
- `writer.acquire`
- `writer.release`
- `input.send_line`
```

- [ ] **Step 5: 提交目录骨架**

Run:
```bash
git add engineering/loop && git commit -m "feat(loop): add rp5-serial provider skeleton"
```

---

## Task 2: 建立 bash 入口与 Python package 骨架

**Files:**
- Create: `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_status.sh`
- Create: `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_monitor.sh`
- Create: `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_interactive.sh`
- Create: `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_automation.sh`
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/__init__.py`

- [ ] **Step 1: 创建 bin 与 python 目录**

Run:
```bash
mkdir -p engineering/loop/connection/providers/rp5-serial/bin && mkdir -p engineering/loop/connection/providers/rp5-serial/python/rp5_serial && mkdir -p engineering/loop/connection/providers/rp5-serial/python/tests
```

Expected: 目录创建成功。

- [ ] **Step 2: 写 4 个 bash 入口骨架**

每个脚本都以以下模板开头：
```bash
#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../../../../harness/lib/harness_bootstrap.sh
source "$SCRIPT_DIR/../../../../../harness/lib/harness_bootstrap.sh"

harness_init "loop-rp5-serial-status"
PYTHON_ROOT="$SCRIPT_DIR/../python"
PYTHONPATH="$PYTHON_ROOT${PYTHONPATH:+:$PYTHONPATH}" python -m rp5_serial.client.status "$@"
harness_exit $?
```

其他脚本仅替换：
- `loop-rp5-serial-monitor`
- `loop-rp5-serial-interactive`
- `loop-rp5-serial-automation`
以及对应 `python -m` 模块路径。

- [ ] **Step 3: 写 `__init__.py`**

内容：
```python
"""rp5_serial provider package."""
```

- [ ] **Step 4: 校验 bash 入口语法**

Run:
```bash
bash -n engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_status.sh engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_monitor.sh engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_interactive.sh engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_automation.sh
```

Expected: 无输出，退出 0。

- [ ] **Step 5: 提交 package 骨架**

Run:
```bash
git add engineering/loop/connection/providers/rp5-serial && git commit -m "feat(loop): add rp5-serial package entrypoints"
```

---

## Task 3: 先写 shared 层测试并实现模型/错误码

**Files:**
- Create: `engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py`
- Create: `engineering/loop/connection/providers/rp5-serial/python/tests/test_lease.py`
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/shared/models.py`
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/shared/errors.py`

- [ ] **Step 1: 先写 `test_session.py`**

```python
from rp5_serial.shared.models import Session


def test_session_to_dict_contains_required_fields():
    session = Session(
        session_id="s-1",
        device_id="rp5",
        mode="monitor",
        writer_owner=None,
        started_at="2026-06-19T10:00:00+0800",
        ended_at=None,
        state="ACTIVE",
    )
    data = session.to_dict()
    assert data["session_id"] == "s-1"
    assert data["device_id"] == "rp5"
    assert data["mode"] == "monitor"
    assert data["state"] == "ACTIVE"
```

- [ ] **Step 2: 先写 `test_lease.py`**

```python
from rp5_serial.shared.models import WriterLease


def test_writer_lease_to_dict_contains_owner_fields():
    lease = WriterLease(
        lease_id="l-1",
        session_id="s-1",
        owner_type="human",
        owner_id="cli-user",
        acquired_at="2026-06-19T10:00:00+0800",
        expires_at="2026-06-19T10:05:00+0800",
        state="HELD",
    )
    data = lease.to_dict()
    assert data["owner_type"] == "human"
    assert data["owner_id"] == "cli-user"
    assert data["state"] == "HELD"
```

- [ ] **Step 3: 运行测试，确认失败**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py engineering/loop/connection/providers/rp5-serial/python/tests/test_lease.py -v
```

Expected: FAIL，提示 `rp5_serial.shared.models` 不存在。

- [ ] **Step 4: 实现 `models.py`**

```python
from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Session:
    session_id: str
    device_id: str
    mode: str
    writer_owner: Optional[str]
    started_at: str
    ended_at: Optional[str]
    state: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WriterLease:
    lease_id: str
    session_id: str
    owner_type: str
    owner_id: str
    acquired_at: str
    expires_at: str
    state: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StreamEvent:
    ts: str
    session_id: str
    seq: int
    direction: str
    source: str
    payload_text: str
    tags: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StatusResponse:
    host_state: str
    serial_state: str
    active_session: Optional[dict]
    active_writer: Optional[dict]
    subscriber_count: int

    def to_dict(self) -> dict:
        return asdict(self)
```

- [ ] **Step 5: 实现 `errors.py`**

```python
OK = "OK"
HOST_NOT_READY = "HOST_NOT_READY"
SERIAL_NOT_AVAILABLE = "SERIAL_NOT_AVAILABLE"
SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
WRITER_BUSY = "WRITER_BUSY"
INVALID_MODE = "INVALID_MODE"
INVALID_REQUEST = "INVALID_REQUEST"
```

- [ ] **Step 6: 重跑 shared 测试**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py engineering/loop/connection/providers/rp5-serial/python/tests/test_lease.py -v
```

Expected: PASS。

- [ ] **Step 7: 提交 shared 模型**

Run:
```bash
git add engineering/loop/connection/providers/rp5-serial/python/rp5_serial/shared engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py engineering/loop/connection/providers/rp5-serial/python/tests/test_lease.py && git commit -m "feat(loop): add rp5-serial shared models"
```

---

## Task 4: 先写 codec 测试并实现最小协议编解码

**Files:**
- Create: `engineering/loop/connection/providers/rp5-serial/python/tests/test_codec.py`
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/shared/codec.py`
- Modify: `engineering/loop/connection/protocol/rp5_serial_protocol.md`

- [ ] **Step 1: 先写 `test_codec.py`**

```python
from rp5_serial.shared.codec import decode_message, encode_message, make_error, make_ok


def test_encode_decode_request_roundtrip():
    payload = {"op": "session.status", "data": {"device_id": "rp5"}}
    encoded = encode_message(payload)
    decoded = decode_message(encoded)
    assert decoded == payload


def test_make_ok_has_expected_shape():
    response = make_ok({"host_state": "READY"})
    assert response["ok"] is True
    assert response["code"] == "OK"
    assert response["data"]["host_state"] == "READY"


def test_make_error_has_expected_shape():
    response = make_error("INVALID_REQUEST", "bad payload")
    assert response["ok"] is False
    assert response["code"] == "INVALID_REQUEST"
    assert response["message"] == "bad payload"
```

- [ ] **Step 2: 运行 codec 测试，确认失败**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_codec.py -v
```

Expected: FAIL，提示 `rp5_serial.shared.codec` 不存在。

- [ ] **Step 3: 实现 `codec.py`**

```python
import json
from rp5_serial.shared.errors import OK


def encode_message(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def decode_message(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8").strip())


def make_ok(data: dict | None = None, message: str = "ok") -> dict:
    return {
        "ok": True,
        "code": OK,
        "message": message,
        "data": data or {},
    }


def make_error(code: str, message: str) -> dict:
    return {
        "ok": False,
        "code": code,
        "message": message,
        "data": {},
    }
```

- [ ] **Step 4: 补全协议文档中的统一响应结构**

写入示例：
```json
{
  "ok": true,
  "code": "OK",
  "message": "ok",
  "data": {}
}
```

- [ ] **Step 5: 重跑 codec 测试**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_codec.py -v
```

Expected: PASS。

- [ ] **Step 6: 提交 codec 与协议文档**

Run:
```bash
git add engineering/loop/connection/protocol/rp5_serial_protocol.md engineering/loop/connection/providers/rp5-serial/python/rp5_serial/shared/codec.py engineering/loop/connection/providers/rp5-serial/python/tests/test_codec.py && git commit -m "feat(loop): add rp5-serial protocol codec"
```

---

## Task 5: 实现 Host 启动骨架与轻量日志

**Files:**
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/logging_utils.py`
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py`

- [ ] **Step 1: 写 Host logger 工具**

```python
import logging
from pathlib import Path


def build_logger(name: str, log_dir: str) -> logging.Logger:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    if logger.handlers:
        return logger
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    file_handler = logging.FileHandler(Path(log_dir) / f"{name}.log", encoding="utf-8")
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)
    return logger
```

- [ ] **Step 2: 先写 `server.py` 的 CLI 骨架**

```python
import argparse
from rp5_serial.host.logging_utils import build_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="rp5 serial host")
    parser.add_argument("--config", required=False, help="host config path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logger = build_logger("rp5_serial_host", "output/host-log")
    logger.info("host starting config=%s", args.config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: 运行 Host help**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m rp5_serial.host.server --help
```

Expected: 输出参数说明，退出 0。

- [ ] **Step 4: 前台启动 Host 骨架**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m rp5_serial.host.server
```

Expected: 输出 `host starting` 日志，退出 0。

- [ ] **Step 5: 提交 Host 骨架**

Run:
```bash
git add engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host && git commit -m "feat(loop): add rp5-serial host skeleton"
```

---

## Task 6: 先写 session/lease 测试并实现 Host 内存态运行时

**Files:**
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/tests/test_lease.py`

- [ ] **Step 1: 扩充 `test_session.py`**

```python
from rp5_serial.host.serial_runtime import RuntimeState


def test_open_session_creates_active_session():
    state = RuntimeState(device_id="rp5")
    session = state.open_session(mode="monitor", owner_id="observer")
    assert session.device_id == "rp5"
    assert state.active_session is not None
    assert state.active_session.mode == "monitor"
```

- [ ] **Step 2: 扩充 `test_lease.py`**

```python
from rp5_serial.host.serial_runtime import RuntimeState


def test_acquire_writer_when_free_succeeds():
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="cli-user")
    lease = state.acquire_writer(owner_type="human", owner_id="cli-user")
    assert lease.owner_id == "cli-user"


def test_acquire_writer_when_busy_fails():
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="cli-user")
    state.acquire_writer(owner_type="human", owner_id="cli-user")
    assert state.acquire_writer(owner_type="workflow", owner_id="auto-1") is None
```

- [ ] **Step 3: 运行测试，确认失败**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py engineering/loop/connection/providers/rp5-serial/python/tests/test_lease.py -v
```

Expected: FAIL，提示 `RuntimeState` 不存在。

- [ ] **Step 4: 实现 `serial_runtime.py` 的内存态运行时**

```python
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from rp5_serial.shared.models import Session, StatusResponse, WriterLease

TZ = timezone(timedelta(hours=8))


def now_iso() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass
class RuntimeState:
    device_id: str
    active_session: Session | None = None
    active_writer: WriterLease | None = None
    subscriber_count: int = 0

    def open_session(self, mode: str, owner_id: str) -> Session:
        session = Session(
            session_id=f"s-{uuid4().hex[:8]}",
            device_id=self.device_id,
            mode=mode,
            writer_owner=None,
            started_at=now_iso(),
            ended_at=None,
            state="ACTIVE",
        )
        self.active_session = session
        return session

    def acquire_writer(self, owner_type: str, owner_id: str) -> WriterLease | None:
        if self.active_writer is not None:
            return None
        if self.active_session is None:
            self.open_session(mode="interactive", owner_id=owner_id)
        lease = WriterLease(
            lease_id=f"l-{uuid4().hex[:8]}",
            session_id=self.active_session.session_id,
            owner_type=owner_type,
            owner_id=owner_id,
            acquired_at=now_iso(),
            expires_at=now_iso(),
            state="HELD",
        )
        self.active_writer = lease
        self.active_session.writer_owner = owner_id
        return lease

    def release_writer(self) -> None:
        self.active_writer = None
        if self.active_session:
            self.active_session.writer_owner = None

    def status(self) -> StatusResponse:
        return StatusResponse(
            host_state="READY",
            serial_state="UNKNOWN",
            active_session=self.active_session.to_dict() if self.active_session else None,
            active_writer=self.active_writer.to_dict() if self.active_writer else None,
            subscriber_count=self.subscriber_count,
        )
```

- [ ] **Step 5: 在 `server.py` 中接入 `RuntimeState`**

至少在 `main()` 中初始化：
```python
from rp5_serial.host.serial_runtime import RuntimeState

state = RuntimeState(device_id="rp5")
logger.info("runtime ready status=%s", state.status().to_dict())
```

- [ ] **Step 6: 重跑 session/lease 测试**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py engineering/loop/connection/providers/rp5-serial/python/tests/test_lease.py -v
```

Expected: PASS。

- [ ] **Step 7: 提交运行时状态管理**

Run:
```bash
git add engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py engineering/loop/connection/providers/rp5-serial/python/tests/test_session.py engineering/loop/connection/providers/rp5-serial/python/tests/test_lease.py && git commit -m "feat(loop): add rp5-serial runtime state"
```

---

## Task 7: 接入真实串口读写与最小 send_line

**Files:**
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/serial_runtime.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host/server.py`

- [ ] **Step 1: 在 `serial_runtime.py` 中增加 pyserial 依赖与串口打开逻辑**

```python
import serial


def open_serial(port: str, baudrate: int):
    return serial.Serial(port=port, baudrate=baudrate, timeout=0.2)
```

- [ ] **Step 2: 增加 read loop 与最近输出缓冲**

最小实现要求：
- 持续读取串口字节
- 按 `\n` 切分为文本行
- 保存最近若干行到内存缓冲
- 后续 monitor / interactive 可读这段缓冲

- [ ] **Step 3: 增加 `send_line()`**

约束：
- 只允许 active writer 调用
- 自动追加 `\n`
- 不支持原始字节发送

示例实现片段：
```python
def send_line(self, text: str) -> None:
    if self.active_writer is None:
        raise RuntimeError("writer not acquired")
    self.serial.write((text + "\n").encode("utf-8"))
```

- [ ] **Step 4: 前台验证 Host 串口启动**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m rp5_serial.host.server --config <host-config>
```

Expected: 串口存在时显示 open success；串口不存在时显示清晰错误。

- [ ] **Step 5: 提交串口 I/O**

Run:
```bash
git add engineering/loop/connection/providers/rp5-serial/python/rp5_serial/host && git commit -m "feat(loop): add rp5-serial serial io"
```

---

## Task 8: 先写 monitor 测试并实现 status/monitor client

**Files:**
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/status.py`
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/monitor.py`
- Create: `engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_status.sh`
- Modify: `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_monitor.sh`

- [ ] **Step 1: 先写 `test_monitor_flow.py`**

```python
from rp5_serial.host.serial_runtime import RuntimeState


def test_status_reports_no_writer_initially():
    state = RuntimeState(device_id="rp5")
    status = state.status().to_dict()
    assert status["active_writer"] is None
```

- [ ] **Step 2: 实现 `status.py`**

最小行为：
```python
import json


def render_status(status: dict) -> str:
    return json.dumps(status, ensure_ascii=False, indent=2)
```

CLI 至少支持：
- 连接 host
- 获取 `session.status`
- 打印结果

- [ ] **Step 3: 实现 `monitor.py`**

最小行为：
- 连接 host
- 发送 `stream.subscribe`
- 打印收到的文本行
- 不申请 writer

- [ ] **Step 4: 运行 monitor 测试**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py -v
```

Expected: PASS。

- [ ] **Step 5: 手工验证 status/monitor bash 入口**

Run:
```bash
bash engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_status.sh --help
bash engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_monitor.sh --help
```

Expected: 输出参数说明或状态提示，退出 0。

- [ ] **Step 6: 提交 status/monitor**

Run:
```bash
git add engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/status.py engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/monitor.py engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_status.sh engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_monitor.sh engineering/loop/connection/providers/rp5-serial/python/tests/test_monitor_flow.py && git commit -m "feat(loop): add rp5-serial status and monitor clients"
```

---

## Task 9: 先写 interactive 测试并实现 interactive client

**Files:**
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/interactive.py`
- Create: `engineering/loop/connection/providers/rp5-serial/python/tests/test_interactive_flow.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_interactive.sh`

- [ ] **Step 1: 先写 `test_interactive_flow.py`**

```python
from rp5_serial.host.serial_runtime import RuntimeState


def test_interactive_acquire_writer_success():
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="cli-user")
    lease = state.acquire_writer(owner_type="human", owner_id="cli-user")
    assert lease is not None


def test_interactive_acquire_writer_busy_returns_none():
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="cli-user")
    state.acquire_writer(owner_type="human", owner_id="cli-user")
    assert state.acquire_writer(owner_type="human", owner_id="cli-user-2") is None
```

- [ ] **Step 2: 运行 interactive 测试，确认最小状态逻辑可复用**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m pytest engineering/loop/connection/providers/rp5-serial/python/tests/test_interactive_flow.py -v
```

Expected: PASS。

- [ ] **Step 3: 实现 `interactive.py`**

最小行为：
- attach 时请求 writer
- 成功后读取 stdin 的每一行
- 对每一行调用 `send_line()`
- 订阅并打印输出
- 退出时 release writer

- [ ] **Step 4: 完成 interactive bash 包装层**

错误时必须输出清晰信息：
- writer busy
- host not ready
- serial not available

- [ ] **Step 5: 手工验证 interactive 帮助命令**

Run:
```bash
bash engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_interactive.sh --help
```

Expected: 输出参数说明，退出 0。

- [ ] **Step 6: 提交 interactive**

Run:
```bash
git add engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/interactive.py engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_interactive.sh engineering/loop/connection/providers/rp5-serial/python/tests/test_interactive_flow.py && git commit -m "feat(loop): add rp5-serial interactive client"
```

---

## Task 10: 实现 automation client 最小 API

**Files:**
- Create: `engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py`
- Modify: `engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_automation.sh`

- [ ] **Step 1: 实现 `automation.py`**

最小 API：
```python
class AutomationClient:
    def send_line(self, text: str) -> None: ...
    def read_until_timeout(self, timeout_sec: float) -> list[str]: ...
    def capture_recent_lines(self, limit: int) -> list[str]: ...
    def release(self) -> None: ...
```

- [ ] **Step 2: attach 时申请 writer**

要求：
- busy 直接失败
- 无排队
- release 后 writer 释放

- [ ] **Step 3: 完成 automation bash 包装层**

bash 入口必须：
- `harness_init "loop-rp5-serial-automation"`
- 作为后续 workflow 的壳层保留标准退出码

- [ ] **Step 4: 手工验证 automation 帮助命令**

Run:
```bash
bash engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_automation.sh --help
```

Expected: 输出参数说明，退出 0。

- [ ] **Step 5: 提交 automation**

Run:
```bash
git add engineering/loop/connection/providers/rp5-serial/python/rp5_serial/client/automation.py engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_automation.sh && git commit -m "feat(loop): add rp5-serial automation client"
```

---

## Task 11: 真机联调与文档回填

**Files:**
- Modify: `engineering/loop/connection/providers/rp5-serial/README.md`
- Modify: `engineering/loop/connection/providers/rp5-serial/WORKFLOW.md`
- Modify: `engineering/loop/connection/profiles/devices/rp5/README.md`

- [ ] **Step 1: 验证正常启动观察场景**

检查项：
- Host 前台启动成功
- WSL2 `status` 可看到 host 与 serial ready
- `monitor` 可看到 boot log

- [ ] **Step 2: 验证人工交互场景**

检查项：
- `interactive` 获取 writer 成功
- 能输入 shell 命令
- 能看到返回输出
- 退出后 writer 释放

- [ ] **Step 3: 验证 writer 冲突场景**

检查项：
- interactive 占用 writer 时，第二个 interactive 或 automation 请求失败
- monitor 仍可观察

- [ ] **Step 4: 回填文档中的运行说明与限制**

必须写明：
- Windows 前台启动命令
- WSL2 status / monitor / interactive / automation 用法
- MVP 限制：仅 `send_line`、单 writer、无排队、无 boot-failure workflow

- [ ] **Step 5: 提交联调文档更新**

Run:
```bash
git add engineering/loop/connection/providers/rp5-serial/README.md engineering/loop/connection/providers/rp5-serial/WORKFLOW.md engineering/loop/connection/profiles/devices/rp5/README.md && git commit -m "docs(loop): document rp5-serial mvp runtime"
```

---

## Task 12: 最终回归检查

**Files:**
- 本计划涉及全部文件

- [ ] **Step 1: 运行 Python 测试集**

Run:
```bash
PYTHONPATH=engineering/loop/connection/providers/rp5-serial/python python -m pytest engineering/loop/connection/providers/rp5-serial/python/tests -v
```

Expected: PASS。

- [ ] **Step 2: 校验 bash 入口语法**

Run:
```bash
bash -n engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_status.sh engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_monitor.sh engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_interactive.sh engineering/loop/connection/providers/rp5-serial/bin/loop_rp5_serial_automation.sh
```

Expected: 无输出，退出 0。

- [ ] **Step 3: 检查日志命名空间**

检查点：
- loop bash 入口日志落到 `engineering/output/log/loop-rp5-serial-*`
- Host 本地日志不强行塞进 harness log

- [ ] **Step 4: 审核 README 一致性**

检查：
- `engineering/loop/README.md`
- `engineering/loop/connection/README.md`
- `engineering/loop/connection/providers/rp5-serial/README.md`
- `engineering/loop/connection/protocol/README.md`

- [ ] **Step 5: 提交最终回归修正**

Run:
```bash
git add engineering/loop && git commit -m "test(loop): validate rp5-serial host client mvp"
```

---

## 计划备注

### MVP 明确限制
- 只支持 `send_line`
- 单 writer，无排队
- Host 前台运行，不含服务托管
- 不含 boot-failure workflow
- 不含 ADB
- 不含激进恢复动作

### 后续自然衔接
完成本计划后，下一份计划应直接接入：
- `boot-failure-debug-loop` v1
- 基于 automation client 的状态机、规则、采样与报告
