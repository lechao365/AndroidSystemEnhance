"""cross-device 共享路径解析与原子写原语（批次四 A5 上移 harness/lib）。

规则：CDP_PROJECT_ROOT 环境变量可覆盖项目根；默认自动探测——本文件位于
harness/lib/，向上回退 2 级（parents[2]）即项目根。仓内状态目录统一为
<project_root>/data/verify-results/（仅 apply 侧写；emit 侧只读传入显式路径）。

兼容：harness/skills/cross-device/lib/python/cdp_paths.py 为 re-export 垫片
（cdp_timing/cdp_receipt 等以 sys.path 同目录方式 import cdp_paths 的消费方
行为不变）；新代码请直接 from harness.lib.cdp_paths import ...
"""
import os
import threading
from pathlib import Path


def atomic_write_text(path, content, encoding="utf-8"):
    """原子写文本（跨模块统一原语，P1-2 收口）：tmp 文件名带 pid + 线程 id
    防并发互写（旧固定 .tmp 名下两进程/同进程两线程同写可产出损坏文件；
    lib-13 补 threading.get_ident()——同 pid 多线程并发写同路径此前互写），
    写后 os.replace——中断不留半写态（半写收据/issue 曾可按 latest 身份
    进入 promote 判定）。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(content, encoding=encoding)
    tmp.replace(path)


def project_root() -> Path:
    root = os.environ.get("CDP_PROJECT_ROOT")
    if root:
        return Path(root)
    return Path(__file__).resolve().parents[2]  # lib -> harness -> 项目根


def data_verify_results_dir() -> Path:
    """apply 侧写收据用（会 mkdir）；emit 侧勿调用（用 project_root()/"data"/"verify-results" 只读）。"""
    d = project_root() / "data" / "verify-results"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_baselines_dir() -> Path:
    """证据快照目录（会 mkdir）：data/baselines。

    promote 阶段把 verify 收据副本固化为 <baseline_id>-<收据名>.md，
    随登记 yaml 一并提交入库，作为晋升证据链的落盘快照。
    """
    d = project_root() / "data" / "baselines"
    d.mkdir(parents=True, exist_ok=True)
    return d


def data_known_issues_dir() -> Path:
    """已知问题登记目录（会 mkdir）；与收据同源，仅 apply 侧写。"""
    d = project_root() / "data" / "known-issues"
    d.mkdir(parents=True, exist_ok=True)
    return d


def log_apply_dir() -> Path:
    """cross-device 工作态目录（会 mkdir）：批次临时文件、链路耗时打点文件。

    gitignore 工作态（不入库）；打点文件 timings-<batch_id>.json 由 cdp_timing.py
    start 创建、finish 落盘，最终数据经 ws_report --timings-file 并入收据持久化。
    """
    d = project_root() / "harness" / "log" / "cross-device"
    d.mkdir(parents=True, exist_ok=True)
    return d