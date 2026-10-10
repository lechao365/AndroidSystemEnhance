#!/usr/bin/env python3
# ============================================================
# check_hot_path_scan.py — 治理检查器热路径遍历约束守卫（方向 2）
# 设计目的：check_skill_refs 全树 rglob 在 apply 机 WSL2 drvfs 慢到 ~39s
#   （扫到 .git 对象/__pycache__ 等非仓库资产）而 emit 本机仅 0.38s——
#   防这类回归重现，selfcheck 每次调用的治理检查器（热路径）一律走
#   git ls-files 列跟踪文件，禁止全树 rglob / os.walk。
# 判定对象：热路径检查器清单（selfcheck 并行 spawn 的脚本）源码；命中
#   `.rglob(` / `os.walk(` 且行内含豁免标记 `# GITLS-FALLBACK`（明确标注的
#   非 git 仓回落分支，如 check_skill_refs）时不判红，其余一律判红。
# 接入：selfcheck 以 scan_rc 透出，ws_report/CI 判红（与 refs 同类）。
# 用法：python3 harness/lib/check_hot_path_scan.py [--repo <仓根>]
# 退出码：0 合规 / 1 热路径存在全树 rglob/os.walk / 2 参数错误
# ============================================================

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# 热路径检查器根集：selfcheck 每次调用并行 spawn 的治理脚本（方向 2）。
# 新增治理检查器须登记于此，否则守卫不覆盖（check_test_discipline 亦在内）。
# import 依赖面（lib-14）不再手工登记：discover_hot_paths 从本根集经 AST
# import 图自动推导（R8），新增依赖文件自动纳入扫描，清单漂移静默无感消除。
_HOT_PATHS = [
    "harness/lib/check_skill_refs.py",
    "harness/lib/check_config.py",
    "harness/lib/check_ioctl_headers.py",
    "harness/lib/check_lcview_events.py",
    "harness/lib/check_test_discipline.py",
    "harness/lib/check_hot_path_scan.py",
    "harness/lib/check_quotepath.py",
    "harness/lib/check_known_issues.py",
    "harness/lib/check_commit_coverage.py",
    "harness/lib/selfcheck.py",
    "harness/lib/check_ruff.py",
    "harness/lib/check_host_tests.py",
    "harness/skills/cross-device/lib/python/gen_manifest.py",
]

# import 依赖面推导的搜索范围（仓内可被 sys.path 注入加载的库目录）。
# 仅这些目录下的模块可能被 spawn 检查器真实 import 执行；依赖推导只认
# git ls-files 列出的**跟踪** py（CDP-DOD-002：不依赖 gitignore 产物）。
_LIB_DIRS = (
    "harness/lib/",
    "harness/skills/cross-device/lib/python/",
)

# 禁令：全树遍历调用（rglob / os.walk）
_BAN = [re.compile(r"\.rglob\s*\("), re.compile(r"os\.walk\s*\(")]
# 豁免标记：行内含该注释即视为明确标注的非 git 仓回落分支（允许保留）
_EXEMPT = "# GITLS-FALLBACK"


def _tracked_py(repo: Path) -> dict[str, list[str]]:
    """git ls-files 列库目录下跟踪 py → {模块基名（去 .py）: [相对路径...]}。

    非 git 仓 / git 不可用 / 命令失败返回空 dict（调用方回落显式根集，不崩）。
    同名模块可有多份物理文件（如 cdp_paths 主实现 + cross-device re-export
    垫片，sys.path 先后决定实际加载者）——按基名映射到**全部**候选，扫描面
    取并集，避免任一物理副本漂移静默漏检。
    """
    try:
        r = subprocess.run(
            ["git", "-C", str(repo), "-c", "core.quotepath=false",
             "ls-files", "*.py"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return {}
    if r.returncode != 0:
        return {}
    out: dict[str, list[str]] = {}
    for rel in (r.stdout or "").splitlines():
        rel = rel.strip()
        if not rel or not rel.startswith(_LIB_DIRS):
            continue
        out.setdefault(Path(rel).stem, []).append(rel)
    return out


def _imported_basenames(path: Path) -> set[str]:
    """AST 提取文件的 import 基名（含延迟 import / importlib / __import__）。

    覆盖形态：`import a.b`（取 b）、`from a.b import c`（取 b 与 c）、
    `importlib.import_module("x")` / `__import__("x")`（运行时字符串名）。
    解析失败（缺失/语法错）返回空集（保守不扩面，避免误伤）。
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                names.add(a.name.split(".")[-1])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.add(node.module.split(".")[-1])
            for a in node.names:
                names.add(a.name.split(".")[-1])
        elif isinstance(node, ast.Call):
            fn = node.func
            fname = (fn.attr if isinstance(fn, ast.Attribute)
                     else fn.id if isinstance(fn, ast.Name) else "")
            if fname in ("import_module", "__import__") and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    names.add(arg.value.split(".")[-1])
    return names


def discover_hot_paths(repo: Path) -> list[str]:
    """扫描面 = 显式根集（_HOT_PATHS）+ AST 推导的仓内 import 依赖面。

    从根集各文件出发，按 import 图递归解析到库目录下跟踪 py（git ls-files
    列跟踪文件，禁全树 rglob/os.walk）；依赖文件因此自动纳入守卫，新增依赖
    无需手工登记。非 git 仓（单测临时目录）无法推导时仅返显式根集。
    """
    ordered = list(_HOT_PATHS)
    seen = set(ordered)
    table = _tracked_py(repo)
    if not table:
        return ordered
    queue = [repo / rel for rel in ordered if (repo / rel).is_file()]
    while queue:
        cur = queue.pop()
        for base in _imported_basenames(cur):
            for rel in table.get(base, ()):
                if rel not in seen:
                    seen.add(rel)
                    ordered.append(rel)
                    queue.append(repo / rel)
    return ordered


def scan(repo: Path) -> list[str]:
    """返回违规明细（空 = 合规）。"""
    findings: list[str] = []
    for rel in discover_hot_paths(repo):
        p = repo / rel
        if not p.is_file():
            findings.append(f"{rel}: 清单文件缺失（热路径守卫覆盖断链）")
            continue
        for lineno, ln in enumerate(p.read_text(encoding="utf-8",
                                                errors="replace").splitlines(),
                                    1):
            if _EXEMPT in ln:
                continue
            if ln.lstrip().startswith("#"):
                continue  # 纯注释行不计（注释提及 rglob 非实际调用）
            for pat in _BAN:
                if pat.search(ln):
                    findings.append(f"{rel}:{lineno}: 全树遍历 "
                                    f"{ln.strip()[:80]}")
                    break
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="治理检查器热路径遍历约束守卫（禁 rglob/os.walk，走 "
                    "git ls-files）")
    ap.add_argument("--repo", default=str(_ROOT), help="仓根（默认脚本相对推断）")
    args = ap.parse_args(argv)
    findings = scan(Path(args.repo))
    if findings:
        print("==== 治理检查器热路径全树 rglob/os.walk（方向 2：须改走 "
              "git ls-files）====")
        for f in findings:
            print(f"  {f}")
        print(f"==== 共 {len(findings)} 处 ====")
        return 1
    print("OK: 热路径检查器无全树 rglob/os.walk（一律 git ls-files）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
