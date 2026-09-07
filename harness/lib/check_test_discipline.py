#!/usr/bin/env python3
# ============================================================
# check_test_discipline.py — 测试改动纪律机械守卫（IDLE-006 配套）
# 设计目的：idle-hardening 的禁止修法（sleep 重试 / xfail / 弱化断言）此前
#   只写在文档里，无机械守卫——修复时以"掩盖而非修复"方式改测试会静默
#   混入批次。本检查器扫**测试改动中新增的行**（git diff HEAD 的 + 行），
#   命中禁令即 rc=1，由 selfcheck 以 discipline_rc 透出、ws_report/CI 判红。
# 扫描对象：改动文件中的测试文件（路径含 /tests/ 或 test 前缀/后缀）。
# 判定语义：只扫新增行（改动引入的），存量合规历史不动；sleep 命中可能为
#   设备等待等合理用途，报告提示人工复核（机械守卫只拦不判死，评审放行）。
# 用法：python3 harness/lib/check_test_discipline.py [--repo <仓根>] [--rev HEAD]
# 退出码：0 无违规 / 1 存在违规 / 2 参数错误
# ============================================================

import argparse
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# 禁令模式 → 违规类别（方向 1：idle-hardening 禁止修法机械化）。
# sleep 分支（lib-15）：在原 time.sleep / 非标识符前导裸调两分支基础上
# 补 行首裸 sleep( 与 asyncio.sleep( ——此前漏 asyncio.sleep 与行首裸调，
# sleep 重试可换皮绕过守卫
_BANNED = [
    (re.compile(r"@pytest\.mark\.xfail\b|pytest\.xfail\s*\("), "xfail"),
    (re.compile(r"@pytest\.mark\.skip(?:if)?\b|pytest\.skip(?:if)?\s*\("),
     "skip"),
    (re.compile(r"(?:^|[^.\w])time\.sleep\s*\("
                r"|[^.\w]sleep\s*\("
                r"|^\s*sleep\s*\("
                r"|asyncio\.sleep\s*\("), "sleep"),
]


def _is_test_file(rel: str) -> bool:
    """测试文件判定：路径含 /tests/、test_ 前缀、_test.py 后缀或 conftest.py
    （约定覆盖 harness 全部测试布局）。"""
    name = Path(rel).name
    return ("/tests/" in rel or name.startswith("test_")
            or name.endswith("_test.py") or name == "conftest.py")


def _git_lines(args: list[str], cwd: Path):
    """git 输出行列表；git 不可用/命令失败返回 None。"""
    try:
        r = subprocess.run(["git", *args], cwd=cwd, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.splitlines() if r.returncode == 0 else None


def scan(repo: Path, rev: str = "HEAD") -> list[str]:
    """返回违规明细列表（空 = 无违规）。

    git 调用失败 fail-closed（lib-07）：.git 存在但 diff 返回码非 0 时
    不得当"无改动"假绿——输出 error 并以哨兵违规判红（rc=1 交
    discipline_rc 透出），防仓库异常场景静默放行。
    """
    changed = _git_lines(["diff", "--name-only", rev], repo)
    if changed is None:
        print(f"error: git diff --name-only {rev} 失败（仓库异常/命令不可用），"
              "无法扫描测试改动，判红", file=sys.stderr)
        return [f"{repo}: git diff 失败，无法扫描（按违规判红）"]
    findings: list[str] = []
    for rel in sorted(changed):
        if not rel.endswith(".py") or not _is_test_file(rel):
            continue
        file_lines = _git_lines(["diff", rev, "--", rel], repo)
        if file_lines is None:
            print(f"error: git diff {rel} 失败，无法扫描，判红",
                  file=sys.stderr)
            findings.append(f"{rel}: git diff 失败，无法扫描（按违规判红）")
            continue
        for ln in file_lines:
            if not ln.startswith("+") or ln.startswith("+++"):
                continue
            added = ln[1:]
            for pat, kind in _BANNED:
                if pat.search(added):
                    findings.append(f"{rel}: {kind}: 新增 {added.strip()[:80]}")
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="测试改动纪律机械守卫（禁新增 xfail/skip/sleep 重试）")
    ap.add_argument("--repo", default=str(_ROOT), help="仓根（默认脚本相对推断）")
    ap.add_argument("--rev", default="HEAD", help="比对基线（默认 HEAD）")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    if not (repo / ".git").exists():
        print("OK: 非 git 仓，跳过测试改动纪律扫描")
        return 0
    findings = scan(repo, rev=args.rev)
    if findings:
        print("==== 测试改动中新增禁戒（xfail/skip/sleep 重试，IDLE-006 "
              "机械守卫）——须改除后再提交 ====")
        for f in findings:
            print(f"  {f}")
        print(f"==== 共 {len(findings)} 处 ====")
        return 1
    print("OK: 测试改动无新增 xfail/skip/sleep 重试")
    return 0


if __name__ == "__main__":
    sys.exit(main())
