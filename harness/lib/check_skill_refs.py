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

裸文件名（无斜杠，方向 3 收紧）：按 basename 在仓内唯一匹配即校验存在
（唯一匹配 → 有效）；多义（多个同名文件无法确定目标）跳过防误报；零命中
（引用不存在的文件）判悬空防漏网——此前无斜杠一律跳过致 harness-paths.conf
类悬空漏网未被发现。
退出码：1（存在悬空引用即判红，--report 落清单可跟踪；0 表示引用完整）；
无 --path 且默认扫描目标为空（扫描根缺失/被全豁免）亦判红防假通过。
  此前 ROOT 解析错误致真扫描根失效、且围栏/示例/占位被误报，检查长期假通过
  （2026-09-02 方向 1/2/3 收紧误报后清零并恢复判红）。

用法：
  python3 harness/lib/check_skill_refs.py            # 全量检查
  python3 harness/lib/check_skill_refs.py --path <rel>  # 仅检查单文件/单目录
  python3 harness/lib/check_skill_refs.py --report <path>  # 悬空清单落盘（可跟踪）
退出码：1（存在悬空引用即判红，--report 落清单可跟踪；0 表示引用完整）。
  此前 ROOT 解析错误致真扫描根失效、且围栏/示例/占位被误报，检查长期假通过
  （2026-09-02 方向 1/2/3 收紧误报后清零并恢复判红）。
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
# TOKEN_RE 不得跨行（方向 1）：行内反引号 token，排除换行防跨行误配
TOKEN_RE = re.compile(r"`([^`\n]+)`")
CMD_RE = re.compile(r"(?:python3|bash)\s+((?:harness|code|data|docs)/[\w\-./]+)")
PATH_STR_RE = re.compile(r"[\"']((?:harness|code|data|docs)/[\w\-./]+\.(?:py|sh|yaml|yml|conf|md|json|cdp|diff))[\"']")
AT_RE = re.compile(r"@(harness/[\w\-./]+\.md)")
EXT_HINT = re.compile(r"(\.md|\.py|\.sh|\.yaml|\.yml|\.conf|\.json|\.txt|\.cdp|\.patch|\.diff)$")
# 占位符：中文 / 省略号 / 尖括号 / 变量
PLACEHOLDER = re.compile(r"[\u4e00-\u9fff]|\.\.\.|^<|^\{|^\$|^~|^\[")
# glob 通配符（docs/**、docs/*/README.md 等模式描述，非真实路径）
_GLOB_HINT = re.compile(r"[*?]")
# 或扩展名复合写法（lcview_check.py/.sh = 校验器 .py 或 .sh，非真实路径）
_OR_EXT_RE = re.compile(r"\.\w+/\.[a-z]+\s*$")
# Android 设备根绝对路径（文档引用设备侧文件路径，非仓库内引用）
_DEVICE_ROOT = ("/vendor/", "/system/", "/data/", "/dev/", "/proc/", "/sys/",
                "/product/", "/apex/")

# 豁免目录（相对 ROOT 清单常量，运行时基于当前 ROOT 拼接；方向 3 可扩展）：
# 设计文档历史计划与运行日志含大量示例/模板引用，纳入豁免减少误报
EXEMPT_RELS = ("docs/superpowers", "harness/log")


def is_remote(p: str) -> bool:
    return p.startswith(("http://", "https://", "mailto:", "ftp://"))


def strip_code_fences(txt: str) -> str:
    """剥离围栏代码块（```...```，含语言标注；方向 1 扫描前剥离）。

    围栏内是代码示例而非文档引用，引用其内路径会大量误报；链接/命令/路径
    正则统一在剥离后的文本上运行。
    """
    return re.sub(r"```.*?```", "", txt, flags=re.DOTALL)


def strip_line_suffix(p: str) -> str:
    """剥离 `:行号` 后缀（引用常见 `path.py:24` 形式，方向 2），再判存在。"""
    return re.sub(r":\d+$", "", p)


def path_like(p: str) -> bool:
    if is_remote(p) or p.startswith("#"):
        return False
    if PLACEHOLDER.search(p) or "<" in p or ">" in p:
        # 含尖括号占位的 token（如 data/verify-results/<ts>-<batch_id>.md）跳过
        return False
    if " " in p:
        # 含空格的 token（多为描述文字，非路径）跳过
        return False
    if "/" not in p:
        # 无斜杠裸文件名：路径无法按目录解析，改由 scan_file._add 按
        # basename 仓内唯一匹配校验（方向 3）；此处仅放行带扩展名的文件
        # 名 token，无扩展名裸词视为描述文字非路径（EXT_HINT 统一兜底）。
        return EXT_HINT.search(p) is not None
    if p.startswith(_DEVICE_ROOT):
        # Android 设备根绝对路径（如 /vendor/etc/...，文档引用设备侧文件）跳过
        return False
    if p.startswith(".vscode/"):
        # 编辑器配置示例（指导创建 .vscode/settings.json 等，非仓库引用）跳过
        return False
    if _GLOB_HINT.search(p):
        # glob 通配符（docs/**、docs/*/README.md 等模式描述）跳过
        return False
    if _OR_EXT_RE.search(p):
        # 或扩展名复合写法（xxx.py/.sh）跳过
        return False
    if not EXT_HINT.search(p) and not p.startswith(("harness/", "docs/", "code/", "data/", "./", "../")):
        return False
    return True


def strip_anchor(p: str) -> str:
    return p.split("#")[0]


# basename 索引缓存（key=ROOT 绝对路径）：同 ROOT 静态扫描复用，
# 防每个裸文件名 token 都全仓 rglob 一次导致扫描变慢
_INDEX_CACHE: dict[str, dict[str, int]] = {}


def _basename_count(name: str) -> int:
    """仓内 basename 为 name 的文件数（方向 3 裸文件名唯一匹配判定）。"""
    root = ROOT.resolve()
    key = str(root)
    idx = _INDEX_CACHE.get(key)
    if idx is None:
        idx = {}
        for f in root.rglob("*"):
            if f.is_file():
                idx[f.name] = idx.get(f.name, 0) + 1
        _INDEX_CACHE[key] = idx
    return idx.get(name, 0)


def resolve(base_dir: Path, p: str) -> bool:
    p = strip_anchor(strip_line_suffix(p))
    if p.startswith("/"):
        return Path(p).exists()
    return (base_dir / p).exists() or (ROOT / p).exists()


def scan_file(f: Path) -> list[str]:
    """返回文件 f 中的悬空引用列表。"""
    try:
        txt = f.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    txt = strip_code_fences(txt)  # 方向 1：扫描前剥离围栏代码块
    misses: list[str] = []
    seen: set[str] = set()

    def _add(p: str) -> None:
        if p in seen:
            return
        seen.add(p)
        if not path_like(p):
            return
        if "/" not in p:
            # 方向 3：无斜杠裸文件名按 basename 仓内唯一匹配校验——
            # 唯一匹配（仓内确有该文件）即有效；多义（多个同名无法确定
            # 目标）跳过防误报；零命中（引用不存在的文件）判悬空防漏网。
            if _basename_count(p) == 0:
                misses.append(p)
            return
        if not resolve(f.parent, p):
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
    exempt = tuple(ROOT / r for r in EXEMPT_RELS)
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
            if any(f.is_relative_to(ex) for ex in exempt):
                # 方向 3 豁免目录：设计文档历史计划/运行日志
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
    targets = iter_scan_targets(args.path)
    if not args.path and not targets:
        # 方向 5：无 --path 且默认扫描目标为空（扫描根缺失/被全豁免）即判红，
        # 防扫描根失效假通过（parents[1] 时代 ROOT 解析错误致扫描恒空的历史教训）
        print("error: 无 --path 且默认扫描目标为空（扫描根缺失或全豁免），判红",
              file=sys.stderr)
        return 1
    for f in targets:
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
        # 方向 5：悬空恢复判红（返回 1）；refs_rc=1 由 ws_report 按 rc 拒写
        # 收据，倒逼悬空清零。明细已落 --report 清单供追踪。
        print(f"\n==== 共 {total} 处悬空引用（判红，见 --report 清单）====")
        return 1
    print("OK: harness/skills + docs 引用完整，无悬空。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
