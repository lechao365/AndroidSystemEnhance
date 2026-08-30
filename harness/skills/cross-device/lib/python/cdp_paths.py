"""cross-device pack 共享路径解析。

规则：CDP_PROJECT_ROOT 环境变量可覆盖项目根；默认自动探测——本文件位于
harness/skills/cross-device/lib/python/，向上回退 3 级（parents[2]）即
cross-device 包目录，parents[4] 为项目根。仓内状态目录统一为
<project_root>/data/verify-results/（仅 apply 侧写；emit 侧只读传入显式路径）。
"""
import os
from pathlib import Path

_PACK_DIR = Path(__file__).resolve().parents[2]  # .../cross-device


def project_root() -> Path:
    root = os.environ.get("CDP_PROJECT_ROOT")
    if root:
        return Path(root)
    return _PACK_DIR.parents[2]  # cross-device -> skills -> harness -> 项目根


def data_verify_results_dir() -> Path:
    """apply 侧写收据用（会 mkdir）；emit 侧勿调用（用 project_root()/"data"/"verify-results" 只读）。"""
    d = project_root() / "data" / "verify-results"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_known_issues_dir() -> Path:
    """已知问题登记目录（会 mkdir）；与收据同源，仅 apply 侧写。"""
    d = project_root() / "data" / "known-issues"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_apply_dir() -> Path:
    """cross-device-apply 工作态目录（会 mkdir）：批次临时文件、链路耗时打点文件。

    gitignore 工作态（不入库）；打点文件 timings-<batch_id>.json 由 cdp_timing.py
    start 创建、finish 落盘，最终数据经 ws_report --timings-file 并入收据持久化。
    """
    d = project_root() / "harness" / "log" / "cross-device-apply"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cdp_parse_script() -> Path:
    return _PACK_DIR / "lib" / "python" / "cdp_parse.py"