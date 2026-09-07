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
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# 热路径检查器清单：selfcheck 每次调用并行 spawn 的治理脚本（方向 2）。
# 新增治理检查器须登记于此，否则守卫不覆盖（check_test_discipline 亦在内）。
_HOT_PATHS = [
    "harness/lib/check_skill_refs.py",
    "harness/lib/check_config.py",
    "harness/lib/check_ioctl_headers.py",
    "harness/lib/check_test_discipline.py",
    "harness/lib/selfcheck.py",
    "harness/skills/cross-device/lib/python/gen_manifest.py",
]

# 禁令：全树遍历调用（rglob / os.walk）
_BAN = [re.compile(r"\.rglob\s*\("), re.compile(r"os\.walk\s*\(")]
# 豁免标记：行内含该注释即视为明确标注的非 git 仓回落分支（允许保留）
_EXEMPT = "# GITLS-FALLBACK"


def scan(repo: Path) -> list[str]:
    """返回违规明细（空 = 合规）。"""
    findings: list[str] = []
    for rel in _HOT_PATHS:
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
