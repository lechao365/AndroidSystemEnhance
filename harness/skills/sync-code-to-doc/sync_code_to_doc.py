#!/usr/bin/env python3
"""sync_code_to_doc - code/rpi5 变动报告生成器

规则详见: harness/skills/sync-code-to-doc/SKILL.md
用法:    python3 .../sync_code_to_doc.py [--check-only] [--dry-run] [--full-diff] [--base <ref>]
         python3 .../sync_code_to_doc.py --check-docs [--docs-root <dir>]
对比语义:
  --base 缺省(HEAD): git diff HEAD —— 工作区/未提交变动(归档后、commit 前使用)
  --base <ref>:      git diff <ref>...HEAD —— 分支相对 ref 的已提交累积变动
                     (promote 前使用 --base origin/main 对比 dev 相对 main 的批次)
  --check-docs:      仅执行文档索引一致性检查(死索引/漏索引/断链/孤儿)，不依赖 git diff
退出码:  0=成功(有变动); 3=参数/环境错误; 4=无变动; 5=文档索引不一致(--check-docs)
"""

from __future__ import annotations

import re
import sys
import argparse
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent.parent))
from harness.lib.harness_lib import (
    log_info, log_warn, log_error, step_begin, step_end,
    harness_init, harness_exit,
)
from harness.lib.paths import path as profile_path, repo_root


MARKDOWN_LINK_RE = re.compile(r"\]\(([^)#\s]+\.md)(?:#[^)]*)?\)")


def _iter_docs_md(docs_root: Path) -> list[Path]:
    """遍历 docs 根下业务 .md（排除 superpowers 目录），按路径排序。"""
    return sorted(
        p for p in docs_root.rglob("*.md")
        if "superpowers" not in p.parts
    )


def check_dead_index(readmes: list[Path]) -> list[tuple[Path, str]]:
    """README 索引中 ./xxx.md 链接指向不存在的文件 → 死索引。

    返回 [(README路径, 失效链接相对名)]。仅检查 ./ 相对链接，忽略 http/https。
    """
    dead: list[tuple[Path, str]] = []
    for readme in readmes:
        try:
            text = readme.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in MARKDOWN_LINK_RE.finditer(text):
            rel = m.group(1).strip().removeprefix("./")
            if rel.startswith(("http://", "https://")):
                continue
            if not (readme.parent / rel).exists():
                dead.append((readme, rel))
    return dead


def _readme_linked_targets(readmes: list[Path]) -> set[Path]:
    """收集所有 README 中 ./xxx.md 链接解析后的绝对路径。"""
    linked: set[Path] = set()
    for readme in readmes:
        try:
            text = readme.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in MARKDOWN_LINK_RE.finditer(text):
            rel = m.group(1).strip().removeprefix("./")
            if rel.startswith(("http://", "https://")):
                continue
            linked.add((readme.parent / rel).resolve())
    return linked


def check_missing_index(docs_root: Path, readmes: list[Path]) -> list[Path]:
    """docs 下存在 .md 且所在目录有 README，但未被任何 README 链接 → 漏索引。"""
    linked = _readme_linked_targets(readmes)
    missing: list[Path] = []
    for md in _iter_docs_md(docs_root):
        if md.name == "README.md":
            continue
        if (md.parent / "README.md").exists() and md.resolve() not in linked:
            missing.append(md)
    return missing


def check_broken_links(docs_root: Path) -> list[tuple[Path, str]]:
    """docs 下非 README 正文的 ./xxx.md 链接目标不存在 → 断链。

    README 的死链已由 check_dead_index 覆盖，此处排除避免重复。
    """
    broken: list[tuple[Path, str]] = []
    for md in _iter_docs_md(docs_root):
        if md.name == "README.md":
            continue
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in MARKDOWN_LINK_RE.finditer(text):
            rel = m.group(1).strip().removeprefix("./")
            if rel.startswith(("http://", "https://")):
                continue
            if not (md.parent / rel).exists():
                broken.append((md, rel))
    return broken


def check_orphans(docs_root: Path) -> list[Path]:
    """docs 下非 README 的 .md，无任何 md（含 README）入链 → 孤儿文档。"""
    referenced: set[Path] = set()
    for md in _iter_docs_md(docs_root):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in MARKDOWN_LINK_RE.finditer(text):
            rel = m.group(1).strip().removeprefix("./")
            if rel.startswith(("http://", "https://")):
                continue
            referenced.add((md.parent / rel).resolve())
    orphans: list[Path] = []
    for md in _iter_docs_md(docs_root):
        if md.name == "README.md":
            continue
        if md.resolve() not in referenced:
            orphans.append(md)
    return orphans


CODE_LINK_RE = re.compile(r"\]\(([^)#\s]+)(?:#(L\d+))?\)")
CODE_COMMENT_RE = re.compile(
    r"//\s*([A-Za-z0-9_./-]+\.(?:c|cpp|cc|h|mk|bp|py|sh|te|aidl)):\s*(\d+)")


def check_code_links(docs_root: Path) -> list[tuple[Path, str]]:
    """docs 内指向 code/rpi5 的链接目标不存在 → code 链接失效。

    返回 [(md, 失效链接)]。仅检查含 code/rpi5 的相对链接，忽略 http/https。
    """
    broken: list[tuple[Path, str]] = []
    for md in _iter_docs_md(docs_root):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in CODE_LINK_RE.finditer(text):
            rel = m.group(1).strip()
            if rel.startswith(("http://", "https://")):
                continue
            if "code/rpi5" not in rel:
                continue
            path_part = rel.partition("#")[0]
            if not (md.parent / path_part).resolve().exists():
                broken.append((md, rel))
    return broken


def check_anchor_bounds(docs_root: Path, code_root: Path) -> list[tuple[Path, str, int, int]]:
    """docs 内 code/rpi5 链接带 #L 锚点且行号超出文件总行数 → 锚点失效。

    返回 [(md, 链接, 行号, 文件总行数)]。文件缺失由 check_code_links 覆盖，此处跳过。
    """
    bad: list[tuple[Path, str, int, int]] = []
    for md in _iter_docs_md(docs_root):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in CODE_LINK_RE.finditer(text):
            rel = m.group(1).strip()
            if rel.startswith(("http://", "https://")) or "code/rpi5" not in rel:
                continue
            anchor = m.group(2) or ""
            if not anchor.startswith("L") or not anchor[1:].isdigit():
                continue
            line = int(anchor[1:])
            target = (md.parent / rel).resolve()
            if not target.is_file():
                continue
            total = len(target.read_text(encoding="utf-8").splitlines())
            if line > total:
                bad.append((md, rel, line, total))
    return bad


def check_code_comments(docs_root: Path, code_root: Path) -> list[tuple[Path, str, int | None]]:
    """形态 D 盲区扫描：代码块内 `// file:行` 注释。

    按 basename 在 code_root 下递归搜索（含 rpi5/rpi-zero2w 等多平台归档）。
    找不到 → (md, file, None)；找到但行号超文件总行数 → (md, file, 行号)。
    """
    bad: list[tuple[Path, str, int | None]] = []
    basename_index: dict[str, Path] | None = None

    def build_index() -> dict[str, Path]:
        idx: dict[str, Path] = {}
        for p in code_root.rglob("*"):
            if p.is_file():
                idx.setdefault(p.name, p)
        return idx

    for md in _iter_docs_md(docs_root):
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in CODE_COMMENT_RE.finditer(text):
            name, line_str = m.group(1).strip(), m.group(2)
            line = int(line_str)
            if basename_index is None:
                basename_index = build_index()
            target = basename_index.get(Path(name).name)
            if target is None:
                bad.append((md, name, None))
                continue
            total = len(target.read_text(encoding="utf-8").splitlines())
            if line > total:
                bad.append((md, name, line))
    return bad


def _render_check_report(docs_root: Path, dead, missing, broken, orphans,
                         code_links, anchors, code_comments) -> None:
    """输出 --check-docs 报告。"""
    print("")
    print("========== 文档索引一致性检查（%s） ==========" % docs_root)
    if dead:
        print("\n[死索引] README 引用 docs 下不存在的文件:")
        for readme, rel in dead:
            print(f"  {readme}  →  ./{rel}")
    if missing:
        print("\n[漏索引] docs 下存在但未被所在目录 README 链接:")
        for md in missing:
            print(f"  {md}")
    if broken:
        print("\n[断链] 正文引用不存在的目标:")
        for md, rel in broken:
            print(f"  {md}  →  ./{rel}")
    if orphans:
        print("\n[孤儿] docs 下无任何入链的文档:")
        for md in orphans:
            print(f"  {md}")
    if code_links:
        print("\n[code链接失效] docs 引用 code/rpi5 下不存在的文件:")
        for md, rel in code_links:
            print(f"  {md}  →  {rel}")
    if anchors:
        print("\n[锚点超界] 行号锚点 #L 超出文件总行数:")
        for md, rel, line, total in anchors:
            print(f"  {md}  →  {rel}  #L{line}（文件仅 {total} 行）")
    if code_comments:
        print("\n[形态D] 代码块注释引用问题（文件缺失或行号超界）:")
        for md, rel, line in code_comments:
            if line is None:
                print(f"  {md}  →  // {rel}（code 下未找到该文件）")
            else:
                print(f"  {md}  →  // {rel}:{line}（超出文件行数）")


def cmd_check_docs(docs_root: Path, code_root: Path | None = None) -> int:
    """执行文档索引一致性检查，返回退出码（0=一致；5=不一致）。

    code_root 缺省时锚点/形态D检查跳过（返回空），供纯 docs 场景使用。
    """
    readmes = sorted(
        p for p in docs_root.rglob("README.md")
        if "superpowers" not in p.parts
    )
    dead = check_dead_index(readmes)
    missing = check_missing_index(docs_root, readmes)
    broken = check_broken_links(docs_root)
    orphans = check_orphans(docs_root)
    code_links = check_code_links(docs_root)
    if code_root is not None and code_root.is_dir():
        anchors = check_anchor_bounds(docs_root, code_root)
        code_comments = check_code_comments(docs_root, code_root)
    else:
        anchors = []
        code_comments = []

    _render_check_report(docs_root, dead, missing, broken, orphans,
                         code_links, anchors, code_comments)

    if not (dead or missing or broken or orphans or code_links
            or anchors or code_comments):
        print("\n一致：无死索引 / 漏索引 / 断链 / 孤儿 / code 链接失效 / 锚点超界 / 形态D问题。")
        return 0
    return 5


def _git(args: list[str], timeout: int = 300) -> str:
    """执行 git 命令，返回 stdout。失败时 log_error（含 stderr）并返回空字符串。"""
    r = _git_result(args, timeout=timeout)
    if r.returncode != 0:
        err = r.stderr.strip()
        log_error(
            f"git 失败({r.returncode}): {' '.join(args)}"
            + (f": {err[:300]}" if err else "")
        )
    return r.stdout


def _git_result(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """执行 git 命令，返回 CompletedProcess。失败时 log_error 并返回失败对象。"""
    try:
        return subprocess.run(
            ["git"] + args,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            cwd=repo_root(),
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError) as e:
        log_error(f"git 失败: {' '.join(args)}: {e}")
        return subprocess.CompletedProcess(args, returncode=-1, stdout="", stderr="")


def _collect_changes(patch_dir: Path, root: Path,
                     base: str = "HEAD") -> list[tuple[str, str, str]]:
    """收集 tracked + untracked 变动列表，返回 (status, path1, path2) 元组列表。

    base="HEAD":  对比工作区 vs HEAD（git diff HEAD，含 staged+unstaged）
    base=<ref>:   对比 <ref>...HEAD（分支相对 ref 的已提交累积变动）
    untracked 一律用 ls-files --others 补（两语义下都报告工作区未跟踪文件）。
    """
    patch_dir_str = str(patch_dir)
    if base == "HEAD":
        tracked = _git(["diff", "HEAD", "--name-status", "--", patch_dir_str]).strip()
    else:
        tracked = _git(
            ["--no-pager", "diff", base + "...HEAD",
             "--name-status", "--", patch_dir_str]
        ).strip()
    untracked_lines = _git(
        ["ls-files", "--others", "--exclude-standard", "--", patch_dir_str]
    ).strip()

    changes: list[tuple[str, str, str]] = []
    if tracked:
        for line in tracked.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2:
                path2 = parts[2] if len(parts) > 2 else ""
                changes.append((parts[0], parts[1], path2))
    if untracked_lines:
        for line in untracked_lines.splitlines():
            changes.append(("A", line, ""))
    return changes


def _classify_group(rel: str) -> str:
    """根据相对路径分类到目录分组 key。"""
    if rel.startswith("kernel/modified/"):
        return "kernel_modified"
    elif rel.startswith("kernel/new/"):
        return "kernel_new"
    elif rel.startswith("aosp/modified/"):
        return "aosp_modified"
    elif rel.startswith("aosp/new/"):
        return "aosp_new"
    elif rel.startswith("others/"):
        return "others"
    else:
        return "root"


def _compute_numstat(stat_path: str, display_path: str, base_status: str,
                     root: Path, base: str = "HEAD") -> tuple[str, str]:
    """计算单个变动的增删行数，返回 (added, deleted)。

    多行 numstat（pathspec 多命中异常）时取与目标路径匹配的行，防御取错。
    """
    if base == "HEAD":
        numstat = _git(["diff", "HEAD", "--numstat", "--", stat_path]).strip()
    else:
        numstat = _git(
            ["--no-pager", "diff", base + "...HEAD", "--numstat", "--", stat_path]
        ).strip()

    numstat_line = ""
    if numstat:
        lines = numstat.splitlines()
        if len(lines) == 1:
            numstat_line = lines[0]
        else:
            for line in lines:
                if line.endswith(stat_path):
                    numstat_line = line
                    break
            if not numstat_line:
                numstat_line = lines[0]

    if numstat_line:
        parts = numstat_line.split("\t")
        added = parts[0] if parts[0] != "-" else "0"
        deleted = parts[1] if parts[1] != "-" else "0"
    else:
        added = "0"
        deleted = "0"

    # untracked 新文件：直接统计行数
    if base_status == "A" and not numstat_line:
        fp = root / display_path
        if fp.is_file():
            try:
                added = str(fp.read_text(encoding="utf-8").count("\n"))
            except Exception as e:
                log_warn(f"读取文件行数失败: {fp}: {e}")
                added = "0"
            deleted = "0"

    return added, deleted


def _format_line(base_status: str, rel: str, added: str, deleted: str,
                 path1: str, root: Path, patch_dir: Path) -> str:
    """格式化单个变动的输出行。"""
    if base_status == "R":
        old_obj = root / path1
        try:
            old_rel = str(old_obj.relative_to(patch_dir).as_posix())
        except ValueError:
            old_rel = path1
        return f"  [R] {old_rel} → {rel}  +{added} -{deleted}"
    elif base_status == "C":
        return f"  [C] {rel}  +{added} -{deleted}"
    elif base_status == "A":
        return f"  [A] {rel}  +{added} -{deleted}"
    elif base_status == "M":
        return f"  [M] {rel}  +{added} -{deleted}"
    elif base_status == "D":
        return f"  [D] {rel}  -{deleted}"
    else:
        return f"  [{base_status}] {rel}  +{added} -{deleted}"


def _render_report(groups: dict[str, list[str]]) -> None:
    """输出分组报告到 stdout。"""
    group_order = [
        ("kernel/modified", "kernel_modified"),
        ("kernel/new", "kernel_new"),
        ("aosp/modified", "aosp_modified"),
        ("aosp/new", "aosp_new"),
        ("others", "others"),
        ("(root)", "root"),
    ]
    for label, key in group_order:
        lines = groups[key]
        if not lines:
            continue
        print("")
        print(f"--- {label}/ ---") 
        for line in lines:
            print(line)


def _render_summary(totals: dict[str, int]) -> None:
    """输出汇总统计到 stdout。"""
    total = sum(totals.values())
    parts: list[str] = []
    if totals["A"] > 0:
        parts.append(f"{totals['A']} 新增")
    if totals["M"] > 0:
        parts.append(f"{totals['M']} 修改")
    if totals["D"] > 0:
        parts.append(f"{totals['D']} 删除")
    if totals["R"] > 0:
        parts.append(f"{totals['R']} 重命名")
    if totals["other"] > 0:
        parts.append(f"{totals['other']} 其他")
    detail = ", ".join(parts)
    print("")
    print(f"总计: {total} 个文件变动 ({detail})")


def _render_full_diff(patch_dir_str: str, root: Path, base: str = "HEAD") -> None:
    """输出完整 diff 正文到 stdout（含 tracked diff + untracked 新文件内容）。"""
    print("")
    print("========== 完整 diff 正文（%s） ==========" % base)

    if base == "HEAD":
        diff_text = _git(["--no-pager", "diff", "HEAD", "--", patch_dir_str])
    else:
        diff_text = _git(["--no-pager", "diff", base + "...HEAD", "--", patch_dir_str])
    if diff_text.strip():
        print(diff_text.rstrip())
    else:
        log_warn("无法获取 diff 正文")

    untracked_files = _git(
        ["ls-files", "--others", "--exclude-standard", "--", patch_dir_str]
    ).strip()
    if untracked_files:
        print("")
        print("--- untracked 新文件完整内容 ---")
        for f in untracked_files.splitlines():
            print("")
            print(f"+++ b/{f} (新文件)")
            try:
                content = (root / f).read_text(encoding="utf-8")
                for content_line in content.splitlines(keepends=True):
                    print(f"+{content_line}", end="")
            except Exception:
                log_warn(f"无法读取文件: {root / f}")


def _process_changes(changes: list[tuple[str, str, str]], patch_dir: Path,
                     root: Path, base: str = "HEAD") -> tuple[dict[str, int], dict[str, list[str]]]:
    """处理变动列表: 分类、计数、计算 numstat、格式化行。

    返回 (totals, groups)。
    """
    totals: dict[str, int] = {"A": 0, "M": 0, "D": 0, "R": 0, "other": 0}
    groups: dict[str, list[str]] = {
        "kernel_modified": [],
        "kernel_new": [],
        "aosp_modified": [],
        "aosp_new": [],
        "others": [],
        "root": [],
    }

    for status, path1, path2 in changes:
        base_status = status[0]
        display_path = path2 if base_status in ("R", "C") else path1
        stat_path = display_path

        dp_obj = root / display_path
        try:
            rel = str(dp_obj.relative_to(patch_dir).as_posix())
        except ValueError:
            rel = display_path

        group_key = _classify_group(rel)

        if base_status in totals:
            totals[base_status] += 1
        else:
            totals["other"] += 1

        added, deleted = _compute_numstat(
            stat_path, display_path, base_status, root, base)
        line = _format_line(base_status, rel, added, deleted, path1, root, patch_dir)
        groups[group_key].append(line)

    return totals, groups


def _verify_base(base: str) -> None:
    """校验 --base ref 可解析（失败 exit 3）。HEAD 为内置语义，跳过校验。"""
    if base == "HEAD":
        return
    r = _git_result(["rev-parse", "--verify", "--quiet", base + "^{commit}"])
    if r.returncode != 0:
        log_error(f"对比基线不可解析（不是有效 commit ref）: {base}")
        harness_exit(3)


def main() -> None:
    parser = argparse.ArgumentParser(description="code/rpi5 变动报告生成器")
    parser.add_argument(
        "--check-only",
        "--dry-run",
        action="store_true",
        dest="check_only",
        help="仅输出报告，不输出 AI 操作提示",
    )
    parser.add_argument(
        "--full-diff",
        action="store_true",
        help="在报告末尾追加 git diff 正文，供 AI 直接读取（零往返）",
    )
    parser.add_argument(
        "--base",
        default="HEAD",
        help="对比基线 ref（默认 HEAD=工作区 vs HEAD；promote 前用 --base origin/main "
             "对比 dev 相对 main 的已提交批次变动）",
    )
    parser.add_argument(
        "--check-docs",
        action="store_true",
        help="仅执行文档索引一致性检查（死索引/漏索引/断链/孤儿），不依赖 git diff",
    )
    parser.add_argument(
        "--docs-root",
        default=None,
        help="docs 根目录（默认仓库 docs/；测试可覆盖）",
    )
    parser.add_argument(
        "--code-root",
        default=None,
        help="code 根目录（默认仓库 code/，覆盖 rpi5/rpi-zero2w 等多平台归档；锚点/形态D检查用，测试可覆盖）",
    )
    args = parser.parse_args()

    harness_init("sync_code_to_doc")

    if args.check_docs:
        docs_root = Path(args.docs_root) if args.docs_root else repo_root() / "docs"
        if not docs_root.is_dir():
            log_error(f"docs 根目录不存在: {docs_root}")
            harness_exit(3)
        code_root = Path(args.code_root) if args.code_root else repo_root() / "code"
        code = cmd_check_docs(docs_root, code_root)
        harness_exit(code)

    patch_dir = profile_path("PATCHS_DIR")
    root = repo_root()

    if not patch_dir.is_dir():
        log_error(f"code 目录不存在: {patch_dir}")
        harness_exit(3)

    _verify_base(args.base)

    head_short = _git(["rev-parse", "--short", "HEAD"]).strip() or "unknown"
    base_label = args.base if args.base != "HEAD" else "HEAD(工作区)"
    log_info(f"基准: {base_label} → HEAD ({head_short})")
    log_info(f"扫描: {patch_dir}/") 

    # --- 获取变动列表 ---
    changes = _collect_changes(patch_dir, root, args.base)

    if not changes:
        print("")
        log_info("无变动")
        harness_exit(4)

    # --- 统计与分组 ---
    step_begin("获取变动列表")
    step_end(True)
    step_begin("按目录分组")

    totals, groups = _process_changes(changes, patch_dir, root, args.base)

    step_end(True)
    step_begin("分组汇总输出")

    _render_report(groups)

    step_end(True)
    step_begin("汇总统计")

    _render_summary(totals)

    step_end(True)

    # --- 完整 diff（可选）---
    if args.full_diff:
        step_begin("完整 diff 正文（%s）" % args.base)
        _render_full_diff(str(patch_dir), root, args.base)
        step_end(True)

    # --- AI 操作提示 ---
    if not args.check_only:
        print(
            "\n下一步（7 步闭环，详见 SKILL.md）：\n"
            "  ① 本报告已列出变动清单（+ --full-diff 可取完整 diff 正文）\n"
            "    对比语义: --base 缺省=工作区 vs HEAD(归档未提交); "
            "--base origin/main=dev 相对 main 已提交批次(promote 前)\n"
            "  ② 依据 harness/config/doc-sync-mapping.yaml"
            " 将变动分发到对应文档目录（01/02）\n"
            "  ③ 读 code/rpi5/manifest.yaml，"
            "按 source 去 workspace（KERNEL_WS/AOSP_WS）取全量源码上下文\n"
            "     （modified 类经 source 拼接；new/others 类直接读 code；"
            "deletions 段取历史文件）\n"
            "  ④ 用行号锚点(#L) + 符号名 + 文件名 "
            "定位受影响章节（注意形态D代码块注释盲区）\n"
            "  ⑤ 输出动作清单级方案（文档->章节->动作），用户确认后落盘\n"
            "  ⑥ 章节级增量落盘，刷新行号锚点（含盲区/区间终点/重复出现处）\n"
             "  ⑦ 一致性自检：锚点有效性 / 路径合规 / 断链 / 模板章节完整性；\n"
             "     --check-docs 检查文档索引一致性（死索引/漏索引/断链/孤儿，退出码 5=不一致）\n"
        )

    harness_exit(0)


if __name__ == "__main__":
    main()
