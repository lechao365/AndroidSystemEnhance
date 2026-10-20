# 特权 allowlist 与 sudo 绕过机制实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `privileged_exec` + 仓库 allowlist，让 harness 在 opencode `NoNewPrivileges=1` 会话内自动完成打包等受控 host 特权操作，默认拒绝并留审计。

**Architecture:** `harness/lib/privileged_exec.py` 读取静态 YAML allowlist，精确校验命令/解释器/参数/路径/超时；父进程 `sudo -n true` 可用走 direct，否则用 `systemd-run --user --property=NoNewPrivileges=no` 临时 unit；每次执行写审计 JSON。`ws_package` 接入 helper，`ws_verify_chain` 删除自带的 systemd-run 包装，`mk_rpi5_full_image.sh` 增加 NNP=1 时 re-exec guard；破坏性刷机仅入默认禁用模板。

**Tech Stack:** Python 3、PyYAML、unittest/pytest、systemd-run、bash、Git。

---

## 文件结构

- Create: `harness/config/privileged-allowlist.yaml` — 静态 allowlist；打包启用，刷机模板禁用。
- Create: `harness/lib/privileged_exec.py` — allowlist 加载/校验、probe、direct/systemd-run、审计、CLI。
- Create: `harness/lib/tests/test_privileged_exec.py` — 核心单测 + 脚本 guard/刷机 wrapper 拒绝测试。
- Create: `harness/scripts/flash_rpi5_boot.sh` — 破坏性 boot 分区 wrapper（默认禁用，不在本批启用）。
- Create: `harness/rules/privileged-exec.md` — `PRIV-001`~`PRIV-004`。
- Modify: `harness/skills/workspace-verify/ws_package.py` — 删除父进程 sudo 探测/直接 subprocess，改调 helper。
- Modify: `harness/skills/workspace-verify/tests/test_ws_package.py` — helper mock 与证据字段断言。
- Modify: `harness/skills/workspace-verify/ws_verify_chain.py` — package 步骤去掉 systemd-run。
- Modify: `harness/skills/workspace-verify/tests/test_ws_verify_chain.py` — 断言 package 直接执行 ws_package。
- Modify: `harness/scripts/mk_rpi5_full_image.sh` — `SCRIPT_DIR` 后加 guard。
- Modify: `harness/skills/publish-main-base/baseline_register.py` — 更新 `sudo_n=false` 告警文案。
- Modify: `harness/skills/publish-main-base/tests/test_baseline_register.py` — 告警断言。
- Modify: `harness/reference/build-reference.md` — BLD-013 首选 allowlist 自动路径。
- Modify: `harness/reference/incremental-dev-reference.md:99-101` — 手工 `dd` 标注优先 allowlist wrapper。
- Modify: `harness/skills/workspace-verify/SKILL.md` — 打包说明。
- Modify: `harness/skills/publish-main-base/SKILL.md` — package 路径说明。
- Modify: `harness/README.md` — 目录/规则索引。

---

## Task 0: 提交设计与计划（meta，需用户授权）

- [ ] **Step 1: 提交 spec 与本计划**

```bash
git add docs/superpowers/specs/2026-10-20-privileged-allowlist-design.md \
  docs/superpowers/plans/2026-10-20-privileged-allowlist.md
git commit -m "文档(harness): 特权 allowlist 设计与实施计划"
```

---

## Task 1: Allowlist 核心、配置与单元测试

**Files:**
- Create: `harness/config/privileged-allowlist.yaml`
- Create: `harness/lib/privileged_exec.py`
- Create: `harness/lib/tests/test_privileged_exec.py`

- [ ] **Step 1: 写失败测试**

把下列内容写入 `harness/lib/tests/test_privileged_exec.py`：

```python
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import privileged_exec as priv


class PrivTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / "repo"
        (self.repo / "harness" / "config").mkdir(parents=True)
        (self.repo / "harness" / "scripts").mkdir(parents=True)
        (self.repo / "harness" / "log" / "privileged").mkdir(parents=True)
        self.script = self.repo / "harness" / "scripts" / "demo.sh"
        self.script.write_text("#!/bin/sh\necho demo\n", encoding="utf-8")
        self.script.chmod(0o755)
        self.old_root = priv._ROOT
        priv._ROOT = self.repo
        self.addCleanup(setattr, priv, "_ROOT", self.old_root)
        self.allowlist = self.repo / "harness" / "config" / "priv.yaml"
        self.write_allowlist()

    def write_allowlist(self, extra="", enabled="true", as_root="false",
                        destructive="false", args='["-mode", "0"]'):
        requires = "true" if as_root == "true" else "false"
        text = f"""
version: 1
default: deny
entries:
  - id: demo.run
    description: demo
    command: harness/scripts/demo.sh
    interpreter: bash
    args: {args}
    requires_privilege: {requires}
    as_root: {as_root}
    mechanism: auto
    timeout_s: 30
    env_passthrough: [PATH, HOME, DEMO]
    reason_required: true
    enabled: {enabled}
    destructive: {destructive}
    human_confirm: {destructive}
    approved_by: test
    approved_at: "2026-10-20"
{extra}
"""
        self.allowlist.write_text(text, encoding="utf-8")

    def test_default_deny_required(self):
        self.allowlist.write_text("version: 1\nentries: []\n", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist(str(self.allowlist))
        self.assertEqual(cm.exception.code, "PRIV_ALLOWLIST_INVALID")

    def test_enabled_requires_approval(self):
        self.allowlist.write_text("""
version: 1
default: deny
entries:
  - id: x
    command: harness/scripts/demo.sh
    enabled: true
""", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError):
            priv.load_allowlist(str(self.allowlist))

    def test_unknown_entry(self):
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.run("nope", ["bash", str(self.script), "-mode", "0"],
                     reason="x", allowlist_path=str(self.allowlist))
        self.assertEqual(cm.exception.code, "PRIV_ENTRY_UNKNOWN")

    def test_disabled_entry(self):
        self.write_allowlist(enabled="false")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.run("demo.run", ["bash", str(self.script), "-mode", "0"],
                     reason="x", allowlist_path=str(self.allowlist))
        self.assertEqual(cm.exception.code, "PRIV_ENTRY_DISABLED")

    def test_expired_entry(self):
        self.write_allowlist(extra='    expires_at: "2020-01-01"')
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.run("demo.run", ["bash", str(self.script), "-mode", "0"],
                     reason="x", allowlist_path=str(self.allowlist))
        self.assertEqual(cm.exception.code, "PRIV_ENTRY_EXPIRED")

    def test_arg_mismatch(self):
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.run("demo.run", ["bash", str(self.script), "-mode", "1"],
                     reason="x", allowlist_path=str(self.allowlist))
        self.assertEqual(cm.exception.code, "PRIV_ARG_MISMATCH")

    def test_command_escape(self):
        self.allowlist.write_text("""
version: 1
default: deny
entries:
  - id: x
    command: ../outside.sh
    args: []
    enabled: true
    approved_by: t
    approved_at: "2026-10-20"
""", encoding="utf-8")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.load_allowlist(str(self.allowlist))
        self.assertEqual(cm.exception.code, "PRIV_COMMAND_DENIED")

    def test_probe_direct(self):
        with mock.patch.object(priv.shutil, "which", return_value="/bin/x"), \
                mock.patch.object(priv.subprocess, "run",
                                  return_value=mock.Mock(returncode=0,
                                                         stdout="",
                                                         stderr="")), \
                mock.patch.object(priv, "_no_new_privs", return_value=False):
            result = priv.probe()
        self.assertTrue(result.sudo_n)
        self.assertEqual(result.mechanism, "direct")

    def test_probe_systemd(self):
        def which(name):
            return "/usr/bin/sudo" if name == "sudo" else "/usr/bin/systemd-run"
        def fake_run(args, **kwargs):
            if args[0] == "/usr/bin/sudo":
                return mock.Mock(returncode=1, stdout="", stderr="no")
            return mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(priv.shutil, "which", side_effect=which), \
                mock.patch.object(priv.subprocess, "run",
                                  side_effect=fake_run), \
                mock.patch.object(priv, "_no_new_privs", return_value=True):
            result = priv.probe()
        self.assertFalse(result.sudo_n)
        self.assertEqual(result.mechanism, "systemd-run")
        self.assertTrue(result.usable)
        self.assertTrue(result.context_sudo_ok)

    def test_run_non_root(self):
        with mock.patch.object(priv.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(priv.subprocess, "run",
                                  return_value=mock.Mock(returncode=0,
                                                         stdout="ok",
                                                         stderr="")) as run:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test",
                              allowlist_path=str(self.allowlist))
        self.assertEqual(result.rc, 0)
        self.assertEqual(result.mechanism, "direct")
        self.assertNotIn("sudo", run.call_args.args[0])
        audit = json.loads(Path(result.audit_path).read_text(encoding="utf-8"))
        self.assertEqual(audit["entry_id"], "demo.run")
        self.assertEqual(audit["rc"], 0)

    def test_run_direct_root(self):
        self.write_allowlist(as_root="true")
        probe = priv.ProbeResult(True, False, True, "direct", True, "", True)
        with mock.patch.object(priv, "probe", return_value=probe), \
                mock.patch.object(priv.shutil, "which",
                                  side_effect=lambda n: f"/bin/{n}"), \
                mock.patch.object(priv.subprocess, "run",
                                  return_value=mock.Mock(returncode=0,
                                                         stdout="ok",
                                                         stderr="")) as run:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test",
                              allowlist_path=str(self.allowlist))
        self.assertEqual(run.call_args.args[0][:3],
                         ["/bin/sudo", "-n", "--"])
        self.assertTrue(result.context_sudo_ok)

    def test_run_systemd_filters_env(self):
        self.write_allowlist(as_root="true")
        probe = priv.ProbeResult(False, True, True, "systemd-run", True, "", True)
        env = {"PATH": "/usr/bin", "HOME": "/h", "DEMO": "d",
               "SECRET": "s", "AOSP_WS": "/ws"}
        with mock.patch.object(priv, "probe", return_value=probe), \
                mock.patch.object(priv.shutil, "which",
                                  side_effect=lambda n: f"/bin/{n}"), \
                mock.patch.object(priv.subprocess, "run",
                                  return_value=mock.Mock(returncode=0,
                                                         stdout="ok",
                                                         stderr="")) as run:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test", env=env,
                              allowlist_path=str(self.allowlist))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "/bin/systemd-run")
        self.assertIn("--property=NoNewPrivileges=no", cmd)
        self.assertIn("--setenv=DEMO=d", cmd)
        self.assertNotIn("--setenv=SECRET=s", cmd)
        self.assertIn("/bin/sudo", cmd)
        self.assertFalse(result.sudo_n)
        self.assertTrue(result.context_sudo_ok)

    def test_non_root_requires_privilege_uses_systemd(self):
        self.write_allowlist(as_root="false")
        self.allowlist.write_text(self.allowlist.read_text(encoding="utf-8")
                                  .replace("requires_privilege: false",
                                           "requires_privilege: true"),
                                  encoding="utf-8")
        probe = priv.ProbeResult(False, True, True, "systemd-run", True, "", True)
        with mock.patch.object(priv, "probe", return_value=probe), \
                mock.patch.object(priv.shutil, "which",
                                  side_effect=lambda n: f"/bin/{n}"), \
                mock.patch.object(priv.subprocess, "run",
                                  return_value=mock.Mock(returncode=0,
                                                         stdout="ok",
                                                         stderr="")) as run:
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="test",
                              allowlist_path=str(self.allowlist))
        cmd = run.call_args.args[0]
        self.assertEqual(cmd[0], "/bin/systemd-run")
        self.assertNotIn("/bin/sudo", cmd)
        self.assertEqual(result.mechanism, "systemd-run")

    def test_destructive_requires_confirm(self):
        self.write_allowlist(as_root="true", destructive="true")
        with self.assertRaises(priv.PrivilegeError) as cm:
            priv.run("demo.run", ["bash", str(self.script), "-mode", "0"],
                     reason="x", allowlist_path=str(self.allowlist))
        self.assertEqual(cm.exception.code, "PRIV_DESTRUCTIVE_DENIED")

    def test_timeout(self):
        with mock.patch.object(priv.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(priv.subprocess, "run",
                                  side_effect=subprocess.TimeoutExpired("x", 1)):
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="x", timeout=1,
                              allowlist_path=str(self.allowlist))
        self.assertEqual(result.rc, 124)
        self.assertIn("PRIV_TIMEOUT", result.error)

    def test_audit_failure_keeps_rc(self):
        with mock.patch.object(priv.shutil, "which", return_value="/bin/bash"), \
                mock.patch.object(priv.subprocess, "run",
                                  return_value=mock.Mock(returncode=0,
                                                         stdout="ok",
                                                         stderr="")), \
                mock.patch.object(priv, "atomic_write_json",
                                  side_effect=OSError("disk")):
            result = priv.run("demo.run",
                              ["bash", str(self.script), "-mode", "0"],
                              reason="x", allowlist_path=str(self.allowlist))
        self.assertEqual(result.rc, 0)
        self.assertIn("AUDIT_WRITE_FAILED", result.error)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest harness/lib/tests/test_privileged_exec.py -q`
Expected: FAIL（`ModuleNotFoundError: No module named 'privileged_exec'` 或 allowlist 缺失）。

- [ ] **Step 3: 写 allowlist**

创建 `harness/config/privileged-allowlist.yaml`：

```yaml
version: 1
default: deny

entries:
  - id: package.rpi5.mode0
    description: RPi5 三镜像打包（mode 0，sudo 分区/格式化/losetup）
    command: harness/scripts/mk_rpi5_full_image.sh
    interpreter: bash
    args: ["-mode", "0"]
    requires_privilege: true
    as_root: false
    mechanism: auto
    timeout_s: 900
    env_passthrough: [PATH, HOME, USER, LANG, AOSP_WS, AOSP_ROOT,
                      ANDROID_PRODUCT_OUT, TARGET_PRODUCT,
                      CDP_BATCH_ID, CDP_RUN_ID]
    reason_required: true
    enabled: true
    destructive: false
    approved_by: release-review
    approved_at: "2026-10-20"

  - id: flash.rpi5.boot_dd
    description: 刷写 RPi5 boot 分区（破坏性，默认禁用）
    command: harness/scripts/flash_rpi5_boot.sh
    interpreter: bash
    args: ["--confirm"]
    requires_privilege: true
    as_root: true
    mechanism: auto
    timeout_s: 900
    env_passthrough: [PATH, HOME, USER, LANG, ANDROID_PRODUCT_OUT,
                      LC_FLASH_DEVICE, LC_FLASH_IMAGE,
                      LC_PRIVILEGE_ALLOW_DESTRUCTIVE]
    reason_required: true
    enabled: false
    destructive: true
    human_confirm: true
    approved_by: ""
    approved_at: ""
```

- [ ] **Step 4: 写核心模块**

把下列完整内容写入 `harness/lib/privileged_exec.py`：

```python
#!/usr/bin/env python3
"""privileged_exec — 仓库内 host 特权操作的唯一执行入口。

设计：静态 allowlist 默认拒绝；命令、参数、解释器、路径、超时精确匹配；
优先 direct sudo，NoNewPrivileges=1 时用 systemd-run 临时 unit；
每次执行落审计 JSON；失败关闭，不进入交互式密码输入。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

import yaml

from verify_common import atomic_write_json

_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ALLOWLIST = "harness/config/privileged-allowlist.yaml"
_ENV_ALLOWLIST = "LC_PRIVILEGED_ALLOWLIST"
_ENV_LOG_DIR = "LC_PRIVILEGED_LOG_DIR"
_ENV_MECHANISM = "LC_PRIVILEGE_MECHANISM"
_ENV_DESTRUCTIVE = "LC_PRIVILEGE_ALLOW_DESTRUCTIVE"
_CONTEXT_ENV = "LC_PRIVILEGE_CONTEXT"

_MECHANISMS = {"auto", "direct", "systemd-run"}
_INTERPRETERS = {"bash", "python3"}
_ENTRY_KEYS = {
    "id", "description", "command", "interpreter", "args",
    "requires_privilege", "as_root",
    "mechanism", "timeout_s", "env_passthrough", "reason_required",
    "enabled", "destructive", "human_confirm", "approved_by",
    "approved_at", "expires_at",
}
_TOP_KEYS = {"version", "default", "entries"}
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SHELL_META = set(";&|`$<>\n\r\x00")


class PrivilegeError(RuntimeError):
    """特权执行拒绝/失败；code 供调用方与收据归因。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class AllowlistEntry:
    id: str
    description: str
    command: str
    command_abs: Path
    interpreter: str | None
    args: tuple[str, ...]
    requires_privilege: bool
    as_root: bool
    mechanism: str
    timeout_s: int
    env_passthrough: tuple[str, ...]
    reason_required: bool
    enabled: bool
    destructive: bool
    human_confirm: bool
    approved_by: str
    approved_at: str
    expires_at: str | None = None


@dataclass(frozen=True)
class ProbeResult:
    sudo_n: bool
    no_new_privs: bool
    systemd_run: bool
    mechanism: str
    usable: bool
    detail: str
    context_sudo_ok: bool = False


@dataclass(frozen=True)
class RunResult:
    rc: int
    entry_id: str
    mechanism: str
    sudo_n: bool
    no_new_privs: bool
    context_sudo_ok: bool
    dur_s: float
    audit_path: str
    output_tail: str
    error: str


def _repo_root() -> Path:
    return _ROOT


def _default_allowlist_path() -> Path:
    return _repo_root() / _DEFAULT_ALLOWLIST


def _audit_dir() -> Path:
    override = os.environ.get(_ENV_LOG_DIR, "").strip()
    if override:
        return Path(override)
    return _repo_root() / "harness" / "log" / "privileged"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _check_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise PrivilegeError("PRIV_SCHEMA", f"{field} 必须是非空字符串")
    bad = sorted({ch for ch in value if ch in _SHELL_META})
    if bad:
        raise PrivilegeError(
            "PRIV_ARG_MISMATCH", f"{field} 含禁止字符: {bad!r}")
    return value


def _bool_field(data: Mapping, key: str, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise PrivilegeError("PRIV_SCHEMA", f"{key} 必须是布尔值")
    return value


def _str_field(data: Mapping, key: str, default: str = "") -> str:
    value = data.get(key, default)
    if not isinstance(value, str):
        raise PrivilegeError("PRIV_SCHEMA", f"{key} 必须是字符串")
    return value


def _resolve_command(command: str) -> Path:
    path = Path(command)
    if path.is_absolute():
        roots: list[Path] = []
        for key in ("AOSP_WS", "KERNEL_WS"):
            try:
                from paths import env_path
                value = env_path(key)
            except Exception:
                value = ""
            if value:
                roots.append(Path(value).resolve())
        resolved = path.resolve()
        if not any(_is_within(resolved, root) for root in roots):
            raise PrivilegeError(
                "PRIV_COMMAND_DENIED",
                f"绝对命令不在 AOSP_WS/KERNEL_WS 受控范围: {command}")
    else:
        resolved = (_repo_root() / path).resolve()
        if not _is_within(resolved, _repo_root().resolve()):
            raise PrivilegeError(
                "PRIV_COMMAND_DENIED", f"命令逃逸仓库: {command}")
    if not resolved.is_file():
        raise PrivilegeError("PRIV_COMMAND_DENIED", f"命令不存在: {resolved}")
    return resolved


def _parse_entry(raw: Mapping) -> AllowlistEntry:
    if not isinstance(raw, Mapping):
        raise PrivilegeError("PRIV_SCHEMA", "entries 项必须是映射")
    unknown = set(raw) - _ENTRY_KEYS
    if unknown:
        raise PrivilegeError(
            "PRIV_SCHEMA", f"条目含未知键: {', '.join(sorted(unknown))}")
    entry_id = _check_text(raw.get("id"), "id")
    command = _check_text(raw.get("command"), f"{entry_id}.command")
    interpreter = raw.get("interpreter")
    if interpreter is not None:
        if interpreter not in _INTERPRETERS:
            raise PrivilegeError(
                "PRIV_SCHEMA", f"{entry_id}.interpreter 仅允许 bash/python3")
    args_raw = raw.get("args", [])
    if not isinstance(args_raw, list):
        raise PrivilegeError("PRIV_SCHEMA", f"{entry_id}.args 必须是列表")
    args = tuple(_check_text(v, f"{entry_id}.args") for v in args_raw)
    env_raw = raw.get("env_passthrough", [])
    if not isinstance(env_raw, list):
        raise PrivilegeError(
            "PRIV_SCHEMA", f"{entry_id}.env_passthrough 必须是列表")
    env_passthrough = []
    for value in env_raw:
        if not isinstance(value, str) or not _ENV_RE.match(value):
            raise PrivilegeError(
                "PRIV_SCHEMA", f"{entry_id}.env_passthrough 非法: {value!r}")
        env_passthrough.append(value)
    mechanism = _str_field(raw, "mechanism", "auto")
    if mechanism not in _MECHANISMS:
        raise PrivilegeError(
            "PRIV_SCHEMA", f"{entry_id}.mechanism 非法: {mechanism}")
    timeout_s = raw.get("timeout_s", 900)
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, int) \
            or not 1 <= timeout_s <= 3600:
        raise PrivilegeError(
            "PRIV_SCHEMA", f"{entry_id}.timeout_s 须为 1..3600 整数")
    enabled = _bool_field(raw, "enabled", False)
    approved_by = _str_field(raw, "approved_by")
    approved_at = _str_field(raw, "approved_at")
    if enabled and (not approved_by or not approved_at):
        raise PrivilegeError(
            "PRIV_SCHEMA", f"{entry_id} 启用必须填 approved_by/approved_at")
    destructive = _bool_field(raw, "destructive", False)
    human_confirm = _bool_field(raw, "human_confirm", False)
    if destructive and enabled and not human_confirm:
        raise PrivilegeError(
            "PRIV_SCHEMA", f"{entry_id} 破坏性条目启用必须 human_confirm=true")
    expires_at = raw.get("expires_at")
    if expires_at is not None:
        if not isinstance(expires_at, str):
            raise PrivilegeError(
                "PRIV_SCHEMA", f"{entry_id}.expires_at 必须是字符串")
        try:
            date.fromisoformat(expires_at)
        except ValueError as exc:
            raise PrivilegeError(
                "PRIV_SCHEMA", f"{entry_id}.expires_at 非法: {expires_at}") \
                from exc
    requires_privilege = _bool_field(raw, "requires_privilege", False)
    as_root = _bool_field(raw, "as_root", False)
    if as_root and not requires_privilege:
        raise PrivilegeError(
            "PRIV_SCHEMA", f"{entry_id}.as_root=true 时 requires_privilege 必须为 true")
    return AllowlistEntry(
        id=entry_id,
        description=_str_field(raw, "description"),
        command=command,
        command_abs=_resolve_command(command),
        interpreter=interpreter,
        args=args,
        requires_privilege=requires_privilege,
        as_root=as_root,
        mechanism=mechanism,
        timeout_s=timeout_s,
        env_passthrough=tuple(env_passthrough),
        reason_required=_bool_field(raw, "reason_required", True),
        enabled=enabled,
        destructive=destructive,
        human_confirm=human_confirm,
        approved_by=approved_by,
        approved_at=approved_at,
        expires_at=expires_at,
    )


def load_allowlist(path: str | None = None) -> dict[str, AllowlistEntry]:
    allowlist_path = Path(
        path or os.environ.get(_ENV_ALLOWLIST, "").strip()
        or _default_allowlist_path())
    try:
        raw = yaml.safe_load(allowlist_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise PrivilegeError(
            "PRIV_ALLOWLIST_MISSING",
            f"allowlist 不可读: {allowlist_path}（{exc}）") from exc
    except yaml.YAMLError as exc:
        raise PrivilegeError(
            "PRIV_ALLOWLIST_INVALID",
            f"allowlist YAML 非法: {allowlist_path}（{exc}）") from exc
    if not isinstance(raw, dict):
        raise PrivilegeError("PRIV_ALLOWLIST_INVALID", "allowlist 顶层必须是映射")
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise PrivilegeError(
            "PRIV_ALLOWLIST_INVALID",
            f"allowlist 含未知键: {', '.join(sorted(unknown))}")
    if raw.get("default") != "deny":
        raise PrivilegeError(
            "PRIV_ALLOWLIST_INVALID", "allowlist default 必须显式设为 deny")
    entries_raw = raw.get("entries")
    if not isinstance(entries_raw, list):
        raise PrivilegeError("PRIV_ALLOWLIST_INVALID", "entries 必须是列表")
    entries: dict[str, AllowlistEntry] = {}
    for item in entries_raw:
        entry = _parse_entry(item)
        if entry.id in entries:
            raise PrivilegeError(
                "PRIV_ALLOWLIST_INVALID", f"条目 id 重复: {entry.id}")
        entries[entry.id] = entry
    return entries


def _no_new_privs() -> bool:
    try:
        text = Path("/proc/self/status").read_text(encoding="utf-8")
    except OSError:
        return False
    match = re.search(r"^NoNewPrivs:\s*(\d+)", text, re.MULTILINE)
    return bool(match and match.group(1) == "1")


def probe() -> ProbeResult:
    sudo_bin = shutil.which("sudo")
    sudo_n = False
    detail = ""
    if sudo_bin:
        try:
            proc = subprocess.run(
                [sudo_bin, "-n", "true"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30)
            sudo_n = proc.returncode == 0
            if not sudo_n:
                detail = ((proc.stderr or "").strip()[-200:]
                          or f"sudo -n true rc={proc.returncode}")
        except subprocess.TimeoutExpired:
            detail = "sudo -n true 超时（>30s）"
        except OSError as exc:
            detail = f"sudo 不可执行: {exc}"
    else:
        detail = "sudo 不在 PATH"
    nnp = _no_new_privs()
    systemd_run_bin = shutil.which("systemd-run")
    systemd_run = systemd_run_bin is not None
    context_sudo_ok = False
    if sudo_n:
        mechanism, usable, context_sudo_ok = "direct", True, True
    elif systemd_run_bin and sudo_bin:
        mechanism = "systemd-run"
        try:
            check = subprocess.run(
                [systemd_run_bin, "--user", "--quiet", "--wait", "--pipe",
                 "--collect", "--property=NoNewPrivileges=no", "--",
                 sudo_bin, "-n", "true"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30)
            context_sudo_ok = check.returncode == 0
            usable = context_sudo_ok
            detail = ("systemd-run context sudo ok"
                      if context_sudo_ok else
                      ((check.stderr or "").strip()[-200:]
                       or f"systemd-run context sudo rc={check.returncode}"))
        except subprocess.TimeoutExpired:
            usable = False
            detail = "systemd-run context sudo 超时（>30s）"
        except OSError as exc:
            usable = False
            detail = f"systemd-run 不可执行: {exc}"
    else:
        mechanism, usable = "none", False
    return ProbeResult(sudo_n, nnp, systemd_run, mechanism, usable, detail,
                       context_sudo_ok)


def _validate_entry(entry: AllowlistEntry, reason: str,
                    timeout: int | None, cwd: str | None,
                    confirm_destructive: bool) -> tuple[int, Path]:
    if not entry.enabled:
        raise PrivilegeError(
            "PRIV_ENTRY_DISABLED",
            f"条目 {entry.id} 未启用（allowlist enabled=false）")
    if entry.expires_at and date.fromisoformat(entry.expires_at) < date.today():
        raise PrivilegeError(
            "PRIV_ENTRY_EXPIRED", f"条目 {entry.id} 已过期: {entry.expires_at}")
    if entry.reason_required and not reason.strip():
        raise PrivilegeError(
            "PRIV_REASON_REQUIRED", f"条目 {entry.id} 要求非空 reason")
    if len(reason) > 500:
        raise PrivilegeError("PRIV_REASON_REQUIRED", "reason 超过 500 字符")
    if entry.destructive and not confirm_destructive \
            and os.environ.get(_ENV_DESTRUCTIVE) != "YES":
        raise PrivilegeError(
            "PRIV_DESTRUCTIVE_DENIED",
            f"条目 {entry.id} 为破坏性操作；需 confirm_destructive=True "
            f"或 {_ENV_DESTRUCTIVE}=YES")
    if timeout is None:
        effective_timeout = entry.timeout_s
    else:
        if isinstance(timeout, bool) or not isinstance(timeout, int) \
                or timeout <= 0:
            raise PrivilegeError("PRIV_SCHEMA", "timeout 必须是正整数")
        if timeout > entry.timeout_s:
            raise PrivilegeError(
                "PRIV_SCHEMA",
                f"timeout={timeout} 超过条目上限 {entry.timeout_s}")
        effective_timeout = timeout
    cwd_abs = Path(cwd or _repo_root()).resolve()
    if not cwd_abs.is_dir():
        raise PrivilegeError("PRIV_SCHEMA", f"cwd 不存在: {cwd_abs}")
    return effective_timeout, cwd_abs


def _canonical_argv(entry: AllowlistEntry, argv: Sequence[str],
                    cwd_abs: Path) -> list[str]:
    if not isinstance(argv, (list, tuple)) or not argv:
        raise PrivilegeError("PRIV_ARG_MISMATCH", "argv 不能为空")
    items = []
    for index, value in enumerate(argv):
        items.append(_check_text(value, f"argv[{index}]"))
    if entry.interpreter:
        if len(items) < 2 or items[0] != entry.interpreter:
            raise PrivilegeError(
                "PRIV_ARG_MISMATCH",
                f"期望 {entry.interpreter} {entry.command} {list(entry.args)}")
        command = Path(items[1])
        command = (cwd_abs / command).resolve() \
            if not command.is_absolute() else command.resolve()
        rest = items[2:]
        interpreter = shutil.which(entry.interpreter)
        if not interpreter:
            raise PrivilegeError(
                "PRIV_INTERPRETER_MISSING",
                f"解释器不在 PATH: {entry.interpreter}")
        if not os.access(entry.command_abs, os.R_OK):
            raise PrivilegeError(
                "PRIV_COMMAND_DENIED",
                f"脚本不可读: {entry.command_abs}")
        canonical = [interpreter, str(entry.command_abs), *entry.args]
    else:
        command = Path(items[0])
        command = (cwd_abs / command).resolve() \
            if not command.is_absolute() else command.resolve()
        rest = items[1:]
        if not os.access(entry.command_abs, os.X_OK):
            raise PrivilegeError(
                "PRIV_COMMAND_DENIED",
                f"命令不可执行: {entry.command_abs}")
        canonical = [str(entry.command_abs), *entry.args]
    if command != entry.command_abs:
        raise PrivilegeError(
            "PRIV_ARG_MISMATCH",
            f"命令不符：期望 {entry.command_abs}，实际 {command}")
    if tuple(rest) != entry.args:
        raise PrivilegeError(
            "PRIV_ARG_MISMATCH",
            f"参数不符：期望 {list(entry.args)}，实际 {rest}")
    return canonical


def _select_mechanism(entry: AllowlistEntry,
                      result: ProbeResult) -> tuple[str, bool, bool, str, bool]:
    if not entry.requires_privilege:
        return "direct", False, _no_new_privs(), "无提权需求", False
    if not result.usable:
        raise PrivilegeError(
            "PRIV_SUDO_UNAVAILABLE",
            result.detail or "direct sudo 与 systemd-run context 均不可用")
    override = os.environ.get(_ENV_MECHANISM, "").strip()
    mechanism = override if override in _MECHANISMS else entry.mechanism
    if mechanism == "auto":
        mechanism = "direct" if result.sudo_n else "systemd-run"
    if mechanism == "direct":
        if not result.sudo_n:
            raise PrivilegeError(
                "PRIV_SUDO_UNAVAILABLE",
                "direct sudo 不可用；检查 NOPASSWD 或改用 systemd-run")
        return ("direct", result.sudo_n, result.no_new_privs,
                result.detail, result.context_sudo_ok)
    if not result.systemd_run:
        raise PrivilegeError(
            "PRIV_SYSTEMD_UNAVAILABLE",
            "systemd-run 不可用；检查 systemd user manager")
    return ("systemd-run", result.sudo_n, result.no_new_privs,
            result.detail, result.context_sudo_ok)


def _systemd_cmd(entry: AllowlistEntry, canonical: Sequence[str],
                 cwd_abs: Path, env: Mapping[str, str],
                 timeout: int) -> list[str]:
    systemd_run = shutil.which("systemd-run")
    if not systemd_run:
        raise PrivilegeError(
            "PRIV_SYSTEMD_UNAVAILABLE", "systemd-run 不在 PATH")
    command = [
        systemd_run, "--user", "--quiet", "--wait", "--pipe", "--collect",
        "--property=NoNewPrivileges=no",
        f"--property=RuntimeMaxSec={timeout + 30}s",
        f"--working-directory={cwd_abs}",
        f"--setenv={_CONTEXT_ENV}=1",
    ]
    for key in entry.env_passthrough:
        if key in env:
            command.append(f"--setenv={key}={env[key]}")
    command.append("--")
    if entry.as_root:
        sudo_bin = shutil.which("sudo")
        if not sudo_bin:
            raise PrivilegeError("PRIV_SUDO_UNAVAILABLE", "sudo 不在 PATH")
        command.extend([sudo_bin, "-n", "--"])
    command.extend(canonical)
    return command


def _write_audit(entry: AllowlistEntry, canonical: Sequence[str],
                 cwd_abs: Path, mechanism: str, sudo_n: bool,
                 no_new_privs: bool, context_sudo_ok: bool, rc: int,
                 dur_s: float, reason: str, error: str,
                 output_tail: str) -> str:
    run_id = os.environ.get("CDP_RUN_ID") or uuid.uuid4().hex
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    path = _audit_dir() / f"{stamp}-{entry.id}-{run_id}.json"
    payload = {
        "schema_version": 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entry_id": entry.id,
        "command": str(entry.command_abs),
        "argv": list(canonical),
        "cwd": str(cwd_abs),
        "mechanism": mechanism,
        "sudo_n": sudo_n,
        "sudo_n_parent": sudo_n,
        "sudo_n_context": context_sudo_ok,
        "no_new_privs": no_new_privs,
        "context_sudo_ok": context_sudo_ok,
        "run_id": run_id,
        "batch_id": os.environ.get("CDP_BATCH_ID", ""),
        "operator": os.environ.get("USER", ""),
        "host": os.uname().nodename,
        "rc": rc,
        "duration_s": round(dur_s, 3),
        "dur_s": round(dur_s, 3),
        "reason": reason,
        "destructive": entry.destructive,
        "user": os.environ.get("USER", ""),
        "error": error,
        "output_tail": output_tail[-2000:],
    }
    try:
        atomic_write_json(path, payload)
    except OSError as exc:
        return f"AUDIT_WRITE_FAILED: {exc}"
    return str(path)


def run(entry_id: str, argv: Sequence[str], *, reason: str = "",
        timeout: int | None = None, cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        confirm_destructive: bool = False,
        allowlist_path: str | None = None) -> RunResult:
    entries = load_allowlist(allowlist_path)
    entry = entries.get(entry_id)
    if entry is None:
        raise PrivilegeError(
            "PRIV_ENTRY_UNKNOWN",
            f"未登记条目: {entry_id}（可选: {', '.join(sorted(entries))}）")
    effective_timeout, cwd_abs = _validate_entry(
        entry, reason, timeout, cwd, confirm_destructive)
    canonical = _canonical_argv(entry, argv, cwd_abs)
    probe_result = probe() if entry.requires_privilege else ProbeResult(
        False, _no_new_privs(), shutil.which("systemd-run") is not None,
        "direct", True, "无提权需求", False)
    mechanism, sudo_n, no_new_privs, detail, context_sudo_ok = \
        _select_mechanism(entry, probe_result)
    full_env = dict(os.environ if env is None else env)
    full_env[_CONTEXT_ENV] = "1"
    if mechanism == "direct":
        command = []
        if entry.as_root:
            sudo_bin = shutil.which("sudo")
            if not sudo_bin:
                raise PrivilegeError(
                    "PRIV_SUDO_UNAVAILABLE", "sudo 不在 PATH")
            command.extend([sudo_bin, "-n", "--"])
        command.extend(canonical)
        run_env = full_env
        subprocess_timeout = effective_timeout
    else:
        command = _systemd_cmd(entry, canonical, cwd_abs, full_env,
                               effective_timeout)
        run_env = dict(os.environ)
        subprocess_timeout = effective_timeout + 60
    started = time.monotonic()
    rc = 127
    error = ""
    output_tail = ""
    try:
        proc = subprocess.run(
            command, cwd=str(cwd_abs), env=run_env, capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=subprocess_timeout)
        rc = proc.returncode
        output_tail = ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
    except subprocess.TimeoutExpired as exc:
        rc = 124
        error = f"PRIV_TIMEOUT: 超过 {effective_timeout}s"
        output_tail = ((exc.stdout or "") + (exc.stderr or ""))[-2000:] \
            if isinstance(exc.stdout, str) and isinstance(exc.stderr, str) \
            else ""
    except OSError as exc:
        rc = 127
        error = f"启动失败: {exc}"
    dur_s = time.monotonic() - started
    if rc != 0 and not error:
        error = f"命令 rc={rc}（{detail}）"
    audit_path = _write_audit(
        entry, canonical, cwd_abs, mechanism, sudo_n, no_new_privs,
        context_sudo_ok, rc, dur_s, reason, error, output_tail)
    if audit_path.startswith("AUDIT_WRITE_FAILED"):
        error = (error + "；" if error else "") + audit_path
    return RunResult(
        rc=rc, entry_id=entry.id, mechanism=mechanism, sudo_n=sudo_n,
        no_new_privs=no_new_privs, context_sudo_ok=context_sudo_ok,
        dur_s=round(dur_s, 3), audit_path=audit_path,
        output_tail=output_tail, error=error)


def _cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="host 特权操作 allowlist 入口（默认拒绝）")
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--list", action="store_true", help="列出 allowlist")
    modes.add_argument("--check", action="store_true", help="检查提权机制")
    modes.add_argument("--entry-id", help="执行指定条目")
    parser.add_argument("--reason", default="")
    parser.add_argument("--timeout", type=int)
    parser.add_argument("--cwd")
    parser.add_argument("--confirm-destructive", action="store_true")
    parser.add_argument("--allowlist")
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.list:
            entries = load_allowlist(args.allowlist)
            for entry in sorted(entries.values(), key=lambda e: e.id):
                print(f"{entry.id}\tenabled={str(entry.enabled).lower()}\t"
                      f"destructive={str(entry.destructive).lower()}\t"
                      f"{entry.description}")
            return 0
        if args.check:
            result = probe()
            entries = load_allowlist(args.allowlist)
            print(json.dumps({
                "probe": asdict(result),
                "allowlist_entries": len(entries),
                "allowlist": str(args.allowlist or _default_allowlist_path()),
            }, ensure_ascii=False))
            return 0 if result.usable else 1
        command = list(args.argv)
        if command and command[0] == "--":
            command = command[1:]
        result = run(
            args.entry_id, command, reason=args.reason,
            timeout=args.timeout, cwd=args.cwd,
            confirm_destructive=args.confirm_destructive,
            allowlist_path=args.allowlist)
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 0 if result.rc == 0 else 1
    except PrivilegeError as exc:
        print(f"error: {exc.code}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(_cli())
```

- [ ] **Step 5: 运行测试确认通过**

Run: `python3 -m pytest harness/lib/tests/test_privileged_exec.py -q`
Expected: PASS（16 passed；若新增测试数量不同，以全绿为准）。

- [ ] **Step 6: 提交**

```bash
git add harness/config/privileged-allowlist.yaml \
  harness/lib/privileged_exec.py \
  harness/lib/tests/test_privileged_exec.py
git commit -m "新增(harness): 特权操作 allowlist 与 privileged_exec 核心"
```

---

## Task 2: `ws_package.py` 接入统一 helper

**Files:**
- Modify: `harness/skills/workspace-verify/ws_package.py:13-25, 150-205`
- Modify: `harness/skills/workspace-verify/tests/test_ws_package.py:1-115, 150-220`

- [ ] **Step 1: 先改测试为 helper 契约**

把 `test_ws_package.py` 的 `_run` 辅助与 sudo 相关用例改成下面这组（保留镜像缺失/BLD-007/batch_id 等未涉及特权的用例）：

```python
    def _run(self, run=True, script_rc=0, probe=None, result=None, **kw):
        kw.setdefault("aosp_ws", str(self.aosp))
        if "evidence_file" in kw and kw["evidence_file"] is None:
            kw.pop("evidence_file")
        else:
            kw.setdefault("evidence_file", str(self.evidence))
        if probe is None:
            probe = wp.priv.ProbeResult(
                True, False, True, "direct", True, "")
        if result is None:
            result = wp.priv.RunResult(
                script_rc, "package.rpi5.mode0", "direct", True, False,
                True, 0.1, "", "ok", "" if script_rc == 0 else "rc")
        with mock.patch.object(wp, "_SCRIPT", self.script), \
                mock.patch.object(wp.priv, "probe", return_value=probe), \
                mock.patch.object(wp.priv, "run", return_value=result) \
                as run_mock:
            rc, ev = wp.run_package(run=run, **kw)
        return rc, ev, run_mock

    def test_success_evidence_complete(self):
        img = self.product_out / "RaspberryVanillaAOSP15-20260905-rpi5.img"
        img.write_bytes(b"\x01" * 8)
        rc, ev, run_mock = self._run(script_rc=0)
        self.assertEqual(rc, 0)
        self.assertEqual(ev["script_rc"], 0)
        self.assertTrue(ev["ran"])
        self.assertTrue(ev["images_ok"])
        self.assertTrue(ev["sudo_bld007"])
        self.assertEqual(len(ev["images"]), 3)
        self.assertTrue(ev["privilege"]["usable"])
        self.assertEqual(ev["privilege"]["mechanism"], "direct")
        self.assertIsNotNone(ev["packaged_img"])
        run_mock.assert_called_once()
        call = run_mock.call_args
        self.assertEqual(call.args[0], "package.rpi5.mode0")
        self.assertEqual(call.args[1],
                         ["bash", str(self.script), "-mode", "0"])
        self.assertEqual(call.kwargs["reason"], "ws_package mode 0")
        self.assertEqual(call.kwargs["env"]["TARGET_PRODUCT"], "aosp_rpi5")
        self.assertEqual(call.kwargs["env"]["ANDROID_PRODUCT_OUT"],
                         str(self.product_out))

    def test_script_failure_recorded(self):
        rc, ev, _ = self._run(script_rc=3)
        self.assertEqual(rc, 1)
        self.assertEqual(ev["script_rc"], 3)
        self.assertIn("rc=3", ev["error"])
        self.assertIn("output_tail", ev)

    def test_sudo_unavailable_refuses(self):
        probe = wp.priv.ProbeResult(
            False, True, False, "none", False, "no systemd")
        rc, ev, run_mock = self._run(probe=probe)
        self.assertEqual(rc, 1)
        self.assertFalse(ev["sudo_n"])
        self.assertFalse(ev["privilege"]["usable"])
        self.assertIn("privileged_exec.py --check", ev["error"])
        self.assertIn("allowlist", ev["error"])
        run_mock.assert_not_called()

    def test_systemd_run_success(self):
        probe = wp.priv.ProbeResult(
            False, True, True, "systemd-run", True, "systemd-run 可用")
        result = wp.priv.RunResult(
            0, "package.rpi5.mode0", "systemd-run", False, True, True,
            0.1, "audit.json", "ok", "")
        rc, ev, _ = self._run(probe=probe, result=result)
        self.assertEqual(rc, 0)
        self.assertFalse(ev["sudo_n"])
        self.assertEqual(ev["privilege"]["mechanism"], "systemd-run")
        self.assertTrue(ev["privilege"]["context_sudo_ok"])
        self.assertEqual(ev["privilege"]["audit"], "audit.json")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_package.py -q`
Expected: FAIL（`wp.priv` 不存在或 `privilege` 字段缺失）。

- [ ] **Step 3: 改 `ws_package.py` 接入 helper**

在 import 区追加：

```python
sys.path.insert(0, str(_SCRIPT_DIR.parents[1] / "lib"))
import privileged_exec as priv  # noqa: E402
```

删除原 `sudo -n true` 探测与 `subprocess.run(["bash", ...])` 块，替换为：

```python
    probe = priv.probe()
    evidence["sudo_n"] = probe.sudo_n
    evidence["privilege"] = {
        "allowlist_id": "package.rpi5.mode0",
        "mechanism": probe.mechanism,
        "parent_sudo_n": probe.sudo_n,
        "no_new_privs": probe.no_new_privs,
        "systemd_run": probe.systemd_run,
        "usable": probe.usable,
        "context_sudo_ok": probe.context_sudo_ok,
        "audit": "",
        "audit_file": "",
        "detail": probe.detail,
    }
    if not probe.usable:
        evidence["error"] = (
            "提权机制不可用（sudo direct 与 systemd-run 均失败）；"
            f"detail={probe.detail or '未知'}。请运行 "
            "python3 harness/lib/privileged_exec.py --check，并检查 "
            "harness/config/privileged-allowlist.yaml；不要等待密码输入")
        return _finish(1)

    env = dict(os.environ)
    env["AOSP_ROOT"] = str(aosp)
    env["ANDROID_PRODUCT_OUT"] = str(product_out)
    env["TARGET_PRODUCT"] = _TARGET_PRODUCT
    try:
        result = priv.run(
            "package.rpi5.mode0",
            ["bash", str(_SCRIPT), "-mode", str(mode)],
            reason=f"ws_package mode {mode}",
            timeout=timeout,
            cwd=str(_SCRIPT_DIR.parents[2]),
            env=env,
        )
    except priv.PrivilegeError as exc:
        evidence["error"] = (
            f"{exc.code}: {exc}；检查 allowlist 或运行 "
            "python3 harness/lib/privileged_exec.py --check")
        return _finish(1)
    evidence["ran"] = True
    evidence["script_rc"] = result.rc
    evidence["output_tail"] = result.output_tail
    evidence["privilege"].update({
        "mechanism": result.mechanism,
        "context_sudo_ok": result.context_sudo_ok,
        "audit": result.audit_path,
        "audit_file": result.audit_path,
        "detail": result.error or evidence["privilege"]["detail"],
    })
    if result.rc != 0:
        evidence["error"] = f"打包脚本 rc={result.rc}"
        if result.error:
            evidence["error"] += f"：{result.error}"
        return _finish(1)
```

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_package.py -q`
Expected: PASS（原有用例 + 新增 helper 用例全绿）。

- [ ] **Step 5: 提交**

```bash
git add harness/skills/workspace-verify/ws_package.py \
  harness/skills/workspace-verify/tests/test_ws_package.py
git commit -m "重构(workspace-verify): 打包改走 privileged_exec allowlist"
```

---

## Task 3: `ws_verify_chain` 去掉自带 systemd-run

**Files:**
- Modify: `harness/skills/workspace-verify/ws_verify_chain.py:64-70, 345-355`
- Modify: `harness/skills/workspace-verify/tests/test_ws_verify_chain.py:171-198`

- [ ] **Step 1: 改测试**

把 `test_package_step_uses_systemd_run_with_batch_id_evidence` 改为：

```python
    def test_package_step_uses_privileged_helper(self):
        calls = []
        ctor = mock.Mock(side_effect=lambda argv, **kw: (
            calls.append(argv),
            mock.Mock(wait=mock.Mock(return_value=0)))[1])
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      use_locks=False)
        self.assertEqual(rc, 0)
        self.assertEqual(result["overall"], "pass")
        pkg = next(c for c in calls
                   if any(os.path.basename(x) == "ws_package.py" for x in c))
        self.assertEqual(pkg[0], sys.executable)
        self.assertTrue(pkg[1].endswith("ws_package.py"))
        self.assertNotIn("systemd-run", pkg)
        self.assertIn("--evidence-file", pkg)
        bid = wc.batch_id_from_text(self.batch.read_text(encoding="utf-8"))
        self.assertEqual(pkg[pkg.index("--evidence-file") + 1],
                         str(wc._SCRIPT_DIR.parents[1] / "log"
                             / "workspace-verify"
                             / f"package-{bid}.json"))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_verify_chain.py -q`
Expected: FAIL（package 命令仍以 `systemd-run` 开头）。

- [ ] **Step 3: 改 `_build_argv` 的 package 分支**

```python
    if name == "package":
        pkg_file = chain_args.get("package_file")
        cmd = [sys.executable, str(_SCRIPT_DIR / "ws_package.py"),
               "--mode", "0"]
        if pkg_file:
            cmd += ["--evidence-file", pkg_file]
        return cmd
```

同时把文件顶部注释中的 “package 用 systemd-run” 改为“package 由 ws_package 内部 privileged_exec 选择机制”。

- [ ] **Step 4: 运行测试确认通过**

Run: `python3 -m pytest harness/skills/workspace-verify/tests/test_ws_verify_chain.py -q`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add harness/skills/workspace-verify/ws_verify_chain.py \
  harness/skills/workspace-verify/tests/test_ws_verify_chain.py
git commit -m "重构(workspace-verify): verify 链移除自带 systemd-run 包装"
```

---

## Task 4: `mk_rpi5_full_image.sh` 递归 guard

**Files:**
- Modify: `harness/scripts/mk_rpi5_full_image.sh:31-33`
- Test: `harness/lib/tests/test_privileged_exec.py`（追加两个静态/语法测试）

- [ ] **Step 1: 加 guard**

在 `SCRIPT_DIR=...` 之后、`ARTIFACT_DIR=...` 之前插入：

```bash
if [ "${LC_PRIVILEGE_CONTEXT:-}" != "1" ] \
   && grep -q '^NoNewPrivs:[[:space:]]*1' /proc/self/status 2>/dev/null; then
  exec python3 "$SCRIPT_DIR/../lib/privileged_exec.py" \
    --entry-id package.rpi5.mode0 --reason "mk_rpi5_full_image direct" \
    -- bash "$SCRIPT_DIR/$(basename "$0")" "$@"
fi
```

- [ ] **Step 2: 追加测试**

在 `test_privileged_exec.py` 的 `PrivTest` 内追加：

```python
    def test_mk_image_guard_order_and_syntax(self):
        script = (Path(priv.__file__).resolve().parents[1]
                  / "scripts" / "mk_rpi5_full_image.sh")
        text = script.read_text(encoding="utf-8")
        guard = text.index("privileged_exec.py")
        script_dir = text.index("SCRIPT_DIR=")
        sudo = text.index("sudo TARGET_PRODUCT=")
        self.assertLess(script_dir, guard)
        self.assertLess(guard, sudo)
        subprocess.run(["bash", "-n", str(script)], check=True)
```

- [ ] **Step 3: 运行**

Run: `bash -n harness/scripts/mk_rpi5_full_image.sh`
Expected: 无输出。

Run: `python3 -m pytest harness/lib/tests/test_privileged_exec.py -q`
Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add harness/scripts/mk_rpi5_full_image.sh \
  harness/lib/tests/test_privileged_exec.py
git commit -m "修复(harness): 打包脚本在 NNP=1 下自动走 privileged_exec"
```

---

## Task 5: `baseline_register` 告警改指 allowlist

**Files:**
- Modify: `harness/skills/publish-main-base/baseline_register.py:571-577`
- Modify: `harness/skills/publish-main-base/tests/test_baseline_register.py:570-587`

- [ ] **Step 1: 改告警文案**

```python
        if package_result == "UNKNOWN" and isinstance(pkg_evidence, dict) \
                and pkg_evidence.get("sudo_n") is False:
            print("warn: package_result=UNKNOWN 且打包证据 sudo_n=false（会话内 "
                  "direct sudo 不可用）：先运行 "
                  "python3 harness/lib/privileged_exec.py --check 检查 "
                  "allowlist/systemd-run；若仍不可用，再按 BLD-013 在 "
                  "opencode 会话外普通终端人工执行打包后重登记（见 "
                  "harness/reference/build-reference.md）", file=sys.stderr)
```

- [ ] **Step 2: 补测试断言**

在 `test_add_candidate_sudo_n_false_warns_manual_path` 中追加：

```python
        self.assertIn("--check", out)
        self.assertIn("allowlist", out)
```

- [ ] **Step 3: 运行**

Run: `python3 -m pytest harness/skills/publish-main-base/tests/test_baseline_register.py -q`
Expected: PASS。

- [ ] **Step 4: 提交**

```bash
git add harness/skills/publish-main-base/baseline_register.py \
  harness/skills/publish-main-base/tests/test_baseline_register.py
git commit -m "重构(publish-main-base): 打包 UNKNOWN 告警改指 privileged allowlist"
```

---

## Task 6: 破坏性刷机 wrapper（默认禁用）

**Files:**
- Create: `harness/scripts/flash_rpi5_boot.sh`
- Modify: `harness/config/privileged-allowlist.yaml`（已含 `flash.rpi5.boot_dd`，本任务只补脚本）
- Test: `harness/lib/tests/test_privileged_exec.py`（wrapper 拒绝测试已在 Task 4 追加）

- [ ] **Step 1: 写 wrapper**

```bash
#!/usr/bin/env bash
set -euo pipefail

if [ "${1:-}" != "--confirm" ]; then
  echo "error: 需要 --confirm 才允许刷写" >&2
  exit 2
fi
if [ "${LC_PRIVILEGE_ALLOW_DESTRUCTIVE:-}" != "YES" ]; then
  echo "error: 需要 LC_PRIVILEGE_ALLOW_DESTRUCTIVE=YES（helper 破坏性确认）" >&2
  exit 2
fi

DEVICE="${LC_FLASH_DEVICE:-}"
IMAGE="${LC_FLASH_IMAGE:-}"
PRODUCT_OUT="${ANDROID_PRODUCT_OUT:-}"
if [ -z "$DEVICE" ] || [ -z "$IMAGE" ] || [ -z "$PRODUCT_OUT" ]; then
  echo "error: LC_FLASH_DEVICE/LC_FLASH_IMAGE/ANDROID_PRODUCT_OUT 必须设置" >&2
  exit 2
fi
case "$DEVICE" in
  /dev/sd[a-z][0-9]*) ;;
  *) echo "error: 只允许 /dev/sdXn 分区: $DEVICE" >&2; exit 2 ;;
esac
if [ ! -f "$IMAGE" ]; then
  echo "error: 镜像不存在: $IMAGE" >&2
  exit 2
fi
IMAGE_REAL=$(readlink -f "$IMAGE")
PRODUCT_REAL=$(readlink -f "$PRODUCT_OUT")
case "$IMAGE_REAL" in
  "$PRODUCT_REAL"/*) ;;
  *) echo "error: 镜像不在 ANDROID_PRODUCT_OUT 内: $IMAGE_REAL" >&2; exit 2 ;;
esac
RM=$(lsblk -no RM "$DEVICE" 2>/dev/null | tr -d '[:space:]' || true)
if [ "$RM" != "1" ]; then
  echo "error: 目标不是可移动设备（removable != 1）: $DEVICE" >&2
  exit 2
fi

echo "target=$DEVICE image=$IMAGE_REAL" >&2
dd if="$IMAGE_REAL" of="$DEVICE" bs=4M conv=fsync status=progress
sync
```

- [ ] **Step 2: 加执行权限/语法检查**

```bash
chmod +x harness/scripts/flash_rpi5_boot.sh
bash -n harness/scripts/flash_rpi5_boot.sh
```

- [ ] **Step 3: 追加 wrapper 拒绝测试并运行**

在 `test_privileged_exec.py` 的 `PrivTest` 内追加：

```python
    def test_flash_wrapper_refuses_without_confirm(self):
        script = (Path(priv.__file__).resolve().parents[1]
                  / "scripts" / "flash_rpi5_boot.sh")
        env = dict(os.environ)
        env.pop("LC_PRIVILEGE_ALLOW_DESTRUCTIVE", None)
        proc = subprocess.run(["bash", str(script)], capture_output=True,
                              text=True, env=env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--confirm", proc.stderr)

    def test_flash_wrapper_requires_destructive_env(self):
        script = (Path(priv.__file__).resolve().parents[1]
                  / "scripts" / "flash_rpi5_boot.sh")
        env = dict(os.environ)
        env.pop("LC_PRIVILEGE_ALLOW_DESTRUCTIVE", None)
        proc = subprocess.run(["bash", str(script), "--confirm"],
                              capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("LC_PRIVILEGE_ALLOW_DESTRUCTIVE", proc.stderr)
```

Run: `python3 -m pytest harness/lib/tests/test_privileged_exec.py -q`
Expected: PASS（未授权时 rc=2，不触碰块设备）。

- [ ] **Step 4: 提交**

```bash
git add harness/scripts/flash_rpi5_boot.sh harness/lib/tests/test_privileged_exec.py
git commit -m "新增(harness): 破坏性 boot 刷写 wrapper 与默认禁用模板"
```

> 本批不启用 `flash.rpi5.boot_dd`；`enabled: false` 保持。

---

## Task 7: 规则与文档

**Files:**
- Create: `harness/rules/privileged-exec.md`
- Modify: `harness/reference/build-reference.md:269-279, 328`
- Modify: `harness/reference/incremental-dev-reference.md:99-101`
- Modify: `harness/skills/workspace-verify/SKILL.md:220-247`
- Modify: `harness/skills/publish-main-base/SKILL.md:36-48, 120-123`
- Modify: `harness/README.md:38-58, 70-85`

- [ ] **Step 1: 写规则文件**

```markdown
# 特权操作 allowlist

> **规则 ID**: `PRIV-001` ~ `PRIV-004`
> **适用范围**: harness 内所有需要 host root 的操作。

## 规则

- **PRIV-001**：所有 host root 操作必须经 `harness/lib/privileged_exec.py` +
  `harness/config/privileged-allowlist.yaml`；禁止在 harness 脚本中新增裸 `sudo`
  （allowlist 目标脚本自身的 sudo 除外）。
- **PRIV-002**：allowlist 默认拒绝；命令/解释器/参数/路径/超时精确匹配；
  禁止 shell 字符串、`..` 逃逸、仓库外路径（AOSP/KERNEL 受控脚本除外）。
- **PRIV-003**：只用 transient unit（`systemd-run --user
  --property=NoNewPrivileges=no`），不持久修改 opencode service；失败关闭，
  不等待密码、不重试、不降级为裸 sudo。
- **PRIV-004**：破坏性条目默认 `enabled: false`；启用需评审 +
  `human_confirm: true` + `LC_PRIVILEGE_ALLOW_DESTRUCTIVE=YES` + 审计。

## 使用

```bash
python3 harness/lib/privileged_exec.py --list
python3 harness/lib/privileged_exec.py --check
python3 harness/lib/privileged_exec.py --entry-id <id> --reason "<原因>" -- <argv...>
```

审计落 `harness/log/privileged/`（gitignore 域），不随收据入库；收据只引用路径。
```

- [ ] **Step 2: 更新 BLD-013**

把 `harness/reference/build-reference.md` 中 BLD-013 段落改成：

```markdown
> **BLD-013**: opencode 以 systemd user unit 运行时设 `NoNewPrivileges=true`，
> 该标志子进程继承且不可撤销——会话内 direct `sudo` 恒被内核拒绝。打包等
> host root 操作首选 `python3 harness/lib/privileged_exec.py`（allowlist 精确
> 授权，内部自动用 `systemd-run --user --property=NoNewPrivileges=no` 临时
> unit）；失败关闭，不等待密码。人工会话外执行仅作回退，见 workspace-verify
> SKILL「人工打包步骤」。
```

并更新规则表 BLD-013 行：

```markdown
| BLD-013 | opencode 会话内 direct sudo 恒被 NoNewPrivileges 拒绝；host root 操作先走 privileged_exec allowlist，失败关闭 | 直接 sudo 失败；allowlist 自动 systemd-run 绕过 |
```

- [ ] **Step 3: 更新 incremental-dev-reference**

在 `harness/reference/incremental-dev-reference.md` 的 boot 分区手工 `dd` 段前加：

```markdown
> 优先使用 allowlist wrapper（默认禁用，需评审启用）：
> `python3 harness/lib/privileged_exec.py --entry-id flash.rpi5.boot_dd \
>   --reason "<原因>" --confirm-destructive -- bash >   harness/scripts/flash_rpi5_boot.sh --confirm`
> 手工 `sudo dd` 仅作 wrapper 不可用时的回退，且须人工确认设备为可移动盘。
```

- [ ] **Step 4: 更新 workspace-verify SKILL**

把「人工打包步骤（opencode 会话内 sudo 不可用，BLD-013）」段改为：

```markdown
### 打包（allowlist 自动，BLD-013）

打包走 `package.rpi5.mode0` allowlist：`ws_package.py` 调
`privileged_exec`，父进程 direct sudo 不可用时自动用 systemd-run 临时 unit。
先检查：

```bash
python3 harness/lib/privileged_exec.py --check
```

会话内直接执行：

```bash
python3 harness/skills/workspace-verify/ws_package.py --mode 0
```

证据落 `harness/log/workspace-verify/package-<batch_id>.json`，含
`privilege.mechanism/context_sudo_ok/audit`。若 `--check` 报
`PRIV_SUDO_UNAVAILABLE`/`PRIV_SYSTEMD_UNAVAILABLE`，才按 BLD-013 回退到
会话外普通终端人工打包；不要再手工套 `systemd-run`。
```

- [ ] **Step 5: 更新 publish-main-base SKILL**

把“拿 PASS 的路径”改为：

```markdown
**拿 PASS 的路径**：`ws_package` 内部走 `package.rpi5.mode0` allowlist，
opencode 会话内会自动用 `systemd-run --user --property=NoNewPrivileges=no`
临时 unit；先运行 `python3 harness/lib/privileged_exec.py --check`。
失败关闭时才回退到会话外人工执行；证据仍由 `ws_package` 落盘并内嵌收据。
```

- [ ] **Step 6: 更新 README**

在 `harness/README.md` 的 `lib/` 列表加：

```markdown
│   ├── privileged_exec.py      # host 特权 allowlist 入口（默认拒绝/审计）
```

在 `config/` 列表加：

```markdown
│   ├── privileged-allowlist.yaml # host 特权操作静态 allowlist
```

在规则索引表加：

```markdown
| `PRIV-001~004` | [rules/privileged-exec.md](rules/privileged-exec.md) | 特权 allowlist/失败关闭/破坏性默认禁用 |
```

- [ ] **Step 7: 运行文档/契约检查**

Run: `python3 harness/lib/check_skill_refs.py`
Expected: 无悬空引用。

Run: `python3 -m pytest harness/lib/tests/test_check_skill_refs.py -q`
Expected: PASS（若存在该测试）。

- [ ] **Step 8: 提交**

```bash
git add harness/rules/privileged-exec.md \
  harness/reference/build-reference.md \
  harness/reference/incremental-dev-reference.md \
  harness/skills/workspace-verify/SKILL.md \
  harness/skills/publish-main-base/SKILL.md \
  harness/README.md
git commit -m "文档(harness): 特权 allowlist 规则与打包说明"
```

---

## Task 8: 全量验证、现场打包与收据

**Files:**
- Create: `data/verify-results/`（由 `ws_report` 生成 manual 收据）
- Modify: none（验证阶段）

- [ ] **Step 1: 运行定向单测**

```bash
python3 -m pytest harness/lib/tests/test_privileged_exec.py -q
python3 -m pytest harness/skills/workspace-verify/tests/test_ws_package.py -q
python3 -m pytest harness/skills/workspace-verify/tests/test_ws_verify_chain.py -q
python3 -m pytest harness/skills/publish-main-base/tests/test_baseline_register.py -q
```

Expected: 全部 PASS。

- [ ] **Step 2: 语法与现场能力检查**

```bash
bash -n harness/scripts/mk_rpi5_full_image.sh
bash -n harness/scripts/flash_rpi5_boot.sh
python3 harness/lib/privileged_exec.py --check
```

Expected: `--check` 输出 JSON，含
`"no_new_privs": true`、`"mechanism": "systemd-run"`、`"usable": true`
（若当前会话 direct sudo 可用则为 `"direct"`）。

- [ ] **Step 3: 现场真实打包**

```bash
python3 harness/skills/workspace-verify/ws_package.py --mode 0 \
  --evidence-file harness/log/workspace-verify/package-priv-allowlist.json
```

Expected: rc=0；证据 `script_rc=0`、`images_ok=true`、
`privilege.mechanism=systemd-run`（或 direct）、`privilege.context_sudo_ok=true`、
`privilege.audit` 指向 `harness/log/privileged/*.json`。

- [ ] **Step 4: 全量 selfcheck**

```bash
env -u LC_VERIFY_ADB_HOST -u LC_VERIFY_ADB_PORT python3 harness/lib/selfcheck.py \
  | tee /tmp/opencode/priv-selfcheck.txt
```

Expected: `pytest_rc=0`，全部 `*_rc=0`。若只有 `commit_coverage_rc=1`
（本次收据尚未落盘），可继续 Step 5；`ws_report --manual` 会按
`range_uncovered_fully_covered` 逐 sha 校验本收据 scope 后豁免，其余 rc 必须为 0。

- [ ] **Step 5: 写 manual 收据**

先按 `LOCAL-DEV-001~003` 写 body 文件（逐项三态）：

```markdown
- 完成：privileged_exec/allowlist 核心与单测（调用方：ws_package/CLI；验证：test_privileged_exec.py）
- 完成：ws_package/ws_verify_chain 接入（调用方：打包与 verify 链；验证：test_ws_package.py/test_ws_verify_chain.py）
- 完成：规则/BLD-013/SKILL 文档同步（验证：check_skill_refs.py/selfcheck）
```

生成收据（`--selfcheck` 直接读取 Step 4 落盘的单行结果）：

```bash
python3 harness/skills/workspace-verify/ws_report.py \
  --manual 4c70de1..HEAD \
  --result skip --build skip --board skip \
  --summary "特权 allowlist 与打包 sudo 绕过机制" \
  --selfcheck "$(cat /tmp/opencode/priv-selfcheck.txt)" \
  --body /tmp/opencode/priv-allowlist-body.md
```

Expected: rc=0，收据落 `data/verify-results/`，`commit_scope` 覆盖本次非 meta 提交。

- [ ] **Step 6: 提交收据（meta）**

```bash
git add data/verify-results/
git commit -m "构建(harness): 特权 allowlist 验证收据"
```

- [ ] **Step 7: 最终检查**

```bash
git status --short
git log --oneline -5
```

Expected: 工作区干净；提交面包含核心、集成、文档、收据，且收据覆盖全部非 meta 提交。
