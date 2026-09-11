#!/usr/bin/env python3
# ============================================================
# check_known_issues.py — 被引用 KI 编号须有对应记录文件（防悬空引用）
# 背景（CDP 2026-09-11 批次方向 2）：promote 基线晋升曾删除终态 KI 记录文件
#   （KIR-006 已废止"promote 删除"），历史悬空 7 条 KI 编号仍挂在
#   harness/config/baseline-status.yaml 的 known_issues_carried /
#   known_issues_closed 上——引用存在但记录文件缺失，缺陷登记链断裂、后续
#   无法追溯现场与修复提交。
# 本检查器扫描 baseline-status.yaml 全部基线（known_issues_carried 字符串
#   与 known_issues_closed 列表的 issue_id），对照 data/known-issues/ 下
#   记录文件（排除 index.md）的实际 issue_id 集合，被引用但无文件即判红，
#   由 selfcheck 以 known_issues_rc 透出、ws_report/CI 判红。
# 语义：
#   - known_issues_carried 可为空串 / 单个 id / 多个 id（逗号/空格/分号分隔）
#   - known_issues_closed 为列表，元素 dict 取 issue_id 键
#   - 记录文件存在性以文件头 "- issue_id:" 字段为准（与 cdp_issue 同源，
#     不依赖 index.md 内容）
# 用法：python3 harness/lib/check_known_issues.py [--repo <仓根>]
# 退出码：0 无悬空引用 / 1 存在悬空引用 / 2 参数错误
# ============================================================

import argparse
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


def ref_ids(root: Path):
    """baseline-status.yaml 全部已知问题引用 id 排序列表。

    yaml 缺失返回 None（调用方判红：依赖缺失无法扫描）；文件缺失/解析
    异常返回空列表（无引用可核对，不判红——历史/异常配置不误伤）。
    """
    if yaml is None:
        return None
    p = root / "harness" / "config" / "baseline-status.yaml"
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except OSError:
        return []
    if not isinstance(data, dict):
        return []
    bases = data.get("baselines")
    if isinstance(bases, list):
        base_iter = bases
    elif isinstance(bases, dict):
        base_iter = bases.values()
    else:
        base_iter = []  # 无 baselines 分组：无基线可核对
    ids: set[str] = set()
    for base in base_iter:
        if not isinstance(base, dict):
            continue
        # known_issues_carried/closed 位于 evidence 段（baseline 证据归属）
        ev = base.get("evidence")
        if not isinstance(ev, dict):
            ev = base  # 兼容旧结构平铺在基线元素
        carried = ev.get("known_issues_carried") or ""
        if isinstance(carried, str):
            for tok in re.split(r"[,;\s]+", carried):
                if tok:
                    ids.add(tok)
        closed = ev.get("known_issues_closed") or []
        if isinstance(closed, list):
            for item in closed:
                iid = (item or {}).get("issue_id")
                if isinstance(iid, str) and iid:
                    ids.add(iid)
    return sorted(ids)


def existing_ids(root: Path) -> set[str]:
    """data/known-issues/ 下记录文件（排除 index.md）的实际 issue_id 集合。"""
    d = root / "data" / "known-issues"
    ids: set[str] = set()
    if not d.is_dir():
        return ids
    for p in d.glob("*.md"):
        if p.name == "index.md":
            continue
        try:
            head = p.read_text(encoding="utf-8").partition("\n## body")[0]
        except OSError:
            continue
        m = re.search(r"^- issue_id:\s*(\S+)", head, re.M)
        if m:
            ids.add(m.group(1))
    return ids


def scan(root: Path) -> list[str]:
    """返回悬空引用违规明细（空 = 无违规）。

    yaml 依赖缺失 fail-closed（判红不静默放行）；记录目录不存在视为无
    文件（引用全部悬空判红）。
    """
    ids = ref_ids(root)
    if ids is None:
        print("error: PyYAML 缺失，无法解析 baseline-status.yaml，判红",
              file=sys.stderr)
        return [f"{root}: PyYAML 缺失，无法扫描（按违规判红）"]
    existing = existing_ids(root)
    return [f"{iid}: baseline-status.yaml 引用但 data/known-issues/ 无"
            f"对应记录文件（悬空引用，按 KIR-006 registry 不清零恢复）"
            for iid in ids if iid not in existing]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="被引用的 KI 编号须有对应记录文件"
                    "（baseline-status.yaml known_issues_carried/closed 对照"
                    " data/known-issues/ 文件集，悬空即判红）")
    ap.add_argument("--repo", default=str(_ROOT), help="仓根（默认脚本相对推断）")
    args = ap.parse_args(argv)
    findings = scan(Path(args.repo))
    if findings:
        print("==== baseline-status.yaml 引用的 KI 编号缺对应记录文件"
              "（悬空引用）——须补齐后再提交 ====")
        for f in findings:
            print(f"  {f}")
        print(f"==== 共 {len(findings)} 处 ====")
        return 1
    print("OK: baseline-status.yaml 引用的 KI 编号均有对应记录文件")
    return 0


if __name__ == "__main__":
    sys.exit(main())
