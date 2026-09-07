#!/usr/bin/env python3
# ============================================================
# check_host_tests.py — 内核 host 单测门禁守卫（P0-A 快检）
# 设计目的：内核纯逻辑 host 单测（LcView ring / LcIod read_logic）是
#   gcc 可编译的业务快检层，此前仅文档纪律（S8）无自动链。接入 selfcheck
#   以 host_rc 透出——AI 改动内核纯逻辑后自检即得编译+单测反馈，无需等
#   完整上板链。
# 判定对象：code/rpi5/kernel/new/vendor/lechao/{LcView,LcIod}/tests 的
#   `make test`（编译+运行，-Wall -Wextra -Werror）。
# 产物清理：make test 后必跑 make clean 删除 host_test 二进制，防污染
#   git status（commit_scope/sync 依赖工作树干净）。
# fail-closed：make 缺失/目录缺失判红（编译失败不可静默绿）。
# 用法：python3 harness/lib/check_host_tests.py [--repo <仓根>]
# 退出码：0 全过 / 1 任一失败或工具不可用 / 2 参数错误
# ============================================================

import argparse
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# 内核 host 单测模块（relative：<kernel_new>/<module>/tests 下 make test）
_HOST_TEST_MODULES = ("LcView", "LcIod")


def _module_dir(repo: Path) -> Path:
    return (repo / "code" / "rpi5" / "kernel" / "new"
            / "vendor" / "lechao")


def _run_make_test(module: str, repo: Path = _ROOT) -> tuple[int, str]:
    """在 <module>/tests 下执行 `make test && make clean`，返回 (rc, 机器行)。"""
    d = _module_dir(repo) / module / "tests"
    if not (d / "Makefile").is_file():
        return 1, f"host_rc=1 | error: {module}/tests 缺失（host 单测守卫断链）"
    try:
        r = subprocess.run(["make", "test"], cwd=d, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=300)
    except FileNotFoundError:
        return 1, f"host_rc=1 | error: make 未安装（{module} host 单测无法执行）"
    except subprocess.TimeoutExpired:
        return 1, f"host_rc=1 | error: make test 超时（>300s，{module}）"
    # 无论成败都清产物（防污染工作树）；clean 失败不覆盖 test rc
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
