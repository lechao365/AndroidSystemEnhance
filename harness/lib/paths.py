"""paths — 项目内路径工具（读取 harness/config/paths.conf，支持环境变量覆盖）

设计说明：迁移自 LcHarness 同源模块（harness_path_util / resolve_conf_refs /
local_paths）的精简版。仅保留本项目需要的路径能力；去掉了 LcHarness 的 catalog/
registry/packs 发现、profile.yaml 锚点、${...} 跨引用等通用机制。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-(.*?))?\}")

_CONF: dict[str, str] | None = None


def repo_root() -> Path:
    """项目根：从本文件向上查找含 AGENTS.md 的目录。"""
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "AGENTS.md").is_file():
            return parent
    raise RuntimeError("paths: 找不到项目根（AGENTS.md 锚点缺失）")


def _expand_env(val: str) -> str:
    """展开 ${VAR} / ${VAR:-default} 环境变量引用（不支持嵌套）。"""
    def _sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        value = os.environ.get(name, "")
        if value:
            return value
        return default if default is not None else ""
    return _ENV_RE.sub(_sub, val)


def _load_conf() -> dict[str, str]:
    global _CONF
    if _CONF is not None:
        return _CONF
    conf_file = repo_root() / "harness" / "config" / "paths.conf"
    if not conf_file.is_file():
        _CONF = {}
        return _CONF
    conf: dict[str, str] = {}
    for line in conf_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        conf[key.strip()] = _expand_env(value.strip().strip('"'))
    _CONF = conf
    return _CONF


def path(key: str) -> Path:
    """返回 paths.conf 中 key 对应的绝对路径（相对路径基于项目根解析）。"""
    conf = _load_conf()
    if key not in conf:
        raise KeyError(f"paths.path: 未知的路径 key '{key}'")
    val = conf[key]
    if not val:
        raise ValueError(f"paths.path: key '{key}' 解析为空值（请设置对应环境变量）")
    p = Path(val)
    if not p.is_absolute():
        p = repo_root() / p
    return p


def env_path(key: str, default: str | None = None) -> str:
    """返回 paths.conf 中 key 的字符串值（已展开环境变量），空时返回 default。"""
    conf = _load_conf()
    val = conf.get(key, "")
    return val if val else (default if default is not None else "")


def config_dir() -> Path:
    """返回 harness/config/ 目录。"""
    return repo_root() / "harness" / "config"


def log_dir() -> Path:
    """返回 harness/log/ 目录（脚本产物落盘，不存在则创建）。"""
    d = repo_root() / "harness" / "log"
    d.mkdir(parents=True, exist_ok=True)
    return d
