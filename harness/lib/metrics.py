#!/usr/bin/env python3
# ============================================================
# metrics.py — harness 自度量统计工具（P0-C）
# 设计目的：AI 一条命令自查项目健康度——聚合 verify 收据 / 趋势行 /
#   known-issues，输出 pass 率、flake 率、验证时长分布、KI 状态板。
#   只读聚合（不写仓内文件）；对空目录/坏数据容错（缺字段不崩）。
# 接入：selfcheck 以 metrics_rc 透出（跑通即 0，聚合异常判红）。
# 用法：python3 harness/lib/metrics.py --report [--json]
#   [--verify-dir <dir>] [--issues-dir <dir>]
# 退出码：0 聚合成功（无论数据多少）/ 1 聚合异常 / 2 参数错误
# ============================================================

import argparse
import json
import math
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_VERIFY_DIR = _ROOT / "data" / "verify-results"
_ISSUES_DIR = _ROOT / "data" / "known-issues"

# 趋势行 result 列（第 3 字段）
_TREND_RESULT_RE = re.compile(r"^\S+\s+\S+\s+(\S+)\s+(\S+)\s+(.*)$")
# known-issues 头字段
_KI_FIELD_RE = re.compile(r"^- (\w+): (.*)$", re.MULTILINE)


def load_receipts(verify_dir: Path) -> list[dict]:
    """读 verify-results/*.md，返回字段 dict 列表（排除 trend.md；解析容错）。"""
    out = []
    if not verify_dir.is_dir():
        return out
    for f in sorted(verify_dir.glob("*.md")):
        if f.name == "trend.md":
            continue
        try:
            txt = f.read_text(encoding="utf-8")
            header = txt.split("\n## body", 1)[0]
            r = {"_file": f.name}
            for m in _KI_FIELD_RE.finditer(header):
                r[m.group(1)] = m.group(2)
            out.append(r)
        except (OSError, UnicodeDecodeError):
            continue
    return out


def load_trend(verify_dir: Path) -> list[dict]:
    """解析 trend.md 每行 {result, stage, summary}；行格式非法跳过。"""
    out = []
    trend = verify_dir / "trend.md"
    if not trend.is_file():
        return out
    for ln in trend.read_text(encoding="utf-8").splitlines():
        m = _TREND_RESULT_RE.match(ln)
        if m:
            out.append({"result": m.group(1), "stage": m.group(2),
                        "summary": m.group(3)})
    return out


def load_known_issues(issues_dir: Path) -> list[dict]:
    """读 known-issues/*.md 头字段（排除 index.md）；解析容错。"""
    out = []
    if not issues_dir.is_dir():
        return out
    for f in sorted(issues_dir.glob("*.md")):
        if f.name == "index.md":
            continue
        try:
            txt = f.read_text(encoding="utf-8")
            r = {"_file": f.name}
            for m in _KI_FIELD_RE.finditer(txt):
                r[m.group(1)] = m.group(2)
            out.append(r)
        except (OSError, UnicodeDecodeError):
            continue
    return out


def _num(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return float(v)
        except (TypeError, ValueError):
            return default


def compute(receipts, trend_lines, issues) -> dict:
    """聚合统计（缺数据容错，任何输入长度均可）。"""
    total = len(receipts)
    counts = {"pass": 0, "fail": 0, "skip": 0, "revert": 0, "other": 0}
    elapsed = []
    for r in receipts:
        res = (r.get("result") or "other").lower()
        counts[res if res in counts else "other"] += 1
        if r.get("elapsed_s") not in ("", None):
            elapsed.append(_num(r.get("elapsed_s")))
    total_pass = counts["pass"]
    pass_rate = total_pass / total if total else 0.0
    flake_count = sum(1 for i in issues if i.get("kind") == "flake")
    return {
        "total": total,
        "pass": counts["pass"], "fail": counts["fail"],
        "skip": counts["skip"], "revert": counts["revert"],
        "pass_rate": round(pass_rate, 3),
        "avg_elapsed_s": round(sum(elapsed) / len(elapsed), 1)
        if elapsed else 0,
        "p90_elapsed_s": sorted(elapsed)[int(math.ceil(
            len(elapsed) * 0.9)) - 1]
        if elapsed else 0,
        "trend_total": len(trend_lines),
        "flake_count": flake_count,
        "known_issues_total": len(issues),
    }


def ki_board(issues_dir: Path) -> dict:
    """known-issues 状态板：{kind: {status: count}}。"""
    board: dict[str, dict[str, int]] = {}
    for i in load_known_issues(issues_dir):
        kind = i.get("kind") or "unknown"
        status = i.get("status") or "unknown"
        board.setdefault(kind, {})
        board[kind][status] = board[kind].get(status, 0) + 1
    return board


def render_stats(stats: dict, as_json: bool = False) -> str:
    """渲染统计（文本或 JSON）。"""
    if as_json:
        return json.dumps(stats, ensure_ascii=False, sort_keys=True,
                          indent=2)
    return "\n".join([
        f"verify 收据总数: {stats['total']}",
        f"  pass={stats['pass']} fail={stats['fail']} "
        f"skip={stats['skip']} revert={stats['revert']}",
        f"pass 率: {stats['pass_rate']:.1%}",
        f"平均验证时长: {stats['avg_elapsed_s']}s  "
        f"P90: {stats['p90_elapsed_s']}s",
        f"trend 行数: {stats['trend_total']}",
        f"flake known-issues: {stats['flake_count']}  "
        f"known-issues 总数: {stats['known_issues_total']}",
    ])


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="harness 自度量统计")
    ap.add_argument("--report", action="store_true",
                    help="输出统计报表（缺省模式）")
    ap.add_argument("--json", action="store_true",
                    help="以 JSON 输出")
    ap.add_argument("--verify-dir", default=str(_VERIFY_DIR))
    ap.add_argument("--issues-dir", default=str(_ISSUES_DIR))
    args = ap.parse_args(argv)
    try:
        receipts = load_receipts(Path(args.verify_dir))
        trend = load_trend(Path(args.verify_dir))
        issues = load_known_issues(Path(args.issues_dir))
        stats = compute(receipts, trend, issues)
        if args.json:
            out = render_stats(stats, as_json=True)
        else:
            board = ki_board(Path(args.issues_dir))
            header = "metrics_rc=0"
            body = render_stats(stats)
            board_lines = " | ".join(
                f"{k}:{v}" for k, v in
                ({"open": board.get("flake", {}).get("open", 0),
                  "fixed": board.get("flake", {}).get("fixed", 0)}).items())
            out = f"{header} | flake: {board_lines}\n{body}"
        print(out)
        return 0
    except Exception as e:  # 聚合异常判红（自检 metrics_rc 依赖）
        print(f"metrics_rc=1 | error: 聚合异常: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
