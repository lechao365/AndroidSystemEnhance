"""harness_path_util — Python 统一路径工具

规则详见: engineering/harness/rules/path-management.md (PATH-001)

职责:
    1. 从 __file__ 向上查找 REPO_ROOT（AGENTS.md 锚点，与 shell 端一致）
    2. 加载 config/paths.conf（单一事实源）
    3. 提供路径查询 API

公共 API:
    repo_root() -> Path              返回 REPO_ROOT 绝对路径
    path(key) -> Path                返回 paths.conf 中 KEY 对应的绝对路径
    env_path(key) -> str             返回环境可覆盖路径
    pythonpath() -> list[str]        返回 Python 包根绝对路径列表
    ensure_dir(key) -> Path          path() + mkdir(parents=True, exist_ok=True)

用法:
    from harness_path_util import path, ensure_dir
    log_dir = path("HOST_LOG_DIR")
    ensure_dir("HOST_LOG_DIR")
"""

from __future__ import annotations

import os
import re
from pathlib import Path


_CONF_CACHE: dict[str, str] | None = None


def _find_repo_root() -> Path:
    """从本文件位置向上查找包含 AGENTS.md 的祖先目录。"""
    p = Path(__file__).resolve()
    for parent in p.parents:
        if (parent / "AGENTS.md").is_file():
            return parent
    raise RuntimeError(
        "harness_path_util: 找不到 repo root（AGENTS.md 锚点缺失），"
        f"from {p}"
    )


def _load_conf() -> dict[str, str]:
    """解析 paths.conf，返回 key->value 字典。"""
    global _CONF_CACHE
    if _CONF_CACHE is not None:
        return _CONF_CACHE

    root = _find_repo_root()
    conf_file = root / "engineering" / "harness" / "config" / "paths.conf"
    if not conf_file.is_file():
        raise FileNotFoundError(f"paths.conf 不存在: {conf_file}")

    conf: dict[str, str] = {}
    pattern = re.compile(r'^\s*([A-Za-z_][A-Za-z0-9_]*)="(.*)"\s*$')
    for line in conf_file.read_text(encoding="utf-8").splitlines():
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = pattern.match(line)
        if m:
            conf[m.group(1)] = m.group(2)

    _CONF_CACHE = conf
    return conf


def repo_root() -> Path:
    """返回 REPO_ROOT 绝对路径。"""
    return _find_repo_root()


def _expand_env(val: str) -> str:
    """展开字符串中的环境变量（如 $HOME / ${HOME}）。"""
    return os.path.expandvars(os.path.expanduser(val))


def path(key: str) -> Path:
    """返回 paths.conf 中 KEY 对应的绝对路径。

    相对路径基于 REPO_ROOT 解析；已是绝对路径则原样返回。
    字符串中的 $HOME 等环境变量会被展开。
    """
    conf = _load_conf()
    if key not in conf:
        raise KeyError(f"harness_path_util.path: 未知的路径 key '{key}'")
    val = _expand_env(conf[key])
    p = Path(val)
    if not p.is_absolute():
        p = repo_root() / p
    return p


def env_path(key: str) -> str:
    """返回环境可覆盖路径（KEY 为 paths.conf 中的 ENV_* 键）。

    ENV_* 值中的 ${ENV_VAR:-default} 会被展开。
    """
    conf = _load_conf()
    if key not in conf:
        raise KeyError(f"harness_path_util.env_path: 未知的路径 key '{key}'")
    return _expand_env(conf[key])


def pythonpath() -> list[str]:
    """返回 Python 包根绝对路径列表（对应 PYTHON_PATH_ROOTS）。"""
    conf = _load_conf()
    roots = conf.get("PYTHON_PATH_ROOTS", "")
    if not roots:
        return []
    result: list[str] = []
    for root in roots.split(":"):
        root = root.strip()
        if not root:
            continue
        p = Path(_expand_env(root))
        if not p.is_absolute():
            p = repo_root() / p
        result.append(str(p))
    return result


def ensure_dir(key: str) -> Path:
    """path(key) + mkdir(parents=True, exist_ok=True)。"""
    d = path(key)
    d.mkdir(parents=True, exist_ok=True)
    return d
