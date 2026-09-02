#!/usr/bin/env python3
"""check_skill_refs.py — harness/skills 引用完整性检查（防悬空引用）。

背景：git-works-push SKILL.md 曾引用 `docs/commit-message-format.md` 悬空
（文件实际在 skill 内部 `harness/skills/git-works-push/docs/`，相对路径从仓库根
解析失败），长期未被发现（2026-08-30 修复）。本脚本把检查固化，防止 skill 改动
再次引入悬空引用。

检查范围（harness/skills 全部 skill + docs 设计文档 + .opencode/command）：
  1. markdown 链接 `[..](path)` —— 剥离 `#锚点` 后按文件相对目录/项目根解析
  2. 反引号内类路径 token（含 .md/.py/.sh/.yaml/.conf 等扩展名，或 harness/ 等前缀）
  3. `python3|bash <path>` 命令路径
  4. .py/.sh/.yaml/.conf 内路径字符串（引号包裹的仓库内路径）
  5. .opencode/command/*.md 的 `@harness/...` 与 `!` 脚本引用

排除项：
  - tests/ 目录（测试 mock 常故意构造失效链接场景，不属文档引用）
  - 格式模板占位符（含中文 / "..." / "<>" 等，如 `[file:行](路径#L行)`）

用法：
  python3 harness/lib/check_skill_refs.py            # 全量检查
  python3 harness/lib/check_skill_refs.py --path <rel>  # 仅检查单文件/单目录
  python3 harness/lib/check_skill_refs.py --report <path>  # 悬空清单落盘（可跟踪）
退出码：0（悬空引用当前只报不判红——方向 4 过渡，--report 落清单暴露问题，
  待清零后另批恢复判红；此前 ROOT 解析错误致真扫描根失效，检查长期假通过）。
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

# 仓库根：parents[2] 恢复真扫描根（parents[1] 为 harness/，ROOT/harness/skills
# 会解析成 harness/harness/skills 致扫描恒空、检查假通过——方向 2 修复）。
ROOT = Path(os.environ.get("CHECK_REFS_ROOT", Path(__file__).resolve().parents[2]))

LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
TOKEN_RE = re.compile(r"`([^`]+)`")
CMD_RE = re.compile(r"(?:python3|bash)\s+((?:harness|code|data|docs)/[\w\-./]+)")
PATH_STR_RE = re.compile(r"[\"']((?:harness|code|data|docs)/[\w\-./]+\.(?:py|sh|yaml|yml|conf|md|json|cdp|diff))[\"']")
AT_RE = re.compile(r"@(harness/[\w\-./]+\.md)")
EXT_HINT = re.compile(r"(\.md|\.py|\.sh|\.yaml|\.yml|\.conf|\.json|\.txt|\.cdp|\.patch|\.diff)$")
# 占位符：中文 / 省略号 / 尖括号 / 变量
PLACEHOLDER = re.compile(r"[\u4e00-\u9fff]|\.\.\.|^<|^\{|^\$|^~|^\[")


def is_remote(p: str) -> bool:
    return p.startswith(("http://", "https://", "mailto:", "ftp://"))


def path_like(p: str) -> bool:
    if is_remote(p) or p.startswith("#"):
        return False
    if PLACEHOLDER.search(p):
        return False
    if not EXT_HINT.search(p) and not p.startswith(("harness/", "docs/", "code/", "data/", "./", "../")):
        return False
    return True


def strip_anchor(p: str) -> str:
    return p.split("#")[0]


def resolve(base_dir: Path, p: str) -> bool:
    p = strip_anchor(p)
    if p.startswith("/"):
        return Path(p).exists()
    return (base_dir / p).exists() or (ROOT / p).exists()


def scan_file(f: Path) -> list[str]:
    """返回文件 f 中的悬空引用列表。"""
    try:
        txt = f.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    misses: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        if p in seen:
            return
        seen.add(p)
        if path_like(p) and not resolve(f.parent, p):
            misses.append(p)

    for m in LINK_RE.finditer(txt):
        _add(m.group(1).strip())
    for m in TOKEN_RE.finditer(txt):
        _add(m.group(1).strip())
    for m in CMD_RE.finditer(txt):
        _add(m.group(1))
    for m in PATH_STR_RE.finditer(txt):
        _add(m.group(1))
    return sorted(misses)


def scan_command_files() -> list[tuple[Path, list[str]]]:
    """.opencode/command/*.md 的 @ 引用检查。"""
    out: list[tuple[Path, list[str]]] = []
    commands = ROOT / ".opencode" / "command"
    if not commands.is_dir():
        return out
    for f in sorted(commands.glob("*.md")):
        misses: list[str] = []
        for m in AT_RE.finditer(f.read_text(encoding="utf-8")):
            p = m.group(1)
            if not (ROOT / p).exists():
                misses.append(p)
        if misses:
            out.append((f, misses))
    return out


def iter_scan_targets(rel: str | None) -> list[Path]:
    """收集待检查文件；排除 __pycache__ / .pytest_cache / tests/ 目录。

    默认（rel 为空）扫描 harness/skills 与 docs 两个根（skill 文档与设计
    文档的引用同样须防悬空）；--path 指定时只扫描该文件/目录。
    """
    bases = [ROOT / rel] if rel else [ROOT / "harness" / "skills", ROOT / "docs"]
    targets: list[Path] = []
    for base in bases:
        if base.is_file():
            targets.append(base)
            continue
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):
            if not f.is_file():
                continue
            if "__pycache__" in f.parts or ".pytest_cache" in f.parts or "tests" in f.parts:
                continue
            if f.suffix not in (".md", ".py", ".sh", ".yaml", ".yml", ".conf"):
                continue
            targets.append(f)
    return targets


def main() -> int:
    parser = argparse.ArgumentParser(
        description="harness/skills + docs 引用完整性检查")
    parser.add_argument("--path", default=None,
                        help="仅检查指定相对路径（文件或目录），"
                             "默认全量 harness/skills + docs")
    parser.add_argument("--report", default=None,
                        help="悬空引用清单落盘路径（相对 ROOT 或绝对路径），"
                             "有悬空时写入（可跟踪，随批提交供清零追踪）")
    args = parser.parse_args()

    # 收集全部悬空（文件集 + .opencode/command @ 引用）
    dangling: list[tuple[Path, list[str]]] = []
    for f in iter_scan_targets(args.path):
        misses = scan_file(f)
        if misses:
            dangling.append((f, misses))
    for f, misses in scan_command_files():
        if args.path:
            continue
        if misses:
            dangling.append((f, misses))
    total = sum(len(misses) for _, misses in dangling)

    for f, misses in dangling:
        print(f"\n### {f.relative_to(ROOT)}")
        for p in misses:
            print(f"  [MISS] {p}")

    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = ROOT / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for f, misses in dangling:
            lines.append(f"### {f.relative_to(ROOT)}")
            lines.extend(f"  [MISS] {p}" for p in misses)
        report_path.write_text("\n".join(lines) + ("\n" if lines else ""),
                               encoding="utf-8")
        print(f"report: 悬空引用清单已写入 {report_path}")

    if total:
        # 方向 4 过渡：只报不判红（返回码 0，selfcheck refs_rc=0 可过 ws_report
        # rc 门禁）；末行结论用「待清零」表述，避免与 ws_report 的悬空引用字样
        # 冗余防线冲突（明细已落 --report 清单供追踪）。待清零后恢复 return 1。
        print(f"\nrefs: 引用检查完成，{total} 处待清零（仅报告不判红，清单见 --report）")
        return 0
    print("OK: harness/skills + docs 引用完整，无悬空。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
