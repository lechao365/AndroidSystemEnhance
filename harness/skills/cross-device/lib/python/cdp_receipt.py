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

# 字段与默认值单一声明（方向 4：消除 _FIELDS 与 __init__ 双写漂移；
# __init__/header_lines/from_text 共用）
_FIELDS = [
    ("schema_version", 1),
    ("batch_id", ""),
    ("batch_base", ""),
    ("verified_commit", ""),
    ("verify_mode", "board"),
    ("result", "fail"),
    ("build", "skip"),
    ("push_board", "skip"),
    ("acceptance", ""),
    ("elapsed_s", 0),
    ("summary", ""),
    ("metrics", ""),
    ("timings", ""),
    ("cases", ""),
    ("selfcheck", ""),
    # 方向 3：teardown 失败（恢复不了本轮改变的状态）时 ws_report 标 "true"；
    # 空 = 未涉及或已恢复（旧收据无此行 → from_text 默认空，向后兼容）
    ("device_dirty", ""),
    # 方向 1（批次 261f10265269）：发布内容与验证内容绑定——
    # verified_tree：落盘时刻排除统一集合（content_tree.EXCLUDE_PATHS）后的
    # git 树对象 id（内容寻址可复算）；commit_scope：该时刻 porcelain 文件
    # 清单加摘要（排除收据目录）。旧收据无此两行 → from_text 默认空，兼容
    ("verified_tree", ""),
    ("commit_scope", ""),
    # package（ws_package 打包证据）：内嵌 ws_package 自描述证据 JSON 单行
    # 串（script_rc/镜像 sha256/字节/耗时等），随收据入库可追溯——此前证据
    # 落 harness/log 属 gitignore 域，基线记 package_result PASS 无可追溯
    # 凭据。旧收据无此行 → from_text 默认空，baseline_register 按兼容语义
    # （回退 --package-evidence/batch_id 探测）处理。
    ("package", ""),
]


class Receipt:
    def __init__(self, **kwargs):
        # 方向 4：字段与默认值单一声明（_FIELDS），构造按名取参
        for name, default in _FIELDS:
            setattr(self, name, kwargs.get(name, default))

    @classmethod
    def from_text(cls, text):
        """解析头部返回 (Receipt, parse_errors)。

        方向 1：非法整数不再静默回落默认值，记入 parse_errors；
        方向 2：同名重复字段记错（防后行覆盖前行造成假绿），保留首个值；
        方向 3：schema_version 非 1 记错，不按 1 解析（契约失效交调用方拒）。
        """
        # 只解析 "## body" 之前的头部，防止正文中的 "- key: value" 行污染字段
        header = text.split("\n## body", 1)[0]
        r = cls()
        errors: list[str] = []
        seen: set[str] = set()
        for m in _FIELD_RE.finditer(header):
            key, val = m.group(1), m.group(2)
            if not hasattr(r, key):
                continue
            if key in seen:
                errors.append(f"重复字段 {key}: {val!r}")
                continue
            seen.add(key)
            if key in ("schema_version", "elapsed_s"):
                try:
                    setattr(r, key, int(val))
                except ValueError:
                    errors.append(f"{key} 非法整数: {val!r}")
            else:
                setattr(r, key, val)
        if r.schema_version != 1:
            errors.append(f"schema_version 非 1（实际 {r.schema_version!r}），不按 1 解析")
        return r, errors

    def header_lines(self):
        return "\n".join(f"- {name}: {getattr(self, name)}" for name, _ in _FIELDS)


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
    """读收据返回 (Receipt, parse_errors)；errors 非空表示头部解析有错。"""
    return Receipt.from_text(Path(path).read_text(encoding="utf-8"))


def read_latest_receipt(verify_dir=None):
    """读最新详情（排除 trend.md），返回 (Receipt, parse_errors)；无收据返回
    (None, [])。"""
    _, r, errs = latest_receipt_with_path(verify_dir)
    return r, errs


def latest_receipt_with_path(verify_dir=None):
    """读最新详情（排除 trend.md），返回 (路径, Receipt, parse_errors)；
    无收据返回 (None, None, [])。"""
    d = verify_dir or data_verify_results_dir()
    files = _detail_files(d)
    if not files:
        return (None, None, [])
    path = files[-1]
    r, errs = read_receipt(path)
    return (path, r, errs)


def latest_board_receipt(verify_dir=None):
    """取最新 verify_mode=board 的收据（从最新往旧扫，跳过 skip/非 board）。

    evidence-scope 推导锚点：登记时须以上板验证收据为准——最新收据可能
    是 -s skip 或非 board 的文档批，其 cases 不代表真实上板证据范围。
    返回 (路径, Receipt, parse_errors)；无 board 收据返回 (None, None, [])。
    parse_errors 不再丢弃（损坏收据的 result/verify_mode/verified_tree 字段
    不可信，据其做覆盖判定与树绑定会掩盖证据断裂）——调用方（publish 侧）
    解析有错即拒，由本函数如实上抛。
    """
    d = verify_dir or data_verify_results_dir()
    for f in reversed(_detail_files(d)):
        r, rerrs = read_receipt(f)
        if r.verify_mode == "board":
            return (f, r, rerrs)
    return (None, None, [])


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


def _recent_nonpass_names(verify_dir: Path, keep: int = 20) -> set:
    """result 非 pass 的最近 keep 份收据文件名集合（方向 5）。

    fail/skip/revert 收据是失败归因与 -s 自检证据，不得因配额老化静默
    丢失；按文件名升序取末 keep 份中 result != "pass" 的。解析出错或读
    失败按非 pass 护（证据内容不明时保守不删）。
    """
    names = set()
    for f in _detail_files(verify_dir)[-keep:]:
        try:
            r, errs = Receipt.from_text(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            names.add(f.name)
            continue
        if errs or r.result != "pass":
            names.add(f.name)
    return names


def _receipt_batch_id(path):
    """从收据文件提取 batch_id；解析失败返 None（调用方保守跳过）。"""
    try:
        m = re.search(r"^-\s+batch_id:\s*(\S+)",
                      path.read_text(encoding="utf-8", errors="replace"),
                      re.MULTILINE)
        return m.group(1) if m else None
    except OSError:
        return None


def prune_details(verify_dir=None):
    """详情老化保留 _DETAIL_KEEP 份（trend.md 不计入配额）。

    同 batch_id 只留最新一份（方向 4）：重检重推的中间态收据先去重、
    不占配额（被 baseline-status.yaml 引用的文件仍按名保留）。
    两类证据链保护（跳过删除）：
    - 被 baseline-status.yaml 引用的收据：已被 promote 引用或即将引用的
      收据是基线证据；
    - result 非 pass 的最近 20 份收据（方向 5）：失败归因与 -s 自检证据。
    """
    d = verify_dir or data_verify_results_dir()
    files = _detail_files(d)
    referred = _referred_receipt_names(d)
    if referred is None:
        return  # 引用解析失败（yaml 不可读）：保守不删任何文件
    # 同 batch_id 去重（方向 4）：每组保文件名最新一份；解析失败/被引用
    # 的文件保守跳过。去重后重取文件列表再进配额老化。
    by_batch = {}
    for f in files:
        by_batch.setdefault(_receipt_batch_id(f), []).append(f)
    dedup_removed = 0
    for fs in by_batch.values():
        for old in fs[:-1]:
            if old.name in referred or _receipt_batch_id(old) is None:
                continue
            old.unlink()
            dedup_removed += 1
    if dedup_removed:
        print(f"info: 同 batch_id 中间态收据去重 {dedup_removed} 份（只留最新）",
              file=sys.stderr)
        files = _detail_files(d)
    guarded = _recent_nonpass_names(d)
    keep = 0
    referred_kept = 0
    nonpass_kept = 0
    for old in files[: max(0, len(files) - _DETAIL_KEEP)]:
        if old.name in referred or old.name in guarded:
            keep += 1
            if old.name in referred:
                referred_kept += 1
            if old.name in guarded and old.name not in referred:
                nonpass_kept += 1
            continue
        old.unlink()
    if referred_kept:
        print(f"info: {referred_kept} 份被 baseline-status.yaml 引用的收据跳过老化（证据链保护）",
              file=sys.stderr)
    if nonpass_kept:
        print(f"info: {nonpass_kept} 份 result 非 pass 的近期收据跳过老化（归因证据保护）",
              file=sys.stderr)
