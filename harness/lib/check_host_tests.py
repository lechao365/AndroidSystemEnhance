#!/usr/bin/env python3
# ============================================================
# check_host_tests.py — 内核 host 单测门禁守卫（P0-A 快检）
# 设计目的：内核纯逻辑 host 单测（LcView ring / LcIod read_logic）是
#   gcc 可编译的业务快检层，此前仅文档纪律（S8）无自动链。接入 selfcheck
#   以 host_rc 透出——AI 改动内核纯逻辑后自检即得编译+单测反馈，无需等
#   完整上板链。
# 判定对象：code/rpi5/kernel/new/vendor/lechao/{LcView,LcIod}/tests 的
#   `make test`（编译+运行，-Wall -Wextra -Werror）。
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
import shutil
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# 内核 host 单测模块（relative：<kernel_new>/<module>/tests 下 make test）
_HOST_TEST_MODULES = ("LcView", "LcIod")

# make 副本根（相对仓根）：/harness/log/ 整体已 gitignore（log/cross-device
# 运行日志同根先例），副本及其 make 产物不污染 code 工作树
_HOST_TEST_STAGE = Path("harness") / "log" / "host-tests"


def _module_dir(repo: Path) -> Path:
    return (repo / "code" / "rpi5" / "kernel" / "new"
            / "vendor" / "lechao")


def _stage_module_copy(repo: Path, module: str) -> Path:
    """把 lechao 模块源码树复制到仓内 gitignored 副本，返回副本模块目录。

    每次先清旧副本再全量重建（rmtree + copytree），保证副本与当前源码
    一致且无上次 make 残留；副本路径确定性位于 harness/log/host-tests，
    与真实 code 工作树隔离（产物不落工作树）。

    拷贝整个 vendor/lechao 目录（而非单模块）：LcView 调用点 host 单测
    （方向 1，lcview_builder.c/lcview_ring.c）经 -I../.. 引用顶层
    kernel_lechao_log.h，须随副本存在才能编译（单模块 copytree 缺该头）。
    """
    src = _module_dir(repo)
    dst = repo / _HOST_TEST_STAGE / "lechao"
    shutil.rmtree(dst, ignore_errors=True)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    return dst / module


def _run_make_test(module: str, repo: Path = _ROOT) -> tuple[int, str]:
    """在仓内副本 <module>/tests 下执行 `make test && make clean`，返回 (rc, 机器行)。

    make 产物只落副本不落 code 工作树（防 git status 污染/对 dirty 工作树
    的非真实验证）；收尾 make clean + rmtree 副本双层清理零残留。
    """
    src = _module_dir(repo) / module
    if not (src / "tests" / "Makefile").is_file():
        return 1, f"host_rc=1 | error: {module}/tests 缺失（host 单测守卫断链）"
    stage = _stage_module_copy(repo, module)
    d = stage / "tests"
    try:
        try:
            r = subprocess.run(["make", "test"], cwd=d, capture_output=True,
                               text=True, encoding="utf-8", errors="replace",
                               timeout=300)
        except FileNotFoundError:
            return 1, f"host_rc=1 | error: make 未安装（{module} host 单测无法执行）"
        except subprocess.TimeoutExpired:
            return 1, f"host_rc=1 | error: make test 超时（>300s，{module}）"
        # 无论成败都清副本内产物（clean 失败不覆盖 test rc）
        try:
            subprocess.run(["make", "clean"], cwd=d, capture_output=True,
                           timeout=60)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass
        stdout_text = r.stdout if isinstance(r.stdout, str) else ""
        tail = stdout_text.strip().splitlines()
        last = tail[-1] if tail else "（无输出）"
        rc = 0 if r.returncode == 0 else 1
        return rc, f"host_rc={rc} | {module}: {last}"
    finally:
        # 副本回收：整个 lechao 副本根（含 kernel_lechao_log.h 与各模块）连
        # make 产物一并移除，code 工作树零残留
        shutil.rmtree(repo / _HOST_TEST_STAGE / "lechao", ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="内核 host 单测门禁守卫")
    ap.add_argument("--repo", default=str(_ROOT), help="仓根（默认脚本相对推断）")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    worst = 0
    for mod in _HOST_TEST_MODULES:
        rc, line = _run_make_test(mod, repo)
        print(line)
        worst = max(worst, rc)
    print("OK: 内核 host 单测全部通过" if worst == 0
          else f"FAIL: 内核 host 单测存在失败（rc={worst}）")
    return worst


if __name__ == "__main__":
    sys.exit(main())
