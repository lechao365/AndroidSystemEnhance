"""log_prune — harness/log 运行日志留存清理（批次五 D2）。

harness 各工作流脚本长期把运行产物写入 harness/log（git-works-push 日报、
promote 头快照、cross-device timings 等），无清理机制会无限膨胀并拖慢
扫描。本工具按 mtime 留存天数 + 目录内文件数上限双规则清理：

- 留存天数：mtime 早于 now-days 的文件删除（默认 30 天）
- 数量上限：单目录超 max_files 时从最旧开始删（默认 500）
- 安全默认 dry-run：只打印计划删除清单，--apply 才真删
- 只匹配目标 glob，不递归整仓，误删面受控

用法：
  python3 harness/lib/log_prune.py                 # dry-run 全默认目标
  python3 harness/lib/log_prune.py --apply         # 实际清理
  python3 harness/lib/log_prune.py --days 14 --max-files 100
  python3 harness/lib/log_prune.py --target 'harness/log/foo/*.log'

目标锚定仓库根（harness/lib 的上级），--target 为相对根的 glob。
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

_LIB_DIR = Path(__file__).resolve().parent
# 仓根锚定：CDP_PROJECT_ROOT 可覆盖（测试/异地隔离——git-works-push 在
# CDP_PROJECT_ROOT=临时仓的集成测试里触发 log_prune 时只清理临时仓，不污染
# 真实仓日志），无 env 时回退 harness/lib 的两级上级（harness/lib/../..）；
# 此前误取 parents[0] 得到 harness/，致 DEFAULT_TARGETS 以"harness/log/..."
# 相对根 glob 恒零命中仍返 0（scanned=0 静默假成功），修整后锚定真仓根
# （test_repo_root_is_repo_root 不 patch REPO_ROOT 自证，防回归）
REPO_ROOT = _LIB_DIR.parents[1]
if os.environ.get("CDP_PROJECT_ROOT", "").strip():
    REPO_ROOT = Path(os.environ["CDP_PROJECT_ROOT"].strip())

# 默认清理目标（相对仓库根的 glob）：各工作流运行产物
DEFAULT_TARGETS = [
    "harness/log/git-works-push/*.log",       # push 日报（日粒度追加）
    "harness/log/cross-device/promote-*.head",             # promote 头快照
    "harness/log/cross-device/timings-*.json",  # cdp_timing 打点归档
]


def _safe_mtime(p):
    """stat 带 TOCTOU 防护（lib-12）：glob 与 stat 之间文件被并发删除
    （其他清理进程/用户）时 FileNotFoundError 跳过，不崩。"""
    try:
        return p.stat().st_mtime
    except (FileNotFoundError, OSError):
        return None


def _prune_dir(pattern, cutoff, max_files, plan):
    """对单个 glob 计划清理并（apply 时）执行删除。

    规则优先级：先按 mtime < cutoff 删全部超龄；剩余文件数仍超
    max_files 时从最旧继续删。异常（权限等）记 reason 跳过该文件，
    不中断整体。mtime 一次采集复用（lib-12：多次裸 stat 有 TOCTOU 崩溃
    风险，消失文件按 None 跳过）。返回本 pattern 处理摘要 dict。
    """
    entries = [(p, _safe_mtime(p)) for p in REPO_ROOT.glob(pattern)]
    mtimes = {p: mt for p, mt in entries if mt is not None}
    files = sorted(mtimes, key=mtimes.get)
    stale = [f for f in files if mtimes[f] < cutoff]
    over = [] if len(files) <= max_files else files[:len(files) - max_files]
    victims = sorted(set(stale) | set(over), key=lambda p: mtimes[p])
    removed = []
    for f in victims:
        entry = {"file": str(f.relative_to(REPO_ROOT)),
                 "mtime": mtimes[f]}
        try:
            if plan["apply"]:
                f.unlink()
            removed.append(entry)
        except OSError as e:
            plan["errors"].append({"file": str(f), "reason": str(e)})
    plan["patterns"].append({
        "pattern": pattern,
        "scanned": len(files),
        "stale": len(stale),
        "over_limit": len(over),
        "removed": removed,
    })


def run(days=30, max_files=500, targets=None, apply=False):
    """执行清理，返回汇总 dict（machine-readable）。"""
    targets = list(targets) if targets is not None else list(DEFAULT_TARGETS)
    plan = {"days": days, "max_files": max_files, "apply": apply,
            "patterns": [], "errors": []}
    cutoff = time.time() - days * 86400
    for pattern in targets:
        _prune_dir(pattern, cutoff, max_files, plan)
    plan["total_removed"] = sum(len(p["removed"]) for p in plan["patterns"])
    return plan


def main(argv=None):
    ap = argparse.ArgumentParser(description="harness/log 留存清理（默认 dry-run）")
    ap.add_argument("--days", type=int, default=30,
                    help="留存天数（默认 30；0 = 全清）")
    ap.add_argument("--max-files", type=int, default=500,
                    help="单目录文件数上限，超限删最旧（默认 500）")
    ap.add_argument("--target", action="append", default=[],
                    help="追加相对仓库根的 glob（可多次）")
    ap.add_argument("--apply", action="store_true",
                    help="实际执行删除（缺省仅 dry-run 打印计划）")
    args = ap.parse_args(argv)
    # --target 越界拒绝（lib-12）：resolve 后必须落在仓根内，`../` 相对
    # 路径越出仓根即拒（docstring"误删面受控"约束，防误删仓外文件）
    root_resolved = REPO_ROOT.resolve()
    for pattern in args.target:
        probe = (REPO_ROOT / pattern).resolve()
        if probe != root_resolved and root_resolved not in probe.parents:
            print(f"error: --target 解析后越出仓根，拒绝: {pattern}",
                  file=sys.stderr)
            return 2
    plan = run(days=args.days, max_files=args.max_files,
               targets=args.target or None, apply=args.apply)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    return 0 if not plan["errors"] else 1


if __name__ == "__main__":
    sys.exit(main())
