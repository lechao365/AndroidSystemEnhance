"""code/rpi5 modified/*.diff 格式校验器。

apply 编辑 .diff 后必须通过本校验：diff --git 头 / 元信息行 / hunk @@ /
行前缀(空格、+、-、反斜杠标记)合法性。防止 AI 编辑引入 diff 外新 context
导致 git apply 失配。

可选 --against <仓库根>：对每个 diff 额外执行 `git -C <根> apply --check`，
非零（上下文不匹配等语义失配）时把 stderr 首行计入错误；未传则仅做格式校验，
行为不变。

注意：--against 的 <仓库根> 必须是 base 状态树（干净未打补丁的 upstream 工作树），
不能传已打过旧补丁的工作树——diff 在已应用的工作树上 apply --check 必假失败
（同文件二次同步场景），语义校验的正确承担方是 sync_code_to_workspace.py
（其 checkout base 后才 apply --check）。
"""
import argparse
import re
import subprocess
import sys
from pathlib import Path

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")
# 显式枚举 git diff 全部元信息行形态（含 new file mode / rename / mode 变更等）；
# @@ 不在此列——hunk 头只认 _HUNK_RE，@@ 开头但不匹配者按非法行报错
_META_RE = re.compile(
    r"^(diff --git |index |--- |\+\+\+ "
    r"|new file mode |deleted file mode "
    r"|old mode |new mode "
    r"|rename from |rename to "
    r"|similarity index |dissimilarity index "
    r"|copy from |copy to "
    r"|\\ No newline)"
)


def _check_hunk_counts(hunk, errs):
    """hunk 收尾对账：声明 old/new 行数与体内容量不符即报错。"""
    if hunk["old_act"] != hunk["old_decl"] or hunk["new_act"] != hunk["new_decl"]:
        errs.append(
            f"L{hunk['start']} hunk 行数不符：声明 old={hunk['old_decl']}/"
            f"new={hunk['new_decl']}，实际 old={hunk['old_act']}/new={hunk['new_act']}")


def validate_diff(path):
    """返回 (ok, errors)。合法结构 + hunk 行数对账（声明 vs 实体）。

    每个 hunk 收尾（遇新 hunk / 元信息行 / 文件结束）时，将体内容量
    （空格/- 计入 old，空格/+ 计入 new，反斜杠行不计）与 @@ 声明值比对。
    """
    errs = []
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        return False, [f"文件不可读: {e}"]
    if b"\r\n" in raw:
        return False, ["文件含 CRLF 行尾（须为 LF）"]
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        return False, [f"非 UTF-8 编码: {e}"]
    lines = text.splitlines()
    if not lines:
        return False, ["空文件"]
    if not lines[0].startswith("diff --git "):
        errs.append(f"首行必须为 diff --git 头: {lines[0]!r}")

    seen_hunk = False
    hunk = None
    in_hunk = False
    for i, ln in enumerate(lines[1:], start=2):
        if ln.startswith("Binary files ") or ln.startswith("GIT binary patch"):
            # 二进制 diff 不支持（code/ 仅收文本 diff）：检出即立即拒绝，
            # 只返单条专用错误，不再继续扫描（避免无 hunk 头/非法行等误导错误）
            return False, [f"L{i} 不支持二进制 diff: {ln[:40]!r}"]
        m = _HUNK_RE.match(ln)
        if m:
            if hunk is not None:
                _check_hunk_counts(hunk, errs)
            seen_hunk = True
            hunk = {
                "old_decl": int(m.group(2)) if m.group(2) else 1,
                "new_decl": int(m.group(4)) if m.group(4) else 1,
                "old_act": 0, "new_act": 0, "start": i,
            }
            in_hunk = True
            continue
        if ln.startswith("@@") and not m:
            # @@ 开头但非合法 hunk 头（_META_RE 已不含 @@）：显式报错而非静默归类
            if hunk is not None:
                _check_hunk_counts(hunk, errs)
            errs.append(f"L{i} @@ 开头但非合法 hunk 头: {ln[:60]!r}")
            hunk = None
            in_hunk = False
            continue
        if in_hunk:
            if ln.startswith(" "):
                hunk["old_act"] += 1
                hunk["new_act"] += 1
                continue
            if ln.startswith("-"):
                hunk["old_act"] += 1
                continue
            if ln.startswith("+"):
                hunk["new_act"] += 1
                continue
            if ln.startswith("\\"):
                continue  # 反斜杠标记行（\ No newline）不计入行数
            if _META_RE.match(ln):
                _check_hunk_counts(hunk, errs)
                hunk = None
                in_hunk = False
                continue
            errs.append(f"L{i} hunk 体内行前缀非法（须 空格/+/-/\\）: {ln[:60]!r}")
            hunk = None
            in_hunk = False
        else:
            if not _META_RE.match(ln):
                errs.append(f"L{i} 非法行（应为元信息行）: {ln[:60]!r}")
    if hunk is not None:
        _check_hunk_counts(hunk, errs)
    if not seen_hunk:
        errs.append("无任何 @@ hunk 头（header-only diff 拒绝）")
    return (not errs), errs


def main(argv=None):
    ap = argparse.ArgumentParser(description=".diff 格式校验")
    ap.add_argument("files", nargs="+", help="diff 文件路径")
    ap.add_argument("--against", metavar="ROOT",
                    help="仓库根：对每个 diff 额外执行 git apply --check 语义校验")
    args = ap.parse_args(argv)
    bad = 0
    for f in args.files:
        ok, errs = validate_diff(f)
        if args.against:
            r = subprocess.run(["git", "-C", args.against, "apply", "--check", f],
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            if r.returncode != 0:
                stderr_lines = (r.stderr or "").strip().splitlines()
                detail = stderr_lines[0] if stderr_lines else f"exit={r.returncode}"
                errs.append(f"git apply --check 拒绝: {detail}")
                ok = False
        for e in errs:
            print(f"{f}: error: {e}")
        if not ok:
            bad += 1
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())