"""cross-device pack 共享路径解析。

规则：CDP_PROJECT_ROOT 环境变量可覆盖项目根；默认自动探测——本文件位于
harness/skills/cross-device/lib/python/，向上回退 3 级（parents[2]）即
cross-device 包目录，parents[4] 为项目根。仓内状态目录统一为
<project_root>/data/verify/（仅 apply 侧写；emit 侧只读传入显式路径）。
"""
import os
from pathlib import Path

_PACK_DIR = Path(__file__).resolve().parents[2]  # .../cross-device


def project_root() -> Path:
    root = os.environ.get("CDP_PROJECT_ROOT")
    if root:
        return Path(root)
    return _PACK_DIR.parents[2]  # cross-device -> skills -> harness -> 项目根


def data_verify_dir() -> Path:
    """apply 侧写收据用（会 mkdir）；emit 侧勿调用（用 project_root()/"data"/"verify" 只读）。"""
    d = project_root() / "data" / "verify"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cdp_parse_script() -> Path:
    return _PACK_DIR / "lib" / "python" / "cdp_parse.py"