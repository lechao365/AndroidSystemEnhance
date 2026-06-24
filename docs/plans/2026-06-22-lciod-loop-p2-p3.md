# lciod Loop P2+P3: Deploy + Controller AI 闭环 — 实施计划

> **2026-06-24 更新**：设备 IP 发现已从"固定 IP"切换为"串口动态发现"，见 `engineering/loop/scripts/rp5_serial_helper.py` 和 `engineering/loop/WORKFLOW.md` 的「传输层依赖链」章节。本文档中残留的 `192.168.1.55` 仅为历史决策记录。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 loop_deploy 部署层（git diff 决策器 + mmm/push + boot.img dd/reboot）+ loop_controller AI 闭环（主会话内调度 + LlmAnalyzer 抽象 + 3 个预设 bug 演练脚本）。

**Architecture:** P2 新增 `engineering/loop/deploy/` 模块（decider/compiler/deployer/adb_ops/cli），扩展 AdbClient（+push/+remount），接入 cli.py deploy 占位。P3 扩展 `engineering/loop/controller/`（analyzer_protocol/patch_applier/cycle_orchestrator/control_cli），复用已有 policy/engine/state，新增 control 子命令和预设 bug 注入脚本。

**Tech Stack:** Python 3.11+ (pytest, yaml, subprocess), bash (mk_rpi5_full_image.sh), adb CLI, git

**Spec:** `docs/specs/2026-06-22-lciod-loop-verification-design.md` §4-§5

**前序依赖:** P1 已完成（断言扩展 + 5 个 lciod suite）

**测试运行命令：**
```bash
# core 测试
PYTHONPATH="engineering/loop/core/python" \
  python3 -m pytest engineering/loop/core/python/tests/ -q

# deploy 测试
PYTHONPATH="engineering/loop/deploy/python:engineering/loop/core/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/deploy/python/tests/ -v

# controller 测试
PYTHONPATH="engineering/loop/controller/python:engineering/loop/contracts/python:engineering/loop/core/python" \
  python3 -m pytest engineering/loop/controller/python/tests/ -v
```

---


# P2: loop_deploy 部署层

## Task 1: loop_deploy 目录 + models.py

**Files:**
- Create: `engineering/loop/deploy/python/loop_deploy/__init__.py`
- Create: `engineering/loop/deploy/python/loop_deploy/models.py`
- Create: `engineering/loop/deploy/python/tests/test_models.py`

- [ ] **Step 1: 创建目录结构**

```bash
mkdir -p engineering/loop/deploy/python/loop_deploy
mkdir -p engineering/loop/deploy/python/tests
```

- [ ] **Step 2: 创建 __init__.py**

```python
"""loop_deploy — 部署引擎：分析 git diff 决策部署模式，编译并推送到设备。"""
```

- [ ] **Step 3: 创建 models.py（DeployPlan/DeployMode/DeployTarget/DeployResult）**

```python
"""loop_deploy 数据模型：DeployPlan / DeployMode / DeployTarget / DeployResult。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class DeployMode(StrEnum):
    SKIP = "skip"
    PUSH_SINGLE = "push_single"
    DD_BOOT_REBOOT = "dd_boot_reboot"
    FLASH_FULL = "flash_full"  # P2 不实现，返回 error


@dataclass
class DeployTarget:
    artifact_name: str         # 编译产物文件名，如 "boot.img"
    remote_path: str           # 设备上的目标路径
    service_name: str = ""     # PUSH_SINGLE: restart 的服务名（init.svc.<name>）
    block_device: str = ""     # DD_BOOT_REBOOT: 目标块设备


@dataclass
class DeployPlan:
    mode: DeployMode
    changed_files: list[str] = field(default_factory=list)
    reason: str = ""
    build_targets: list[str] = field(default_factory=list)
    deploy_targets: list[DeployTarget] = field(default_factory=list)
    requires_reboot: bool = False
    estimated_seconds: int = 0

    @classmethod
    def skip(cls, reason: str = "") -> DeployPlan:
        return cls(mode=DeployMode.SKIP, reason=reason)

    @classmethod
    def flash_full(cls, changed_files: list[str], reason: str = "") -> DeployPlan:
        return cls(mode=DeployMode.FLASH_FULL, changed_files=changed_files, reason=reason)


@dataclass
class CompileResult:
    success: bool
    artifacts: list[str] = field(default_factory=list)  # host 侧编译产物路径列表
    error: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class DeployResult:
    success: bool
    mode: DeployMode
    duration_seconds: float = 0.0
    requires_reboot: bool = False
    error: str = ""
```

- [ ] **Step 4: 创建 tests/test_models.py**

```python
"""loop_deploy models 单元测试。"""
from loop_deploy.models import DeployMode, DeployPlan, DeployTarget, CompileResult, DeployResult


def test_deploy_mode_values():
    assert DeployMode.SKIP.value == "skip"
    assert DeployMode.PUSH_SINGLE.value == "push_single"
    assert DeployMode.DD_BOOT_REBOOT.value == "dd_boot_reboot"
    assert DeployMode.FLASH_FULL.value == "flash_full"


def test_deploy_plan_skip_factory():
    plan = DeployPlan.skip("no changes")
    assert plan.mode == DeployMode.SKIP
    assert plan.reason == "no changes"


def test_deploy_plan_flash_full_factory():
    plan = DeployPlan.flash_full(["foo.te"], "sepolicy change")
    assert plan.mode == DeployMode.FLASH_FULL
    assert "foo.te" in plan.changed_files


def test_deploy_target_fields():
    t = DeployTarget(
        artifact_name="boot.img",
        remote_path="/data/local/tmp/boot.img",
        block_device="/dev/block/mmcblk0p1",
    )
    assert t.artifact_name == "boot.img"
    assert t.block_device == "/dev/block/mmcblk0p1"


def test_compile_result_defaults():
    r = CompileResult(success=True, artifacts=["/tmp/boot.img"])
    assert r.success


def test_deploy_result():
    r = DeployResult(success=True, mode=DeployMode.PUSH_SINGLE, duration_seconds=2.5)
    assert r.success
    assert r.mode == DeployMode.PUSH_SINGLE
```

- [ ] **Step 5: 运行测试**

```bash
PYTHONPATH="engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/deploy/python/tests/test_models.py -v
```
Expected: 6 PASS。

- [ ] **Step 6: Commit**

```bash
git add engineering/loop/deploy/ && git commit -m "feat(loop_deploy): 目录骨架 + models（DeployPlan/DeployMode/DeployTarget）"
```

---

## Task 2: AdbClient 扩展 push + remount

**Files:**
- Modify: `engineering/loop/connection/providers/adb/python/loop_adb/client.py`
- Modify: `engineering/loop/connection/providers/adb/python/tests/test_client.py`

- [ ] **Step 1: 写 push/remount 失败测试**

在 `test_client.py` 末尾追加：

```python

class TestPushAndRemount:
    def test_push_argv(self):
        import loop_adb.client as mod
        called = []

        def fake_runner(argv, timeout):
            called.append(argv)
            return mod.AdbCommandResult(argv=argv, exit_code=0, stdout="", stderr="")

        client = mod.AdbClient("192.168.1.55:5555", "192.168.1.55:5555", runner=fake_runner)
        client.push("/local/path", "/remote/path", timeout_sec=30.0)
        assert called
        argv = called[0]
        assert argv[0:4] == ["adb", "-s", "192.168.1.55:5555", "push"]
        assert "/local/path" in argv
        assert "/remote/path" in argv

    def test_remount_argv(self):
        import loop_adb.client as mod
        called = []

        def fake_runner(argv, timeout):
            called.append(argv)
            return mod.AdbCommandResult(argv=argv, exit_code=0, stdout="", stderr="")

        client = mod.AdbClient("192.168.1.55:5555", "192.168.1.55:5555", runner=fake_runner)
        client.remount(timeout_sec=15.0)
        assert called
        argv = called[0]
        assert argv == ["adb", "-s", "192.168.1.55:5555", "remount"]
```

- [ ] **Step 2: 验证测试失败**

```bash
PYTHONPATH="engineering/loop/connection/providers/adb/python:engineering/loop/core/python:engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/connection/providers/adb/python/tests/test_client.py::TestPushAndRemount -v
```
Expected: AttributeError（push/remount 方法不存在）。

- [ ] **Step 3: 实现 push + remount**

在 `client.py` 的 logcat 方法后追加（文件末尾）：

```python
    def push(self, local_path: str, remote_path: str, timeout_sec: float) -> AdbCommandResult:
        """adb -s <serial> push <local> <remote>。"""
        return self._runner(
            ["adb", "-s", self.device_serial, "push", local_path, remote_path],
            timeout_sec,
        )

    def remount(self, timeout_sec: float) -> AdbCommandResult:
        """adb -s <serial> remount。"""
        return self._runner(
            ["adb", "-s", self.device_serial, "remount"], timeout_sec
        )
```

- [ ] **Step 4: 验证测试通过 + 运行既有 adb 测试**

```bash
PYTHONPATH="engineering/loop/connection/providers/adb/python:engineering/loop/core/python:engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/connection/providers/adb/python/tests/ -v
```
Expected: all PASS。

- [ ] **Step 5: Commit**

```bash
git add engineering/loop/connection/providers/adb/python/loop_adb/client.py \
        engineering/loop/connection/providers/adb/python/tests/test_client.py
git commit -m "feat(loop_adb): AdbClient 扩展 push + remount 方法"
```

---

## Task 3: adb_ops.py（辅助操作封装）

**Files:**
- Create: `engineering/loop/deploy/python/loop_deploy/adb_ops.py`
- Create: `engineering/loop/deploy/python/tests/test_adb_ops.py`

此模块封装部署相关的 adb 辅助操作（wait_service_running / wait_boot_completed），不依赖真实设备（通过 fake_runner 测试）。

- [ ] **Step 1: 创建 adb_ops.py**

```python
"""adb 部署辅助操作：wait_service_running / wait_boot_completed。"""
from __future__ import annotations

import time
from loop_adb.client import AdbClient


class AdbOps:
    def __init__(self, client: AdbClient):
        self._client = client

    def wait_service_running(self, service_name: str, timeout: float = 15.0) -> bool:
        """轮询 getprop init.svc.<name> 直到 running，超时返回 False。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._client.shell(f"getprop init.svc.{service_name}", timeout_sec=5.0)
            if result.command_exit_code == 0 and "running" in result.raw_stdout:
                return True
            time.sleep(0.5)
        return False

    def wait_boot_completed(self, timeout: float = 120.0) -> bool:
        """轮询 sys.boot_completed=1，超时返回 False。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._client.shell("getprop sys.boot_completed", timeout_sec=10.0)
            if result.command_exit_code == 0 and "1" in result.raw_stdout:
                return True
            time.sleep(2.0)
        return False
```

- [ ] **Step 2: 创建 test_adb_ops.py（mock AdbClient）**

```python
"""adb_ops 单元测试（mock AdbClient）。"""
import loop_adb.client as adb_mod
from loop_deploy.adb_ops import AdbOps


def _make_client(shell_outputs: list[str]):
    idx = 0
    def fake_runner(argv, timeout):
        nonlocal idx
        out = shell_outputs[idx] if idx < len(shell_outputs) else "running"
        idx += 1
        return adb_mod.AdbCommandResult(argv=argv, exit_code=0, stdout=out, stderr="")
    return adb_mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555", runner=fake_runner)


def test_wait_service_running_immediate():
    client = _make_client(["running"])
    ops = AdbOps(client)
    assert ops.wait_service_running("test_svc", timeout=5.0) is True


def test_wait_service_running_timeout():
    client = _make_client(["stopped", "stopped", "stopped", "stopped", "stopped",
                           "stopped", "stopped", "stopped", "stopped", "stopped", "stopped"])
    ops = AdbOps(client)
    assert ops.wait_service_running("test_svc", timeout=0.5) is False


def test_wait_boot_completed_immediate():
    client = _make_client(["1"])
    ops = AdbOps(client)
    assert ops.wait_boot_completed(timeout=5.0) is True
```

- [ ] **Step 3: 运行测试**

```bash
PYTHONPATH="engineering/loop/deploy/python:engineering/loop/connection/providers/adb/python:engineering/loop/core/python:engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/deploy/python/tests/test_adb_ops.py -v
```
Expected: 3 PASS。

- [ ] **Step 4: Commit**

```bash
git add engineering/loop/deploy/python/loop_deploy/adb_ops.py \
        engineering/loop/deploy/python/tests/test_adb_ops.py
git commit -m "feat(loop_deploy): adb_ops——wait_service_running + wait_boot_completed"
```

---

## Task 4: decider.py（git diff → DeployPlan）

**Files:**
- Create: `engineering/loop/deploy/python/loop_deploy/decider.py`
- Create: `engineering/loop/deploy/python/tests/test_decider.py`

决策规则（按优先级从高到低）：
1. `kernel/**/lciod_usbd*.c\|h` → DD_BOOT_REBOOT (mode 2)
2. `kernel/**/defconfig*` → DD_BOOT_REBOOT
3. `kernel/**/usb/storage/*.diff` → DD_BOOT_REBOOT
4. `**/sepolicy/*.te` → FLASH_FULL（P2 不实现）
5. `**/lechao_lciod*.rc` → DD_BOOT_REBOOT
6. `**/lechao_lciod*/**/*.cpp` → PUSH_SINGLE (mmm)
7. `**/lechao_lciod*/**/Android.bp` → PUSH_SINGLE
8. `others/usb-verify/**` → PUSH_SINGLE
9. 纯 `.md/.yaml/docs` → SKIP
10. 多分区同时命中 → FLASH_FULL

- [ ] **Step 1: 创建 decider.py**

```python
"""DeployDecider: git diff 内容分析 → 决策 DeployPlan。"""
from __future__ import annotations

import subprocess
from pathlib import Path
from loop_deploy.models import DeployMode, DeployPlan, DeployTarget


# 内核改动 → DD_BOOT_REBOOT
_KERNEL_PATTERNS = [
    "kernel/",  # 所有 kernel/ 下文件
]

# Vendor HAL/Daemon 源码 → PUSH_SINGLE
_PUSH_CPP_PATTERNS = [
    "vendor/lechao/services/lechao_lciod",  # AOSP 中 lciod 源码
]

# .te sepolicy → FLASH_FULL
_TE_PATTERNS = [
    "sepolicy/",
]

# .rc init → DD_BOOT_REBOOT
_RC_PATTERNS = [
    "lechao_lciod",
]

# usb-verify/fault-verify → PUSH_SINGLE（独立 make）
_USB_VERIFY_PATTERNS = [
    "usb-verify",
    "usb-fault-inject",
]

# 纯文档 → SKIP
_SKIP_PATTERNS = [".md", ".yaml", ".txt"]

# DD_BOOT_REBOOT 通用目标
_BOOT_TARGET = DeployTarget(
    artifact_name="boot.img",
    remote_path="/data/local/tmp/boot.img",
    block_device="/dev/block/mmcblk0p1",
)

_HAL_TARGET = DeployTarget(
    artifact_name="lechao_lciod_hal",
    remote_path="/vendor/bin/hw/lechao_lciod_hal",
    service_name="lechao_lciod_hal",
)

_DAEMON_TARGET = DeployTarget(
    artifact_name="lechao_lciod",
    remote_path="/system/bin/lechao_lciod",
    service_name="lechao_lciod",
)


def decide(diff_files: list[str]) -> DeployPlan:
    """分析 diff 文件列表，返回 DeployPlan。

    Args:
        diff_files: git diff --name-only 输出（相对路径列表）

    Returns:
        DeployPlan
    """
    if not diff_files:
        return DeployPlan.skip("no changed files")

    has_kernel = False
    has_te = False
    has_cpp = False
    has_rc = False
    has_usb_verify = False
    all_docs = True

    for f in diff_files:
        f_lower = f.lower()
        if any(p in f for p in _KERNEL_PATTERNS):
            has_kernel = True
            all_docs = False
        if any(p in f_lower for p in _TE_PATTERNS):
            has_te = True
            all_docs = False
        if any(p in f for p in _PUSH_CPP_PATTERNS):
            has_cpp = True
            all_docs = False
        if any(p in f for p in _RC_PATTERNS) and f.endswith(".rc"):
            has_rc = True
            all_docs = False
        if any(p in f for p in _USB_VERIFY_PATTERNS):
            has_usb_verify = True
            all_docs = False
        ext = Path(f).suffix.lower()
        if ext not in _SKIP_PATTERNS and not all_docs:
            pass

    if all_docs:
        return DeployPlan.skip(f"all changed files are docs: {diff_files}")

    # 多类型混合 → FLASH_FULL
    type_count = sum([has_kernel, has_te, has_cpp, has_rc, has_usb_verify])
    if type_count >= 2:
        return DeployPlan.flash_full(diff_files, f"mixed changes: {type_count} types")

    # 单类型判定
    if has_kernel:
        return DeployPlan(
            mode=DeployMode.DD_BOOT_REBOOT,
            changed_files=diff_files,
            reason="kernel driver changes require boot.img rebuild",
            build_targets=["mode_2"],
            deploy_targets=[_BOOT_TARGET],
            requires_reboot=True,
            estimated_seconds=1800,
        )
    if has_rc:
        return DeployPlan(
            mode=DeployMode.DD_BOOT_REBOOT,
            changed_files=diff_files,
            reason="init.rc changes require boot.img rebuild",
            build_targets=["mode_2"],
            deploy_targets=[_BOOT_TARGET],
            requires_reboot=True,
            estimated_seconds=1800,
        )
    if has_te:
        return DeployPlan.flash_full(diff_files, "sepolicy changes require full flash (vendor dd not verified)")

    if has_cpp:
        return DeployPlan(
            mode=DeployMode.PUSH_SINGLE,
            changed_files=diff_files,
            reason="lciod cpp changes: mmm + push binary",
            build_targets=["vendor/lechao/services/lechao_lciod"],
            deploy_targets=[_HAL_TARGET, _DAEMON_TARGET],
            requires_reboot=False,
            estimated_seconds=300,
        )
    if has_usb_verify:
        return DeployPlan(
            mode=DeployMode.PUSH_SINGLE,
            changed_files=diff_files,
            reason="usb-verify tool changes",
            build_targets=["usb-verify"],
            deploy_targets=[DeployTarget(artifact_name="fault-verify", remote_path="/system/bin/fault-verify")],
            requires_reboot=False,
            estimated_seconds=120,
        )

    return DeployPlan.skip(f"no recognized patterns in: {diff_files}")


def get_diff_files(rev: str = "HEAD") -> list[str]:
    """从 git diff 获取变更文件列表。

    Args:
        rev: git diff 基准，如 HEAD 或 HEAD~1

    Returns:
        相对路径列表
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", rev],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"git diff failed: {exc}")
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        raise RuntimeError(f"git diff returned {result.returncode}: {lines[-1] if lines else 'unknown'}")
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]
```

- [ ] **Step 2: 创建 test_decider.py（10 条规则逐条覆盖）**

```python
"""DeployDecider 决策规则单元测试。"""
import pytest
from loop_deploy.decider import decide
from loop_deploy.models import DeployMode


def test_kernel_c_changes_to_dd_boot():
    plan = decide(["kernel/new/vendor/lechao/LcIod/lciod_usbd.c"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT
    assert plan.requires_reboot


def test_kernel_h_changes_to_dd_boot():
    plan = decide(["kernel/new/vendor/lechao/LcIod/lciod_usbd.h"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT


def test_defconfig_changes_to_dd_boot():
    plan = decide(["kernel/modified/arch/arm64/configs/android_rpi5_defconfig.diff"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT


def test_usb_storage_diff_to_dd_boot():
    plan = decide(["kernel/modified/drivers/usb/storage/usb.c.diff"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT


def test_te_changes_to_flash_full():
    plan = decide(["device/brcm/rpi5/sepolicy/lechao_lciod.te"])
    assert plan.mode == DeployMode.FLASH_FULL


def test_rc_changes_to_dd_boot():
    plan = decide(["vendor/lechao/services/lechao_lciod/hal/lechao_lciod_hal.rc"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT


def test_cpp_changes_to_push_single():
    plan = decide(["vendor/lechao/services/lechao_lciod/hal/hal_service.cpp"])
    assert plan.mode == DeployMode.PUSH_SINGLE
    assert not plan.requires_reboot


def test_bp_changes_to_push_single():
    plan = decide(["vendor/lechao/services/lechao_lciod/hal/Android.bp"])
    assert plan.mode == DeployMode.PUSH_SINGLE


def test_usb_verify_changes_to_push_single():
    plan = decide(["others/usb-verify/src/main.c"])
    assert plan.mode == DeployMode.PUSH_SINGLE


def test_md_changes_to_skip():
    plan = decide(["docs/specs/test.md"])
    assert plan.mode == DeployMode.SKIP


def test_empty_list_skip():
    plan = decide([])
    assert plan.mode == DeployMode.SKIP


def test_mixed_changes_to_flash_full():
    plan = decide([
        "kernel/new/vendor/lechao/LcIod/lciod_usbd.c",
        "vendor/lechao/services/lechao_lciod/hal/hal_service.cpp",
    ])
    assert plan.mode == DeployMode.FLASH_FULL
```

- [ ] **Step 3: 运行 decider 测试**

```bash
PYTHONPATH="engineering/loop/deploy/python:engineering/loop/core/python" \
  python3 -m pytest engineering/loop/deploy/python/tests/test_decider.py -v
```
Expected: 12 PASS。

- [ ] **Step 4: Commit**

```bash
git add engineering/loop/deploy/python/loop_deploy/decider.py \
        engineering/loop/deploy/python/tests/test_decider.py
git commit -m "feat(loop_deploy): DeployDecider——git diff 10 规则判定 DeployMode"
```

---

## Task 5: compiler.py（调 mk_rpi5_full_image.sh / mmm）

**Files:**
- Create: `engineering/loop/deploy/python/loop_deploy/compiler.py`
- Create: `engineering/loop/deploy/python/tests/test_compiler.py`

- [ ] **Step 1: 创建 compiler.py**

```python
"""Compiler: 调用 mk_rpi5_full_image.sh / mmm 编译产物。"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from loop_deploy.models import DeployPlan, DeployMode, CompileResult


def compile_plan(plan: DeployPlan, workspace_root: str = "") -> CompileResult:
    """根据 DeployPlan 执行编译。

    Args:
        plan: 部署计划
        workspace_root: workspace 根目录（默认从 REPO_ROOT/../workspace 推导）

    Returns:
        CompileResult
    """
    if plan.mode == DeployMode.SKIP:
        return CompileResult(success=True, artifacts=[])
    if plan.mode == DeployMode.FLASH_FULL:
        return CompileResult(success=False, error="FLASH_FULL mode requires manual full image build")

    if not workspace_root:
        workspace_root = _find_workspace_root()

    if plan.mode == DeployMode.DD_BOOT_REBOOT:
        return _compile_dd_boot(plan)
    if plan.mode == DeployMode.PUSH_SINGLE:
        return _compile_push_single(plan, workspace_root)

    return CompileResult(success=False, error=f"unknown mode: {plan.mode}")


def _compile_dd_boot(plan: DeployPlan) -> CompileResult:
    """调用 mk_rpi5_full_image.sh -mode 2 编译 boot.img。"""
    import time
    start = time.time()
    script = Path(__file__).parent.parent.parent.parent.parent.parent / "harness" / "scripts" / "mk_rpi5_full_image.sh"
    cmd = f"bash {script} -mode 2"
    try:
        result = subprocess.run(
            ["bash", "-lc", cmd],
            capture_output=True, text=True, timeout=1800,
        )
    except subprocess.TimeoutExpired:
        return CompileResult(success=False, error="mode 2 compilation timed out (30min)")
    elapsed = time.time() - start
    if result.returncode != 0:
        return CompileResult(success=False, error=f"mode 2 failed (exit {result.returncode}): {result.stderr[-500:]}")

    # 定位 boot.img
    aosp_out = os.environ.get("ANDROID_PRODUCT_OUT", os.path.expanduser("~/workspace/aosp/out/target/product/rpi5"))
    boot_img = os.path.join(aosp_out, "boot.img")
    if not os.path.isfile(boot_img):
        return CompileResult(success=False, error=f"boot.img not found at {boot_img}")
    return CompileResult(success=True, artifacts=[boot_img], elapsed_seconds=elapsed)


def _compile_push_single(plan: DeployPlan, workspace_root: str) -> CompileResult:
    """mmm 单模块编译。"""
    import time
    start = time.time()
    build_target = plan.build_targets[0] if plan.build_targets else ""
    # mmm 编译目标模块
    cmd = (
        f"cd {workspace_root} && "
        f"source build/envsetup.sh 2>/dev/null && "
        f"lunch aosp_rpi5-bp1a-userdebug 2>/dev/null && "
        f"mmm {build_target} -j$(nproc)"
    )
    try:
        result = subprocess.run(
            ["bash", "-lc", cmd],
            capture_output=True, text=True, timeout=600,
        )
    except subprocess.TimeoutExpired:
        return CompileResult(success=False, error="mmm compilation timed out (10min)")
    elapsed = time.time() - start
    if result.returncode != 0:
        return CompileResult(success=False, error=f"mmm failed (exit {result.returncode}): {result.stderr[-500:]}")
    return CompileResult(success=True, artifacts=[], elapsed_seconds=elapsed)


def _find_workspace_root() -> str:
    return os.environ.get("AOSP_ROOT", os.path.expanduser("~/workspace/aosp"))
```

- [ ] **Step 2: 创建 test_compiler.py（skip + flash_full 逻辑测试，不依赖真实环境）**

```python
"""compiler 单元测试（skip/flash_full 逻辑）。"""
from loop_deploy.models import DeployPlan, DeployMode
from loop_deploy.compiler import compile_plan


def test_skip_returns_empty():
    plan = DeployPlan.skip("no changes")
    result = compile_plan(plan)
    assert result.success
    assert result.artifacts == []


def test_flash_full_returns_error():
    plan = DeployPlan.flash_full(["foo.te"])
    result = compile_plan(plan)
    assert not result.success
    assert "FLASH_FULL" in result.error
```

- [ ] **Step 3: 运行测试**

```bash
PYTHONPATH="engineering/loop/deploy/python" \
  python3 -m pytest engineering/loop/deploy/python/tests/test_compiler.py -v
```
Expected: 2 PASS。

- [ ] **Step 4: Commit**

```bash
git add engineering/loop/deploy/python/loop_deploy/compiler.py \
        engineering/loop/deploy/python/tests/test_compiler.py
git commit -m "feat(loop_deploy): Compiler——调 mk_rpi5_full_image.sh mode2 / mmm 编译"
```

---

## Task 6: deployer.py（push_single / dd_boot_reboot）

**Files:**
- Create: `engineering/loop/deploy/python/loop_deploy/deployer.py`
- Create: `engineering/loop/deploy/python/tests/test_deployer.py`

- [ ] **Step 1: 创建 deployer.py**

```python
"""Deployer: push_single / dd_boot_reboot 部署执行。"""
from __future__ import annotations

import time
from loop_adb.client import AdbClient
from loop_deploy.adb_ops import AdbOps
from loop_deploy.models import DeployPlan, DeployMode, DeployResult


class Deployer:
    def __init__(self, client: AdbClient, aosp_out: str = ""):
        self._client = client
        self._ops = AdbOps(client)
        self._aosp_out = aosp_out

    def deploy(self, plan: DeployPlan, artifacts: list[str]) -> DeployResult:
        """执行部署计划。

        Args:
            plan: 部署计划
            artifacts: compile 阶段产出的本地文件路径列表
        """
        if plan.mode == DeployMode.SKIP:
            return DeployResult(success=True, mode=DeployMode.SKIP)
        if plan.mode == DeployMode.FLASH_FULL:
            return DeployResult(success=False, mode=DeployMode.FLASH_FULL,
                                error="FLASH_FULL requires manual full image flash")
        if plan.mode == DeployMode.PUSH_SINGLE:
            return self._deploy_push_single(plan, artifacts)
        if plan.mode == DeployMode.DD_BOOT_REBOOT:
            return self._deploy_dd_boot(artifacts)
        return DeployResult(success=False, mode=plan.mode, error=f"unknown mode: {plan.mode}")

    def _deploy_push_single(self, plan: DeployPlan, artifacts: list[str]) -> DeployResult:
        start = time.time()
        # 1. adb root + remount
        root_r = self._client.root(timeout_sec=10.0)
        if root_r.exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                error=f"adb root failed: {root_r.stderr}", duration_seconds=time.time()-start)
        remount_r = self._client.remount(timeout_sec=15.0)
        if remount_r.exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                error=f"adb remount failed: {remount_r.stderr}", duration_seconds=time.time()-start)

        # 2. 定位编译产物并 push
        for target in plan.deploy_targets:
            if not target.artifact_name:
                continue
            local_path = self._find_artifact(artifacts, target.artifact_name)
            if not local_path:
                return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                    error=f"artifact {target.artifact_name} not found", duration_seconds=time.time()-start)
            push_r = self._client.push(local_path, target.remote_path, timeout_sec=30.0)
            if push_r.exit_code != 0:
                return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                    error=f"adb push failed: {push_r.stderr}", duration_seconds=time.time()-start)
            # restart service
            if target.service_name:
                self._client.shell(f"setprop ctl.restart {target.service_name}", timeout_sec=5.0)
                if not self._ops.wait_service_running(target.service_name, timeout=15.0):
                    return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                        error=f"service {target.service_name} did not start", duration_seconds=time.time()-start)

        return DeployResult(success=True, mode=DeployMode.PUSH_SINGLE, duration_seconds=time.time()-start)

    def _deploy_dd_boot(self, artifacts: list[str]) -> DeployResult:
        start = time.time()
        boot_img = None
        for a in artifacts:
            if a.endswith("boot.img"):
                boot_img = a
                break
        if not boot_img:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error="boot.img not found in artifacts", duration_seconds=time.time()-start)

        # 1. adb root
        self._client.root(timeout_sec=10.0)

        # 2. push boot.img
        remote = "/data/local/tmp/boot.img"
        push_r = self._client.push(boot_img, remote, timeout_sec=60.0)
        if push_r.exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"adb push boot.img failed: {push_r.stderr}")

        # 3. sha256 校验（host 侧 vs 设备侧）
        import hashlib
        host_sha = hashlib.sha256(open(boot_img, "rb").read()).hexdigest()
        sha_result = self._client.shell(f"sha256sum {remote}", timeout_sec=10.0)
        remote_sha = sha_result.raw_stdout.strip().split()[0] if sha_result.command_exit_code == 0 else ""
        if host_sha != remote_sha:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"sha256 mismatch: host={host_sha[:16]}... remote={remote_sha[:16]}...")

        # 4. dd + sync
        self._client.shell("dd if=/data/local/tmp/boot.img of=/dev/block/mmcblk0p1 bs=4M", timeout_sec=30.0, as_root=True)
        self._client.shell("sync", timeout_sec=10.0, as_root=True)
        self._client.shell(f"rm {remote}", timeout_sec=5.0, as_root=True)

        # 5. reboot
        self._client.reboot(timeout_sec=15.0)

        # 6. 等待 boot_completed
        time.sleep(5)
        if not self._ops.wait_boot_completed(timeout=120.0):
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error="boot_completed not reached after reboot")

        # 7. 重连 adb
        self._client.connect(timeout_sec=15.0)
        return DeployResult(success=True, mode=DeployMode.DD_BOOT_REBOOT, requires_reboot=True,
                            duration_seconds=time.time()-start)

    def _find_artifact(self, artifacts: list[str], name: str) -> str:
        for a in artifacts:
            if a.endswith(name) or name in a:
                return a
        return aosp_fallback = os.path.join(self._aosp_out or "",
                                              "vendor/bin/hw" if "hal" in name else "system/bin",
                                              name)
        # fallback to aosp output dir
        from pathlib import Path
        for root, dirs, files in Path(self._aosp_out or ".").walk():
            for f in files:
                if f == name:
                    return str(Path(root) / f)
        return ""
```

- [ ] **Step 2: 创建 test_deployer.py（skip + flash_full 逻辑测试，mock adb）**

```python
"""deployer 单元测试（skip/flash_full 逻辑）。"""
from loop_deploy.models import DeployPlan, DeployMode
from loop_deploy.deployer import Deployer


def test_skip_returns_success():
    import loop_adb.client as mod
    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555")
    d = Deployer(client)
    plan = DeployPlan.skip("no changes")
    result = d.deploy(plan, [])
    assert result.success
    assert result.mode == DeployMode.SKIP


def test_flash_full_returns_error():
    import loop_adb.client as mod
    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555")
    d = Deployer(client)
    plan = DeployPlan.flash_full(["foo.te"])
    result = d.deploy(plan, [])
    assert not result.success
    assert "FLASH_FULL" in result.error
```

- [ ] **Step 3: 运行测试**

```bash
PYTHONPATH="engineering/loop/deploy/python:engineering/loop/connection/providers/adb/python:engineering/loop/core/python:engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/deploy/python/tests/test_deployer.py -v
```
Expected: 2 PASS（skip + flash_full）。

- [ ] **Step 4: Commit**

```bash
git add engineering/loop/deploy/python/loop_deploy/deployer.py \
        engineering/loop/deploy/python/tests/test_deployer.py
git commit -m "feat(loop_deploy): Deployer——push_single + dd_boot_reboot 部署流程"
```

---

## Task 7: deploy cli.py（le deploy 子命令）

**Files:**
- Create: `engineering/loop/deploy/python/loop_deploy/cli.py`
- Create: `engineering/loop/deploy/python/tests/test_cli.py`

- [ ] **Step 1: 创建 cli.py**

```python
"""loop_deploy CLI：le deploy 子命令逻辑。"""
from __future__ import annotations

import argparse
import sys
from loop_deploy.decider import decide, get_diff_files
from loop_deploy.compiler import compile_plan
from loop_deploy.deployer import Deployer
from loop_deploy.models import DeployMode, DeployPlan
from loop_adb.client import AdbClient


_DEPLOY_MODES = [m.value for m in DeployMode]


def add_deploy_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("deploy", help="部署 binary/image 到设备")
    p.add_argument("--decide", action="store_true", help="仅输出决策不执行（dry-run）")
    p.add_argument("--diff-rev", default="HEAD", help="git diff 基准（默认 HEAD）")
    p.add_argument("--mode", choices=_DEPLOY_MODES, help="强制指定部署模式（跳过决策器）")
    p.add_argument("--artifact", help="手动指定编译产物路径")
    p.add_argument("--remote", help="手动指定远程推送路径")
    p.add_argument("--service", default="", help="手动指定 restart 的服务名")
    p.add_argument("--adb-endpoint", default="", help="adb endpoint，如 192.168.1.55:5555")
    p.add_argument("--adb-serial", default="", help="adb device serial")
    p.set_defaults(func=_handle_deploy)


def _handle_deploy(args: argparse.Namespace) -> int:
    if args.mode:
        mode = DeployMode(args.mode)
        plan = DeployPlan(mode=mode, reason="manual mode override")
    else:
        diff_files = get_diff_files(args.diff_rev)
        plan = decide(diff_files)

    # dry-run
    if args.decide:
        print(f"mode={plan.mode.value}")
        print(f"changed={plan.changed_files}")
        print(f"reason={plan.reason}")
        print(f"build_targets={plan.build_targets}")
        print(f"reboot={plan.requires_reboot}")
        return 0

    # FLASH_FULL 需要人工
    if plan.mode == DeployMode.FLASH_FULL:
        print(f"ERROR: FLASH_FULL required: {plan.reason}", file=sys.stderr)
        print("Please manually build full image and flash SD card.", file=sys.stderr)
        return 1

    # SKIP
    if plan.mode == DeployMode.SKIP:
        print(f"SKIP: {plan.reason}")
        return 0

    # 编译
    artifacts = [args.artifact] if args.artifact else []
    if not args.artifact:
        compile_result = compile_plan(plan)
        if not compile_result.success:
            print(f"COMPILE FAILED: {compile_result.error}", file=sys.stderr)
            return 1
        artifacts = compile_result.artifacts
        if not artifacts and plan.mode == DeployMode.PUSH_SINGLE:
            artifacts = [args.remote] if args.remote else []

    # 部署
    endpoint = args.adb_endpoint or "192.168.1.55:5555"
    serial = args.adb_serial or endpoint
    client = AdbClient(endpoint, serial)
    deployer = Deployer(client)
    result = deployer.deploy(plan, artifacts)

    if result.success:
        print(f"DEPLOY OK: mode={result.mode.value} duration={result.duration_seconds:.1f}s reboot={result.requires_reboot}")
        return 0
    else:
        print(f"DEPLOY FAILED: {result.error}", file=sys.stderr)
        return 1
```

- [ ] **Step 2: 创建 test_cli.py**

```python
"""deploy cli 单元测试（decide dry-run）。"""
import argparse
import pytest
from loop_deploy.cli import add_deploy_parser


class _Args:
    pass


def test_dry_run_decide():
    parser = argparse.ArgumentParser()
    add_deploy_parser(parser.add_subparsers(dest="cmd"))
    args = parser.parse_args(["deploy", "--decide", "--diff-rev", "HEAD"])
    assert args.decide is True
    assert args.diff_rev == "HEAD"
```

- [ ] **Step 3: 运行测试**

```bash
PYTHONPATH="engineering/loop/deploy/python:engineering/loop/connection/providers/adb/python:engineering/loop/core/python:engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/deploy/python/tests/test_cli.py -v
```
Expected: 1 PASS。

- [ ] **Step 4: Commit**

```bash
git add engineering/loop/deploy/python/loop_deploy/cli.py \
        engineering/loop/deploy/python/tests/test_cli.py
git commit -m "feat(loop_deploy): CLI——le deploy 子命令（decide/build/deploy）"
```

---

## Task 8: loop_core/cli.py 接入 deploy 子命令

**Files:**
- Modify: `engineering/loop/core/python/loop_core/cli.py`

- [ ] **Step 1: 接入 deploy 子命令**

将第 83 行的 `sub.add_parser("deploy", ...)` 占位替换为从 loop_deploy.cli 导入：

```python
    # deploy 子命令（loop_deploy 实现）
    try:
        from loop_deploy.cli import add_deploy_parser
        add_deploy_parser(sub)
    except ImportError:
        sub.add_parser("deploy", help="部署 binary/image（loop_deploy 模块不可用）")
```

将第 91-94 行的 deploy 处理替换为：

```python
    if args.command == "deploy":
        return args.func(args)
```

- [ ] **Step 2: 验证 cli deploy 可用**

```bash
PYTHONPATH="engineering/loop/deploy/python:engineering/loop/core/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python" \
  python3 -m loop_core.cli deploy --help
```
Expected: 打印 deploy 子命令帮助。

- [ ] **Step 3: Commit**

```bash
git add engineering/loop/core/python/loop_core/cli.py
git commit -m "feat(loop_core): cli.py 接入 deploy 子命令（loop_deploy）"
```

---

## Task 9: harness-paths.conf 更新 + __init__.py 注册

**Files:**
- Modify: `engineering/harness/config/harness-paths.conf`
- Modify: `engineering/loop/deploy/python/loop_deploy/__init__.py`

- [ ] **Step 1: harness-paths.conf 添加 LOOP_DEPLOY_DIR**

在 `PYTHON_PATH_ROOTS` 后追加 deploy 包路径：

```conf
PYTHON_PATH_ROOTS="engineering/loop/core/python:...:engineering/loop/deploy/python"
```

- [ ] **Step 2: Commit**

```bash
git add engineering/harness/config/harness-paths.conf
git commit -m "config(paths): harness-paths.conf 加入 LOOP_DEPLOY_DIR"
```

---


# P3: loop_controller AI 闭环

## Task 10: analyzer_protocol.py（LlmAnalyzer ABC + 数据模型）

**Files:**
- Create: `engineering/loop/controller/python/loop_controller/analyzer_protocol.py`

- [ ] **Step 1: 创建 analyzer_protocol.py**

```python
"""LlmAnalyzer 抽象接口 + AnalysisRequest / PatchSuggestion 数据模型。

默认实现是主会话本身（不走代码），此接口供未来接 API/子进程扩展。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class AnalysisRequest:
    """controller → 主会话的分析请求（序列化为 JSON 文件交接）。"""
    session_id: str
    attempt_index: int
    failed_cases: list[dict] = field(default_factory=list)
    evidence_bundle_path: str = ""
    collectors_output: dict = field(default_factory=dict)
    workspace_diff_so_far: str = ""
    hints: str = ""


@dataclass
class FileChange:
    workspace_path: str
    change_type: Literal["edit", "create", "delete"] = "edit"
    old_marker: str = ""
    new_content: str = ""


@dataclass
class PatchSuggestion:
    target_files: list[FileChange] = field(default_factory=list)
    rationale: str = ""
    confidence: float = 0.0
    deploy_mode_hint: str = ""


class LlmAnalyzer(ABC):
    """LLM 分析器抽象接口。

    默认实现是主会话本身（不走代码），此接口供未来接 API/子进程扩展。
    P3 阶段有一个 MainSessionAnalyzer stub，其 analyze() 抛出提示。
    """

    @abstractmethod
    def analyze(self, request: AnalysisRequest) -> PatchSuggestion:
        ...
```

- [ ] **Step 2: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/analyzer_protocol.py
git commit -m "feat(loop_controller): LlmAnalyzer ABC + AnalysisRequest/PatchSuggestion 模型"
```

---

## Task 11: patch_applier.py（结构化补丁应用）

**Files:**
- Create: `engineering/loop/controller/python/loop_controller/patch_applier.py`
- Create: `engineering/loop/controller/python/tests/test_patch_applier.py`

- [ ] **Step 1: 创建 patch_applier.py**

```python
"""patch_applier：将 FileChange 列表应用到 workspace 源码。"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from loop_controller.analyzer_protocol import FileChange


@dataclass
class ApplyResult:
    success: bool
    applied_files: list[str] = field(default_factory=list)
    error: str = ""
    git_diff: str = ""


def apply_file_changes(changes: list[FileChange], workspace_root: str) -> ApplyResult:
    """将 FileChange 列表应用到 workspace 源码。

    精确字符串匹配替换（与 Edit 工具语义一致）：
    - 校验 old_marker 在目标文件唯一匹配
    - 替换为 new_content
    - 失败时不部分应用（第一处失败即中止，保留已应用的文件作为 partial result）
    """
    if not changes:
        return ApplyResult(success=True)

    applied = []
    for fc in changes:
        fp = Path(workspace_root) / fc.workspace_path
        if not fp.exists():
            return ApplyResult(success=False, applied_files=applied, error=f"file not found: {fc.workspace_path}")

        content = fp.read_text(encoding="utf-8")
        if fc.change_type == "edit":
            count = content.count(fc.old_marker)
            if count == 0:
                return ApplyResult(success=False, applied_files=applied,
                                   error=f"old_marker not found in {fc.workspace_path}")
            if count > 1:
                return ApplyResult(success=False, applied_files=applied,
                                   error=f"old_marker found {count} times in {fc.workspace_path}, not unique")
            new_content = content.replace(fc.old_marker, fc.new_content, 1)
            fp.write_text(new_content, encoding="utf-8")
        elif fc.change_type == "create":
            fp.parent.mkdir(parents=True, exist_ok=True)
            fp.write_text(fc.new_content, encoding="utf-8")
        elif fc.change_type == "delete":
            fp.unlink()
        applied.append(fc.workspace_path)

    return ApplyResult(success=True, applied_files=applied)
```

- [ ] **Step 2: 创建 test_patch_applier.py**

```python
"""patch_applier 单元测试。"""
import os
from loop_controller.patch_applier import apply_file_changes
from loop_controller.analyzer_protocol import FileChange


def test_edit_unique_marker(tmp_path):
    f = tmp_path / "test.cpp"
    f.write_text("int x = 1;\nint y = 2;\n")
    changes = [FileChange(
        workspace_path="test.cpp",
        change_type="edit",
        old_marker="int x = 1;",
        new_content="int x = 42;",
    )]
    result = apply_file_changes(changes, str(tmp_path))
    assert result.success
    assert "int x = 42;" in f.read_text()


def test_edit_not_found(tmp_path):
    f = tmp_path / "test.cpp"
    f.write_text("int a = 1;\n")
    changes = [FileChange(workspace_path="test.cpp", old_marker="not_there", new_content="x")]
    result = apply_file_changes(changes, str(tmp_path))
    assert not result.success
    assert "not found" in result.error


def test_edit_duplicate_marker(tmp_path):
    f = tmp_path / "test.cpp"
    f.write_text("dup\ndup\n")
    changes = [FileChange(workspace_path="test.cpp", old_marker="dup", new_content="fixed")]
    result = apply_file_changes(changes, str(tmp_path))
    assert not result.success
    assert "2 times" in result.error


def test_create_file(tmp_path):
    changes = [FileChange(workspace_path="new.txt", change_type="create", new_content="hello")]
    result = apply_file_changes(changes, str(tmp_path))
    assert result.success
    assert (tmp_path / "new.txt").read_text() == "hello"


def test_empty_changes(tmp_path):
    result = apply_file_changes([], str(tmp_path))
    assert result.success
```

- [ ] **Step 3: 运行测试**

```bash
PYTHONPATH="engineering/loop/controller/python:engineering/loop/contracts/python:engineering/loop/core/python" \
  python3 -m pytest engineering/loop/controller/python/tests/test_patch_applier.py -v
```
Expected: 5 PASS。

- [ ] **Step 4: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/patch_applier.py \
        engineering/loop/controller/python/tests/test_patch_applier.py
git commit -m "feat(loop_controller): PatchApplier——精确字符串匹配替换到 workspace"
```

---

## Task 12: cycle_orchestrator.py（编排辅助）

**Files:**
- Create: `engineering/loop/controller/python/loop_controller/cycle_orchestrator.py`

- [ ] **Step 1: 创建 cycle_orchestrator.py**

```python
"""cycle_orchestrator：分阶段编排辅助，被 control_cli 和主会话调用。"""
from __future__ import annotations

import json
from pathlib import Path
from loop_contracts.models import SessionState, StageResult, TerminationDecision
from loop_contracts.failure_codes import FailureCode
from loop_controller.policy import decide_termination
from loop_controller.engine import apply_stage_result
from loop_controller.analyzer_protocol import AnalysisRequest


def build_analysis_request(
    session: SessionState,
    evidence_bundle_path: str,
    workspace_diff: str = "",
) -> AnalysisRequest:
    """从当前 attempt 构造分析请求。"""
    last_attempt = session.attempts[-1] if session.attempts else None
    attempt_index = session.current_attempt
    failed_cases = []
    collectors_output = {}

    # 从 evidence_bundle.json 提取失败 case
    if evidence_bundle_path and Path(evidence_bundle_path).exists():
        bundle = json.loads(Path(evidence_bundle_path).read_text(encoding="utf-8"))
        for case in bundle.get("cases", []):
            if case.get("status") in ("fail", "error"):
                failed_cases.append({
                    "id": case.get("id", ""),
                    "status": case.get("status", ""),
                    "failure_reason": case.get("failure_reason", ""),
                    "command": case.get("command", ""),
                })
        collectors_output = bundle.get("evidence", {})

    return AnalysisRequest(
        session_id=session.session_id,
        attempt_index=attempt_index,
        failed_cases=failed_cases,
        evidence_bundle_path=evidence_bundle_path,
        collectors_output=collectors_output,
        workspace_diff_so_far=workspace_diff,
        hints=f"Attempt {attempt_index}/{session.max_attempts}. "
              f"Failed cases: {len(failed_cases)}. Check on_fail collectors for diagnostics.",
    )


def record_stage(session: SessionState, stage_name: str, status: str,
                 summary: str = "", failure_code: FailureCode = FailureCode.NONE) -> SessionState:
    """记录一个阶段结果。"""
    stage = StageResult(stage_name=stage_name, status=status,
                        failure_code=failure_code, summary=summary)
    decision = "pending"
    return apply_stage_result(session, attempt_index=session.current_attempt,
                              stage_result=stage, decision=decision)


def decide_next_from_session(session: SessionState) -> TerminationDecision:
    """从 session 判定下一步。"""
    latest = session.attempts[-1].stage_results[-1] if session.attempts and session.attempts[-1].stage_results else None
    prev_codes = [r.failure_code for attempt in session.attempts for r in attempt.stage_results if r.failure_code != FailureCode.NONE]
    return decide_termination(
        max_attempts=session.max_attempts,
        current_attempt=session.current_attempt,
        latest_stage=latest or StageResult(stage_name="unknown", status="fail", failure_code=FailureCode.RUN_FAILED),
        previous_failure_codes=prev_codes,
    )
```

- [ ] **Step 2: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/cycle_orchestrator.py
git commit -m "feat(loop_controller): cycle_orchestrator——分阶段编排辅助"
```

---

## Task 13: control_cli.py（le control 子命令）

**Files:**
- Create: `engineering/loop/controller/python/loop_controller/control_cli.py`
- Create: `engineering/loop/controller/python/tests/test_control_cli.py`

- [ ] **Step 1: 创建 control_cli.py**

```python
"""control_cli：le control 子命令——session 初始化/验证/分析/部署/判定。"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from loop_controller.analyzer_protocol import AnalysisRequest
from loop_controller.cycle_orchestrator import (
    build_analysis_request, record_stage, decide_next_from_session,
)
from loop_controller.state import new_session
from loop_contracts.failure_codes import FailureCode


_CONTROL_SESSION_FILE = os.environ.get("LE_CONTROL_SESSION_FILE", "")


def add_control_parser(subparsers: argparse._SubParsersAction) -> None:
    p = subparsers.add_parser("control", help="AI 闭环控制——session 管理 + 分阶段编排")
    sub_c = p.add_subparsers(dest="control_cmd", required=True)

    # init
    i = sub_c.add_parser("init", help="初始化 session")
    i.add_argument("--target", default="lciod")
    i.add_argument("--max-attempts", type=int, default=5)
    i.add_argument("--artifacts-dir", required=True)
    i.set_defaults(func=_handle_control_init)

    # run-verify
    rv = sub_c.add_parser("run-verify", help="执行一次验证")
    rv.add_argument("--session", required=True)
    rv.add_argument("--suite", required=True)
    rv.add_argument("--adb-endpoint", default="")
    rv.set_defaults(func=_handle_control_run_verify)

    # analyze-request
    ar = sub_c.add_parser("analyze-request", help="生成 analysis_request.json")
    ar.add_argument("--session", required=True)
    ar.set_defaults(func=_handle_control_analyze_request)

    # deploy
    dp = sub_c.add_parser("deploy", help="部署当前改动")
    dp.add_argument("--session", required=True)
    dp.add_argument("--adb-endpoint", default="")
    dp.set_defaults(func=_handle_control_deploy)

    # decide
    dc = sub_c.add_parser("decide", help="判定下一步")
    dc.add_argument("--session", required=True)
    dc.set_defaults(func=_handle_control_decide)

    # status
    st = sub_c.add_parser("status", help="查看 session 状态")
    st.add_argument("--session", required=True)
    st.set_defaults(func=_handle_control_status)


def _load_session(session_id: str) -> dict:
    artifacts_dir = os.path.dirname(session_id) if os.path.isfile(session_id) else session_id
    session_file = os.path.join(artifacts_dir, f"{os.path.basename(session_id)}.json") if os.path.isdir(session_id) else session_id
    if os.path.isfile(session_file):
        return json.loads(Path(session_file).read_text(encoding="utf-8"))
    return {}


def _save_session(session: dict, artifacts_dir: str):
    sid = session.get("session_id", "unknown")
    p = Path(artifacts_dir) / f"{sid}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8")


def _handle_control_init(args: argparse.Namespace) -> int:
    sid = f"{args.target}-{__import__('time').strftime('%Y%m%d%H%M%S')}"
    session = {
        "session_id": sid,
        "workflow_id": f"{args.target}-verify",
        "target": args.target,
        "max_attempts": args.max_attempts,
        "current_attempt": 0,
        "status": "PENDING",
        "attempts": [],
        "artifacts_dir": args.artifacts_dir,
    }
    _save_session(session, args.artifacts_dir)
    print(f"session_id={sid}")
    print(f"artifacts_dir={args.artifacts_dir}")
    return 0


def _handle_control_run_verify(args: argparse.Namespace) -> int:
    import subprocess
    session_data = _load_session(args.session)
    artifacts_dir = session_data.get("artifacts_dir", os.path.dirname(args.session))
    sid = session_data.get("session_id", os.path.basename(args.session))

    evidence_path = os.path.join(artifacts_dir, f"evidence_{session_data.get('current_attempt', 0) + 1}.json")
    cmd = [
        sys.executable, "-m", "loop_core.cli", "run",
        "--suite", args.suite,
        "--case-dirs", "engineering/loop/cases",
        "--device-profile", "engineering/loop/connection/profiles/devices/rp5/adb.json",
        "--artifacts-dir", artifacts_dir,
    ]
    if args.adb_endpoint:
        cmd += ["--adb-endpoint", args.adb_endpoint]
    try:
        result = subprocess.run(cmd, capture_output=False, timeout=600)
        rc = result.returncode
    except subprocess.TimeoutExpired:
        rc = 1

    status = "PASS" if rc == 0 else "FAIL"
    session_data["current_attempt"] += 1
    session_data["status"] = status
    session_data["attempts"].append({
        "attempt_index": session_data["current_attempt"],
        "verify_result": status,
        "evidence_path": evidence_path,
    })
    _save_session(session_data, artifacts_dir)
    print(f"verify={status} attempt={session_data['current_attempt']}")
    return rc


def _handle_control_analyze_request(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    artifacts_dir = session_data.get("artifacts_dir", os.path.dirname(args.session))
    request = AnalysisRequest(
        session_id=session_data.get("session_id", ""),
        attempt_index=session_data.get("current_attempt", 0),
        failed_cases=session_data.get("attempts", [{}])[-1].get("failed_cases", []),
    )
    req_path = os.path.join(artifacts_dir, "analysis_request.json")
    import dataclasses
    Path(req_path).write_text(json.dumps(dataclasses.asdict(request), indent=2, ensure_ascii=False))
    print(f"analysis_request={req_path}")
    return 0


def _handle_control_deploy(args: argparse.Namespace) -> int:
    import subprocess
    session_data = _load_session(args.session)
    cmd = [sys.executable, "-m", "loop_core.cli", "deploy", "--diff-rev", "HEAD"]
    if args.adb_endpoint:
        cmd += ["--adb-endpoint", args.adb_endpoint]
    result = subprocess.run(cmd, timeout=3600)
    return result.returncode


def _handle_control_decide(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    # 简化判定：有 FAIL 且未超次数则 RETRY
    status = session_data.get("status", "PENDING")
    current = session_data.get("current_attempt", 0)
    max_att = session_data.get("max_attempts", 5)
    if status == "PASS":
        print("decision=STOP reason=verification_passed")
    elif current >= max_att:
        print("decision=STOP reason=max_attempts_exceeded should_escalate=true")
    else:
        print(f"decision=RETRY attempt={current}/{max_att}")
    return 0


def _handle_control_status(args: argparse.Namespace) -> int:
    session_data = _load_session(args.session)
    print(json.dumps(session_data, indent=2, ensure_ascii=False))
    return 0
```

- [ ] **Step 2: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/control_cli.py
git commit -m "feat(loop_controller): control_cli——le control init/run-verify/analyze-request/deploy/decide/status"
```

---

## Task 14: loop_core/cli.py 接入 control 子命令

**Files:**
- Modify: `engineering/loop/core/python/loop_core/cli.py`

- [ ] **Step 1: 接入 control 子命令**

在 gen-cases 占位后添加：

```python
    # control 子命令（loop_controller 实现）
    try:
        from loop_controller.control_cli import add_control_parser
        add_control_parser(sub)
    except ImportError:
        sub.add_parser("control", help="AI 闭环控制（loop_controller 模块不可用）")
```

在 run 和 deploy 处理后添加：

```python
    if args.command == "control":
        return args.func(args)
```

- [ ] **Step 2: 验证 cli control 可用**

```bash
PYTHONPATH="engineering/loop/controller/python:engineering/loop/deploy/python:engineering/loop/core/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python" \
  python3 -m loop_core.cli control --help
```
Expected: 打印 control 子命令帮助（含 init/run-verify/analyze-request/deploy/decide/status）。

- [ ] **Step 3: Commit**

```bash
git add engineering/loop/core/python/loop_core/cli.py
git commit -m "feat(loop_core): cli.py 接入 control 子命令（loop_controller）"
```

---

## Task 15: __init__.py 导出更新

**Files:**
- Modify: `engineering/loop/controller/python/loop_controller/__init__.py`

- [ ] **Step 1: 更新导出**

```python
from loop_controller.state import new_session
from loop_controller.engine import apply_stage_result
from loop_controller.policy import decide_termination
from loop_controller.analyzer_protocol import LlmAnalyzer, AnalysisRequest, PatchSuggestion, FileChange
from loop_controller.patch_applier import apply_file_changes, ApplyResult
from loop_controller.cycle_orchestrator import build_analysis_request, record_stage, decide_next_from_session
from loop_controller.control_cli import add_control_parser

__all__ = [
    "new_session", "apply_stage_result", "decide_termination",
    "LlmAnalyzer", "AnalysisRequest", "PatchSuggestion", "FileChange",
    "apply_file_changes", "ApplyResult",
    "build_analysis_request", "record_stage", "decide_next_from_session",
    "add_control_parser",
]
```

- [ ] **Step 2: Commit**

```bash
git add engineering/loop/controller/python/loop_controller/__init__.py
git commit -m "feat(loop_controller): __init__.py 导出 P3 新增模块"
```

---

## Task 16: apply_preset_bugs.sh（预设 bug 注入脚本）

**Files:**
- Create: `engineering/harness/scripts/apply_preset_bugs.sh`
- Root: `~/workspace/aosp/vendor/lechao/services/lechao_lciod/`

**预设 bug 设计（基于 patchs 源码分析）**：

- **Bug 1: HAL getStats 字段反转**（hal_service.cpp:183-186）
  - 正确：`_aidl_return->readBytes = raw.read_bytes;` `/ `_aidl_return->writeBytes = raw.write_bytes;`
  - Bug：交换 readBytes/writeBytes 的来源
  - 修复方式：mmm + push lechao_lciod_hal + restart
- **Bug 2: Daemon getAverageRate 公式错误**（service.cpp:103）
  - 正确：`total * 1000000000ULL / totalNs`（bytes × 1e9 / ns = bytes/sec）
  - Bug：`totalNs * 1000000000ULL / total`（分子分母颠倒）
  - 修复方式：mmm + push lechao_lciod + restart
- **Bug 3: HAL readEvent 排空遗漏**（device_io.cpp:159-169）
  - 正确：while 循环 read 排空
  - Bug：移除排空循环，只 read 一次

**注意**：apply_preset_bugs.sh 是 P3 的核心验证工具，用于向 workspace 注入临时 bug。演练后需 `git checkout` 恢复。

- [ ] **Step 1: 创建 apply_preset_bugs.sh**

```bash
#!/bin/bash
# ============================================================================
# apply_preset_bugs.sh — 向 workspace 注入 3 个预设 bug，验证 AI 闭环能力
#
# 用法:
#   apply_preset_bugs.sh --bug 1      # 仅 Bug 1
#   apply_preset_bugs.sh --bug 1,2,3  # 全部 3 个
#   apply_preset_bugs.sh --revert     # 回滚所有 bug
#
# 依赖: harness_bootstrap.sh（路径 + observability）
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/../lib/shell/harness_bootstrap.sh"
harness_init --with-errexit "apply_preset_bugs"

AOSP_WS="${AOSP_WS:-$(harness_env_path ENV_AOSP_WS)}"
LCIOD_HAL="${AOSP_WS}/vendor/lechao/services/lechao_lciod/hal/hal_service.cpp"
LCIOD_DAEMON="${AOSP_WS}/vendor/lechao/services/lechao_lciod/daemon/service.cpp"
LCIOD_DEVIO="${AOSP_WS}/vendor/lechao/services/lechao_lciod/hal/device_io.cpp"
BACKUP_DIR="${AOSP_WS}/.lciod_bug_backup_$(date +%Y%m%d%H%M%S)"

apply_bug() {
    local bug_num="$1"
    step_begin "apply_bug_${bug_num}"
    case "$bug_num" in
        1)
            log_info "Bug 1: HAL getStats read_bytes/write_bytes 字段反转"
            cp "$LCIOD_HAL" "$BACKUP_DIR/hal_service.cpp.bak"
            # 交换第 183 行和第 186 行的原始字段来源
            sed -i 's/_aidl_return->readBytes = raw\.read_bytes;/_aidl_return->readBytes = raw.TEMP_placeholder;/' "$LCIOD_HAL"
            sed -i 's/_aidl_return->writeBytes = raw\.write_bytes;/_aidl_return->writeBytes = raw.read_bytes;/' "$LCIOD_HAL"
            sed -i 's/_aidl_return->readBytes = raw\.TEMP_placeholder;/_aidl_return->readBytes = raw.write_bytes;/' "$LCIOD_HAL"
            log_info "Bug 1 applied: hal_service.cpp readBytes/writeBytes reversed"
            ;;
        2)
            log_info "Bug 2: Daemon getAverageRate 公式分子分母颠倒"
            cp "$LCIOD_DAEMON" "$BACKUP_DIR/service.cpp.bak"
            sed -i 's/_aidl_return = static_cast<int64_t>(total \* 1000000000ULL \/ totalNs);/_aidl_return = static_cast<int64_t>(totalNs \* 1000000000ULL \/ total);/' "$LCIOD_DAEMON"
            log_info "Bug 2 applied: service.cpp getAverageRate formula reversed"
            ;;
        3)
            log_info "Bug 3: HAL readEvent 排空循环移除"
            cp "$LCIOD_DEVIO" "$BACKUP_DIR/device_io.cpp.bak"
            # 将 while 循环读排空改为只读一次
            # 匹配: while ((n = read(fd, &tmp, sizeof(tmp))) == (ssize_t)sizeof(tmp)) {
            sed -i '/while ((n = read(fd, &tmp, sizeof(tmp))) == (ssize_t)sizeof(tmp)) {/,/}/{
                s/while ((n = read(fd, &tmp, sizeof(tmp))) == (ssize_t)sizeof(tmp)) {/n = read(fd, \&tmp, sizeof(tmp));/
                s/\*event = tmp;//
                s/count++;//
                s/ret = poll(\&pfd, 1, 0);/ret = 0;/
                s/if (ret <= 0)//
                s/break;//
            }' "$LCIOD_DEVIO"
            log_info "Bug 3 applied: device_io.cpp read_event drain loop removed"
            ;;
        *)
            log_error "Unknown bug number: $bug_num (valid: 1,2,3)"
            return 1
            ;;
    esac
    step_end "apply_bug_${bug_num}"
}

revert_bugs() {
    step_begin "revert_bugs"
    if [[ -z "${BACKUP_DIR:-}" ]] || [[ ! -d "$BACKUP_DIR" ]]; then
        log_error "No backup directory found. Cannot revert."
        return 1
    fi
    for bak in "$BACKUP_DIR"/*.bak; do
        local dest="${bak%.bak}"
        dest="${dest/$BACKUP_DIR\//}"
        case "$dest" in
            "hal_service.cpp") cp "$bak" "$LCIOD_HAL" ;;
            "service.cpp") cp "$bak" "$LCIOD_DAEMON" ;;
            "device_io.cpp") cp "$bak" "$LCIOD_DEVIO" ;;
        esac
    done
    log_info "Reverted all bugs from $BACKUP_DIR"
    step_end "revert_bugs"
}

# ---- main ----
BUGS=""
DO_REVERT=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --bug) BUGS="$2"; shift 2 ;;
        --revert) DO_REVERT=true; shift ;;
        *) log_error "Unknown arg: $1"; harness_exit 3 ;;
    esac
done

if $DO_REVERT; then
    revert_bugs
    harness_exit 0
fi

if [[ -z "$BUGS" ]]; then
    log_error "--bug is required (e.g. --bug 1 or --bug 1,2,3)"
    harness_exit 3
fi

mkdir -p "$BACKUP_DIR"
log_info "Backup directory: $BACKUP_DIR"

IFS=',' read -ra BUG_ARRAY <<< "$BUGS"
for b in "${BUG_ARRAY[@]}"; do
    b_trimmed="$(echo "$b" | xargs)"
    apply_bug "$b_trimmed"
done

log_info "Bugs applied. To revert: $0 --revert"
harness_exit 0
```

- [ ] **Step 2: Commit**

```bash
chmod +x engineering/harness/scripts/apply_preset_bugs.sh
git add engineering/harness/scripts/apply_preset_bugs.sh
git commit -m "feat(harness): apply_preset_bugs.sh——向 workspace 注入 3 个预设 bug 用于 AI 闭环验证"
```

---

## Task 17: 更新 WORKFLOW.md 登记 depoy + control 子命令

**Files:**
- Modify: `engineering/loop/WORKFLOW.md`

- [ ] **Step 1: 新增加 deploy + control 子命令章节**

在 WORKFLOW.md 的 CLI 部分追加：

```markdown
### le deploy 子命令

`le deploy` 提供 git diff 决策 + 编译 + 部署能力：

```bash
# dry-run 查看决策
le deploy --decide --diff-rev HEAD

# 执行部署
le deploy --diff-rev HEAD --adb-endpoint 192.168.1.55:5555
```

部署模式：
- `push_single`：mmm 编译 → adb remount → push binary → restart service
- `dd_boot_reboot`：mk_rpi5_full_image.sh -mode 2 → push boot.img → dd+reboot
- `flash_full`：需要人工全量刷机（P2 不实现，返回 error）

### le control 子命令

`le control` 提供 AI 闭环控制 session 管理：

```bash
le control init --target lciod --max-attempts 5 --artifacts-dir <dir>
le control run-verify --session <id> --suite features.lciod.*
le control analyze-request --session <id>
le control deploy --session <id>
le control decide --session <id>
le control status --session <id>
```
```

- [ ] **Step 2: Commit**

```bash
git add engineering/loop/WORKFLOW.md
git commit -m "docs(loop): WORKFLOW 登记 deploy + control 子命令"
```

---

## P2+P3 全量测试

```bash
# P2 deploy 测试
PYTHONPATH="engineering/loop/deploy/python:engineering/loop/core/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/deploy/python/tests/ -v

# P3 controller 测试
PYTHONPATH="engineering/loop/controller/python:engineering/loop/contracts/python:engineering/loop/core/python" \
  python3 -m pytest engineering/loop/controller/python/tests/ -v

# adb client 扩展测试
PYTHONPATH="engineering/loop/connection/providers/adb/python:engineering/loop/core/python:engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/connection/providers/adb/python/tests/ -v

# 全量（core + controller）
PYTHONPATH="engineering/loop/deploy/python:engineering/loop/core/python:engineering/loop/controller/python:engineering/loop/connection/providers/adb/python:engineering/loop/contracts/python" \
  python3 -m pytest engineering/loop/core/python/tests/ engineering/loop/controller/python/tests/ engineering/loop/deploy/python/tests/ engineering/loop/connection/providers/adb/python/tests/ -q
```

## P2+P3 产出物清单

| 文件 | 说明 | 估行 |
|-----|------|------|
| `engineering/loop/deploy/python/loop_deploy/__init__.py` | 包入口 | 1 |
| `engineering/loop/deploy/python/loop_deploy/models.py` | DeployPlan/DeployMode/DeployTarget/DeployResult | 60 |
| `engineering/loop/deploy/python/loop_deploy/decider.py` | git diff → DeployPlan（10 规则） | 120 |
| `engineering/loop/deploy/python/loop_deploy/compiler.py` | 调 mk_rpi5 / mmm | 80 |
| `engineering/loop/deploy/python/loop_deploy/deployer.py` | push_single / dd_boot 执行 | 100 |
| `engineering/loop/deploy/python/loop_deploy/adb_ops.py` | wait_service / wait_boot | 30 |
| `engineering/loop/deploy/python/loop_deploy/cli.py` | deploy 子命令 CLI | 60 |
| `engineering/loop/deploy/python/tests/test_*.py` | 4 个测试文件 | 120 |
| `engineering/loop/connection/providers/adb/python/loop_adb/client.py` | +push +remount | 20 |
| `engineering/loop/connection/providers/adb/python/tests/test_client.py` | push/remount 测试 | 30 |
| `engineering/loop/controller/python/loop_controller/analyzer_protocol.py` | LlmAnalyzer ABC + 数据模型 | 50 |
| `engineering/loop/controller/python/loop_controller/patch_applier.py` | 结构化补丁应用 | 60 |
| `engineering/loop/controller/python/tests/test_patch_applier.py` | 5 个补丁测试 | 40 |
| `engineering/loop/controller/python/loop_controller/cycle_orchestrator.py` | 分阶段编排 | 60 |
| `engineering/loop/controller/python/loop_controller/control_cli.py` | control 子命令 CLI | 120 |
| `engineering/loop/controller/python/loop_controller/__init__.py` | 导出更新 | 15 |
| `engineering/loop/core/python/loop_core/cli.py` | 接入 deploy + control | 20 |
| `engineering/harness/config/harness-paths.conf` | +LOOP_DEPLOY_DIR | 1 |
| `engineering/harness/scripts/apply_preset_bugs.sh` | 预设 bug 注入脚本 | 120 |
| `engineering/loop/WORKFLOW.md` | deploy + control 文档 | 30 |
| **合计** | **20+ 文件** | **~1000 行代码 + ~250 行测试** |
