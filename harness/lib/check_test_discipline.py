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
    (re.compile(r"@pytest\.mark\.skip(?:if)?\b|pytest\.skip(?:if)?\s*\("
                # tst-01：补 unittest 变体（@unittest.skip / skipIf / skipUnless
                # 是本仓最惯用的掩盖修法，此前漏网）与模块级 pytestmark 写法
                r"|@unittest\.skip(?:Unless|If)?\s*\("
                r"|pytestmark\s*=\s*(\[\s*)?pytest\.mark\.skip(?:if)?\s*\("),
     "skip"),
    (re.compile(r"(?:^|[^.\w])time\.sleep\s*\("
                r"|[^.\w]sleep\s*\("
                r"|^\s*sleep\s*\("
                r"|asyncio\.sleep\s*\("), "sleep"),
]

# 测试用例定义（def test_*）识别：用例数净减判红的计数口径
_CASE_RE = re.compile(r"def\s+test_\w+\s*\(")

# 测试删除/用例净减豁免清单路径（相对 repo）：显式豁免通道——正常重构
# 删测试或合并用例须在该清单登记（每行一个相对路径，# 注释），防守卫卡死
# 合法重构，同时杜绝"删测试换绿"的静默流失
_EXEMPT_FILE = "harness/config/test-delete-exempt.txt"


def _is_test_file(rel: str) -> bool:
    """测试文件判定：路径含 /tests/、test_ 前缀、_test.py 后缀或 conftest.py
    （约定覆盖 harness 全部测试布局）。"""
    name = Path(rel).name
    return ("/tests/" in rel or name.startswith("test_")
            or name.endswith("_test.py") or name == "conftest.py")


def _load_exempt(repo: Path) -> set[str]:
    """读取测试删除/用例净减豁免清单（不存在即空集）。"""
    path = repo / _EXEMPT_FILE
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return set()
    return {ln.strip() for ln in lines
            if ln.strip() and not ln.strip().startswith("#")}


def _is_production_py(rel: str) -> bool:
    """行为性改动判定对象：harness/lib 与 harness/skills 下的非测试 .py。
    （方向 1，7.5 清单最后一条实质门禁——skills 加 73 行 0 测试照样绿）"""
    return (rel.endswith(".py") and not _is_test_file(rel)
            and (rel.startswith("harness/lib/")
                 or rel.startswith("harness/skills/")))


def _corresponding_test(rel: str) -> str:
    """生产 .py → 期望对应测试文件路径（与 selfcheck._quick_test_targets
    映射一致）；非 harness/lib|skills 返回空串（不判）。"""
    stem = Path(rel).stem
    if rel.startswith("harness/lib/"):
        return f"harness/lib/tests/test_{stem}.py"
    if rel.startswith("harness/skills/"):
        parts = rel.split("/")
        if len(parts) >= 3:
            return f"harness/skills/{parts[2]}/tests/test_{stem}.py"
    return ""


def _git_lines(args: list[str], cwd: Path):
    """git 输出行列表；git 不可用/命令失败返回 None。"""
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                           cwd=cwd, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.splitlines() if r.returncode == 0 else None


def scan(repo: Path, rev: str = "HEAD") -> list[str]:
    """返回违规明细列表（空 = 无违规）。

    git 调用失败 fail-closed（lib-07）：.git 存在但 diff/ls-files 返回码
    非 0 时不得当"无改动"假绿——输出 error 并以哨兵违规判红（rc=1 交
    discipline_rc 透出），防仓库异常场景静默放行。
    文件面 = 已跟踪改动 ∪ 未跟踪非忽略文件（方向 4 未跟踪并入，见内文）。
    """
    tracked = _git_lines(["diff", "--name-only", rev], repo)
    if tracked is None:
        print(f"error: git diff --name-only {rev} 失败（仓库异常/命令不可用），"
              "无法扫描测试改动，判红", file=sys.stderr)
        return [f"{repo}: git diff 失败，无法扫描（按违规判红）"]
    # 方向 2：删除文件单独列出（git diff --name-only 虽含 D，--diff-filter=D
    # 更明确；删除的测试文件此前只扫新增行完全不可见——删测试换绿可静默
    # 通过 discipline_rc=0，本批删 308 行即证）
    deleted = _git_lines(["diff", "--diff-filter=D", "--name-only", rev], repo)
    if deleted is None:
        print(f"error: git diff --diff-filter=D {rev} 失败（仓库异常/命令不可用），"
              "无法扫描测试文件删除，判红", file=sys.stderr)
        return [f"{repo}: git diff 删除列表失败，无法扫描（按违规判红）"]
    untracked = _git_lines(["ls-files", "--others", "--exclude-standard"],
                           repo)
    if untracked is None:
        print("error: git ls-files --others --exclude-standard 失败"
              "（仓库异常/命令不可用），无法扫描未跟踪测试文件，判红",
              file=sys.stderr)
        return [f"{repo}: git ls-files 失败，无法扫描未跟踪文件（按违规判红）"]
    # git diff --name-only 不列未跟踪文件，新增但未 git add 的测试文件其违禁
    # 新增行（xfail/skip/sleep）此前整文件漏判假绿——上板前假证据；
    # --exclude-standard 让 .gitignore 生效；两路输出均相对 repo（路径基准
    # 一致），去重后统一排序。
    untracked_set = set(untracked)
    deleted_set = set(deleted or [])
    exempt = _load_exempt(repo)
    changed = sorted(set(tracked) | untracked_set)
    findings: list[str] = []
    for rel in changed:
        if not rel.endswith(".py") or not _is_test_file(rel):
            continue
        if rel in exempt:
            # 显式豁免通道：登记的重构删减/合并用例放行（理由须随 commit
            # message 说明，禁以删测试换绿）
            continue
        if rel in deleted_set:
            findings.append(
                f"{rel}: 测试文件删除未登记豁免"
                f"（正常重构删测试须在 {_EXEMPT_FILE} 登记）")
            continue
        if rel in untracked_set:
            # 未跟踪新文件整体视作新增行（git diff 不展示未跟踪内容）
            try:
                raw = (repo / rel).read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                print(f"error: 读取未跟踪测试文件 {rel} 失败，无法扫描，判红",
                      file=sys.stderr)
                findings.append(f"{rel}: 读取失败，无法扫描（按违规判红）")
                continue
            file_lines = ["+" + ln for ln in raw.splitlines()]
        else:
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
        # 方向 2：用例数净减判红（def test_ 删除 > 新增）——删测试用例换绿
        # 同样静默流失覆盖，须显式豁免。未跟踪新文件视为纯新增（无净减）
        if rel not in untracked_set:
            add_cases = sum(
                1 for ln in file_lines
                if ln.startswith("+") and not ln.startswith("+++")
                and _CASE_RE.search(ln))
            del_cases = sum(
                1 for ln in file_lines
                if ln.startswith("-") and not ln.startswith("---")
                and _CASE_RE.search(ln))
            if del_cases > add_cases:
                findings.append(
                    f"{rel}: 用例数净减 {del_cases - add_cases}"
                    f"（删除 {del_cases} 新增 {add_cases}；正常重构合并用例"
                    f"须在 {_EXEMPT_FILE} 登记豁免）")
    # 方向 1（7.5 清单最后一条实质门禁）：harness/lib 与 harness/skills 下
    # .py 行为性改动须同批次带对应 tests/ 改动，否则判红——skills 加 73 行
    # 0 测试照样绿的漏洞（discipline_rc 只扫测试文件本身，生产改动无测试
    # 静默放行）。纯重构/文档注释改动走显式豁免通道（_EXEMPT_FILE 登记）。
    changed_set = set(changed)
    for rel in changed:
        if rel in exempt:
            continue
        if not _is_production_py(rel):
            continue
        want = _corresponding_test(rel)
        if not want:
            continue
        if not any(t == want or t.startswith(want) for t in changed_set):
            findings.append(
                f"{rel}: harness/lib|skills 下 .py 行为性改动须同批次带对应"
                f" tests/ 改动（{want} 未在本批改动；纯重构/文档注释改动请"
                f"在 {_EXEMPT_FILE} 登记豁免）")
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="测试改动纪律机械守卫（禁新增 xfail/skip/sleep 重试、"
                    "禁静默删除测试文件或用例数净减——正常重构须登记 "
                    "harness/config/test-delete-exempt.txt 豁免）")
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
