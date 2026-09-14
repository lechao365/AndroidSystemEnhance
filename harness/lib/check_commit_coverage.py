#!/usr/bin/env python3
# ============================================================
# check_commit_coverage.py — 非 meta 提交须被某份收据 commit_scope 覆盖
# 背景（本地直连开发纳入证据流程，批次 133b55812a81 方向 3）：apply 设备
#   上直连用 LLM 开发、不走 cross-device-apply 是合法路径，但每次非 meta
#   提交（业务/工具代码改动）必须有收据 commit_scope 覆盖，manual 与 CDP
#   同等。此前直连开发的提交可能完全没有收据（如 d547eb9 检查器加固修复
#   无任何收据覆盖），验证链断裂无从追溯。
# 本检查器扫描自最近 promoted baseline 的 source_commit 起的所有提交：
#   - meta 提交（提交标题 type ∈ {构建, 文档}：基线发布/晋升、纯文档）豁免；
#   - 非 meta 提交的改动文件集须被某份收据（data/verify-results/*.md）的
#     commit_scope 覆盖（目录项按前缀匹配、排除收据目录自引用）；
#   - 未覆盖即判红，并直接打印补齐命令（ws_report.py --manual <区间>）。
# 由 selfcheck 以 commit_coverage_rc 透出、ws_report/CI 判红。
# 用法：python3 harness/lib/check_commit_coverage.py [--repo <仓根>]
# 退出码：0 覆盖完整 / 1 存在未覆盖非 meta 提交 / 2 参数错误
# ============================================================

import argparse
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

# meta 提交 = 提交标题 type ∈ {构建, 文档}（基线发布/晋升与纯文档豁免收据覆盖）
_META_TYPE_RE = re.compile(r"^(构建|文档)\(")
# 非 meta 提交标题（新增/修复/重构/杂项）：无匹配即非标准提交亦视为非 meta
_NON_META_TYPE_RE = re.compile(r"^(新增|修复|重构|杂项)\(")

# 文档类文件后缀/前缀（批次 e503284f97b9 方向 2 fail-open 修复）：meta 提交
# 豁免须同时满足"标题 meta 且改动全为文档类"，堵 文档(x): 标题挟带代码改动
# 逃过收据覆盖的空子——仅靠标题判定是 fail-open，代码改动必须被收据覆盖
_DOC_SUFFIXES = {".md", ".txt", ".rst"}
_DOC_PREFIXES = ("docs/", "doc/")

# 构建元文件豁免面（批次 b410b688d206 方向 1 收窄，e3f5f80d7b22 方向 1 补全）：
# 构建(baseline) 发布/晋升提交本职 add baseline-status.yaml 与证据目录
# （publish_main_base.sh 晋升提交一并 add data/baselines/ 与 data/known-issues/），
# 豁免须改动面限于 baseline-status.yaml 与这两个证据目录——此前仅 baseline-
# status.yaml 致晋升提交 files 子集判定恒假、promote 每次必红（18f0f14 首发）
_BUILD_META_EXEMPT_PATHS = frozenset({
    "harness/config/baseline-status.yaml",
    "data/baselines/",
    "data/known-issues/",
})


def _build_meta_exempt_file(path: str) -> bool:
    """构建( 提交豁免面文件判定：baseline-status.yaml 精确 + 证据目录前缀。

    目录项（结尾 /）按前缀匹配其下文件（与 commit_scope 目录匹配同语义）。
    """
    if path in _BUILD_META_EXEMPT_PATHS:
        return True
    return any(path.startswith(p) for p in _BUILD_META_EXEMPT_PATHS
               if p.endswith("/"))

# 程序读取的 md 面（批次 b410b688d206 方向 1 收窄）：harness/rules/ 是判据、
# data/known-issues/ 是 known-issue 数据，被程序读取的 md 改动即改判据/关
# known-issue，不得按纯文档豁免（否则 文档(x): 标题即可零收据改判据）
_PROGRAM_MD_PREFIXES = ("harness/rules/", "data/known-issues/")

# 收据目录自引用豁免：commit_scope 生成侧排除 data/verify-results/，判定侧同排除
_VERIFY_PREFIX = "data/verify-results"


def _git(args: list[str], cwd: Path):
    """git 命令输出；失败返回 None（调用方按上下文判定降级/判红）。"""
    try:
        r = subprocess.run(["git", "-c", "core.quotepath=false", *args],
                           cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r

def recent_promoted_baseline_commit(root: Path) -> str:
    """最近 promoted baseline 的 source_commit（12hex）；无则空串。

    读 baseline-status.yaml，取 status=promoted 且文件顺序最后（登记顺序即
    晋升时序）的 source_commit。yaml 缺失/解析失败返回空串（调用方跳过扫描）。
    """
    try:
        import yaml
    except ImportError:
        return ""
    p = root / "harness" / "config" / "baseline-status.yaml"
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ""
    bases = data.get("baselines")
    if not isinstance(bases, list):
        return ""
    last = ""
    for b in bases:
        if isinstance(b, dict) and b.get("status") == "promoted":
            sc = (b.get("source_commit") or "").strip()
            if sc:
                last = sc
    return last


def _subject(sha: str, cwd: Path) -> str:
    """提交标题首行（git log --format=%s）。"""
    r = _git(["log", "-1", "--format=%s", sha], cwd)
    return (r.stdout or "").strip() if r and r.returncode == 0 else ""


def _is_ancestor_of_head(sha: str, cwd: Path) -> bool:
    """sha 是否 HEAD 祖先（git merge-base --is-ancestor；失败即 False fail-closed）。

    方向 3（批次 6a3a0969d477）：promote 重建 dev 后 dev 侧 BH 悬空（仅 verified
    tag 可达），无 tag 即 rev-list 失败判红；回填 main 侧 squash sha 后为祖先。
    """
    r = _git(["merge-base", "--is-ancestor", sha, "HEAD"], cwd)
    return r is not None and r.returncode == 0


def is_meta_subject(subject: str) -> bool:
    """提交标题是否 meta（type=构建/文档）→ 收据覆盖候选豁免。"""
    return bool(_META_TYPE_RE.match(subject or ""))


def _is_doc_file(path: str) -> bool:
    """路径是否文档类文件（meta 提交豁免的改动面判据）。

    批次 b410b688d206 方向 1 收窄：
      - docs/ 前缀不再不看扩展名——docs/ 下 .py/.sh 等非文档文件不得豁免；
      - harness/rules/ 与 data/known-issues/ 下被程序读取的 md 不算文档。
    """
    if path == _VERIFY_PREFIX or path.startswith(_VERIFY_PREFIX + "/"):
        return True  # 收据目录自引用豁免（上层已排除，双保险）
    if path.startswith(_PROGRAM_MD_PREFIXES):
        return False
    if path.startswith(_DOC_PREFIXES):
        return any(path.endswith(s) for s in _DOC_SUFFIXES)
    return any(path.endswith(s) for s in _DOC_SUFFIXES)


def _commit_is_meta_exempt(sha: str, cwd: Path) -> bool:
    """提交是否 meta 豁免：标题 meta 且改动面符合对应 meta 语义。

    fail-open 修复（批次 e503284f97b9 方向 2）：仅凭标题豁免是漏洞——写
    文档(x): 标题即可把代码改动夹带进 meta 提交逃过收据覆盖。分两类：
      - 构建(baseline/...)：发布/晋升提交本职即收口整个变更（含代码），
        豁免须改动面限于 baseline-status.yaml 与收据目录（收窄，批次
        b410b688d206 方向 1）——此前无条件豁免，写 构建(任意词) 即可
        零收据挟带任意 harness 代码；
      - 文档(...)：标题文档但改动含非文档文件（.py/.sh/.yml 等）时按非
        meta 处理（须被收据覆盖）；改动文件读不到时 fail-closed 不豁免
        （无法证实是纯文档）。
    """
    subject = _subject(sha, cwd)
    if not is_meta_subject(subject):
        return False
    if subject.startswith("构建("):
        # 注意：不设「构建(baseline): 发布」标题前缀豁免——纯标题免检是回归
        # （批次 6a3a0969d477 方向 1 撤回 9e148fa）：实测「构建(baseline): 发布
        # 后门」标题挟带 code/ 与改写检查器自身均 rc=0，三批收窄回到起点。
        # 发布汇总提交由 promote 侧把 source_commit 回填为 main 侧 squash sha
        # 解决（重建 dev 后区间天然为空，起点之后夹带仍判红），而非标题豁免。
        # 其余 构建( 元提交仍须改动面限于构建元文件（防挟带代码逃过收据覆盖）
        files = commit_files(sha, cwd)
        if files is None:
            return False
        return all(_build_meta_exempt_file(f) for f in files)
    files = commit_files(sha, cwd)
    if files is None:
        return False
    return all(_is_doc_file(f) for f in files)


def _is_non_meta(subject: str) -> bool:
    """提交标题是否非 meta（type=新增/修复/重构/杂项）；非标准提交保守视为非 meta。"""
    return not is_meta_subject(subject)


def commit_files(sha: str, cwd: Path) -> set[str]:
    """提交改动文件集（排除 data/verify-results/ 收据目录自引用）。

    git diff-tree -r --name-status --no-renames；目录项（结尾 /）原样保留
    （收据 commit_scope 的目录项按前缀匹配其下文件）。git 失败返回 None。
    """
    r = _git(["diff-tree", "-r", "--name-status", "--no-renames", sha],
             cwd)
    if r is None or r.returncode != 0:
        return None
    files = set()
    for ln in (r.stdout or "").splitlines():
        parts = ln.split("\t")
        if len(parts) < 2:
            continue
        path = parts[1].strip()
        if path == _VERIFY_PREFIX or path.startswith(_VERIFY_PREFIX + "/"):
            continue
        files.add(path)
    return files


def range_non_meta_name_status(base: str, head: str, cwd: Path) -> list[str] | None:
    """git 区间内非 meta 提交的改动 name-status 行列表（排除收据目录自引用）。

    供 ws_report --manual 回填 commit_scope：收据须覆盖本次全部非 meta 提交，
    meta（构建/文档）提交改动豁免；区间为空/无非 meta 提交返回空列表，
    git 命令失败返回 None（调用方按 fail-closed 拒写/置空）。
    """
    r = _git(["rev-list", "--no-merges", f"{base}..{head}"], cwd)
    if r is None or r.returncode != 0:
        return None
    lines: list[str] = []
    for sha in (r.stdout or "").splitlines():
        sha = sha.strip()
        if not sha:
            continue
        if _commit_is_meta_exempt(sha, cwd):
            continue
        d = _git(["diff-tree", "-r", "--name-status", "--no-renames", sha], cwd)
        if d is None or d.returncode != 0:
            continue
        for ln in (d.stdout or "").splitlines():
            parts = ln.split("\t")
            if len(parts) < 2:
                continue
            path = parts[1].strip()
            if path == _VERIFY_PREFIX or path.startswith(_VERIFY_PREFIX + "/"):
                continue
            if ln not in lines:
                lines.append(ln)
    return lines


def _covered_by_scope(files: set[str], scope_paths: set[str]) -> bool:
    """提交文件集是否被某收据 scope 全部覆盖（目录项按前缀匹配）。"""
    dirs = {p for p in scope_paths if p.endswith("/")}
    for f in files:
        if f in scope_paths:
            continue
        if any(f.startswith(d) for d in dirs):
            continue
        return False
    return True


def _receipt_scopes(root: Path) -> tuple[list[set[str]], list[str]]:
    """全部收据 commit_scope 的路径集列表 + 不可用收据错误清单。

    返回 (scopes, errors)：
    - scopes：可作覆盖证据的收据路径集（commit_scope 有效、result != fail）；
    - errors：跳过收据的原因（未跟踪 / 解析失败 / result=fail / 无 scope），
      供调用方判红——收据是覆盖判定唯一证据源，坏收据静默丢弃等于手写一份
      垃圾收据即免检（fail-open）。

    fail-open 修复（批次 e503284f97b9 方向 2）：
      1. 只扫 git 跟踪的收据（git ls-files），未跟踪手写 .md 不算证据；
      2. 解析失败/无 scope 记录错误而非静默 continue；
      3. result=fail 收据不作覆盖证据（失败收据不能证明覆盖）。
    """
    here = Path(__file__).resolve().parent
    cdp_lib = here.parent / "skills" / "cross-device" / "lib" / "python"
    if str(cdp_lib) not in sys.path:
        sys.path.insert(0, str(cdp_lib))
    from cdp_receipt import Receipt  # noqa: E402
    from commit_scope import parse_scope  # noqa: E402

    scopes: list[set[str]] = []
    errors: list[str] = []
    d = root / "data" / "verify-results"
    if not d.is_dir():
        return scopes, errors
    # 证据只认 HEAD 中已提交的收据（git ls-tree HEAD，内容经 git show 读）：
    # 此前 ls-files 查索引（git add 不 commit 即授予覆盖）与 read_text 读工作区
    # （只改工作区内容即改判定）都不是 HEAD——两处一起收窄为 git show HEAD
    # 读（批次 b410b688d206 方向 2），未提交/仅索引/工作区改动的收据不作证据。
    r = _git(["ls-tree", "-r", "--name-only", "HEAD", "--", _VERIFY_PREFIX],
             root)
    if r is None or r.returncode != 0:
        # git ls-tree 失败 fail-closed：无法证实 HEAD 收据面即判红，不静默放行
        errors.append("<ls-tree-failed>: git ls-tree HEAD 枚举收据失败，"
                      "无法证实覆盖证据")
        return scopes, errors
    # 工作区未跟踪收据补充判红（不在 HEAD 但存在的手写收据须显式提示，
    # 审计链要求坏证据可见——手写未跟踪收据仍不作证据，且不静默无视）
    r_untracked = _git(["ls-files", "--others", "--exclude-standard", "--",
                        _VERIFY_PREFIX], root)
    if r_untracked is not None and r_untracked.returncode == 0:
        for ln in (r_untracked.stdout or "").splitlines():
            rel = ln.strip()
            if rel and rel.endswith(".md") and not rel.endswith("/trend.md"):
                errors.append(f"{rel}: 未跟踪（手写收据不作覆盖证据）")
    for rel in sorted(ln.strip() for ln in (r.stdout or "").splitlines()
                      if ln.strip()):
        if not rel.startswith(_VERIFY_PREFIX + "/"):
            continue
        if not rel.endswith(".md") or rel.endswith("/trend.md"):
            continue
        g = _git(["show", f"HEAD:{rel}"], root)
        if g is None or g.returncode != 0:
            errors.append(f"{rel}: 从 HEAD 读取失败（git show）")
            continue
        txt = g.stdout or ""
        # 非收据文件（不含收据头 key-value，如测试夹具/文档）跳过不判红——
        # 只有形如收据的 md 才是覆盖证据候选；手写伪造收据若模仿头仍会被
        # 后续解析/result 白名单判定拦截（方向 2）
        if "batch_id:" not in txt and "schema_version:" not in txt:
            continue
        try:
            rpt, errs = Receipt.from_text(txt)
        except (OSError, UnicodeDecodeError) as e:
            errors.append(f"{rel}: 解析失败（{e}）")
            continue
        if errs or rpt is None:
            errors.append(f"{rel}: 解析失败")
            continue
        if rpt.result == "fail":
            # fail 收据不作覆盖证据（合法失败记录，跳过不判红），依赖它的
            # 提交会因无覆盖而判红
            continue
        if rpt.result not in ("pass", "skip"):
            # 白名单（方向 2，批次 b410b688d206）：仅 pass/skip 可作覆盖
            # 证据——此前只精确匹配小写 fail 才跳过，FAIL/空/尾空格等非
            # 标准 result 全被放行，改判据/关 known-issue 可零收据通关
            errors.append(f"{rel}: result 非法（仅 pass/skip 可作覆盖证据，"
                          f"实际 {rpt.result!r}）")
            continue
        if not (rpt.commit_scope or "").strip():
            # commit_scope 字段引入前的历史收据（无此字段）合法，跳过不判红
            continue
        _counts, paths = parse_scope(rpt.commit_scope)
        if not paths:
            continue
        scopes.append(paths)
    return scopes, errors


def uncovered_commits(root: Path) -> list[tuple[str, str]]:
    """返回未覆盖/无法证实覆盖的提交清单 [(sha 或哨兵, subject)]。

    判红语义（fail-closed）：调用方无法证实"自最近 promoted baseline 起的
    非 meta 提交均被收据覆盖"即判红，不静默放行。判红来源：
      - 无/坏 baseline-status.yaml：无法界定覆盖起点（fail-open 修复，
        批次 e503284f97b9 方向 2——此前全放行且打印 OK）；
      - git rev-list 自 baseline 起枚举失败：无法证实覆盖；
      - 收据不可用（未跟踪/解析失败/result=fail/无 scope）：覆盖证据损坏，
        手写垃圾收据不再免检；
      - 非 meta 提交改动未被任何可用收据 scope 覆盖。
    """
    base = recent_promoted_baseline_commit(root)
    if not base:
        return [("<no-baseline>",
                 "baseline-status.yaml 缺失/无 promoted 记录/解析失败，无法"
                 "界定覆盖起点——须登记 baseline 或人工核查（fail-closed）")]
    # 方向 3（批次 6a3a0969d477）：source_commit 合法性校验——12hex 格式且
    # 须为 HEAD 祖先（promote 重建 dev 后 dev 侧 BH 不再是祖先，悬空起点会让
    # rev-list 区间失控含汇总提交/失败判红；回填 main 侧 squash sha 后区间
    # 天然为空）。任一不满足即判红（fail-closed，无法可靠界定覆盖起点）。
    if not re.fullmatch(r"[0-9a-f]{12}", base):
        return [("<invalid-source-commit>",
                 f"source_commit={base!r} 非 12hex，无法界定覆盖起点（fail-closed）")]
    if not _is_ancestor_of_head(base, root):
        return [("<source-commit-not-ancestor>",
                 f"source_commit={base} 非 HEAD 祖先（promote 重建 dev 后悬空，"
                 "须回填 main 侧 squash sha），无法界定覆盖起点（fail-closed）")]
    r = _git(["rev-list", "--no-merges", f"{base}..HEAD"], root)
    if r is None or r.returncode != 0:
        return [("<git-rev-list-failed>",
                 "git rev-list 自最近 baseline 起枚举提交失败，无法证实覆盖")]
    scopes, scope_errs = _receipt_scopes(root)
    out: list[tuple[str, str]] = []
    for e in scope_errs:
        out.append(("<receipt-invalid>", e))
    for sha in (r.stdout or "").splitlines():
        sha = sha.strip()
        if not sha:
            continue
        if _commit_is_meta_exempt(sha, root):
            continue
        files = commit_files(sha, root)
        if files is None:
            out.append((sha, _subject(sha, root) + "（改动文件读取失败）"))
            continue
        if not files:
            continue
        if not any(_covered_by_scope(files, sp) for sp in scopes):
            out.append((sha, _subject(sha, root)))
    return out


def range_uncovered_fully_covered(base12: str, head12: str, scope_str: str,
                                  root: Path) -> bool:
    """区间内每个未覆盖 sha 均被 scope 单行覆盖 → 豁免可证（方向 3）。

    供 ws_report --manual 的 commit_coverage_rc 豁免判据。此前 _manual_gap_exists
    是存在性检查——区间内有任一未覆盖提交即豁免整个 rc，收据 scope 只补一半
    （覆盖部分提交）照样静音其余判红。改为逐 sha 判定：
      - 区间内每个未覆盖提交（uncovered_commits 命中且落在区间）的改动
        文件集均被本收据 scope 覆盖才返 True；
      - 任一未覆盖提交未被本 scope 覆盖 / 区间无未覆盖提交（rc=1 属静音
        或伪造）/ scope 非法 / git 枚举失败（fail-closed）→ 返 False 不豁免。
    """
    here = Path(__file__).resolve().parent
    cdp_lib = here.parent / "skills" / "cross-device" / "lib" / "python"
    if str(cdp_lib) not in sys.path:
        sys.path.insert(0, str(cdp_lib))
    from commit_scope import parse_scope  # noqa: E402

    _counts, scope_paths = parse_scope(scope_str)
    if not scope_paths:
        return False
    uncov = uncovered_commits(root)
    if not uncov:
        return False
    r = _git(["rev-list", "--no-merges", f"{base12}..{head12}"], root)
    if r is None or r.returncode != 0:
        return False
    in_range = {ln.strip() for ln in (r.stdout or "").splitlines()
                if ln.strip()}
    in_range_gap = False
    for sha, _ in uncov:
        if sha not in in_range:
            continue
        in_range_gap = True
        files = commit_files(sha, root)
        if files is None:
            return False
        if not files:
            continue
        if not _covered_by_scope(files, scope_paths):
            return False
    return in_range_gap


def scope_covers_baseline_head(scope_str: str, root: Path) -> bool:
    """scope 单行是否覆盖最近 baseline..HEAD 内任一非 meta 提交（方向 3）。

    供 cdp_receipt.prune_details 第三类保护：收据是 commit_coverage 判定的
    唯一覆盖凭据，覆盖最近 promoted baseline..HEAD 区间提交的收据一旦被
    prune 老化删掉，依赖它的提交即翻红自锁（124520 manual 收据独自兜住
    区间全部 7 个提交的实例）。scope 非法/无法界定起点/枚举失败均返 False
    （保守不触发保护）。
    """
    base = recent_promoted_baseline_commit(root)
    if not base:
        return False
    here = Path(__file__).resolve().parent
    cdp_lib = here.parent / "skills" / "cross-device" / "lib" / "python"
    if str(cdp_lib) not in sys.path:
        sys.path.insert(0, str(cdp_lib))
    from commit_scope import parse_scope  # noqa: E402

    _counts, scope_paths = parse_scope(scope_str)
    if not scope_paths:
        return False
    r = _git(["rev-list", "--no-merges", f"{base}..HEAD"], root)
    if r is None or r.returncode != 0:
        return False
    for sha in (r.stdout or "").splitlines():
        sha = sha.strip()
        if not sha:
            continue
        if _commit_is_meta_exempt(sha, root):
            continue
        files = commit_files(sha, root)
        if files is None:
            continue
        if files and _covered_by_scope(files, scope_paths):
            return True
    return False


def fix_cmd(root: Path, head: str = "HEAD") -> str:
    """判红补齐命令：ws_report --manual 生成 manual 收据并回填 commit_scope。

    区间 = 最近 promoted baseline source_commit..head；--result skip（本地
    直连开发通常为 harness/文档改动，无需上板）。具体 result/build/board/
    summary/selfcheck 由执行者按实际验证结果调整。
    """
    base = recent_promoted_baseline_commit(root) or "<baseline>"
    return (f"python3 harness/skills/workspace-verify/ws_report.py "
            f"--manual {base}..{head} --result skip --build skip --board skip "
            f"--summary '<本地直连开发摘要>' --selfcheck '<pytest 摘要与各 *_rc>' "
            f"--body <逐项自报文件>")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="自最近 promoted baseline 起非 meta 提交须被某份收据 "
                    "commit_scope 覆盖（manual 与 CDP 同等；判红打印补齐命令）")
    ap.add_argument("--repo", default=str(_ROOT), help="仓根（默认脚本相对推断）")
    args = ap.parse_args(argv)
    root = Path(args.repo)
    if not (root / ".git").exists():
        print("OK: 非 git 仓，跳过提交覆盖扫描")
        return 0
    uncovered = uncovered_commits(root)
    if uncovered:
        print("==== 自最近 promoted baseline 起存在未被收据 commit_scope 覆盖的"
              "非 meta 提交（本地直连开发须补 manual 收据）——须补齐后再提交 ====")
        for sha, subject in uncovered:
            print(f"  {sha} {subject}")
        print("补齐命令（ws_report --manual 生成 manual 收据并回填 commit_scope）：")
        print(f"  {fix_cmd(root)}")
        print(f"==== 共 {len(uncovered)} 处 ====")
        return 1
    print("OK: 自最近 promoted baseline 起非 meta 提交均被收据 commit_scope 覆盖")
    return 0


if __name__ == "__main__":
    sys.exit(main())
