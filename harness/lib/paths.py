"""paths — 项目内路径工具（读取 harness/config/paths.conf，支持环境变量覆盖）

设计说明：迁移自 LcHarness 同源模块（harness_path_util / resolve_conf_refs /
local_paths）的精简版。仅保留本项目需要的路径能力；去掉了 LcHarness 的 catalog/
registry/packs 发现、profile.yaml 锚点、${...} 跨引用等通用机制。

双名加载收敛（批次四 A1）：本模块历史上有两种 import 方式并存——
`from paths import ...`（sys.path 含 harness/lib）与
`from harness.lib.paths import ...`（sys.path 含仓库根）。Python 会为两个
模块名创建独立模块对象，_CONF 缓存随之分裂（改了一半的隐患）。模块尾部的
sys.modules 别名注册使两种名字拿到同一模块对象，单一事实源语义成立。
"""

from __future__ import annotations

import os
import re
import sys as _sys
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
    """展开 ${VAR} / ${VAR:-default} 环境变量引用（不支持嵌套）。

    展开后若以 ~ 开头再经 expanduser 展开：`~/workspace` 未展开时
    Path("~/workspace").is_absolute() 为 False，会被 path() 拼成
    repo_root()/~/workspace 的错误相对路径。
    """
    def _sub(m: re.Match) -> str:
        name, default = m.group(1), m.group(2)
        value = os.environ.get(name, "")
        if value:
            return value
        return default if default is not None else ""
    return os.path.expanduser(_ENV_RE.sub(_sub, val))


def _load_conf() -> dict[str, str]:
    global _CONF
    if _CONF is not None:
        return _CONF
    conf_file = repo_root() / "harness" / "config" / "paths.conf"
    if not conf_file.is_file():
        # C3：conf 缺失不再静默降级为空配置（后续 path() 报"未知 key"
        # 误导排查）——首次加载即 stderr 留痕，指明缺失路径
        print(f"warn: paths.conf 缺失: {conf_file}（路径解析将回落空配置）",
              file=_sys.stderr)
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
        raise KeyError(f"paths.path: 未知的路径 key '{key}'（conf: "
                       f"{repo_root() / 'harness' / 'config' / 'paths.conf'}）")
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


# 数据目录名单一事实源（R2 字面量收敛）：仓内 data/ 下各数据目录名与相对
# 路径常量集中于此（本模块有双名加载收敛，harness/lib 各模块以 `from paths
# import ...` 复用，不会被误缓存为跨设备 cdp_paths 垫片语义）。消费方两类
# 语义——「工程根绝对定位」（cdp_paths.data_*_dir）与「相对传入 root 只读
# 定位」（check_known_issues/check_commit_coverage/metrics/selfcheck）；后者
# 只复用目录名/相对路径常量，不得误用会 mkdir 的 base 目录函数。
DATA_DIRNAME = "data"
DATA_VERIFY_RESULTS_DIRNAME = "verify-results"
DATA_KNOWN_ISSUES_DIRNAME = "known-issues"
# 相对仓根路径（供 git 路径前缀匹配、EXCLUDE_PATHS 等字符串场景复用）
DATA_VERIFY_RESULTS_REL = f"{DATA_DIRNAME}/{DATA_VERIFY_RESULTS_DIRNAME}"
DATA_KNOWN_ISSUES_REL = f"{DATA_DIRNAME}/{DATA_KNOWN_ISSUES_DIRNAME}"


def config_dir() -> Path:
    """返回 harness/config/ 目录。"""
    return repo_root() / "harness" / "config"


def log_dir() -> Path:
    """返回 harness/log/ 目录（脚本产物落盘，不存在则创建）。"""
    d = repo_root() / "harness" / "log"
    d.mkdir(parents=True, exist_ok=True)
    return d


# 双名别名注册（模块尾部，见 docstring「双名加载收敛」）：两种 import 名
# 共享同一模块对象，_CONF 等模块级状态不再分裂。双向注册：无论以哪个名字
# 首次加载，另一名字即刻可用。
_OTHER_NAME = "harness.lib.paths" if __name__ == "paths" else "paths"
if __name__ in ("paths", "harness.lib.paths") and _OTHER_NAME not in _sys.modules:
    _sys.modules[_OTHER_NAME] = _sys.modules[__name__]
    if _OTHER_NAME == "harness.lib.paths":
        # 短名先加载场景：别名仅写 sys.modules 不够——`import harness.lib.paths`
        # 与 `from harness.lib import paths` 命中缓存后不会回填父包属性链，
        # 后续 harness.lib.paths 属性访问仍 AttributeError。显式加载 harness.lib
        # 并绑定属性；harness 包不可定位（sys.path 无仓库根）时静默跳过
        # （sys.modules 别名仍生效，加载不得因别名块失败而中断）。
        try:
            import importlib as _importlib
            _parent = _importlib.import_module("harness.lib")
            setattr(_parent, "paths", _sys.modules[__name__])
        except ImportError:
            pass
