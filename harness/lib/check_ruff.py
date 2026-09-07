#!/usr/bin/env python3
# ============================================================
# check_ruff.py — ruff 静态检查门禁守卫（P0-B）
# 设计目的：Python 静态检查左移——AI/人工改动 harness Python 后即时获得
#   ruff 反馈。接入 selfcheck 以 ruff_rc 透出，ws_report/CI 判红。
# 判定对象：harness/ 下全部 Python（配置在仓根 ruff.toml）。
# fail-closed：ruff 二进制缺失/执行异常判红（无法检查不得静默绿）。
# 用法：python3 harness/lib/check_ruff.py [--repo <仓根>] [--scope <路径>]
# 退出码：0 合规 / 1 存在违规或工具不可用 / 2 参数错误
# ============================================================

import argparse
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def _run_ruff(scope: str, repo: Path = _ROOT) -> tuple[int, str]:
    """运行 ruff check <scope>，返回 (rc, 机器行)。

    fail-closed：FileNotFoundError（ruff 未安装）与任何非零退出均判红，
    无法检查不得静默绿；rc=0 时结论行带 ruff_rc=0。
    """
    target = str(repo / scope) if not Path(scope).is_absolute() else scope
    try:
        r = subprocess.run(["ruff", "check", target], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=120)
    except FileNotFoundError:
        return 1, "ruff_rc=1 | error: ruff 未安装（pip install ruff）"
    except subprocess.TimeoutExpired:
        return 1, "ruff_rc=1 | error: ruff 超时（>120s）"
    tail = (r.stdout or "").strip().splitlines()
    last = tail[-1] if tail else "（无输出）"
    return r.returncode, f"ruff_rc={r.returncode} | {last}"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="ruff 静态检查门禁守卫")
    ap.add_argument("--repo", default=str(_ROOT), help="仓根（默认脚本相对推断）")
    ap.add_argument("--scope", default="harness", help="扫描范围（相对仓根路径）")
    args = ap.parse_args(argv)
    rc, line = _run_ruff(args.scope, Path(args.repo))
    print(line)
    return rc


if __name__ == "__main__":
    sys.exit(main())
