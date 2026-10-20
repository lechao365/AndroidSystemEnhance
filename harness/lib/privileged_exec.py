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
import pwd
import re
import shutil
import signal
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
_ENV_DESTRUCTIVE = "LC_PRIVILEGE_ALLOW_DESTRUCTIVE"
_ENV_HELPER = "LC_PRIVILEGE_HELPER"
_CONTEXT_ENV = "LC_PRIVILEGE_CONTEXT"
_HELPER_HINT = (f"；如已配置外置 helper，请设置 {_ENV_HELPER}"
                "（本批不执行 helper）")
_SAFE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
_SAFE_ENV_KEYS = ("HOME", "USER", "LANG")
_TRUSTED_ROOT_KEYS = {
    "AOSP_WS", "AOSP_ROOT", "ANDROID_PRODUCT_OUT", "KERNEL_WS",
}
_ENV_DENY_EXACT = {
    "BASH_ENV", "ENV", "SHELLOPTS", "BASHOPTS", "CDPATH", "GLOBIGNORE",
    "PERL5LIB", "NODE_OPTIONS", "RUBYOPT", "GCONV_PATH", "IFS",
    "SUDO_ASKPASS", "LOCPATH", "NLSPATH",
}
_ENV_DENY_PREFIXES = ("LD_", "DYLD_", "PYTHON", "MALLOC_")

_MECHANISMS = {"auto", "direct", "systemd-run"}
_INTERPRETERS = {"bash", "python3"}
_ENTRY_KEYS = {
    "id", "description", "command", "interpreter", "args",
    "requires_privilege", "as_root", "trusted_roots",
    "mechanism", "timeout_s", "env_passthrough", "reason_required",
    "enabled", "destructive", "human_confirm", "approved_by",
    "approved_at", "expires_at",
}
_TOP_KEYS = {"version", "default", "entries"}
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ID_RE = re.compile(r"^[A-Za-z0-9._-]+$")
_RUN_ID_RE = re.compile(r"[^A-Za-z0-9._-]")
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
    trusted_roots: tuple[tuple[str, Path], ...] = ()


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

    @property
    def audit_file(self) -> str:
        """对齐 spec 命名。"""
        return self.audit_path


def _repo_root() -> Path:
    return _ROOT


def _default_allowlist_path() -> Path:
    return _repo_root() / _DEFAULT_ALLOWLIST


def _audit_dir() -> Path:
    return _repo_root() / "harness" / "log" / "privileged"


def _is_within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _which(name: str) -> str | None:
    """仅用安全 PATH 解析，不信任调用方环境。"""
    return shutil.which(name, path=_SAFE_PATH)


def _env_name_allowed(name: str) -> bool:
    if name in _ENV_DENY_EXACT:
        return False
    return not name.startswith(_ENV_DENY_PREFIXES)


def _trusted_identity() -> tuple[str, str]:
    try:
        pw = pwd.getpwuid(os.getuid())
        return pw.pw_dir, pw.pw_name
    except (KeyError, OSError):
        return "/", ""


def _validate_path_env(entry: AllowlistEntry, target: Mapping[str, str],
                       cwd_abs: Path) -> None:
    """受控路径 env 只接受 allowlist 静态 trusted_roots。"""
    trusted = dict(entry.trusted_roots)
    for key in _TRUSTED_ROOT_KEYS:
        if key not in target:
            continue
        if key not in trusted:
            raise PrivilegeError(
                "PRIV_ENV_PATH_DENIED",
                f"{key} 未在条目 trusted_roots 中登记")
        value = target[key]
        try:
            raw_path = Path(value)
            if not raw_path.is_absolute():
                attempted = (cwd_abs / raw_path).resolve()
                raise PrivilegeError(
                    "PRIV_ENV_PATH_DENIED",
                    f"{key} 必须是绝对路径（按 cwd 解析为 {attempted}）: "
                    f"{value}")
            resolved = raw_path.resolve()
        except PrivilegeError:
            raise
        except (OSError, ValueError) as exc:
            raise PrivilegeError(
                "PRIV_ENV_PATH_DENIED",
                f"{key} 路径非法: {exc}") from exc
        expected = trusted[key]
        if key == "ANDROID_PRODUCT_OUT":
            if not _is_within(resolved, expected):
                raise PrivilegeError(
                    "PRIV_ENV_PATH_DENIED",
                    f"ANDROID_PRODUCT_OUT 不在 trusted_roots 内: {resolved}")
        elif resolved != expected:
            raise PrivilegeError(
                "PRIV_ENV_PATH_DENIED",
                f"{key} 与 trusted_roots 不一致: {resolved}")


def _build_target_env(entry: AllowlistEntry,
                      env: Mapping[str, str] | None,
                      cwd_abs: Path) -> dict[str, str]:
    """构造目标命令环境：安全 PATH/身份 + allowlist 白名单键。"""
    source = os.environ if env is None else env
    home, user = _trusted_identity()
    target = {"PATH": _SAFE_PATH, "HOME": home, "USER": user}
    lang = source.get("LANG") or os.environ.get("LANG")
    if lang is not None:
        target["LANG"] = lang
    for key in entry.env_passthrough:
        # PATH/HOME/USER 使用可信值，忽略调用方传入。
        if key in ("PATH", "HOME", "USER"):
            continue
        if key in source:
            target[key] = source[key]
    target["PYTHONNOUSERSITE"] = "1"
    target[_CONTEXT_ENV] = "1"
    _validate_path_env(entry, target, cwd_abs)
    return target


def _build_systemd_client_env() -> dict[str, str]:
    """systemd-run/probe 客户端最小环境（D-Bus 基础设施不传给 unit）。"""
    home, user = _trusted_identity()
    client_env = {
        "PATH": _SAFE_PATH,
        "HOME": home,
        "USER": user,
        "PYTHONNOUSERSITE": "1",
        _CONTEXT_ENV: "1",
    }
    lang = os.environ.get("LANG")
    if lang is not None:
        client_env["LANG"] = lang
    for key in ("XDG_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS"):
        value = os.environ.get(key)
        if value is not None:
            client_env[key] = value
    return client_env


def _sanitize_id(value: str) -> str:
    safe = _RUN_ID_RE.sub("_", value or "")[:64]
    return safe or "unknown"


def _safe_run_id() -> str:
    raw = os.environ.get("CDP_RUN_ID", "")
    return _RUN_ID_RE.sub("_", raw)[:64] or uuid.uuid4().hex


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


def _resolve_command(command: str, must_exist: bool = True) -> Path:
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
    if must_exist and not resolved.is_file():
        raise PrivilegeError("PRIV_COMMAND_DENIED", f"命令不存在: {resolved}")
    return resolved


def _parse_entry(raw: Mapping) -> AllowlistEntry:
    if not isinstance(raw, Mapping):
        raise PrivilegeError("PRIV_SCHEMA", "entries 项必须是映射")
    unknown = set(raw) - _ENTRY_KEYS
    if unknown:
        raise PrivilegeError(
            "PRIV_SCHEMA",
            f"条目含未知键: {', '.join(sorted(map(str, unknown)))}")
    entry_id = _check_text(raw.get("id"), "id")
    if not _ID_RE.fullmatch(entry_id):
        raise PrivilegeError(
            "PRIV_SCHEMA",
            f"id 仅允许 [A-Za-z0-9._-]+: {entry_id}")
    if len(entry_id) > 64:
        raise PrivilegeError("PRIV_SCHEMA", f"id 长度不得超过 64: {entry_id}")
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
        if not _env_name_allowed(value):
            raise PrivilegeError(
                "PRIV_SCHEMA",
                f"{entry_id}.env_passthrough 危险变量被拒绝: {value}")
        env_passthrough.append(value)
    trusted_raw = raw.get("trusted_roots", {})
    if not isinstance(trusted_raw, Mapping):
        raise PrivilegeError(
            "PRIV_SCHEMA", f"{entry_id}.trusted_roots 必须是映射")
    trusted_roots: list[tuple[str, Path]] = []
    for key, value in trusted_raw.items():
        if key not in _TRUSTED_ROOT_KEYS:
            raise PrivilegeError(
                "PRIV_SCHEMA",
                f"{entry_id}.trusted_roots 非法键: {key}")
        if not isinstance(value, str) or not value:
            raise PrivilegeError(
                "PRIV_SCHEMA",
                f"{entry_id}.trusted_roots.{key} 必须是非空字符串")
        try:
            path = Path(value)
            if not path.is_absolute():
                raise PrivilegeError(
                    "PRIV_SCHEMA",
                    f"{entry_id}.trusted_roots.{key} 必须是绝对路径: {value}")
            resolved = path.resolve()
        except PrivilegeError:
            raise
        except (OSError, ValueError) as exc:
            raise PrivilegeError(
                "PRIV_SCHEMA",
                f"{entry_id}.trusted_roots.{key} 路径非法: {exc}") from exc
        if not resolved.is_absolute():
            raise PrivilegeError(
                "PRIV_SCHEMA",
                f"{entry_id}.trusted_roots.{key} resolve 后不是绝对路径")
        trusted_roots.append((key, resolved))
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
    approved_by = _str_field(raw, "approved_by").strip()
    approved_at = _str_field(raw, "approved_at").strip()
    if enabled and (not approved_by or not approved_at):
        raise PrivilegeError(
            "PRIV_SCHEMA", f"{entry_id} 启用必须填 approved_by/approved_at")
    if enabled and "requires_privilege" not in raw:
        raise PrivilegeError(
            "PRIV_SCHEMA", f"{entry_id} 启用必须显式声明 requires_privilege")
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
        command_abs=_resolve_command(command, must_exist=enabled),
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
        trusted_roots=tuple(trusted_roots),
    )


def load_allowlist() -> dict[str, AllowlistEntry]:
    allowlist_path = _default_allowlist_path()
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
            f"allowlist 含未知键: {', '.join(sorted(map(str, unknown)))}")
    version = raw.get("version")
    if isinstance(version, bool) or not isinstance(version, int) or version != 1:
        raise PrivilegeError(
            "PRIV_ALLOWLIST_INVALID", "allowlist version 只接受 1")
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
    nnp = _no_new_privs()
    sudo_bin = _which("sudo")
    systemd_run_bin = _which("systemd-run")
    client_env = _build_systemd_client_env()
    systemd_run = systemd_run_bin is not None
    sudo_n = False
    context_sudo_ok = False
    details: list[str] = []

    if nnp:
        # NoNewPrivileges=1 下父进程 sudo 必然被拒，跳过无意义探测。
        details.append("NoNewPrivs=1，跳过父进程 sudo 探测")
    elif sudo_bin:
        try:
            proc = subprocess.run(
                [sudo_bin, "-n", "true"], capture_output=True, text=True,
                encoding="utf-8", errors="replace", env=client_env,
                timeout=30)
            sudo_n = proc.returncode == 0
            if sudo_n:
                details.append("sudo direct ok")
            else:
                details.append((proc.stderr or "").strip()[-200:]
                               or f"sudo -n true rc={proc.returncode}")
        except subprocess.TimeoutExpired:
            details.append("sudo -n true 超时（>30s）")
        except OSError as exc:
            details.append(f"sudo 不可执行: {exc}")
    else:
        details.append("sudo 不在 PATH")

    # 只要 systemd-run 可用就实际探测 transient context，即使 direct 可用。
    if systemd_run_bin and sudo_bin:
        try:
            check = subprocess.run(
                [systemd_run_bin, "--user", "--quiet", "--wait", "--pipe",
                 "--collect", "--property=NoNewPrivileges=no", "--",
                 sudo_bin, "-n", "true"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", env=client_env, timeout=30)
            context_sudo_ok = check.returncode == 0
            details.append("systemd-run context sudo ok"
                           if context_sudo_ok else
                           ((check.stderr or "").strip()[-200:]
                            or f"systemd-run context sudo rc={check.returncode}"))
        except subprocess.TimeoutExpired:
            details.append("systemd-run context sudo 超时（>30s）")
        except OSError as exc:
            details.append(f"systemd-run 不可执行: {exc}")
    elif not systemd_run_bin:
        details.append("systemd-run 不在 PATH")
    # sudo 缺失的情况已在上面记录，避免重复。

    if sudo_n:
        mechanism = "direct"
    elif systemd_run_bin:
        mechanism = "systemd-run"
    else:
        mechanism = "none"
    usable = sudo_n or context_sudo_ok
    detail = "；".join(item for item in details if item)
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
        interpreter = _which(entry.interpreter)
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
            (result.detail or "direct sudo 与 systemd-run context 均不可用")
            + _HELPER_HINT)
    mechanism = entry.mechanism
    if mechanism == "auto":
        mechanism = "direct" if result.sudo_n else "systemd-run"
    if mechanism == "direct":
        if not result.sudo_n:
            raise PrivilegeError(
                "PRIV_SUDO_UNAVAILABLE",
                "direct sudo 不可用；检查 NOPASSWD 或改用 systemd-run"
                + _HELPER_HINT)
        return ("direct", result.sudo_n, result.no_new_privs,
                result.detail, result.context_sudo_ok)
    if not result.systemd_run:
        raise PrivilegeError(
            "PRIV_SYSTEMD_UNAVAILABLE",
            "systemd-run 不可用；检查 systemd user manager" + _HELPER_HINT)
    if not result.context_sudo_ok:
        raise PrivilegeError(
            "PRIV_SUDO_UNAVAILABLE",
            "systemd-run context sudo 不可用" + _HELPER_HINT)
    return ("systemd-run", result.sudo_n, result.no_new_privs,
            result.detail, result.context_sudo_ok)


def _systemd_cmd(entry: AllowlistEntry, canonical: Sequence[str],
                 cwd_abs: Path, env: Mapping[str, str],
                 timeout: int) -> list[str]:
    systemd_run = _which("systemd-run")
    if not systemd_run:
        raise PrivilegeError(
            "PRIV_SYSTEMD_UNAVAILABLE",
            "systemd-run 不在 PATH" + _HELPER_HINT)
    command = [
        systemd_run, "--user", "--quiet", "--wait", "--pipe", "--collect",
        "--property=NoNewPrivileges=no",
        f"--property=RuntimeMaxSec={timeout + 30}s",
        f"--working-directory={cwd_abs}",
        f"--setenv={_CONTEXT_ENV}=1",
    ]
    for key in dict.fromkeys(
            ("PATH", "PYTHONNOUSERSITE", *_SAFE_ENV_KEYS,
             *entry.env_passthrough)):
        if key in env:
            command.append(f"--setenv={key}={env[key]}")
    command.append("--")
    if entry.as_root:
        sudo_bin = _which("sudo")
        if not sudo_bin:
            raise PrivilegeError(
                "PRIV_SUDO_UNAVAILABLE", "sudo 不在 PATH" + _HELPER_HINT)
        command.extend([sudo_bin, "-n", "--"])
    command.extend(canonical)
    return command


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except OSError:
        try:
            proc.kill()
        except OSError:
            pass


def _execute(command: Sequence[str], cwd: Path, env: Mapping[str, str],
             timeout: int) -> tuple[int, str, str]:
    """执行命令并回收超时进程组，返回 (rc, output_tail, error)。"""
    try:
        proc = subprocess.Popen(
            list(command), cwd=str(cwd), env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            start_new_session=True)
    except OSError as exc:
        return 127, "", f"启动失败: {exc}"
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
        tail = ((stdout or "") + (stderr or ""))[-2000:]
        return proc.returncode, tail, ""
    except KeyboardInterrupt:
        _kill_process_group(proc)
        try:
            proc.communicate(timeout=5)
        except (subprocess.TimeoutExpired, OSError):
            pass
        raise
    except subprocess.TimeoutExpired:
        _kill_process_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            stdout, stderr = "", ""
        except OSError:
            stdout, stderr = "", ""
        tail = ((stdout or "") + (stderr or ""))[-2000:]
        return 124, tail, f"PRIV_TIMEOUT: 超过 {timeout}s"


def _secure_write_audit(filename: str,
                        payload: Mapping) -> tuple[str, str]:
    try:
        audit_dir = _audit_dir().resolve()
        if not audit_dir.exists():
            try:
                audit_dir.mkdir(parents=True, mode=0o700, exist_ok=False)
                os.chmod(audit_dir, 0o700)
            except FileExistsError:
                pass
        path = (audit_dir / filename).resolve()
        if not _is_within(path, audit_dir):
            return "", "PRIV_AUDIT_WRITE_FAILED: 审计路径逃逸"
        atomic_write_json(path, payload)
        os.chmod(path, 0o600)
    except Exception as exc:
        return "", f"PRIV_AUDIT_WRITE_FAILED: {exc}"
    return str(path), ""


def _write_audit(entry: AllowlistEntry, canonical: Sequence[str],
                 cwd_abs: Path, mechanism: str, sudo_n: bool,
                 no_new_privs: bool, context_sudo_ok: bool, rc: int,
                 dur_s: float, reason: str, error: str) -> tuple[str, str]:
    run_id = _safe_run_id()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    filename = (f"{stamp}-{entry.id}-{run_id}-"
                f"{uuid.uuid4().hex[:8]}.json")
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
        "reason": (reason or "")[:2000],
        "destructive": entry.destructive,
        "user": os.environ.get("USER", ""),
        "error": (error or "")[:2000],
    }
    try:
        return _secure_write_audit(filename, payload)
    except Exception as exc:
        return "", f"PRIV_AUDIT_WRITE_FAILED: {exc}"


def _write_rejection_audit(entry_id: str, code: str, reason: str,
                           error: str, *, argv: Sequence[str] | None = None,
                           cwd: str = "",
                           destructive: bool = False) -> tuple[str, str]:
    """尽力记录拒绝审计；失败由调用方忽略，不掩盖原始异常。"""
    run_id = _safe_run_id()
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    filename = (f"{stamp}-reject-{_sanitize_id(entry_id)}-{run_id}-"
                f"{uuid.uuid4().hex[:8]}.json")
    payload = {
        "schema_version": 1,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "entry_id": (entry_id or "")[:200],
        "code": code,
        "argv": list(argv or []),
        "cwd": cwd or "",
        "reason": (reason or "")[:2000],
        "error": (error or "")[:2000],
        "rc": None,
        "mechanism": "",
        "sudo_n_parent": False,
        "sudo_n_context": False,
        "run_id": run_id,
        "batch_id": os.environ.get("CDP_BATCH_ID", ""),
        "operator": os.environ.get("USER", ""),
        "host": os.uname().nodename,
        "destructive": bool(destructive),
    }
    return _secure_write_audit(filename, payload)


def _run_impl(entry_id: str, argv: Sequence[str], *, reason: str = "",
              timeout: int | None = None, cwd: str | None = None,
              env: Mapping[str, str] | None = None,
              confirm_destructive: bool = False,
              _rejection_ctx: dict | None = None) -> RunResult:
    entries = load_allowlist()
    entry = entries.get(entry_id)
    if entry is None:
        raise PrivilegeError(
            "PRIV_ENTRY_UNKNOWN",
            f"未登记条目: {entry_id}（可选: {', '.join(sorted(entries))}）")
    if _rejection_ctx is not None:
        _rejection_ctx["destructive"] = entry.destructive
    effective_timeout, cwd_abs = _validate_entry(
        entry, reason, timeout, cwd, confirm_destructive)
    canonical = _canonical_argv(entry, argv, cwd_abs)
    probe_result = probe() if entry.requires_privilege else ProbeResult(
        False, _no_new_privs(), _which("systemd-run") is not None,
        "direct", True, "无提权需求", False)
    mechanism, sudo_n, no_new_privs, detail, context_sudo_ok = \
        _select_mechanism(entry, probe_result)
    target_env = _build_target_env(entry, env, cwd_abs)
    if mechanism == "direct":
        command = []
        if entry.as_root:
            sudo_bin = _which("sudo")
            if not sudo_bin:
                raise PrivilegeError(
                    "PRIV_SUDO_UNAVAILABLE",
                    "sudo 不在 PATH" + _HELPER_HINT)
            command.extend([sudo_bin, "-n", "--"])
        command.extend(canonical)
        run_env = target_env
        subprocess_timeout = effective_timeout
    else:
        command = _systemd_cmd(entry, canonical, cwd_abs, target_env,
                               effective_timeout)
        run_env = _build_systemd_client_env()
        subprocess_timeout = effective_timeout + 60
    started = time.monotonic()
    rc, output_tail, error = _execute(
        command, cwd_abs, run_env, subprocess_timeout)
    dur_s = time.monotonic() - started
    if rc != 0 and not error:
        error = f"命令 rc={rc}（{detail}）"
    audit_path, audit_error = _write_audit(
        entry, canonical, cwd_abs, mechanism, sudo_n, no_new_privs,
        context_sudo_ok, rc, dur_s, reason, error)
    if audit_error:
        error = (error + "；" if error else "") + audit_error
    return RunResult(
        rc=rc, entry_id=entry.id, mechanism=mechanism, sudo_n=sudo_n,
        no_new_privs=no_new_privs, context_sudo_ok=context_sudo_ok,
        dur_s=round(dur_s, 3), audit_path=audit_path,
        output_tail=output_tail, error=error)


def run(entry_id: str, argv: Sequence[str], *, reason: str = "",
        timeout: int | None = None, cwd: str | None = None,
        env: Mapping[str, str] | None = None,
        confirm_destructive: bool = False) -> RunResult:
    ctx: dict = {"destructive": False}
    try:
        return _run_impl(
            entry_id, argv, reason=reason, timeout=timeout, cwd=cwd,
            env=env, confirm_destructive=confirm_destructive,
            _rejection_ctx=ctx)
    except PrivilegeError as exc:
        try:
            _write_rejection_audit(
                entry_id, exc.code, reason, str(exc), argv=argv,
                cwd=cwd or "", destructive=bool(ctx.get("destructive")))
        except Exception:
            pass
        raise


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
    parser.add_argument("argv", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.list:
            entries = load_allowlist()
            for entry in sorted(entries.values(), key=lambda e: e.id):
                print(f"{entry.id}\tenabled={str(entry.enabled).lower()}\t"
                      f"destructive={str(entry.destructive).lower()}\t"
                      f"command={entry.command}\t{entry.description}")
            return 0
        if args.check:
            result = probe()
            entries = load_allowlist()
            print(json.dumps({
                "probe": asdict(result),
                "allowlist_entries": len(entries),
                "allowlist": str(_default_allowlist_path()),
            }, ensure_ascii=False))
            return 0 if result.usable else 3
        command = list(args.argv)
        if command and command[0] == "--":
            command = command[1:]
        result = run(
            args.entry_id, command, reason=args.reason,
            timeout=args.timeout, cwd=args.cwd,
            confirm_destructive=args.confirm_destructive)
        print(json.dumps(asdict(result), ensure_ascii=False))
        return 0 if result.rc == 0 else 1
    except PrivilegeError as exc:
        print(f"error: {exc.code}: {exc}", file=sys.stderr)
        if exc.code in ("PRIV_SUDO_UNAVAILABLE", "PRIV_SYSTEMD_UNAVAILABLE"):
            return 3
        return 2


if __name__ == "__main__":
    sys.exit(_cli())
