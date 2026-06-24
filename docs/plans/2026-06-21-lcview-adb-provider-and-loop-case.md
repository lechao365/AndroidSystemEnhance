# lcview ADB Provider + Loop Case Implementation Plan

> **2026-06-24 更新**：设备 IP 发现已从"固定 IP"切换为"串口动态发现"，见 `engineering/loop/scripts/rp5_serial_helper.py` 和 `engineering/loop/WORKFLOW.md` 的「传输层依赖链」章节。本文档中残留的 `192.168.1.55` 仅为历史决策记录。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 Loop Engineering 增加可复用 `adb` provider、通用 `transport=adb` 运行通道、`lcview` feature suite，以及“serial bootstrap → adb feature run → serial fallback evidence”的单入口 workflow。

**Architecture:** 保持 `loop_core` 单 transport 语义不变：bootstrap 继续复用 `rp5-serial` 与 `system.network_adbd`，feature run 改由新 `adb` provider 执行。`adb` provider 负责连接、shell、root、pull、logcat、reboot wait、runtime context；`lcview` 专用逻辑留在 suite / collector / workflow 层，并通过结构化 failure code 把 `BOOTSTRAP_FAIL`、`ADB_EXEC_FAIL`、`LCVIEW_*` 失败分开。

**Tech Stack:** Python 3.10+（dataclass / subprocess / pathlib / pytest）、YAML suite DSL、现有 `loop_core` / `rp5-serial` provider、host `adb` CLI、harness bash workflow。

**Spec:** `docs/specs/2026-06-21-lcview-adb-provider-and-loop-case-design.md`

---

## File Structure

- Modify: `engineering/loop/core/python/loop_core/cli.py`
  - 新增 provider 选择入口与 `adb` live 参数；把串口 live mode 改成按 `DeviceProfile.transport` 分派。
- Create: `engineering/loop/core/python/loop_core/provider_loader.py`
  - 统一构建 live transport，隔离 `serial` / `adb` provider 导入与参数装配。
- Modify: `engineering/loop/core/python/loop_core/config.py`
  - 扩充 `DeviceProfile.transport` 说明，补 `adb` 语义测试入口。
- Modify: `engineering/loop/core/python/loop_core/case_loader.py`
  - 为 suite 增加 `final_collectors` / collector `required` / `failure_code` 等静态校验。
- Modify: `engineering/loop/core/python/loop_core/collector.py`
  - 支持 `mode: runtime_context|adb_pull|adb_logcat`，供 `lcview` 采集结构化证据。
- Modify: `engineering/loop/core/python/loop_core/executor.py`
  - 执行 `final_collectors`，并把 required collector 失败折算为 suite fail。
- Modify: `engineering/loop/core/python/loop_core/models.py`
  - 给 case / collector / bundle 增加 `failure_code`、`runtime_context` 等字段。
- Modify: `engineering/loop/core/python/loop_core/runner.py`
  - 透传 `artifacts_dir`，统一注入 transport runtime context。
- Modify: `engineering/loop/core/python/loop_core/evidence.py`
  - 在 `summary.txt` 渲染 `runtime_context` / `failure_code` / collector artifact 路径。
- Create: `engineering/loop/connection/providers/adb/README.md`
  - 说明 provider 边界、运行方式、限制与调试入口。
- Create: `engineering/loop/connection/providers/adb/python/loop_adb/__init__.py`
  - `loop_adb` 包导出。
- Create: `engineering/loop/connection/providers/adb/python/loop_adb/client.py`
  - `adb` 子进程封装：connect / disconnect / wait / shell / root / pull / logcat。
- Create: `engineering/loop/connection/providers/adb/python/loop_adb/transport.py`
  - `BaseTransport` 适配层：命令式 shell capture、reboot wait、runtime context。
- Create: `engineering/loop/connection/providers/adb/python/tests/test_client.py`
  - 锁定 adb 命令拼装、超时、错误映射、root/shell/pull/logcat 语义。
- Create: `engineering/loop/connection/providers/adb/python/tests/test_transport.py`
  - 锁定 transport acquire/release、send/capture、reboot、runtime context。
- Create: `engineering/loop/connection/profiles/devices/rp5/adb.json`
  - RPi5 的 `transport=adb` profile。
- Modify: `engineering/loop/connection/profiles/devices/rp5/README.md`
  - 从“仅串口”更新为“serial bootstrap + adb feature”双 profile 说明。
- Modify: `engineering/harness/config/harness-paths.conf`
  - 把 adb provider Python 根加入 `PYTHON_PATH_ROOTS`。
- Create: `engineering/loop/cases/system/adb-shell-success.yaml`
  - 最小 adb smoke suite，先证明 provider 可执行 shell / root / reboot wait。
- Create: `engineering/loop/cases/features/lcview/common.yaml`
  - `lcview` 公共前提检查、公共 collector、`final_collectors`。
- Create: `engineering/loop/cases/features/lcview/end_to_end.yaml`
  - `lcview` 主链路验收：cleanup → trigger → jsonl → evidence。
- Create: `engineering/harness/workflows/lcview-adb-run/WORKFLOW.md`
  - 单入口流程契约：bootstrap / adb run / fallback / 汇总。
- Create: `engineering/harness/workflows/lcview-adb-run/run_lcview_adb_suite.sh`
  - 观测增强后的 workflow 脚本。
- Modify: `engineering/loop/README.md`
  - 补 `transport=adb`、adb suite、lcview feature 入口说明。
- Modify: `engineering/harness/README.md`
  - README 同步：新增 workflow 导航。
- Modify: `engineering/harness/workflows/README.md`
  - 工作流清单新增 `lcview-adb-run`。
- Modify: `engineering/loop/core/python/tests/test_cli.py`
  - CLI 透传 adb 参数与 provider 选择测试。
- Modify: `engineering/loop/core/python/tests/test_config.py`
  - `DeviceProfile.transport=adb` 基本测试。
- Create: `engineering/loop/core/python/tests/test_provider_loader.py`
  - provider 选择与缺参错误测试。
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`
  - `final_collectors` / required collector / failure_code 校验。
- Modify: `engineering/loop/core/python/tests/test_collector.py`
  - `adb_pull` / `adb_logcat` / `runtime_context` collector 测试。
- Modify: `engineering/loop/core/python/tests/test_executor.py`
  - required final collector 失败、failure_code 归并测试。
- Modify: `engineering/loop/core/python/tests/test_runner.py`
  - `artifacts_dir` / runtime context 透传测试。
- Modify: `engineering/loop/core/python/tests/test_evidence.py`
  - `summary.txt` 渲染 runtime context / failure code / artifact paths。

---

### Task 1: 引入 live provider loader 与 `transport=adb` CLI 入口

**Files:**
- Create: `engineering/loop/core/python/loop_core/provider_loader.py`
- Modify: `engineering/loop/core/python/loop_core/cli.py`
- Modify: `engineering/loop/core/python/loop_core/config.py`
- Modify: `engineering/harness/config/harness-paths.conf`
- Modify: `engineering/loop/core/python/tests/test_cli.py`
- Modify: `engineering/loop/core/python/tests/test_config.py`
- Create: `engineering/loop/core/python/tests/test_provider_loader.py`

- [ ] **Step 1: 先写失败测试，锁定 `adb` provider 选择语义**

创建 `engineering/loop/core/python/tests/test_provider_loader.py`：

```python
import types

import pytest

from loop_core.config import DeviceProfile
from loop_core.provider_loader import build_live_transport


class _Args(types.SimpleNamespace):
    host = "127.0.0.1"
    port = 9700
    adb_endpoint = "192.168.1.55:5555"
    adb_serial = "192.168.1.55:5555"
    adb_root_mode = "auto"
    adb_connect_timeout = 15.0
    adb_command_timeout = 10.0


def test_build_live_transport_rejects_unknown_transport():
    profile = DeviceProfile(device_id="rp5", transport="bluetooth")
    with pytest.raises(ValueError, match="unsupported transport"):
        build_live_transport(profile, _Args())


def test_build_live_transport_requires_adb_endpoint():
    profile = DeviceProfile(device_id="rp5", transport="adb")
    args = _Args(adb_endpoint="")
    with pytest.raises(ValueError, match="adb endpoint is required"):
        build_live_transport(profile, args)
```

在 `engineering/loop/core/python/tests/test_cli.py` 末尾追加：

```python
def test_cli_live_mode_uses_provider_loader(tmp_path, monkeypatch):
    import loop_core.cli as cli

    captured = {}

    class FakeTransport:
        def acquire_writer(self):
            return True

        def release(self):
            pass

    class FakeRunner:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            from loop_core.models import EvidenceBundle
            return EvidenceBundle(
                bundle_id="eb-test",
                device_id="rp5",
                suite="test",
                timestamp="2026-06-21T00:00:00+08:00",
                summary={"total": 0, "passed": 0, "failed": 0, "skipped": 0, "overall": "PASS"},
                cases=[],
                evidence={},
            )

        def build_failure_bundle(self, reason):
            raise AssertionError(reason)

    def fake_build_live_transport(profile, args):
        captured["transport_name"] = profile.transport
        captured["adb_endpoint"] = args.adb_endpoint
        return FakeTransport()

    monkeypatch.setattr(cli, "build_live_transport", fake_build_live_transport)
    monkeypatch.setattr(cli, "LoopRunner", FakeRunner)

    suite_path = tmp_path / "t.yaml"
    suite_path.write_text("suite: test\nversion: 1\ncases: []\n", encoding="utf-8")
    profile_path = tmp_path / "p.json"
    profile_path.write_text('{"device_id":"rp5","transport":"adb"}', encoding="utf-8")

    rc = cli.main([
        "run",
        "--suite", str(suite_path),
        "--device-profile", str(profile_path),
        "--artifacts-dir", str(tmp_path / "out"),
        "--adb-endpoint", "192.168.1.55:5555",
    ])

    assert rc == 0
    assert captured["transport_name"] == "adb"
    assert captured["adb_endpoint"] == "192.168.1.55:5555"
```

在 `engineering/loop/core/python/tests/test_config.py` 末尾追加：

```python
from loop_core.config import DeviceProfile


def test_device_profile_allows_adb_transport():
    profile = DeviceProfile(device_id="rp5", transport="adb")
    assert profile.transport == "adb"
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest \
  engineering/loop/core/python/tests/test_provider_loader.py \
  engineering/loop/core/python/tests/test_cli.py \
  engineering/loop/core/python/tests/test_config.py -v
```

Expected: FAIL，至少出现 `loop_core.provider_loader` 不存在、CLI 没有 `--adb-endpoint`、live mode 仍然硬编码 `rp5_serial`。

- [ ] **Step 3: 实现 provider loader 与 CLI 参数分派**

创建 `engineering/loop/core/python/loop_core/provider_loader.py`：

```python
"""live transport provider loader。"""
from __future__ import annotations

from loop_core.config import DeviceProfile


def build_live_transport(profile: DeviceProfile, args):
    if profile.transport == "serial":
        from rp5_serial.client.automation import AutomationClient
        from rp5_serial.transport import Rp5SerialTransport

        client = AutomationClient(args.host, args.port)
        client.connect()
        return Rp5SerialTransport(client)

    if profile.transport == "adb":
        if not args.adb_endpoint:
            raise ValueError("adb endpoint is required for transport=adb")
        from loop_adb.transport import AdbTransport

        return AdbTransport(
            endpoint=args.adb_endpoint,
            device_serial=args.adb_serial or args.adb_endpoint,
            root_mode=args.adb_root_mode,
            connect_timeout_sec=args.adb_connect_timeout,
            command_timeout_sec=args.adb_command_timeout,
        )

    raise ValueError(f"unsupported transport: {profile.transport}")
```

修改 `engineering/loop/core/python/loop_core/cli.py`：

```python
from loop_core.provider_loader import build_live_transport
```

在 `run` 子命令参数区追加：

```python
run_parser.add_argument("--adb-endpoint", default="", help="adb endpoint，例如 192.168.1.55:5555")
run_parser.add_argument("--adb-serial", default="", help="adb device serial；缺省回落到 endpoint")
run_parser.add_argument(
    "--adb-root-mode",
    choices=["auto", "adb_root", "su0", "none"],
    default="auto",
    help="adb 提权策略",
)
run_parser.add_argument("--adb-connect-timeout", type=float, default=15.0, help="adb connect / wait 超时")
run_parser.add_argument("--adb-command-timeout", type=float, default=10.0, help="adb 单命令默认超时")
```

把原 live mode 的 `rp5_serial` 硬编码块替换为：

```python
    if args.fixture:
        transport = FixtureTransport.from_jsonl(args.fixture)
    else:
        try:
            transport = build_live_transport(profile, args)
        except ImportError:
            print("ERROR: live mode provider 缺失，请检查 PYTHONPATH", file=sys.stderr)
            return 1
        except (OSError, ValueError) as exc:
            print(f"ERROR: live mode 初始化失败: {exc}", file=sys.stderr)
            return 1
```

修改 `engineering/harness/config/harness-paths.conf`：

```bash
PYTHON_PATH_ROOTS="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python"
```

- [ ] **Step 4: 回归测试，确认 provider loader 与 CLI 通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest \
  engineering/loop/core/python/tests/test_provider_loader.py \
  engineering/loop/core/python/tests/test_cli.py \
  engineering/loop/core/python/tests/test_config.py -v
```

Expected: PASS

- [ ] **Step 5: 提交本任务改动**

```bash
git add engineering/loop/core/python/loop_core/provider_loader.py \
        engineering/loop/core/python/loop_core/cli.py \
        engineering/loop/core/python/loop_core/config.py \
        engineering/harness/config/harness-paths.conf \
        engineering/loop/core/python/tests/test_provider_loader.py \
        engineering/loop/core/python/tests/test_cli.py \
        engineering/loop/core/python/tests/test_config.py
git commit -m "feat(loop-core): add adb live provider selection"
```

---

### Task 2: 落地 `loop_adb` client 与最小 transport shell 执行面

**Files:**
- Create: `engineering/loop/connection/providers/adb/README.md`
- Create: `engineering/loop/connection/providers/adb/python/loop_adb/__init__.py`
- Create: `engineering/loop/connection/providers/adb/python/loop_adb/client.py`
- Create: `engineering/loop/connection/providers/adb/python/loop_adb/transport.py`
- Create: `engineering/loop/connection/providers/adb/python/tests/test_client.py`
- Create: `engineering/loop/connection/providers/adb/python/tests/test_transport.py`

- [ ] **Step 1: 先写失败测试，锁定 adb shell / exit code / connect 语义**

创建 `engineering/loop/connection/providers/adb/python/tests/test_client.py`：

```python
from pathlib import Path

import pytest

from loop_adb.client import AdbClient, AdbCommandError, AdbCommandResult


class FakeRunner:
    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    def __call__(self, argv, timeout_sec):
        self.calls.append((argv, timeout_sec))
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_connect_builds_expected_command():
    runner = FakeRunner([
        AdbCommandResult(argv=["adb"], exit_code=0, stdout="connected to 192.168.1.55:5555\n", stderr=""),
    ])
    client = AdbClient(endpoint="192.168.1.55:5555", device_serial="192.168.1.55:5555", runner=runner)
    result = client.connect(timeout_sec=15.0)
    assert result.exit_code == 0
    assert runner.calls[0][0] == ["adb", "connect", "192.168.1.55:5555"]


def test_shell_wraps_exit_code_marker():
    runner = FakeRunner([
        AdbCommandResult(
            argv=["adb"],
            exit_code=0,
            stdout="hello\n__LE_EXIT_CODE__=0\n",
            stderr="",
        ),
    ])
    client = AdbClient(endpoint="192.168.1.55:5555", device_serial="192.168.1.55:5555", runner=runner)
    result = client.shell("echo hello", timeout_sec=5.0)
    assert result.output_lines == ["hello"]
    assert result.command_exit_code == 0


def test_shell_timeout_raises_adb_command_error():
    runner = FakeRunner([TimeoutError("timed out")])
    client = AdbClient(endpoint="192.168.1.55:5555", device_serial="192.168.1.55:5555", runner=runner)
    with pytest.raises(AdbCommandError, match="timed out"):
        client.shell("getprop", timeout_sec=1.0)
```

创建 `engineering/loop/connection/providers/adb/python/tests/test_transport.py`：

```python
from loop_adb.transport import AdbTransport


class FakeClient:
    def __init__(self):
        self.sent_commands = []

    def connect(self, timeout_sec):
        return None

    def disconnect(self, timeout_sec=5.0):
        return None

    def shell(self, command, timeout_sec, as_root=False):
        self.sent_commands.append((command, timeout_sec, as_root))
        return type("ShellResult", (), {
            "output_lines": ["ok"],
            "command_exit_code": 0,
        })()


def test_transport_capture_since_runs_pending_shell_command():
    transport = AdbTransport(endpoint="192.168.1.55:5555", client=FakeClient())
    assert transport.acquire_writer() is True
    boundary = transport.mark_output_boundary()
    transport.send_line("echo ok")
    capture = transport.capture_since(boundary, timeout_sec=5.0, recent_limit=50)
    assert [line.text for line in capture.lines] == ["ok"]
    assert capture.exit_code == 0
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/adb/python" \
python3 -m pytest \
  engineering/loop/connection/providers/adb/python/tests/test_client.py \
  engineering/loop/connection/providers/adb/python/tests/test_transport.py -v
```

Expected: FAIL，提示 `loop_adb` 包不存在。

- [ ] **Step 3: 实现 `AdbClient` 与最小 `AdbTransport`**

创建 `engineering/loop/connection/providers/adb/python/loop_adb/client.py`：

```python
"""adb 子进程封装。"""
from __future__ import annotations

from dataclasses import dataclass, field
import subprocess


class AdbCommandError(RuntimeError):
    pass


@dataclass
class AdbCommandResult:
    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class AdbShellResult:
    argv: list[str]
    output_lines: list[str]
    command_exit_code: int
    raw_stdout: str
    stderr: str


class AdbClient:
    def __init__(self, endpoint: str, device_serial: str, runner=None) -> None:
        self.endpoint = endpoint
        self.device_serial = device_serial
        self._runner = runner or self._run_subprocess

    def _run_subprocess(self, argv: list[str], timeout_sec: float) -> AdbCommandResult:
        try:
            completed = subprocess.run(argv, capture_output=True, text=True, timeout=timeout_sec, check=False)
        except subprocess.TimeoutExpired as exc:
            raise AdbCommandError(f"adb command timed out: {' '.join(argv)}") from exc
        except OSError as exc:
            raise AdbCommandError(f"failed to execute adb: {exc}") from exc
        return AdbCommandResult(argv=argv, exit_code=completed.returncode, stdout=completed.stdout or "", stderr=completed.stderr or "")

    def connect(self, timeout_sec: float) -> AdbCommandResult:
        return self._runner(["adb", "connect", self.endpoint], timeout_sec)

    def disconnect(self, timeout_sec: float = 5.0) -> AdbCommandResult:
        return self._runner(["adb", "disconnect", self.endpoint], timeout_sec)

    def shell(self, command: str, timeout_sec: float, as_root: bool = False) -> AdbShellResult:
        wrapped = command if not as_root else f"su 0 sh -c {command!r}"
        shell_cmd = f"{wrapped}; rc=$?; printf '\n__LE_EXIT_CODE__=%s\n' \"$rc\""
        result = self._runner(["adb", "-s", self.device_serial, "shell", shell_cmd], timeout_sec)
        lines = result.stdout.splitlines()
        if not lines or not lines[-1].startswith("__LE_EXIT_CODE__="):
            raise AdbCommandError("adb shell result missing exit code marker")
        exit_code = int(lines[-1].split("=", 1)[1])
        return AdbShellResult(
            argv=result.argv,
            output_lines=lines[:-1],
            command_exit_code=exit_code,
            raw_stdout=result.stdout,
            stderr=result.stderr,
        )
```

创建 `engineering/loop/connection/providers/adb/python/loop_adb/transport.py`：

```python
"""adb provider transport 适配层。"""
from __future__ import annotations

from loop_core.models import ObservedLine
from loop_core.transport import BaseTransport, CommandCapture
from loop_adb.client import AdbClient


class AdbTransport(BaseTransport):
    def __init__(
        self,
        endpoint: str,
        device_serial: str | None = None,
        root_mode: str = "auto",
        connect_timeout_sec: float = 15.0,
        command_timeout_sec: float = 10.0,
        client: AdbClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.device_serial = device_serial or endpoint
        self.root_mode = root_mode
        self.connect_timeout_sec = connect_timeout_sec
        self.command_timeout_sec = command_timeout_sec
        self.client = client or AdbClient(endpoint, self.device_serial)
        self._writer_held = False
        self._pending_command = ""
        self._boundary = 0
        self.client.connect(timeout_sec=self.connect_timeout_sec)

    def acquire_writer(self) -> bool:
        if self._writer_held:
            return False
        self._writer_held = True
        return True

    def release(self) -> None:
        self._writer_held = False

    def send_line(self, text: str) -> None:
        if not self._writer_held:
            raise RuntimeError("writer not acquired")
        self._pending_command = text

    def mark_output_boundary(self) -> int:
        self._boundary += 1
        return self._boundary

    def capture_since(self, boundary, timeout_sec, recent_limit, prompt_markers=None):
        del boundary, recent_limit, prompt_markers
        result = self.client.shell(self._pending_command, timeout_sec=timeout_sec)
        lines = [ObservedLine(t=float(index), text=line) for index, line in enumerate(result.output_lines, start=1)]
        self._pending_command = ""
        return CommandCapture(lines=lines, prompt_visible=False, exit_code=result.command_exit_code)

    def capture_window(self, timeout_sec, recent_limit):
        del timeout_sec, recent_limit
        return []

    def wait_for_pattern(self, patterns, timeout_sec, recent_limit):
        del patterns, timeout_sec, recent_limit
        return None
```

创建 `engineering/loop/connection/providers/adb/python/loop_adb/__init__.py`：

```python
from loop_adb.client import AdbClient, AdbCommandError
from loop_adb.transport import AdbTransport

__all__ = ["AdbClient", "AdbCommandError", "AdbTransport"]
```

在 `engineering/loop/connection/providers/adb/README.md` 写入最小说明：

```md
# adb Provider

为 Loop Engineering 提供 `transport=adb` live transport。

## 范围
- adb connect / disconnect
- adb shell 命令执行
- root / pull / logcat / reboot wait（后续任务补齐）

## Python 包
- `python/loop_adb/client.py`
- `python/loop_adb/transport.py`
```

- [ ] **Step 4: 运行 provider 单元测试，确认最小 adb 执行面通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/adb/python" \
python3 -m pytest \
  engineering/loop/connection/providers/adb/python/tests/test_client.py \
  engineering/loop/connection/providers/adb/python/tests/test_transport.py -v
```

Expected: PASS

- [ ] **Step 5: 提交本任务改动**

```bash
git add engineering/loop/connection/providers/adb/README.md \
        engineering/loop/connection/providers/adb/python/loop_adb/__init__.py \
        engineering/loop/connection/providers/adb/python/loop_adb/client.py \
        engineering/loop/connection/providers/adb/python/loop_adb/transport.py \
        engineering/loop/connection/providers/adb/python/tests/test_client.py \
        engineering/loop/connection/providers/adb/python/tests/test_transport.py
git commit -m "feat(adb-provider): add minimal adb transport"
```

---

### Task 3: 补齐 adb root / pull / logcat / reboot / runtime context 能力

**Files:**
- Modify: `engineering/loop/connection/providers/adb/python/loop_adb/client.py`
- Modify: `engineering/loop/connection/providers/adb/python/loop_adb/transport.py`
- Modify: `engineering/loop/connection/providers/adb/python/tests/test_client.py`
- Modify: `engineering/loop/connection/providers/adb/python/tests/test_transport.py`
- Create: `engineering/loop/connection/profiles/devices/rp5/adb.json`
- Modify: `engineering/loop/connection/profiles/devices/rp5/README.md`

- [ ] **Step 1: 先写失败测试，锁定 root / pull / reboot / runtime context 语义**

在 `engineering/loop/connection/providers/adb/python/tests/test_client.py` 末尾追加：

```python
def test_pull_builds_expected_command(tmp_path):
    runner = FakeRunner([
        AdbCommandResult(argv=["adb"], exit_code=0, stdout="", stderr=""),
    ])
    client = AdbClient(endpoint="192.168.1.55:5555", device_serial="192.168.1.55:5555", runner=runner)
    local_dir = tmp_path / "artifacts"
    client.pull("/data/vendor/lechao_lcview/logs", str(local_dir), timeout_sec=20.0)
    assert runner.calls[0][0] == [
        "adb", "-s", "192.168.1.55:5555", "pull", "/data/vendor/lechao_lcview/logs", str(local_dir)
    ]


def test_root_uses_adb_root_when_enabled():
    runner = FakeRunner([
        AdbCommandResult(argv=["adb"], exit_code=0, stdout="restarting adbd as root\n", stderr=""),
    ])
    client = AdbClient(endpoint="192.168.1.55:5555", device_serial="192.168.1.55:5555", runner=runner)
    result = client.root(timeout_sec=10.0)
    assert result.exit_code == 0
    assert runner.calls[0][0] == ["adb", "-s", "192.168.1.55:5555", "root"]
```

在 `engineering/loop/connection/providers/adb/python/tests/test_transport.py` 末尾追加：

```python
from loop_core.models import RebootResult


def test_transport_reboot_and_wait_uses_client_hooks():
    class RebootClient(FakeClient):
        def reboot(self, timeout_sec):
            self.reboot_called = timeout_sec

        def wait_for_device(self, timeout_sec):
            self.wait_called = timeout_sec

        def shell(self, command, timeout_sec, as_root=False):
            self.sent_commands.append((command, timeout_sec, as_root))
            if command == "getprop sys.boot_completed":
                return type("ShellResult", (), {
                    "output_lines": ["1"],
                    "command_exit_code": 0,
                })()
            return type("ShellResult", (), {
                "output_lines": ["ok"],
                "command_exit_code": 0,
            })()

    client = RebootClient()
    transport = AdbTransport(endpoint="192.168.1.55:5555", client=client)
    result = transport.reboot_and_wait(
        boot_markers=["Booting Linux", "init second stage"],
        panic_markers=["Kernel panic"],
        prompt_markers=["console:/ $"],
    )
    assert isinstance(result, RebootResult)
    assert result.status == "pass"
    assert client.wait_called == 180.0


def test_transport_describe_runtime_context_includes_recent_commands(tmp_path):
    client = FakeClient()
    transport = AdbTransport(endpoint="192.168.1.55:5555", client=client)
    transport.acquire_writer()
    boundary = transport.mark_output_boundary()
    transport.send_line("getprop ro.build.fingerprint")
    transport.capture_since(boundary, timeout_sec=5.0, recent_limit=20)
    context = transport.describe_runtime_context(artifacts_dir=str(tmp_path))
    assert context["adb_endpoint"] == "192.168.1.55:5555"
    assert context["adb_recent_commands"] == ["getprop ro.build.fingerprint"]
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/adb/python" \
python3 -m pytest \
  engineering/loop/connection/providers/adb/python/tests/test_client.py \
  engineering/loop/connection/providers/adb/python/tests/test_transport.py -v
```

Expected: FAIL，提示缺少 `pull` / `root` / `reboot_and_wait` / `describe_runtime_context`。

- [ ] **Step 3: 增强 `AdbClient` 与 `AdbTransport`**

在 `engineering/loop/connection/providers/adb/python/loop_adb/client.py` 增加：

```python
    def root(self, timeout_sec: float) -> AdbCommandResult:
        return self._runner(["adb", "-s", self.device_serial, "root"], timeout_sec)

    def wait_for_device(self, timeout_sec: float) -> AdbCommandResult:
        return self._runner(["adb", "-s", self.device_serial, "wait-for-device"], timeout_sec)

    def reboot(self, timeout_sec: float) -> AdbCommandResult:
        return self._runner(["adb", "-s", self.device_serial, "reboot"], timeout_sec)

    def pull(self, remote_path: str, local_path: str, timeout_sec: float) -> AdbCommandResult:
        return self._runner(["adb", "-s", self.device_serial, "pull", remote_path, local_path], timeout_sec)

    def logcat(self, buffers: list[str], timeout_sec: float) -> AdbCommandResult:
        argv = ["adb", "-s", self.device_serial, "logcat", "-d"]
        for buffer_name in buffers:
            argv.extend(["-b", buffer_name])
        return self._runner(argv, timeout_sec)
```

把 `engineering/loop/connection/providers/adb/python/loop_adb/transport.py` 扩充为：

```python
from pathlib import Path
import time

from loop_core.models import RebootResult


class AdbTransport(BaseTransport):
    def __init__(
        self,
        endpoint: str,
        device_serial: str | None = None,
        root_mode: str = "auto",
        connect_timeout_sec: float = 15.0,
        command_timeout_sec: float = 10.0,
        client: AdbClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.device_serial = device_serial or endpoint
        self.root_mode = root_mode
        self.connect_timeout_sec = connect_timeout_sec
        self.command_timeout_sec = command_timeout_sec
        self.client = client or AdbClient(endpoint, self.device_serial)
        self._writer_held = False
        self._pending_command = ""
        self._boundary = 0
        self._recent_commands: list[str] = []
        self._reconnect_count = 0
        self._last_wait_for_device_result = "not_run"
        self.client.connect(timeout_sec=self.connect_timeout_sec)

    def _should_use_root(self) -> bool:
        return self.root_mode in {"auto", "adb_root", "su0"}

    def _remember_command(self, command: str) -> None:
        self._recent_commands.append(command)
        if len(self._recent_commands) > 10:
            self._recent_commands = self._recent_commands[-10:]

    def capture_since(self, boundary, timeout_sec, recent_limit, prompt_markers=None):
        del boundary, recent_limit, prompt_markers
        self._remember_command(self._pending_command)
        result = self.client.shell(
            self._pending_command,
            timeout_sec=timeout_sec or self.command_timeout_sec,
            as_root=self.root_mode == "su0",
        )
        lines = [ObservedLine(t=float(index), text=line) for index, line in enumerate(result.output_lines, start=1)]
        self._pending_command = ""
        return CommandCapture(lines=lines, prompt_visible=False, exit_code=result.command_exit_code)

    def reboot_and_wait(
        self,
        boot_markers,
        panic_markers,
        boot_complete_timeout=180.0,
        l1_timeout=30.0,
        l2_timeout=90.0,
        l3_timeout=60.0,
        prompt_markers=None,
    ) -> RebootResult:
        del boot_markers, panic_markers, l1_timeout, l2_timeout, prompt_markers
        start = time.monotonic()
        self.client.reboot(timeout_sec=self.command_timeout_sec)
        self.client.wait_for_device(timeout_sec=boot_complete_timeout)
        self._last_wait_for_device_result = "pass"
        verify = self.client.shell("getprop sys.boot_completed", timeout_sec=l3_timeout)
        if verify.command_exit_code == 0 and any(line.strip() == "1" for line in verify.output_lines):
            return RebootResult(
                status="pass",
                transcript_lines=["adb reboot", "wait-for-device", "sys.boot_completed=1"],
                stage_reached="l3_verified",
                boot_duration_sec=round(time.monotonic() - start, 3),
            )
        return RebootResult(
            status="fail",
            transcript_lines=["adb reboot", "wait-for-device"],
            failure_reason="boot_completed_not_ready",
            stage_reached="l2_init_ready",
            boot_duration_sec=round(time.monotonic() - start, 3),
        )

    def describe_runtime_context(self, artifacts_dir: str | None = None) -> dict:
        return {
            "adb_endpoint": self.endpoint,
            "adb_device_serial": self.device_serial,
            "adb_recent_commands": list(self._recent_commands),
            "adb_reconnect_count": self._reconnect_count,
            "adb_wait_for_device_result": self._last_wait_for_device_result,
            "adb_logcat_snapshot_path": "",
        }
```

创建 `engineering/loop/connection/profiles/devices/rp5/adb.json`：

```json
{
  "device_id": "rp5",
  "transport": "adb",
  "prompt_markers": [],
  "boot_markers": ["sys.boot_completed=1"],
  "panic_markers": ["Kernel panic", "adbd not running"],
  "line_ending": "\n",
  "default_capture_timeout": 10.0,
  "default_recent_limit": 400
}
```

把 `engineering/loop/connection/profiles/devices/rp5/README.md` 改为补充双 profile：

```md
## 当前 profile

- `default.json`：`transport=serial`，用于 boot / bootstrap / fallback
- `adb.json`：`transport=adb`，用于 feature suite 与 adb shell 验收
```

- [ ] **Step 4: 运行 provider 测试，确认增强能力通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/adb/python" \
python3 -m pytest \
  engineering/loop/connection/providers/adb/python/tests/test_client.py \
  engineering/loop/connection/providers/adb/python/tests/test_transport.py -v
```

Expected: PASS

- [ ] **Step 5: 提交本任务改动**

```bash
git add engineering/loop/connection/providers/adb/python/loop_adb/client.py \
        engineering/loop/connection/providers/adb/python/loop_adb/transport.py \
        engineering/loop/connection/providers/adb/python/tests/test_client.py \
        engineering/loop/connection/providers/adb/python/tests/test_transport.py \
        engineering/loop/connection/profiles/devices/rp5/adb.json \
        engineering/loop/connection/profiles/devices/rp5/README.md
git commit -m "feat(adb-provider): add pull reboot and runtime context"
```

---

### Task 4: 让 loop_core 支持 final collectors、collector artifact 与 failure code

**Files:**
- Modify: `engineering/loop/core/python/loop_core/case_loader.py`
- Modify: `engineering/loop/core/python/loop_core/collector.py`
- Modify: `engineering/loop/core/python/loop_core/executor.py`
- Modify: `engineering/loop/core/python/loop_core/models.py`
- Modify: `engineering/loop/core/python/loop_core/runner.py`
- Modify: `engineering/loop/core/python/loop_core/evidence.py`
- Modify: `engineering/loop/core/python/tests/test_case_loader.py`
- Modify: `engineering/loop/core/python/tests/test_collector.py`
- Modify: `engineering/loop/core/python/tests/test_executor.py`
- Modify: `engineering/loop/core/python/tests/test_runner.py`
- Modify: `engineering/loop/core/python/tests/test_evidence.py`

- [ ] **Step 1: 先写失败测试，锁定 `final_collectors` 与 `failure_code` 语义**

在 `engineering/loop/core/python/tests/test_case_loader.py` 末尾追加：

```python
def test_suite_final_collectors_are_preserved(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
final_collectors: [pull_logs]
cases:
  - id: ok
    command: "echo ok"
    assert: {type: contains, value: "ok"}
collectors:
  pull_logs:
    mode: adb_pull
    required: true
    remote_paths: ["/data/vendor/lechao_lcview/logs"]
""")
    suite = load_suite(path, [str(tmp_path)])
    assert suite.final_collectors == ["t.pull_logs"]


def test_required_collector_without_remote_paths_is_rejected(tmp_path):
    path = _write(tmp_path, "t.yaml", """
suite: t
version: 1
cases:
  - id: ok
    command: "echo ok"
    assert: {type: contains, value: "ok"}
collectors:
  bad_pull:
    mode: adb_pull
    required: true
""")
    with pytest.raises(ValueError, match="adb_pull collector requires remote_paths"):
        load_suite(path, [str(tmp_path)])
```

在 `engineering/loop/core/python/tests/test_executor.py` 末尾追加：

```python
def test_required_final_collector_failure_makes_suite_fail(tmp_path):
    suite_yaml = """
suite: t
version: 1
final_collectors: [pull_logs]
cases:
  - id: ok
    command: ""
    assert: {type: prompt_visible}
collectors:
  pull_logs:
    mode: adb_pull
    required: true
    remote_paths: ["/data/vendor/lechao_lcview/logs"]
    failure_code: LCVIEW_EVIDENCE_FAIL
"""
    path = _write(tmp_path, "t.yaml", suite_yaml)
    suite = load_suite(path, [str(tmp_path)])

    class TransportWithContext(FixtureTransport):
        def pull_artifact(self, remote_path, local_dir, timeout_sec):
            raise OSError("pull failed")

    transport = TransportWithContext([{"t": 0.1, "text": "console:/ $"}])
    transport.acquire_writer()
    bundle = CaseExecutor(transport, AssertionEngine()).execute_suite(
        suite, device_id="rp5", prompt_markers=["console:/ $"]
    )
    assert bundle.summary["overall"] == "FAIL"
    assert bundle.summary["failure_code"] == "LCVIEW_EVIDENCE_FAIL"
```

在 `engineering/loop/core/python/tests/test_collector.py` 末尾追加：

```python
def test_adb_pull_collector_returns_artifact_paths(tmp_path):
    class PullTransport(FixtureTransport):
        def pull_artifact(self, remote_path, local_dir, timeout_sec):
            out = tmp_path / "logs"
            out.mkdir(exist_ok=True)
            file_path = out / "sample.jsonl"
            file_path.write_text('{"id":1}\n', encoding="utf-8")
            return [str(file_path)]

    transport = PullTransport([])
    transport.acquire_writer()
    result = Collector(transport).run(
        "pull_logs",
        {"mode": "adb_pull", "remote_paths": ["/data/vendor/lechao_lcview/logs"], "required": True},
        capture_timeout=5.0,
        recent_limit=50,
        artifacts_dir=str(tmp_path),
    )
    assert result.status == "ok"
    assert result.artifact_paths
```

- [ ] **Step 2: 运行测试确认先失败**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py \
  engineering/loop/core/python/tests/test_collector.py \
  engineering/loop/core/python/tests/test_executor.py \
  engineering/loop/core/python/tests/test_runner.py \
  engineering/loop/core/python/tests/test_evidence.py -v
```

Expected: FAIL，提示 `final_collectors` / `artifacts_dir` / `failure_code` 尚未实现。

- [ ] **Step 3: 修改数据模型与执行链路**

在 `engineering/loop/core/python/loop_core/models.py` 修改 dataclass：

```python
@dataclass
class CollectorResult:
    name: str
    commands: list[str]
    outputs: list[dict]
    hints: str = ""
    status: str = "ok"
    partial: bool = False
    error: str = ""
    artifact_paths: list[str] = field(default_factory=list)
    required: bool = False
    failure_code: str = ""


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
    runtime_context: dict = field(default_factory=dict)
```

在 `engineering/loop/core/python/loop_core/case_loader.py`：

```python
@dataclass
class CaseSuite:
    name: str
    version: int
    cases: list[TestCase]
    collectors: dict[str, dict]
    defaults: SuiteDefaults = field(default_factory=SuiteDefaults)
    warnings: list[str] = field(default_factory=list)
    final_collectors: list[str] = field(default_factory=list)
```

在 `load_suite()` 返回时加入：

```python
    final_collectors = _resolve_collector_refs(
        raw.get("final_collectors", []),
        suite_name,
        all_collectors,
    )
```

并把 `final_collectors=final_collectors` 传入 `CaseSuite(...)`。

在 `_validate_collectors()` 中增加：

```python
        if mode == "adb_pull" and not spec.get("remote_paths"):
            raise ValueError("adb_pull collector requires remote_paths")
```

把 `engineering/loop/core/python/loop_core/collector.py` 的 `run()` 签名改为：

```python
    def run(self, name: str, spec: dict, capture_timeout: float = 5.0,
            recent_limit: int = 400,
            prompt_markers: list[str] | None = None,
            artifacts_dir: str | None = None) -> CollectorResult:
```

在 `Collector.run()` 里增加 `adb_pull` 分支：

```python
        if mode == "adb_pull":
            artifact_paths: list[str] = []
            outputs: list[dict] = []
            for remote_path in spec.get("remote_paths", []):
                pulled = self.transport.pull_artifact(remote_path, artifacts_dir or ".", capture_timeout)
                artifact_paths.extend(pulled)
                outputs.append({"command": f"pull {remote_path}", "lines": pulled, "duration_sec": 0.0})
            return CollectorResult(
                name=name,
                commands=[],
                outputs=outputs,
                hints=hints,
                status="ok",
                partial=False,
                artifact_paths=artifact_paths,
                required=bool(spec.get("required", False)),
                failure_code=spec.get("failure_code", ""),
            )
```

在 `engineering/loop/core/python/loop_core/executor.py`：

```python
            for cname in list(triggered_collectors) + list(suite.final_collectors):
```

并在 collector 执行后加入：

```python
                result = evidence[cname]
                if result.required and result.status != "ok":
                    warnings.append(f"required collector failed: {cname}")
```

在 summary 统计后加入：

```python
        required_failures = [
            cr for cr in evidence.values()
            if cr.required and cr.status != "ok"
        ]
        if required_failures:
            overall = "FAIL"
            summary["failure_code"] = required_failures[0].failure_code or "EVIDENCE_FAIL"
```

在 `engineering/loop/core/python/loop_core/runner.py` 构造器增加 `artifacts_dir` 并传给 executor：

```python
        artifacts_dir: str = "",
```

调用 `execute_suite()` 时传：

```python
                artifacts_dir=self.artifacts_dir,
```

在 `_enrich_bundle()` 中增加：

```python
        describe = getattr(self.transport, "describe_runtime_context", None)
        if callable(describe):
            bundle.runtime_context = describe(self.artifacts_dir) or {}
```

在 `engineering/loop/core/python/loop_core/evidence.py` 渲染：

```python
    if bundle.runtime_context:
        lines.append("")
        lines.append("=== Runtime Context ===")
        for key, value in bundle.runtime_context.items():
            lines.append(f"{key}: {value}")
```

并在 collector 区块加：

```python
            if cr.artifact_paths:
                lines.append(f"        artifacts: {', '.join(cr.artifact_paths[:5])}")
```

- [ ] **Step 4: 运行 loop_core 测试，确认增强语义通过**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest \
  engineering/loop/core/python/tests/test_case_loader.py \
  engineering/loop/core/python/tests/test_collector.py \
  engineering/loop/core/python/tests/test_executor.py \
  engineering/loop/core/python/tests/test_runner.py \
  engineering/loop/core/python/tests/test_evidence.py -v
```

Expected: PASS

- [ ] **Step 5: 提交本任务改动**

```bash
git add engineering/loop/core/python/loop_core/case_loader.py \
        engineering/loop/core/python/loop_core/collector.py \
        engineering/loop/core/python/loop_core/executor.py \
        engineering/loop/core/python/loop_core/models.py \
        engineering/loop/core/python/loop_core/runner.py \
        engineering/loop/core/python/loop_core/evidence.py \
        engineering/loop/core/python/tests/test_case_loader.py \
        engineering/loop/core/python/tests/test_collector.py \
        engineering/loop/core/python/tests/test_executor.py \
        engineering/loop/core/python/tests/test_runner.py \
        engineering/loop/core/python/tests/test_evidence.py
git commit -m "feat(loop-core): support final collectors and failure codes"
```

---

### Task 5: 用 adb smoke suite 先打通 provider 最小真路径

**Files:**
- Create: `engineering/loop/cases/system/adb-shell-success.yaml`
- Modify: `engineering/loop/README.md`
- Modify: `engineering/loop/core/python/tests/test_cli.py`

- [ ] **Step 1: 先写 suite 文件与最小说明**

创建 `engineering/loop/cases/system/adb-shell-success.yaml`：

```yaml
suite: system.adb_shell
version: 1
defaults:
  capture_timeout: 10.0
  recent_limit: 200
cases:
  - id: adb_shell_reachable
    command: "echo adb_ok"
    assert:
      type: contains
      value: "adb_ok"
    severity: critical
    description: "验证 adb shell 可执行"

  - id: boot_completed
    command: "getprop sys.boot_completed"
    assert:
      type: contains
      value: "1"
    severity: critical
    requires: [adb_shell_reachable]
    description: "验证 Android 已完成启动"

  - id: adbd_running
    command: "getprop init.svc.adbd"
    assert:
      type: contains
      value: "running"
    severity: critical
    requires: [adb_shell_reachable]
    description: "验证 adbd 进程状态"

  - id: root_identity
    command: "id"
    assert:
      type: regex
      pattern: "uid="
    severity: warn
    requires: [adb_shell_reachable]
    description: "验证 shell 身份命令可执行"
```

在 `engineering/loop/README.md` 的系统场景说明附近追加：

```md
## `system.adb_shell` 场景

`engineering/loop/cases/system/adb-shell-success.yaml` 是 `transport=adb` 的最小 smoke suite：

1. `adb shell` 可达
2. `sys.boot_completed=1`
3. `init.svc.adbd=running`
4. `id` 命令可执行

建议在实现任何 feature adb suite 前先单独跑通本场景。
```

- [ ] **Step 2: 为 CLI 增加 adb smoke 调用测试**

在 `engineering/loop/core/python/tests/test_cli.py` 末尾追加：

```python
def test_cli_adb_suite_path_keeps_case_dirs(tmp_path, monkeypatch):
    import loop_core.cli as cli

    captured = {}

    class FakeTransport:
        def acquire_writer(self):
            return True

        def release(self):
            pass

    class FakeRunner:
        def __init__(self, **kwargs):
            captured.update(kwargs)

        def run(self):
            from loop_core.models import EvidenceBundle
            return EvidenceBundle(
                bundle_id="eb-test",
                device_id="rp5",
                suite="system.adb_shell",
                timestamp="2026-06-21T00:00:00+08:00",
                summary={"total": 0, "passed": 0, "failed": 0, "skipped": 0, "overall": "PASS"},
                cases=[],
                evidence={},
            )

        def build_failure_bundle(self, reason):
            raise AssertionError(reason)

    monkeypatch.setattr(cli, "build_live_transport", lambda profile, args: FakeTransport())
    monkeypatch.setattr(cli, "LoopRunner", FakeRunner)

    suite_path = tmp_path / "adb-shell-success.yaml"
    suite_path.write_text("suite: system.adb_shell\nversion: 1\ncases: []\n", encoding="utf-8")
    profile_path = tmp_path / "adb.json"
    profile_path.write_text('{"device_id":"rp5","transport":"adb"}', encoding="utf-8")

    rc = cli.main([
        "run",
        "--suite", str(suite_path),
        "--device-profile", str(profile_path),
        "--case-dirs", str(tmp_path),
        "--artifacts-dir", str(tmp_path / "out"),
        "--adb-endpoint", "192.168.1.55:5555",
    ])

    assert rc == 0
    assert captured["suite"].name == "system.adb_shell"
```

- [ ] **Step 3: 运行 smoke 相关测试**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 -m pytest engineering/loop/core/python/tests/test_cli.py -v
```

Expected: PASS

- [ ] **Step 4: 真机手工 smoke 验证 provider**

Run:
```bash
bash engineering/harness/scripts/le.sh run \
  --suite engineering/loop/cases/system/adb-shell-success.yaml \
  --device-profile engineering/loop/connection/profiles/devices/rp5/adb.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir engineering/output/runs/adb-shell-success \
  --adb-endpoint 192.168.1.55:5555
```

Expected: PASS，且 `engineering/output/runs/adb-shell-success/evidence_bundle.json` 中 `execution_config.provider_type` 为 `AdbTransport`。

- [ ] **Step 5: 提交本任务改动**

```bash
git add engineering/loop/cases/system/adb-shell-success.yaml \
        engineering/loop/README.md \
        engineering/loop/core/python/tests/test_cli.py
git commit -m "feat(loop): add adb shell smoke suite"
```

---

### Task 6: 落地 `lcview` 公共 suite 与 collector 证据模型

**Files:**
- Create: `engineering/loop/cases/features/lcview/common.yaml`
- Modify: `engineering/loop/README.md`

- [ ] **Step 1: 编写 `common.yaml`，先锁定前提检查与公共 collector**

创建 `engineering/loop/cases/features/lcview/common.yaml`：

```yaml
suite: features.lcview.common
version: 1
defaults:
  capture_timeout: 15.0
  recent_limit: 400
final_collectors:
  - lcview_pull_logs
  - lcview_invalid_log
  - lcview_service_state
  - lcview_runtime_context
cases:
  - id: adb_shell_reachable
    command: "echo lcview_adb_ok"
    assert: {type: contains, value: "lcview_adb_ok"}
    severity: critical

  - id: boot_completed
    command: "getprop sys.boot_completed"
    assert: {type: contains, value: "1"}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lcview_logcat, lcview_kmsg, serial_recent]

  - id: lcview_hal_service_state
    command: "getprop init.svc.lechao_lcview_hal"
    assert: {type: regex, pattern: "running|stopped"}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lcview_logcat, lcview_service_state, serial_recent]

  - id: lcview_daemon_service_state
    command: "getprop init.svc.lechao_lcview"
    assert: {type: regex, pattern: "running|stopped"}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lcview_logcat, lcview_service_state, serial_recent]

  - id: lcview_schema_present
    command: "test -f /vendor/etc/lcview_events.json && echo present"
    assert: {type: contains, value: "present"}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lcview_files, lcview_logcat, serial_recent]

  - id: lcview_data_dir_ready
    command: "test -d /data/vendor/lechao_lcview/logs && echo ready"
    assert: {type: contains, value: "ready"}
    severity: critical
    requires: [adb_shell_reachable]
    on_fail:
      collectors: [lcview_files, lcview_service_state, serial_recent]

collectors:
  lcview_logcat:
    mode: adb_logcat
    buffers: [main, system, crash]
    filters: ["lechao_lcview", "lechao_lcview_hal"]
    failure_code: LCVIEW_PIPELINE_FAIL
    hints: "检查 lcview daemon / hal logcat 中的 connect/open/schema/filewriter 错误"

  lcview_kmsg:
    commands:
      - "dmesg | grep -i lcview"
    failure_code: LCVIEW_PIPELINE_FAIL
    hints: "检查内核侧 lcview / lciod 相关日志"

  lcview_service_state:
    commands:
      - "getprop init.svc.lechao_lcview"
      - "getprop init.svc.lechao_lcview_hal"
      - "ps -A | grep -E 'lechao_lcview|lcview'"
    failure_code: LCVIEW_PREREQ_FAIL
    hints: "检查 daemon / hal 服务状态与进程存在性"

  lcview_files:
    commands:
      - "ls -l /vendor/etc/lcview_events.json"
      - "ls -l /data/vendor/lechao_lcview"
      - "ls -lt /data/vendor/lechao_lcview/logs"
    failure_code: LCVIEW_PREREQ_FAIL
    hints: "检查 schema / data dir / logs 目录状态"

  lcview_pull_logs:
    mode: adb_pull
    remote_paths:
      - "/data/vendor/lechao_lcview/logs"
    required: true
    failure_code: LCVIEW_EVIDENCE_FAIL
    hints: "拉取 jsonl 目录，验证结构化日志是否回收到 host"

  lcview_invalid_log:
    mode: adb_pull
    remote_paths:
      - "/data/vendor/lechao_lcview/invalid_records.log"
    required: false
    failure_code: LCVIEW_EVIDENCE_FAIL
    hints: "采集 invalid_records.log，辅助判断 schema / decode 问题"

  lcview_runtime_context:
    mode: runtime_context
    required: true
    failure_code: ADB_EXEC_FAIL
    hints: "记录 adb endpoint / recent commands / reconnect / logcat snapshot"
```

- [ ] **Step 2: 用 loader 测试检查 `common.yaml` 结构可加载**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 - <<'PY'
from loop_core.case_loader import load_suite
suite = load_suite(
    "engineering/loop/cases/features/lcview/common.yaml",
    ["engineering/loop/cases"],
)
print(suite.name)
print(suite.final_collectors)
PY
```

Expected: 输出 `features.lcview.common`，且 `final_collectors` 为 FQN 列表。

- [ ] **Step 3: 更新 README，登记 feature 目录与使用方式**

在 `engineering/loop/README.md` 的目录结构段改为：

```md
│   ├── features/                  基于业务特性的验收场景（如 lcview）
│   ├── modules/                   模块级用例（第二步）
│   └── system/                    系统级用例
```

并追加：

```md
## `features.lcview` 场景

`engineering/loop/cases/features/lcview/common.yaml` 提供：

- adb shell reachability
- `sys.boot_completed`
- HAL / daemon service state
- schema / data dir readiness
- pull logs / invalid log / runtime context final collectors
```

- [ ] **Step 4: 提交本任务改动**

```bash
git add engineering/loop/cases/features/lcview/common.yaml \
        engineering/loop/README.md
git commit -m "feat(loop): add lcview common suite"
```

---

### Task 7: 落地 `lcview` end-to-end 主流程 suite

**Files:**
- Create: `engineering/loop/cases/features/lcview/end_to_end.yaml`

- [ ] **Step 1: 编写端到端 suite，覆盖 cleanup → trigger → jsonl → pull**

创建 `engineering/loop/cases/features/lcview/end_to_end.yaml`：

```yaml
suite: features.lcview.end_to_end
version: 1
include:
  - common/shell
  - features/lcview/common
defaults:
  capture_timeout: 20.0
  recent_limit: 500
cases:
  - id: lcview_cleanup_old_logs
    command: "rm -f /data/vendor/lechao_lcview/logs/*.jsonl /data/vendor/lechao_lcview/invalid_records.log && echo cleaned"
    assert: {type: contains, value: "cleaned"}
    severity: critical
    requires: [adb_shell_reachable, lcview_data_dir_ready]
    on_fail:
      collectors: [lcview_files, lcview_logcat, serial_recent]

  - id: lcview_capture_pre_state
    command: "ls -lt /data/vendor/lechao_lcview/logs || true"
    assert: {type: not_contains, value: ".jsonl"}
    severity: warn
    requires: [lcview_cleanup_old_logs]

  - id: lcview_restart_hal
    command: "setprop ctl.restart lechao_lcview_hal && echo hal_restarted"
    assert: {type: contains, value: "hal_restarted"}
    severity: critical
    requires: [lcview_cleanup_old_logs]
    on_fail:
      collectors: [lcview_logcat, lcview_service_state, serial_recent]

  - id: lcview_restart_daemon
    command: "setprop ctl.restart lechao_lcview && echo daemon_restarted"
    assert: {type: contains, value: "daemon_restarted"}
    severity: critical
    requires: [lcview_restart_hal]
    on_fail:
      collectors: [lcview_logcat, lcview_service_state, serial_recent]

  - id: lcview_trigger_usb_read_window
    command: "TARGET=$(ls /storage | head -n 1); FILE=$(find /storage/$TARGET -type f | head -n 1); dd if=$FILE of=/dev/null bs=1M count=8 && echo trigger_done"
    assert: {type: contains, value: "trigger_done"}
    severity: critical
    requires: [lcview_restart_daemon]
    on_fail:
      collectors: [lcview_logcat, lcview_kmsg, lcview_service_state, serial_recent]

  - id: lcview_jsonl_generated
    command: "ls /data/vendor/lechao_lcview/logs/*.jsonl"
    assert: {type: regex, pattern: ".*\\.jsonl"}
    severity: critical
    requires: [lcview_trigger_usb_read_window]
    on_fail:
      collectors: [lcview_files, lcview_logcat, lcview_kmsg, serial_recent]

  - id: lcview_jsonl_non_empty
    command: "for f in /data/vendor/lechao_lcview/logs/*.jsonl; do test -s $f && echo non_empty:$f; done"
    assert: {type: contains, value: "non_empty:"}
    severity: critical
    requires: [lcview_jsonl_generated]
    on_fail:
      collectors: [lcview_files, lcview_pull_logs, lcview_logcat, serial_recent]

  - id: lcview_invalid_log_clean_or_bounded
    command: "test ! -f /data/vendor/lechao_lcview/invalid_records.log || wc -l /data/vendor/lechao_lcview/invalid_records.log"
    assert: {type: regex, pattern: "(^$|^[[:space:]]*[0-9]+[[:space:]]+)"}
    severity: warn
    requires: [lcview_jsonl_generated]
    on_fail:
      collectors: [lcview_invalid_log, lcview_logcat]

  - id: lcview_logcat_no_fatal_breakage
    command: "logcat -d -s lechao_lcview:V lechao_lcview_hal:V | grep -E 'open failed|schema load failed|failed to connect|cannot open' || true"
    assert: {type: equals, value: ""}
    severity: critical
    requires: [lcview_jsonl_generated]
    on_fail:
      collectors: [lcview_logcat, lcview_service_state, serial_recent]
```

- [ ] **Step 2: 静态加载 suite，确认 include / requires / collectors 全解析**

Run:
```bash
PYTHONPATH="engineering/loop/core/python" \
python3 - <<'PY'
from loop_core.case_loader import load_suite
suite = load_suite(
    "engineering/loop/cases/features/lcview/end_to_end.yaml",
    ["engineering/loop/cases"],
)
print(suite.name)
print(len(suite.cases))
print(sorted(suite.collectors.keys())[:5])
PY
```

Expected: 输出 `features.lcview.end_to_end`，并且不会抛 requires / collector unresolved 错误。

- [ ] **Step 3: 提交本任务改动**

```bash
git add engineering/loop/cases/features/lcview/end_to_end.yaml
git commit -m "feat(loop): add lcview end to end suite"
```

---

### Task 8: 落地 bootstrap→adb feature→fallback 的 harness workflow

**Files:**
- Create: `engineering/harness/workflows/lcview-adb-run/WORKFLOW.md`
- Create: `engineering/harness/workflows/lcview-adb-run/README.md`
- Create: `engineering/harness/workflows/lcview-adb-run/run_lcview_adb_suite.sh`
- Modify: `engineering/harness/workflows/README.md`
- Modify: `engineering/harness/README.md`

- [ ] **Step 1: 先写 WORKFLOW 契约文档**

创建 `engineering/harness/workflows/lcview-adb-run/WORKFLOW.md`：

```md
# lcview-adb-run Workflow

## 目标
提供单入口 workflow：
1. 用 serial profile 跑 `system/network-adbd-success.yaml`
2. 提取 adb endpoint
3. 用 adb profile 跑 `features/lcview/end_to_end.yaml`
4. adb run 失败时补采 serial context
5. 汇总 bootstrap / feature artifacts 与 failure code

## 输入参数
- `--serial-host`
- `--serial-port`
- `--adb-endpoint`（可选；为空时自动发现）
- `--artifacts-dir`
- `--serial-profile`
- `--adb-profile`

## 失败分型
- bootstrap 阶段失败：`BOOTSTRAP_FAIL`
- adb endpoint 缺失或 connect 失败：`ADB_CONNECT_FAIL`
- adb suite 运行异常：`ADB_EXEC_FAIL`
- `lcview` 前提失败：`LCVIEW_PREREQ_FAIL`
- trigger 失败：`LCVIEW_TRIGGER_FAIL`
- pipeline 失败：`LCVIEW_PIPELINE_FAIL`
- pull / final collector 失败：`LCVIEW_EVIDENCE_FAIL`
```

创建 `engineering/harness/workflows/lcview-adb-run/README.md`：

```md
# lcview-adb-run

串口 bootstrap 后切换 adb 执行 `lcview` feature suite 的单入口 workflow。详细流程见 `WORKFLOW.md`。
```

- [ ] **Step 2: 写 workflow 脚本骨架并接入 observability / path rules**

创建 `engineering/harness/workflows/lcview-adb-run/run_lcview_adb_suite.sh`：

```bash
#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=../../lib/shell/harness_bootstrap.sh
source "$SCRIPT_DIR/../../lib/shell/harness_bootstrap.sh"

harness_init "lcview-adb-run"

SERIAL_HOST="127.0.0.1"
SERIAL_PORT="9700"
ADB_ENDPOINT=""
ARTIFACTS_DIR="$(harness_path RUNS_DIR)/lcview-adb-run"
SERIAL_PROFILE="$(harness_path ENGINEERING_DIR)/loop/connection/profiles/devices/rp5/default.json"
ADB_PROFILE="$(harness_path ENGINEERING_DIR)/loop/connection/profiles/devices/rp5/adb.json"
CASE_DIR="$(harness_path ENGINEERING_DIR)/loop/cases"
BOOTSTRAP_SUITE="$(harness_path ENGINEERING_DIR)/loop/cases/system/network-adbd-success.yaml"
FEATURE_SUITE="$(harness_path ENGINEERING_DIR)/loop/cases/features/lcview/end_to_end.yaml"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --serial-host) SERIAL_HOST="$2"; shift 2 ;;
    --serial-port) SERIAL_PORT="$2"; shift 2 ;;
    --adb-endpoint) ADB_ENDPOINT="$2"; shift 2 ;;
    --artifacts-dir) ARTIFACTS_DIR="$2"; shift 2 ;;
    --serial-profile) SERIAL_PROFILE="$2"; shift 2 ;;
    --adb-profile) ADB_PROFILE="$2"; shift 2 ;;
    *) log_error "unknown arg: $1"; harness_exit 2 ;;
  esac
done

BOOTSTRAP_OUT="$ARTIFACTS_DIR/bootstrap"
FEATURE_OUT="$ARTIFACTS_DIR/feature"
FALLBACK_OUT="$ARTIFACTS_DIR/fallback"
mkdir -p "$BOOTSTRAP_OUT" "$FEATURE_OUT" "$FALLBACK_OUT"

step_begin "bootstrap" "run serial network-adbd bootstrap"
bash "$(harness_path HARNESS_DIR)/scripts/le.sh" run \
  --suite "$BOOTSTRAP_SUITE" \
  --host "$SERIAL_HOST" \
  --port "$SERIAL_PORT" \
  --device-profile "$SERIAL_PROFILE" \
  --case-dirs "$CASE_DIR" \
  --artifacts-dir "$BOOTSTRAP_OUT"
bootstrap_rc=$?
step_end "bootstrap" "$bootstrap_rc"

if [[ $bootstrap_rc -ne 0 ]]; then
  log_error "BOOTSTRAP_FAIL"
  harness_status_emit FAIL "bootstrap"
  harness_exit "$bootstrap_rc"
fi

if [[ -z "$ADB_ENDPOINT" ]]; then
  step_begin "discover-adb-endpoint" "discover adb endpoint from serial helper"
  ADB_ENDPOINT="$(python3 "$(harness_path HARNESS_DIR)/scripts/rp5_serial_helper.py" device-ip --host "$SERIAL_HOST" --port "$SERIAL_PORT")":5555
  step_end "discover-adb-endpoint" 0
fi

step_begin "feature" "run lcview adb feature suite"
bash "$(harness_path HARNESS_DIR)/scripts/le.sh" run \
  --suite "$FEATURE_SUITE" \
  --device-profile "$ADB_PROFILE" \
  --case-dirs "$CASE_DIR" \
  --artifacts-dir "$FEATURE_OUT" \
  --adb-endpoint "$ADB_ENDPOINT"
feature_rc=$?
step_end "feature" "$feature_rc"

if [[ $feature_rc -ne 0 ]]; then
  step_begin "fallback" "collect serial fallback context"
  bash "$(harness_path HARNESS_DIR)/scripts/le.sh" run \
    --suite "$(harness_path ENGINEERING_DIR)/loop/cases/system/boot-success.yaml" \
    --host "$SERIAL_HOST" \
    --port "$SERIAL_PORT" \
    --device-profile "$SERIAL_PROFILE" \
    --case-dirs "$CASE_DIR" \
    --artifacts-dir "$FALLBACK_OUT"
  step_end "fallback" 0
fi

harness_exit "$feature_rc"
```

- [ ] **Step 3: 运行脚本静态校验**

Run:
```bash
bash engineering/harness/scripts/validate_harness_scripts.sh
```

Expected: PASS，至少不应出现路径硬编码与 bootstrap/observability 缺失问题。

- [ ] **Step 4: 更新 workflow 索引 README**

在 `engineering/harness/workflows/README.md` 表格追加：

```md
| [lcview-adb-run](./lcview-adb-run/) | 串口 bootstrap 后切换 adb 执行 lcview | 两阶段 transport 编排 + fallback evidence | `run_lcview_adb_suite.sh` |
```

在 `engineering/harness/README.md` 快速导航表追加：

```md
| 跑 lcview 的 serial→adb 双阶段验收 | [workflows/lcview-adb-run/](./workflows/lcview-adb-run/) |
```

- [ ] **Step 5: 提交本任务改动**

```bash
git add engineering/harness/workflows/lcview-adb-run/WORKFLOW.md \
        engineering/harness/workflows/lcview-adb-run/README.md \
        engineering/harness/workflows/lcview-adb-run/run_lcview_adb_suite.sh \
        engineering/harness/workflows/README.md \
        engineering/harness/README.md
git commit -m "feat(harness): add lcview adb workflow"
```

---

### Task 9: 端到端验证、文档收尾与计划自检

**Files:**
- Modify: `engineering/loop/README.md`
- Modify: `engineering/loop/connection/providers/adb/README.md`
- Modify: `docs/plans/2026-06-21-lcview-adb-provider-and-loop-case.md`

- [ ] **Step 1: 跑 Python 单元测试全量回归**

Run:
```bash
PYTHONPATH="engineering/loop/core/python:engineering/loop/connection/providers/rp5-serial/python:engineering/loop/connection/providers/adb/python" \
python3 -m pytest \
  engineering/loop/core/python/tests/ \
  engineering/loop/connection/providers/rp5-serial/python/tests/ \
  engineering/loop/connection/providers/adb/python/tests/ \
  -v --import-mode=importlib
```

Expected: PASS

- [ ] **Step 2: 跑 adb smoke 真机验证**

Run:
```bash
bash engineering/harness/scripts/le.sh run \
  --suite engineering/loop/cases/system/adb-shell-success.yaml \
  --device-profile engineering/loop/connection/profiles/devices/rp5/adb.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir engineering/output/runs/adb-shell-success \
  --adb-endpoint 192.168.1.55:5555
```

Expected: PASS

- [ ] **Step 3: 跑 workflow 真机验收 `lcview`**

Run:
```bash
bash engineering/harness/workflows/lcview-adb-run/run_lcview_adb_suite.sh \
  --serial-host 127.0.0.1 \
  --serial-port 9700 \
  --artifacts-dir engineering/output/runs/lcview-adb-run
```

Expected: `feature/evidence_bundle.json` 存在；若失败，`fallback/` 下存在串口补证；summary 中可见 `failure_code`。

- [ ] **Step 4: 检查 `lcview` 证据落点是否满足 spec**

人工核对以下文件存在：

```text
engineering/output/runs/lcview-adb-run/bootstrap/evidence_bundle.json
engineering/output/runs/lcview-adb-run/feature/evidence_bundle.json
engineering/output/runs/lcview-adb-run/feature/summary.txt
engineering/output/runs/lcview-adb-run/feature/artifacts/
```

并在 `feature/evidence_bundle.json` 中确认：

```json
{
  "runtime_context": {
    "adb_endpoint": "192.168.1.55:5555"
  },
  "summary": {
    "failure_code": "LCVIEW_* 或 ADB_*"
  }
}
```

- [ ] **Step 5: 更新 provider README 的最终使用示例**

在 `engineering/loop/connection/providers/adb/README.md` 追加：

````md
## Smoke 示例

```bash
bash engineering/harness/scripts/le.sh run \
  --suite engineering/loop/cases/system/adb-shell-success.yaml \
  --device-profile engineering/loop/connection/profiles/devices/rp5/adb.json \
  --case-dirs engineering/loop/cases \
  --artifacts-dir engineering/output/runs/adb-shell-success \
  --adb-endpoint 192.168.1.55:5555
```

## `lcview` 示例

```bash
bash engineering/harness/workflows/lcview-adb-run/run_lcview_adb_suite.sh \
  --serial-host 127.0.0.1 \
  --serial-port 9700 \
  --artifacts-dir engineering/output/runs/lcview-adb-run
```
````

- [ ] **Step 6: 自检计划与实现一致性，然后提交收尾变更**

自检命令：

```bash
python3 - <<'PY'
from pathlib import Path
text = Path("docs/plans/2026-06-21-lcview-adb-provider-and-loop-case.md").read_text(encoding="utf-8")
for token in ["TO" "DO", "TB" "D", "place" "holder", "待" "补", "待" "定"]:
    if token in text:
        raise SystemExit(f"found forbidden token: {token}")
print("plan forbidden token scan clean")
PY
```

提交：

```bash
git add engineering/loop/README.md \
        engineering/loop/connection/providers/adb/README.md \
        docs/plans/2026-06-21-lcview-adb-provider-and-loop-case.md
git commit -m "docs(loop): finalize adb lcview plan and usage"
```

---

## Self-Review

- Spec coverage:
  - `transport=adb` provider：Task 1-3
  - `lcview common + end_to_end suite`：Task 6-7
  - bootstrap → adb feature workflow：Task 8
  - runtime context / pull / logcat / failure taxonomy：Task 3-4
  - smoke suite 与真机验证：Task 5、Task 9
- Placeholder scan:
  - 本计划不保留任何占位标记。
- Type consistency:
  - `failure_code` 在 collector / summary 两层统一；`runtime_context` 统一挂在 `EvidenceBundle`；`final_collectors` 统一由 `CaseSuite` 持有。

Plan complete and saved to `docs/plans/2026-06-21-lcview-adb-provider-and-loop-case.md`. Two execution options:

1. Subagent-Driven (recommended) - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. Inline Execution - Execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?



