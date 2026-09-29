#!/usr/bin/env python3
# ============================================================
# check_host_tests.py — host 单测门禁守卫（P0-A 快检）
# 设计目的：源码仓内 gcc 可编译的纯逻辑单测（LcView ring / LcIod
#   read_logic / usb-verify 事件与统计断言 / usb-fault-inject expect
#   schema）此前仅文档纪律（S8）无自动链。接入 selfcheck 以 host_rc 透出
#   ——AI 改动纯逻辑后自检即得编译+单测反馈，无需等完整上板链。
# 判定对象（_HOST_TEST_SPECS，R-18 P5 方向 6/7 扩展）：
#   - code/rpi5/kernel/new/vendor/lechao/{LcView,LcIod}/tests
#   - code/rpi5/others/usb-verify/tests
#   - code/rpi-zero2w/others/usb-fault-inject/tests
#   均执行 `make test`（编译+运行，-Wall -Wextra -Werror）。
# 副本隔离：make 不直接在 code 源码树内跑（产物会落工作树污染 git status、
#   对 dirty 工作树产生非真实验证），先全量拷贝模块源码到仓内 gitignored
#   副本 harness/log/host-tests/<module>，在副本内 make；产物只落副本。
# 产物清理：make test 后必跑 make clean 清副本内 host_test 二进制，收尾
#   rmtree 整个副本（双层清理，code 工作树零残留）。
# fail-closed：make 缺失/目录缺失判红（编译失败不可静默绿）。
# 用法：python3 harness/lib/check_host_tests.py [--repo <仓根>]
# 退出码：0 全过 / 1 任一失败或工具不可用 / 2 参数错误
# ============================================================

import argparse
import os
import shutil
import subprocess
import sys
import threading
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# host 单测模块描述（R-18 P5 方向 6/7 扩展）：
#   (label, module_src_rel, copy_root_rel, stage_name)
#     label           — 报告/日志中的模块名（如 "LcView"、"usb-verify"）
#     module_src_rel  — 模块源码目录（相对 code 仓根），其 tests/ 下跑 make test
#     copy_root_rel   — 拷贝根（相对 code 仓根）：kernel 侧 LcView/LcIod 须拷贝
#                       整个 vendor/lechao 根（调用点 host 单测经 -I../.. 引用
#                       顶层 kernel_lechao_log.h）；others 工具拷贝自身目录即可
#     stage_name      — 副本内子目录名（等于 label，副本下 <label>/tests）
# 当前四模块：
#   - code/rpi5/kernel/new/vendor/lechao/{LcView,LcIod}（内核纯逻辑）
#   - code/rpi5/others/usb-verify（R-18 P5 方向 6：事件名/统计断言/事件断言）
#   - code/rpi-zero2w/others/usb-fault-inject（R-18 P5 方向 7：expect schema）
_HOST_TEST_SPECS = (
    ("LcView", "rpi5/kernel/new/vendor/lechao/LcView",
     "rpi5/kernel/new/vendor/lechao", "LcView"),
    ("LcIod", "rpi5/kernel/new/vendor/lechao/LcIod",
     "rpi5/kernel/new/vendor/lechao", "LcIod"),
    ("usb-verify", "rpi5/others/usb-verify",
     "rpi5/others", "usb-verify"),
    ("usb-fault-inject", "rpi-zero2w/others/usb-fault-inject",
     "rpi-zero2w/others", "usb-fault-inject"),
)

# make 副本根（相对仓根）：/harness/log/ 整体已 gitignore（log/cross-device
# 运行日志同根先例），副本及其 make 产物不污染 code 工作树
_HOST_TEST_STAGE = Path("harness") / "log" / "host-tests"

# make 超时预算（秒）：供 subprocess 与最坏耗时估算共用（KI-20260912-005）
_MAKE_TEST_TIMEOUT_S = 300
_MAKE_CLEAN_TIMEOUT_S = 60
# 全模块最坏耗时上界：每模块 make test + make clean 均可能吃满超时
_HOST_TEST_WORST_S = len(_HOST_TEST_SPECS) * (
    _MAKE_TEST_TIMEOUT_S + _MAKE_CLEAN_TIMEOUT_S)


def _copy_root_rel(spec) -> str:
    return spec[2]


def _module_src_dir(repo: Path, spec) -> Path:
    return repo / "code" / spec[1]


def _host_stage_root(repo: Path) -> Path:
    """本进程/线程独占的副本根：pid+tid 后缀隔离并发路（KI-20260912-001）。"""
    return (repo / _HOST_TEST_STAGE
            / f"lechao-{os.getpid()}-{threading.get_ident()}")


def _stage_module_copy(repo: Path, spec) -> Path:
    """把模块源码树复制到仓内 gitignored 副本，返回副本模块目录。

    每次先清旧副本再全量重建（rmtree + copytree），保证副本与当前源码
    一致且无上次 make 残留；副本根按 pid+tid 独占（KI-20260912-001），
    并发/多线程执行互不踩踏，且与真实 code 工作树隔离（产物不落工作树）。

    拷贝根按 spec 区分：kernel 侧拷贝整个 vendor/lechao 目录（LcView 调用点
    host 单测经 -I../.. 引用顶层 kernel_lechao_log.h，单模块 copytree 缺该
    头）；others 工具（usb-verify/usb-fault-inject）拷贝自身目录即可，其
    单测经 -I.. / -I../src 引用同目录源码。
    """
    src = repo / "code" / _copy_root_rel(spec)
    dst = _host_stage_root(repo)
    shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(src, dst)
    except OSError:
        shutil.rmtree(dst, ignore_errors=True)
        raise
    return dst / spec[3]


def _run_make_test(spec: tuple, repo: Path = _ROOT) -> tuple[int, str]:
    """在仓内副本 <module>/tests 下执行 `make test && make clean`，返回 (rc, 机器行)。

    make 产物只落副本不落 code 工作树（防 git status 污染/对 dirty 工作树
    的非真实验证）；收尾 make clean + rmtree 副本双层清理零残留。
    """
    label = spec[0]
    src = _module_src_dir(repo, spec)
    if not (src / "tests" / "Makefile").is_file():
        return 1, f"host_rc=1 | error: {label}/tests 缺失（host 单测守卫断链）"
    d = None
    try:
        try:
            d = _stage_module_copy(repo, spec) / "tests"
        except OSError as e:
            # 复制失败也须归因透出 host_rc（KI-20260912-006），且半成品
            # 已在 _stage_module_copy 内清理，不产生副本残留
            return 1, f"host_rc=1 | error: 副本复制失败（{label}）: {e}"
        try:
            r = subprocess.run(["make", "test"], cwd=d, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=_MAKE_TEST_TIMEOUT_S)
        except FileNotFoundError:
            return 1, f"host_rc=1 | error: make 未安装（{label} host 单测无法执行）"
        except subprocess.TimeoutExpired:
            return 1, (f"host_rc=1 | error: make test 超时"
                       f"（>{_MAKE_TEST_TIMEOUT_S}s，{label}）")
        # 无论成败都清副本内产物（clean 失败不覆盖 test rc）
        try:
            subprocess.run(["make", "clean"], cwd=d, capture_output=True,
                           timeout=_MAKE_CLEAN_TIMEOUT_S)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        stdout_text = r.stdout if isinstance(r.stdout, str) else ""
        tail = stdout_text.strip().splitlines()
        last = tail[-1] if tail else "（无输出）"
        rc = 0 if r.returncode == 0 else 1
        return rc, f"host_rc={rc} | {label}: {last}"
    finally:
        # 副本回收：整个 pid+tid 独占副本根（含 kernel_lechao_log.h 与各
        # 模块）连 make 产物一并移除，code 工作树零残留
        if d is not None:
            shutil.rmtree(_host_stage_root(repo), ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="内核 host 单测门禁守卫")
    ap.add_argument("--repo", default=str(_ROOT), help="仓根（默认脚本相对推断）")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    worst = 0
    for spec in _HOST_TEST_SPECS:
        rc, line = _run_make_test(spec, repo)
        print(line)
        worst = max(worst, rc)
    print("OK: host 单测全部通过" if worst == 0
          else f"FAIL: host 单测存在失败（rc={worst}）")
    return worst


if __name__ == "__main__":
    sys.exit(main())
