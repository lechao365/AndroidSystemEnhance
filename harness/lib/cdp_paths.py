"""cross-device 共享路径解析与原子写原语（批次四 A5 上移 harness/lib）。

规则：CDP_PROJECT_ROOT 环境变量可覆盖项目根；默认自动探测——本文件位于
harness/lib/，向上回退 2 级（parents[2]）即项目根。仓内状态目录统一为
<project_root>/data/verify-results/（仅 apply 侧写；emit 侧只读传入显式路径）。

兼容：harness/skills/cross-device/lib/python/cdp_paths.py 为 re-export 垫片
（cdp_timing/cdp_receipt 等以 sys.path 同目录方式 import cdp_paths 的消费方
行为不变）；新代码请直接 from harness.lib.cdp_paths import ...
"""
import contextlib
import os
import sys
import threading
import time
from pathlib import Path


def _data_dir_names():
    """数据目录名常量（单点定义在 paths.py，R2 防 cdp_paths 与消费侧漂移）。

    cdp_paths 以两种名字被加载：cross-device 垫片经仓根 `harness.lib.cdp_paths`
    （仓根在 sys.path，harness/lib 不在）或裸名 `cdp_paths`（harness/lib 在
    sys.path）。两向回落解析同一 paths 模块；两者皆不可解析的受限环境
    （如 publish 集成测试的复制树子进程，sys.path 无 lib/仓根）回落与 paths
    常量同值的本地字面量——保证不崩溃且语义不变（漂移由 test_cdp_paths 的
    一致性守卫兜住）。延迟 import 保证模块加载本身零依赖。
    """
    try:
        from paths import (DATA_DIRNAME, DATA_KNOWN_ISSUES_DIRNAME,
                           DATA_VERIFY_RESULTS_DIRNAME)
    except ImportError:
        try:
            from harness.lib.paths import (DATA_DIRNAME,
                                           DATA_KNOWN_ISSUES_DIRNAME,
                                           DATA_VERIFY_RESULTS_DIRNAME)
        except ImportError:
            return ("data", "verify-results", "known-issues")
    return (DATA_DIRNAME, DATA_VERIFY_RESULTS_DIRNAME,
            DATA_KNOWN_ISSUES_DIRNAME)


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


@contextlib.contextmanager
def file_lock(path, timeout: float = 10.0):
    """文件级跨进程互斥（读→改→写临界区防丢更新，R4 去重共享助手）。

    语义（自 cdp_timing._locked / cdp_receipt._trend_locked 逐字抽取，行为
    不变）：以 `<path>.lock` 旁路锁文件承载 flock 排他锁；非阻塞重试直到
    拿锁或超时（默认 10s），拿不到锁**降级不加锁直写**（打点/趋势属诊断面，
    防临界区卡死整链）；无 fcntl 平台（非 POSIX）直接降级。降级时 stderr
    留告警便于归因，仍不阻断调用方。

    消费方：cdp_timing（timings 打点）与 cdp_receipt（trend.md）——两处
    此前各自同构复制，收拢到本模块（两者均已依赖 cdp_paths，不引入新耦合）。
    """
    try:
        import fcntl
    except ImportError:
        yield
        return
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a")
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    # 拿锁超时降级不加锁直写（行为与抽取前一致，仅增告警）
                    print(f"warn: file_lock 拿锁超时（>{timeout}s），降级不加锁"
                          f"直写: {path}", file=sys.stderr)
                    break
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
        fh.close()


def project_root() -> Path:
    root = os.environ.get("CDP_PROJECT_ROOT")
    if root:
        return Path(root)
    return Path(__file__).resolve().parents[2]  # lib -> harness -> 项目根


def data_verify_results_dir() -> Path:
    """apply 侧写收据用（会 mkdir）；emit 侧勿调用（用 project_root()/"data"/"verify-results" 只读）。"""
    data_dir, verify_dir, _ = _data_dir_names()
    d = project_root() / data_dir / verify_dir
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
    data_dir, _, issues_dir = _data_dir_names()
    d = project_root() / data_dir / issues_dir
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