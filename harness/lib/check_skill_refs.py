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
import subprocess
import sys
from pathlib import Path

# 仓库根：parents[2] 恢复真扫描根（parents[1] 为 harness/，ROOT/harness/skills
# 会解析成 harness/harness/skills 致扫描恒空、检查假通过——方向 2 修复）。
# CHECK_REFS_ROOT 为空串时视为未设置：Path("") 会解析成当前目录（.）致扫描根
# 漂移，取值须 strip 后判空再回落默认值。
_REF_ROOT = os.environ.get("CHECK_REFS_ROOT", "").strip()
ROOT = Path(_REF_ROOT) if _REF_ROOT else Path(__file__).resolve().parents[2]

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
    if p.startswith("harness/log/"):
        # 运行期产物域（方向 2）：harness/log 全 gitignore，SKILL/文档引用其
        # 下路径是描述落盘位置（如 sync-code-to-workspace artifacts），干净
        # 克隆下不存在——判悬空会在 CI 恒红，且产物域非仓库资产无引用完整性
        # 意义，整前缀豁免（harness/log 内容本身亦在 EXEMPT_RELS 不扫描）。
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

# git ls-files 缓存（key=ROOT 绝对路径，None=非 git 仓回落 rglob）
_GIT_LS_CACHE: dict[str, list[Path] | None] = {}


def _git_ls_files() -> list[Path] | None:
    """git ls-files 一次性列出工作树文件面：已跟踪 + 未跟踪非忽略文件
    （相对 ROOT）；非 git 仓返 None。

    方向 4 未跟踪并入：此前只列跟踪文件，新增但未 git add 的 SKILL/文档/
    脚本不进扫描面，其内悬空引用漏判——上板前假证据。git ls-files 一旦给
    --others 就不再隐含 --cached（实测只列未跟踪），故显式 --cached 保留
    跟踪文件面（语义不回退）；--others 并入未跟踪，--exclude-standard 让
    .gitignore 生效排除忽略产物；输出 sorted(set()) 去重合并排序（git 输出
    untracked/tracked 两组各自有序、合并非全局有序，调用方需确定性）。
    全树 rglob 在 apply 机 WSL2 drvfs 慢到 ~39s（扫到 .git 对象/__pycache__
    等大量非仓库资产），而 emit 本机仅 0.38s——refs 是自检关键路径，改用
    git ls-files（本仓 git 仓，快一两个数量级）。"""
    key = str(ROOT.resolve())
    if key in _GIT_LS_CACHE:
        return _GIT_LS_CACHE[key]
    try:
        r = subprocess.run(["git", "ls-files", "--cached", "--others",
                            "--exclude-standard"], cwd=ROOT,
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace")
    except Exception:
        r = None
    if r is None or r.returncode != 0:
        _GIT_LS_CACHE[key] = None
        return None
    files = sorted({Path(ln) for ln in r.stdout.splitlines() if ln})
    _GIT_LS_CACHE[key] = files
    return files


def _basename_count(name: str) -> int:
    """仓内 basename 为 name 的文件数（方向 3 裸文件名唯一匹配判定）。

    索引须排除 EXEMPT_RELS 目录（docs/superpowers、harness/log）：这些目录
    含大量非仓库资产的同名文件（日志产物/历史计划），纳入索引会把"引用
    不存在的文件"误判为"多义跳过"（防误报变漏网）。
    数据源优先 git ls-files（跟踪+未跟踪非忽略，快）；非 git 仓回落全树 rglob。
    """
    root = ROOT.resolve()
    key = str(root)
    idx = _INDEX_CACHE.get(key)
    if idx is None:
        idx = {}
        files = _git_ls_files()
        if files is not None:
            # git ls-files 输出相对 ROOT，豁免用相对路径比较
            exempt_rel = tuple(Path(r) for r in EXEMPT_RELS)
            for f in files:
                if any(f.is_relative_to(ex) for ex in exempt_rel):
                    continue
                idx[f.name] = idx.get(f.name, 0) + 1
        else:
            exempt = tuple(root / r for r in EXEMPT_RELS)
            for f in root.rglob("*"):  # GITLS-FALLBACK: 非 git 仓回落
                if not f.is_file():
                    continue
                if ".git" in f.parts:
                    continue
                if any(f.is_relative_to(ex) for ex in exempt):
                    continue
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
    """.opencode/command/*.md 的 @ 引用检查。

    读文件异常防护（lib-08，对齐 scan_file 口径）：OSError/UnicodeDecodeError
    结构化跳过（stderr warn 留痕），不崩也不误报悬空。
    """
    out: list[tuple[Path, list[str]]] = []
    commands = ROOT / ".opencode" / "command"
    if not commands.is_dir():
        return out
    for f in sorted(commands.glob("*.md")):
        try:
            txt = f.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            print(f"warn: command 文件读取失败，跳过: {f}: {e}", file=sys.stderr)
            continue
        misses: list[str] = []
        for m in AT_RE.finditer(txt):
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
    files = _git_ls_files()
    if files is not None:
        # 文件面=跟踪+未跟踪非忽略（无 __pycache__/.pytest_cache 且不含
        # .git），输出相对 ROOT（快）；tests 目录与豁免/后缀过滤与 rglob
        # 口径一致
        base_rels = [Path(rel)] if rel else [Path("harness/skills"), Path("docs")]
        exempt_rel = tuple(Path(r) for r in EXEMPT_RELS)
        for f in files:
            if not any(f.is_relative_to(b) for b in base_rels):
                continue
            if "tests" in f.parts:
                continue
            if any(f.is_relative_to(ex) for ex in exempt_rel):
                continue
            if f.suffix not in (".md", ".py", ".sh", ".yaml", ".yml", ".conf"):
                continue
            targets.append(ROOT / f)
        return targets
    for base in bases:
        if base.is_file():
            targets.append(base)
            continue
        if not base.is_dir():
            continue
        for f in sorted(base.rglob("*")):  # GITLS-FALLBACK: 非 git 仓回落
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
    if not targets:
        # 方向 5 + lib-05 fail-closed：默认分支与 --path 分支统一判空判红——
        # --path 指向不存在/拼错路径时 targets 同样为空，此前漏判致静默
        # 假绿 exit 0（扫描对象缺失 ≠ 引用完整）
        print("error: 扫描目标为空（无 --path 且扫描根缺失/全豁免，或 --path "
              "指向不存在的路径），判红", file=sys.stderr)
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
