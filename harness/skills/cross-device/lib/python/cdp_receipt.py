"""data/verify-results 收据模块：写详情、读详情、趋势行、老化保留。

收据文件: data/verify-results/<YYYYMMDD-HHMMSS>-<batch_id>.md（markdown key-value 头 + 正文）
趋势文件: data/verify-results/trend.md（每批一行，保留 _TREND_KEEP 行）
注意: trend.md 不属于详情（文件名排序恒在最后，读取/老化必须显式排除）。
"""
import datetime
import re
import sys
from pathlib import Path

import yaml

from cdp_paths import data_verify_results_dir, project_root

_DETAIL_KEEP = 50
_TREND_KEEP = 200
# 多行模式：^$ 锚定每一行（缺 MULTILINE 会导致 from_text 全默认值）
# 值用 (.*) 允许空值（如 build/push_board 空值显式解析为空串，baseline_register
# 据此记 FAIL 不记 SKIP；与 cdp_issue._FIELD_RE 同款）
_FIELD_RE = re.compile(r"^- (\w+): (.*)$", re.MULTILINE)

_FIELDS = [
    "schema_version", "batch_id", "batch_base", "verified_commit",
    "verify_mode", "result", "build", "push_board", "acceptance",
    "elapsed_s", "summary", "metrics", "timings", "cases", "selfcheck",
]


class Receipt:
    def __init__(self, schema_version=1, batch_id="", batch_base="",
                 verified_commit="", verify_mode="board", result="fail",
                 build="skip", push_board="skip", acceptance="", elapsed_s=0,
                 summary="", metrics="", timings="", cases="", selfcheck=""):
        self.schema_version = schema_version
        self.batch_id = batch_id
        self.batch_base = batch_base
        self.verified_commit = verified_commit
        self.verify_mode = verify_mode
        self.result = result
        self.build = build
        self.push_board = push_board
        self.acceptance = acceptance
        self.elapsed_s = elapsed_s
        self.summary = summary
        self.metrics = metrics
        self.timings = timings
        self.cases = cases
        self.selfcheck = selfcheck

    @classmethod
    def from_text(cls, text):
        # 只解析 "## body" 之前的头部，防止正文中的 "- key: value" 行污染字段
        header = text.split("\n## body", 1)[0]
        r = cls()
        for m in _FIELD_RE.finditer(header):
            key, val = m.group(1), m.group(2)
            if hasattr(r, key):
                if key in ("schema_version", "elapsed_s"):
                    try:
                        setattr(r, key, int(val))
                    except ValueError:
                        pass  # 非法数值回落默认值，不崩
                else:
                    setattr(r, key, val)
        return r

    def header_lines(self):
        return "\n".join(f"- {f}: {getattr(self, f)}" for f in _FIELDS)


def _detail_files(verify_dir: Path):
    """详情文件列表（排除 trend.md），按文件名升序。"""
    return sorted(f for f in verify_dir.glob("*.md") if f.name != "trend.md")


def write_receipt(receipt, body_text):
    """写详情文件并老化，返回路径。

    防覆盖：同秒同 batch_id 冲突时时间戳顺延 1 秒（保持 <YYYYMMDD-HHMMSS>-<batch_id>.md
    命名格式），保证文件名唯一且按写入顺序排序——latest 恒取最新写入，失败重跑不丢
    上一份现场（对照 cdp_issue.write_issue 的 -n 防冲突）。
    """
    d = data_verify_results_dir()
    base = datetime.datetime.now()
    ts = base.strftime("%Y%m%d-%H%M%S")
    path = d / f"{ts}-{receipt.batch_id}.md"
    n = 0
    while path.exists():
        n += 1
        ts = (base + datetime.timedelta(seconds=n)).strftime("%Y%m%d-%H%M%S")
        path = d / f"{ts}-{receipt.batch_id}.md"
    content = receipt.header_lines() + "\n\n## body\n\n" + body_text.strip() + "\n"
    path.write_text(content, encoding="utf-8")
    prune_details(d)
    return path


def read_receipt(path):
    return Receipt.from_text(Path(path).read_text(encoding="utf-8"))


def read_latest_receipt(verify_dir=None):
    """读最新详情（排除 trend.md）。无收据返回 None。"""
    _, r = latest_receipt_with_path(verify_dir)
    return r


def latest_receipt_with_path(verify_dir=None):
    """读最新详情（排除 trend.md），返回 (路径, Receipt)；无收据返回 (None, None)。"""
    d = verify_dir or data_verify_results_dir()
    files = _detail_files(d)
    if not files:
        return (None, None)
    return (files[-1], read_receipt(files[-1]))


def latest_board_receipt(verify_dir=None):
    """取最新 verify_mode=board 的收据（从最新往旧扫，跳过 skip/非 board）。

    evidence-scope 推导锚点：登记时须以上板验证收据为准——最新收据可能
    是 -s skip 或非 board 的文档批，其 cases 不代表真实上板证据范围。
    返回 (路径, Receipt)；无 board 收据返回 (None, None)。
    """
    d = verify_dir or data_verify_results_dir()
    for f in reversed(_detail_files(d)):
        r = read_receipt(f)
        if r.verify_mode == "board":
            return (f, r)
    return (None, None)


def append_trend(timestamp, batch_id, result, stage, summary, metrics="",
                 timing=None):
    """趋势行追加：行尾可带 metrics 与 timing 两段 JSON（竖线分隔，跨批可 diff）。

    timing 为链路耗时摘要 JSON（如 {"elapsed_s": 27, "segs": {...}}，由
    ws_report 传参），非空时在 metrics 之后再追加一段，供 emit 从 trend
    快速查看各批耗时，无需回读收据 timings。
    """
    d = data_verify_results_dir()
    trend = d / "trend.md"
    line = f"{timestamp} {batch_id} {result} {stage} {summary}"
    if metrics:
        # 结构化指标以 JSON 追加行尾（跨批可 diff；emit 消费只读行尾提示不受影响）
        line += f" | {metrics}"
    if timing:
        line += f" | {timing}"
    # 原子写：读全量 → 追加新行 → 截断保留 _TREND_KEEP 行 → replace（避免先 append
    # 再整体重写的非原子读-写，中断会留下半写/丢行态）
    lines = trend.read_text(encoding="utf-8").splitlines() if trend.exists() else []
    lines.append(line)
    tmp = trend.with_suffix(".md.tmp")
    tmp.write_text("\n".join(lines[-_TREND_KEEP:]) + "\n", encoding="utf-8")
    tmp.replace(trend)


def read_trend_last(verify_dir=None):
    d = verify_dir or data_verify_results_dir()
    trend = d / "trend.md"
    if not trend.exists():
        return ""
    lines = trend.read_text(encoding="utf-8").splitlines()
    return lines[-1] if lines else ""


def _referred_receipt_names(verify_dir: Path):
    """baseline-status.yaml 引用的收据文件名集合（sync_manifest 指向
    data/verify-results/ 的文件名），老化删除时受保护。

    防断链：promote 门禁依赖 sync_manifest 指向的收据做证据链比对，
    被引用文件一旦被 prune 删除即断链（BL-20260828-01 已实际丢失）。
    yaml 缺失/非法时 warn 并返回 None（调用方保守不删任何文件——保护优先）。
    """
    cfg = project_root() / "harness" / "config" / "baseline-status.yaml"
    if not cfg.is_file():
        return set()
    try:
        data = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        print(f"WARN: baseline-status.yaml 读取失败，跳过老化保护判定: {e}",
              file=sys.stderr)
        return None
    names = set()
    for b in data.get("baselines") or []:
        if not isinstance(b, dict):
            continue
        ref = (b.get("sync_manifest") or "").strip()
        if ref:
            names.add(Path(ref).name)
    return names


def prune_details(verify_dir=None):
    """详情老化保留 _DETAIL_KEEP 份（trend.md 不计入配额）。

    被 baseline-status.yaml 引用的收据跳过删除（证据链保护）：已被 promote
    引用或即将引用的收据是基线证据，不得因配额被老化删除。
    """
    d = verify_dir or data_verify_results_dir()
    files = _detail_files(d)
    referred = _referred_receipt_names(d)
    if referred is None:
        return  # 引用解析失败（yaml 不可读）：保守不删任何文件
    keep = 0
    for old in files[: max(0, len(files) - _DETAIL_KEEP)]:
        if old.name in referred:
            keep += 1
            continue
        old.unlink()
    if keep:
        print(f"info: {keep} 份被 baseline-status.yaml 引用的收据跳过老化（证据链保护）",
              file=sys.stderr)
