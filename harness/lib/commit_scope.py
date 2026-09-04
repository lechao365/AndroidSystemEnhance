"""提交面清单与比对（批次 261f10265269 方向 1/2）。

commit_scope = 收据落盘时刻 git status --porcelain 的文件清单加摘要，
排除 data/verify-results/（本批收据与 trend.md 自引用豁免）；
git_works_push 提交前把实际提交面（staged 或待推提交）与最新收据
commit_scope 比对，不一致即拒——发布内容与验证内容绑定。

格式（单行，随收据 header 落盘）：
    add=2 mod=1 del=0 | path1, path2, path3
"""

import re
import sys
from pathlib import Path

# 自引用豁免前缀：收据目录（本批收据 + trend.md）在 scope 生成与比对两侧同排除
EXCLUDE_PREFIX = ("data/verify-results",)

_SCOPE_RE = re.compile(
    r"add=(\d+) mod=(\d+) del=(\d+) \| (.*)")


def porcelain_to_name_status(line):
    """git status --porcelain 单行 → name-status 风格行（"X\\tpath"）。

    XY 首字符为状态；??（未跟踪）→ A 即新增；重命名 old -> new 只取
    终态路径归 mod（比对以工作树终态为准）。
    """
    if len(line) < 4:
        return line
    xy, path = line[:2], line[3:]
    if " -> " in path:
        path = path.split(" -> ", 1)[1]
    stripped = xy.strip()
    # ?? → A（未跟踪即新增）；A/D 保留；其余（M/R/C/T…）统一映射 mod，
    # 与 scope 三态计数（add/mod/del）对齐
    if stripped == "??":
        st = "A"
    elif stripped in ("A", "D"):
        st = stripped
    else:
        st = "M"
    return f"{st}\t{path}"


def classify_status(line, exclude=EXCLUDE_PREFIX):
    """name-status --no-renames 单行 → (类别, 路径)；类别 ∈ add/mod/del。

    A→add，D→del，其余（M/T 及未知状态）归 mod（保守并入提交面比对）；
    命中 exclude 前缀的路径返 None（自引用豁免）。
    """
    parts = line.split("\t", 1)
    if len(parts) != 2 or not parts[1]:
        return None
    status, path = parts[0].strip(), parts[1].strip()
    if not path or any(path == p or path.startswith(p) for p in exclude):
        return None
    if status.startswith("A"):
        return ("add", path)
    if status.startswith("D"):
        return ("del", path)
    return ("mod", path)


def format_scope(status_lines, exclude=EXCLUDE_PREFIX):
    """name-status 行列表 → scope 单行（排除 exclude 前缀项）。"""
    buckets = {"add": [], "mod": [], "del": []}
    for ln in status_lines:
        c = classify_status(ln, exclude)
        if c:
            buckets[c[0]].append(c[1])
    paths = buckets["add"] + buckets["mod"] + buckets["del"]
    return (f"add={len(buckets['add'])} mod={len(buckets['mod'])} "
            f"del={len(buckets['del'])} | " + ", ".join(paths))


def parse_scope(scope_str):
    """scope 单行 → (计数 dict, 路径 set)；格式非法返 (None, None)。"""
    m = _SCOPE_RE.fullmatch(scope_str.strip())
    if not m:
        return None, None
    counts = {"add": int(m.group(1)), "mod": int(m.group(2)),
              "del": int(m.group(3))}
    paths = {p.strip() for p in m.group(4).split(",") if p.strip()}
    return counts, paths


def compare(scope_str, status_lines, exclude=EXCLUDE_PREFIX):
    """实际提交面 vs 收据 scope；一致返 []，否则差异行列表。

    以路径集为主判据（计数由路径派生，仅供展示）；收据 scope 格式
    非法按不一致处理（无法证明绑定即拒）。
    """
    counts, receipt_paths = parse_scope(scope_str)
    if counts is None:
        return [f"收据 commit_scope 格式非法: {scope_str!r}"]
    actual_paths = set()
    for ln in status_lines:
        c = classify_status(ln, exclude)
        if c:
            actual_paths.add(c[1])
    diffs = []
    for p in sorted(receipt_paths - actual_paths):
        diffs.append(f"收据声明但不在提交面: {p}")
    for p in sorted(actual_paths - receipt_paths):
        diffs.append(f"提交面存在但收据未声明: {p}")
    return diffs


def latest_scope(verify_dir=None):
    """最新收据的 commit_scope 字段；无收据/字段缺失返 ""。

    最新 = 文件名升序最后一（文件名含时间戳）；trend.md 不参与。
    """
    # cdp_receipt 位于 cross-device lib，脚本环境（git_works_push 相对路径
    # 调用）不在 sys.path——按 commit_scope.py 自身位置动态定位注入
    here = Path(__file__).resolve().parent
    cdp_lib = here.parent / "skills" / "cross-device" / "lib" / "python"
    if str(cdp_lib) not in sys.path:
        sys.path.insert(0, str(cdp_lib))
    from cdp_receipt import Receipt, data_verify_results_dir
    d = verify_dir or data_verify_results_dir()
    files = sorted(f for f in d.glob("*.md") if f.name != "trend.md")
    if not files:
        return ""
    r, _errs = Receipt.from_text(files[-1].read_text(encoding="utf-8"))
    return r.commit_scope or ""


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("usage: commit_scope.py --latest-scope | "
              "--check <scope>（stdin: name-status 行）", file=sys.stderr)
        return 3
    if args[0] == "--latest-scope":
        try:
            scope = latest_scope()
        except Exception as e:  # 收据目录/解析异常 → 无 scope，调用方降级跳过
            print(f"warn: 读取最新收据 commit_scope 失败: {e}", file=sys.stderr)
            return 1
        if not scope:
            print("warn: 最新收据无 commit_scope 字段（旧收据或无收据）",
                  file=sys.stderr)
            return 1
        print(scope)
        return 0
    if args[0] == "--check":
        if len(args) < 2:
            print("error: --check 需 scope 参数", file=sys.stderr)
            return 3
        diffs = compare(args[1], [ln for ln in sys.stdin.read().splitlines()
                                  if ln.strip()])
        if diffs:
            print("\n".join(diffs))
            return 1
        return 0
    print(f"error: 未知参数 {args[0]}", file=sys.stderr)
    return 3


if __name__ == "__main__":
    sys.exit(main())
