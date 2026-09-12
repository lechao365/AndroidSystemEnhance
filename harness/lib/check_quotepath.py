#!/usr/bin/env python3
# ============================================================
# check_quotepath.py — 禁裸 git 路径输出命令未带 core.quotepath=false
# 背景（KI：2026-09-11 promote verify-tree 树等价误红回滚）：git 默认
#   core.quotepath=true 对非 ASCII 路径（如 data/known-issues/*.md 中文标题、
#   docs/01-打点增强/*.md）输出带引号 + 八进制转义行（"pa\303\202th" 形态），
#   调用方若对该输出做 == / startswith / 前缀匹配会因引号前缀匹配不上而
#   误判（本地 drvfs 常设全局 quotepath=false 掩盖，净克隆/CI 默认配置暴露）。
# 本检查器扫**生产代码**（harness/lib、harness/skills、code/ 的 .py/.sh，
#   排除 tests/ 与 __pycache__/）：调用 git diff / git ls-files / git status
#   （输出含路径的解析点）且**同一命令行未带 -c core.quotepath=false** 即
#   rc=1，由 selfcheck 以 quotepath_rc 透出、ws_report/CI 判红。
# 判定语义（只抓真实命令调用，注释/文档字符串/错误消息文本不算）：
#   - .py：列表字面 ["git", "diff|ls-files|status"（直接调用）或
#     ["git", *args]（通用封装，内部也须带 -c 才能覆盖全部调用点）；
#   - .sh：命令位置的 git（行首/$( /<(/管道/分号 后）紧跟 diff|ls-files|status，
#     引号内错误消息文本不抓（git 前是引号/变量名等非命令位）。
#   判定基于行级启发（跨行命令的 diff 子命令几乎都在首行）；docstring 行
#   以 tokenize 排除（多行 docstring 中间行会误报 git 词）。
# 用法：python3 harness/lib/check_quotepath.py [--repo <仓根>]
# 退出码：0 无违规 / 1 存在违规 / 2 参数错误
# ============================================================

import argparse
import io
import re
import subprocess
import sys
import tokenize
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

_CMDS = ("diff", "ls-files", "status")

# .py：直接调用 ["git", "diff|ls-files|status"（git 与子命令字面同列表，
# 单/双引号均支持）
_PY_DIRECT_RE = re.compile(r'\[["\']git["\']\s*,\s*["\'](?:diff|ls-files|status)["\']')
# .py：*args 展开包装器（["git", *args] 或 ["git", "-C", <root>, *args]）——
# 内部 subprocess 组装，须带 -c 覆盖全部调用点
_PY_STAR_ARGS_RE = re.compile(
    r'\[["\']git["\']\s*(?:,\s*["\']-C["\']\s*,\s*[^,\]]+\s*)?,\s*\*[a-z_]')
# .py：+ args 拼接包装器（["git"] + args）同族，须带 -c 覆盖全部调用点
_PY_CONCAT_RE = re.compile(r'\[["\']git["\']\]\s*\+\s*\w+')

# .sh 命令位判定正则：git 后可选 --no-pager / -x 单选项，再子命令
_SH_GIT_RE = re.compile(
    r"\bgit\s+(?:--no-pager\s+|-[a-z]\S*\s+)*(?:diff|ls-files|status)\b")


def _sh_command_hit(line: str) -> bool:
    """.sh 行内 git 是否出现在命令位置。

    判定：git 子命令匹配位置之前到行首的引号须闭合（偶数）——引号内
    （错误消息 echo "git status 失败" 等文本）不是命令位，引号数奇数即排除；
    引号闭合则 git 前可为行首/空格（if git、|| git）/$( /< ( 等命令位。
    """
    for m in _SH_GIT_RE.finditer(line):
        before = line[: m.start()]
        if before.count('"') % 2 == 0 and before.count("'") % 2 == 0:
            return True
    return False


def _is_scan_file(rel: str) -> bool:
    """扫描对象：生产 .py/.sh；排除测试与缓存目录。"""
    if not (rel.endswith(".py") or rel.endswith(".sh")):
        return False
    if rel.endswith((".pyc", ".pyo")):
        return False
    if "/tests/" in rel or "/__pycache__/" in rel:
        return False
    name = Path(rel).name
    if name.startswith("test_") or name.endswith("_test.py"):
        return False
    return True


def _docstring_lines(text: str) -> set[int]:
    """tokenize 识别 docstring 覆盖的行号集合（多行 docstring 中间行排除）。

    判定：字符串 token 出现在语句起始位（前一 token 为 NEWLINE/NL/INDENT/
    模块起始/':'）视为 docstring；赋值/参数/列表元素等普通字符串行保留扫描。
    """
    excluded: set[int] = set()
    try:
        prev_start = True  # 文件开头视为语句起始位
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.STRING and prev_start:
                excluded.update(range(tok.start[0], tok.end[0] + 1))
            prev_start = (tok.type in (tokenize.NEWLINE, tokenize.NL,
                                       tokenize.INDENT)
                          or tok.type == tokenize.OP and tok.string == ":"
                          or tok.type == tokenize.ENDMARKER)
    except Exception:
        pass
    return excluded


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


def scan(repo: Path) -> list[str]:
    """返回违规明细列表（空 = 无违规）。

    git ls-files 失败 fail-closed（lib-07 同源）：仓库异常按违规判红，
    不静默放行（否则仓库异常场景门禁假绿）。
    """
    files = _git_lines(["ls-files", "--cached", "--others",
                        "--exclude-standard"], repo)
    if files is None:
        print("error: git ls-files 失败（仓库异常/命令不可用），"
              "无法扫描 git 路径解析点，判红", file=sys.stderr)
        return [f"{repo}: git ls-files 失败，无法扫描（按违规判红）"]
    # 方向 3：.sh 可执行位守卫——以 100644 入库的 .sh（drvfs 本机全看着可
    # 执行，净克隆/CI 才暴露不可执行）判红；与 quotepath 同源"库态缺陷
    # 本地掩盖、净克隆暴露"，一次门禁覆盖
    sh_mode_map: dict[str, str] = {}
    mode_lines = _git_lines(["ls-files", "-s"], repo) or []
    for ln in mode_lines:
        parts = ln.split()
        if len(parts) >= 4:
            sh_mode_map[parts[3]] = parts[0]
    findings: list[str] = []
    for rel in files:
        if rel.endswith(".sh") and sh_mode_map.get(rel, "").startswith("100644"):
            findings.append(
                f"{rel}: .sh 以 100644 入库（应 100755 可执行——drvfs 本机"
                f"全看着可执行，净克隆/CI 才暴露）")
            continue
        if not _is_scan_file(rel):
            continue
        try:
            text = (repo / rel).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            print(f"error: 读取 {rel} 失败，无法扫描，判红", file=sys.stderr)
            findings.append(f"{rel}: 读取失败，无法扫描（按违规判红）")
            continue
        doc_lines = _docstring_lines(text)
        lines = text.splitlines()
        for i in range(1, len(lines) + 1):
            ln = lines[i - 1]
            s = ln.strip()
            if not s or s.startswith("#") or s.startswith("//"):
                continue
            if i in doc_lines:
                continue
            if "core.quotepath" in ln:
                continue
            if rel.endswith(".py"):
                hit = bool(_PY_DIRECT_RE.search(ln)
                           or _PY_STAR_ARGS_RE.search(ln)
                           or _PY_CONCAT_RE.search(ln))
            else:
                hit = _sh_command_hit(ln)
            if hit:
                findings.append(
                    f"{rel}:{i}: 裸 git diff/ls-files/status 未带"
                    f" -c core.quotepath=false: {s[:90]}")
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="禁裸 git diff/ls-files/status 未带 -c core.quotepath=false"
                    "（非 ASCII 路径输出被引号转义致前缀匹配失效）")
    ap.add_argument("--repo", default=str(_ROOT), help="仓根（默认脚本相对推断）")
    args = ap.parse_args(argv)
    repo = Path(args.repo)
    if not (repo / ".git").exists():
        print("OK: 非 git 仓，跳过 quotepath 扫描")
        return 0
    findings = scan(repo)
    if findings:
        print("==== 裸 git diff/ls-files/status 未带 -c core.quotepath=false"
              "（非 ASCII 路径输出转义致解析失效）或 .sh 以 100644 入库"
              "（净克隆/CI 才暴露不可执行）——须修后再提交 ====")
        for f in findings:
            print(f"  {f}")
        print(f"==== 共 {len(findings)} 处 ====")
        return 1
    print("OK: git 路径输出点均已带 -c core.quotepath=false，.sh 均以 100755 入库")
    return 0


if __name__ == "__main__":
    sys.exit(main())
