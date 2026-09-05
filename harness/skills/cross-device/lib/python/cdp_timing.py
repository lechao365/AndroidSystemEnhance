"""cross-device 链路耗时打点：apply/verify 共用，粗粒度起步、逐阶段 mark。

核心语义：把 apply 链路（precheck/编辑/verify/收据/push）与 verify 内部
（同步/编译/推送/单测/验收）的 wall-clock 耗时从 AI 会话上下文（易丢失、
不可度量）转移到打点文件持久化，最终随收据落盘 data/verify-results，
供 emit 侧读 timings 字段定位耗时瓶颈。

用法（AI 按阶段切换调用，阶段名自由）:
    cdp_timing.py start --batch <12hex> | --batch-file <cdp 批次文件>
    cdp_timing.py mark --name <阶段名>        # 记录一个时间戳
    cdp_timing.py finish [--file <path>]      # 计算相邻段耗时并落盘
mark/finish 的 batch 识别（脚本自动打点依赖）：显式 --batch/--file >
环境变量 CDP_BATCH_ID > current-batch.json（start 落盘记录当前批次指针，
多文件共存仍定位本批）；该级取不到时 stderr warn 后返 0（取消静默跳过，
失败不阻断口径）。
退出码: 0 正常 / 2 参数错误 / 3 未 start 即 mark/finish（显式来源时）
打点文件: <project_root>/harness/log/cross-device/timings-<batch_id>.json
（gitignore 工作态；ws_report --timings-file 读原始打点文件经 compute_segments
计算段耗时并入收据 timings 字段——finish 仅归档/人工查看，不依赖其先跑）

兜底段语义（重要，finish 两义）：compute_segments 的末段名固定为 "finish"
——它是"末个 mark 到算段时刻"的兜底段，与 finish **子命令**（归档命令）
同名不同义。该段耗时 = 末个 mark 之后的所有未打点活动（如 -s 批次的
selfcheck、编排空转、收据写盘），不细分无法归因。定位耗时需在阶段
边界自发 mark：selfcheck.py 跑完发 apply_selfcheck、ws_report 解析打点
前发 report，使兜底段收窄为纯写收据。
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

from cdp_parse import batch_id_from_text
from cdp_paths import log_apply_dir


# 链路阶段名常量表：apply/verify 已知链路段。mark 表外名仅 stderr warn
# 不阻断（仍记录），供 emit 侧定位耗时瓶颈时识别未知段（方向 5 固化）。
# edit_validate/gen_manifest：编辑阶段细分（diff 校验器/清单重生成各自
# 自发 mark），把 edit 段内的机械校验耗时单独归因（方向 2 增）。
# edit_plan/edit_retry：编辑打点约定（读完方向发 edit_plan、自愈重试前发
# edit_retry，方向 3 增）。
# edit_item：分方向编辑打点（B2），apply 每完成一个方向 mark 一次，
# 同名自动 #N 序号，单方向耗时在收据 segments 逐项可见。
# report_post：report mark 之后至收据落盘的尾部工作段（B5，ws_report 在
# content_tree/commit_scope 完成后自发直写）——覆盖门禁段之后的尾部开销，
# 此前该段工作不落任何段不可归因。
KNOWN_SEGMENTS = frozenset([
    "precheck", "edit",
    "edit_validate", "gen_manifest", "edit_plan", "edit_retry", "edit_item",
    "verify_sync", "verify_build", "verify_push",
    "verify_unit_test", "verify_acceptance", "apply_selfcheck", "report",
    "report_post",
])

# 条件段：仅在特定条件满足时打点（非每批必出）——edit_validate（跑过 diff
# 校验器）、gen_manifest（跑过清单重生成）、edit_plan（编辑规划）、edit_retry
# （自愈重试）、edit_item（分方向编辑，B2）。missing 判定（ws_report）应把
# 条件段排除在应有段集之外：未产出不判缺（方向 1 定）。
CONDITIONAL_SEGMENTS = frozenset([
    "edit_validate", "gen_manifest", "edit_plan", "edit_retry", "edit_item",
])

# gap_before_<name> 落段的余量阈值（秒）：mark 带 dur_s 时，相邻差额减去
# dur_s 后的未打点活动余量小于该值即不落段（避免计时精度/AI 空转噪声污染
# 归因，方向 2 定）。
GAP_THRESHOLD = 1.0

# 同名 mark 重复次数后缀（方向 4）：第 n 次同名段名为 name#n（首次无序号），
# 返工轮次可数。gap_before_* 为派生段，剥序号判定（missing/段名表校验）时
# 一律忽略。
_SUFFIX_RE = re.compile(r"#\d+$")


def _base_seg_name(name: str) -> str:
    """剥段名序号后缀（name#n → name）；gap_before_* 派生段返回空串（忽略）。

    供段名表校验（mark 是否已知）、ws_report missing 判定与重复次数汇总
    共用：返工轮次段（apply_selfcheck#2 等）剥序号后与常量表比对，gap 段
    不参与应有段判定。
    """
    if name.startswith("gap_before_"):
        return ""
    return _SUFFIX_RE.sub("", name)


def _timing_path(batch_id: str) -> Path:
    """打点文件路径：timings-<batch_id>.json（工作态目录）。"""
    return log_apply_dir() / f"timings-{batch_id}.json"


def _current_batch_path() -> Path:
    """current-batch.json：start 落盘记录当前批次指针（自动 mark/finish 定位）。"""
    return log_apply_dir() / "current-batch.json"


def _write_current_batch(batch_id: str) -> None:
    """start 落 current-batch.json 记 batch_id（原子写，中断不留半写态）。"""
    p = _current_batch_path()
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"batch_id": batch_id}, ensure_ascii=False) + "\n",
                   encoding="utf-8")
    tmp.replace(p)


def _read_current_batch() -> str | None:
    """读 current-batch.json 的 batch_id；缺失/损坏返回 None。"""
    try:
        data = json.loads(_current_batch_path().read_text(encoding="utf-8"))
        bid = (data or {}).get("batch_id", "").strip()
        return bid or None
    except (OSError, json.JSONDecodeError):
        return None


def _archive_previous_timings(current_batch_id: str) -> None:
    """start 归档：把工作态目录已有 timings（当前批次除外）移入 archive/ 子目录。

    保持工作态目录只留当前批打点文件 + current-batch.json（多批残留会让
    自动识别歧义）；archive/ 仅供人工/emit 查阅历史，不参与自动定位
    （glob 不递归）。当前批次文件保留在工作态顶层（start 覆盖重建）。
    """
    d = log_apply_dir()
    archive = d / "archive"
    moved = 0
    for f in sorted(d.glob("timings-*.json")):
        if f.name == f"timings-{current_batch_id}.json":
            continue
        archive.mkdir(parents=True, exist_ok=True)
        f.replace(archive / f.name)
        moved += 1
    if moved:
        print(f"info: {moved} 份历史打点文件归档到 {archive}", file=sys.stderr)


def _resolve_timing_path(args) -> tuple[Path | None, bool]:
    """解析打点文件路径，返回 (path, silent)。

    优先级：显式 --batch/--file > 环境变量 CDP_BATCH_ID > current-batch.json
    （start 落盘记录当前批次指针，多文件共存仍定位本批）。该级取不到时
    stderr warn 后返 0（取消静默跳过：缺打点不再无提示，调用方仍不阻断，
    失败不阻断口径不变）。
    """
    batch = getattr(args, "batch", None)
    if batch:
        return _timing_path(batch), False
    if getattr(args, "file", None):
        return Path(args.file), False
    env_id = os.environ.get("CDP_BATCH_ID", "").strip()
    if env_id:
        return _timing_path(env_id), False
    cur = _read_current_batch()
    if cur:
        return _timing_path(cur), False
    print("warn: 无显式 --batch/--file、无 CDP_BATCH_ID 且 current-batch.json "
          "缺失（未 start），自动 mark/finish 跳过", file=sys.stderr)
    return None, True


def _load(path: Path):
    """读打点文件；缺失/损坏返回 None（mark 未 start 时据此报 3）。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _save(path: Path, data) -> None:
    """原子写：临时文件 + replace（对齐 append_trend 惯例，中断不留半写态）。"""
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    tmp.replace(path)


def _wall() -> float:
    """epoch 秒小数（高精度 wall clock）。"""
    return time.time()


def compute_segments(data) -> list[dict]:
    """由 start_wall + marks + 当前时刻计算段耗时（ws_report 落收据复用）。

    首段 = 首个 mark - start_wall；末段 = 当前时刻 - 末个 mark；
    无 mark 或 start_wall 缺失时返回空列表（不崩，调用方按缺打点处理）。

    归因修正（方向 2）：mark 带 dur_s（自测真实耗时）时该段耗时取 dur_s，
    相邻差额（interval）减去 dur_s 后的余量是段前未被 mark 覆盖的未打点
    活动（如自检前的编排空转），另落 gap_before_<name> 段，余量小于
    GAP_THRESHOLD 不落段；无 dur_s 或 dur_s 非法（非数值/越界）时回退旧
    算法（整段差额归后一个 mark 名）。gap 段在 name 段之前（时间序）。

    同名段名（方向 4）：同一 mark 名第 n 次出现时段名为 name#n（首次不加
    序号），返工轮次在收据段表可见可数；mark 记录本身 name 不变。
    """
    start = data.get("start_wall")
    marks = data.get("marks") or []
    if start is None or not marks:
        return []
    segs = []
    prev = start
    seen: dict[str, int] = {}
    for m in marks:
        name = m["name"]
        seen[name] = seen.get(name, 0) + 1
        seg_name = name if seen[name] == 1 else f"{name}#{seen[name]}"
        interval = m["wall"] - prev
        dur = m.get("dur_s")
        if isinstance(dur, (int, float)) and 0 <= dur <= interval:
            gap = interval - dur
            if gap >= GAP_THRESHOLD:
                segs.append({"name": f"gap_before_{seg_name}",
                             "elapsed_s": round(gap, 3)})
            segs.append({"name": seg_name, "elapsed_s": round(dur, 3)})
        else:
            segs.append({"name": seg_name, "elapsed_s": round(interval, 3)})
        prev = m["wall"]
    segs.append({"name": "finish", "elapsed_s": round(_wall() - prev, 3)})
    return segs


def _cmd_start(batch_id: str) -> int:
    """start：初始化打点文件（覆盖重建，AI 可重打点）。

    落 current-batch.json 记 batch_id（自动 mark/finish 定位本批指针），
    并把工作态目录已有的历史 timings 移入 archive/ 子目录。
    """
    data = {"batch_id": batch_id, "start_wall": _wall(), "marks": []}
    _save(_timing_path(batch_id), data)
    _archive_previous_timings(batch_id)
    _write_current_batch(batch_id)
    print(f"timing started: {_timing_path(batch_id)}")
    return 0


def _cmd_mark(path: Path, name: str, zero: bool = False, dur_s=None) -> int:
    """mark：追加一个时间戳；未 start 返 3（AI 漏 start 可发现）。

    zero=True 记零 mark：wall 取最近 mark（无 mark 则 start_wall）同一时刻——
    跳过段（如无编译/无上板时的 sync/build/push/unit_test）以 0 耗时占位，
    收据 timings 段完整可归因（缺段 vs 0 耗时语义不同：缺段=去向不明）。

    dur_s（方向 1）：调用方自测该段的真实墙钟耗时，写入 mark 记录供
    compute_segments 归因（该段耗时取 dur_s，差额余量落 gap_before_<name>）。
    """
    data = _load(path)
    if data is None:
        print(f"error: 未 start（缺打点文件 {path}），先执行 cdp_timing.py start", file=sys.stderr)
        return 3
    if zero:
        marks = data.get("marks") or []
        wall = marks[-1]["wall"] if marks else data.get("start_wall")
        if wall is None:
            print("error: 无 start_wall 且无 marks，无法记零", file=sys.stderr)
            return 3
    else:
        wall = _wall()
    mark = {"name": name, "wall": wall}
    if dur_s is not None:
        mark["dur_s"] = round(float(dur_s), 3)
    data.setdefault("marks", []).append(mark)
    # 段名表校验剥序号（方向 4）：AI 显式传 name#n 时按基础名比对
    if _base_seg_name(name) not in KNOWN_SEGMENTS:
        print(f"warn: 段名 {name!r} 不在常量表 "
              f"（{', '.join(sorted(KNOWN_SEGMENTS))}），仅告警不阻断",
              file=sys.stderr)
    _save(path, data)
    print(f"mark: {name} @ {wall:.3f}" + ("（零耗时占位）" if zero else ""))
    return 0


def _cmd_finish(path: Path) -> int:
    """finish：计算相邻段耗时并落盘输出 JSON（缺段不崩，输出已有段）。

    保留 start_wall/marks 原始字段（ws_report --timings-file 两种结构皆可读，
    后续 mark 仍可追加）；仅新增 wall_end + segments。
    """
    data = _load(path)
    if data is None:
        print(f"error: 未 start（缺打点文件 {path}），先执行 cdp_timing.py start", file=sys.stderr)
        return 3
    out = {
        "batch_id": data.get("batch_id", ""),
        "start_wall": data.get("start_wall"),
        "wall_end": _wall(),
        "marks": data.get("marks", []),
        "segments": compute_segments(data),
    }
    _save(path, out)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    print(f"timing finished: {path}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="cross-device 链路耗时打点")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_start = sub.add_parser("start", help="初始化打点文件")
    p_start.add_argument("--batch", default=None, help="12 位 batch_id")
    p_start.add_argument("--batch-file", default=None,
                         help="CDP 批次文件（内部经 batch_id_from_text 求 batch_id）")

    p_mark = sub.add_parser("mark", help="追加一个时间戳")
    p_mark.add_argument("--name", required=True, help="阶段名（如 precheck/edit/verify_build）")
    p_mark.add_argument("--batch", default=None, help="12 位 batch_id（缺省从打点目录取最新）")
    p_mark.add_argument("--zero", action="store_true",
                        help="记零 mark：wall 取最近 mark 同刻（跳过段占位，段耗时 0）")
    p_mark.add_argument("--dur-s", type=float, default=None,
                        help="自测真实耗时秒数（写入 mark，compute_segments 归因："
                             "该段耗时取 dur_s，差额余量落 gap_before_<name>；与 --zero 互斥）")

    p_finish = sub.add_parser("finish", help="计算段耗时并落盘")
    p_finish.add_argument("--file", default=None, help="打点文件路径（缺省取最新）")
    p_finish.add_argument("--batch", default=None, help="12 位 batch_id（缺省取最新）")

    args = ap.parse_args(argv)

    if args.cmd == "start":
        if bool(args.batch) == bool(args.batch_file):
            print("error: start 须且仅须 --batch 与 --batch-file 之一", file=sys.stderr)
            return 2
        if args.batch_file:
            try:
                batch_id = batch_id_from_text(
                    Path(args.batch_file).read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError) as e:
                print(f"error: 批次文件不可读: {e}", file=sys.stderr)
                return 2
        else:
            batch_id = args.batch
        return _cmd_start(batch_id)

    path = None
    silent = False
    if args.cmd in ("mark", "finish"):
        path, silent = _resolve_timing_path(args)
        if silent:
            return 0
        if path is None:
            print("error: 找不到打点文件（未 start 或目录为空）", file=sys.stderr)
            return 3

    if args.cmd == "mark":
        if args.zero and args.dur_s is not None:
            print("error: --zero 与 --dur-s 互斥（零 mark 段耗时恒 0，"
                  "无自测耗时可报）", file=sys.stderr)
            return 2
        return _cmd_mark(path, args.name, zero=args.zero, dur_s=args.dur_s)
    if args.cmd == "finish":
        return _cmd_finish(path)
    return 2


if __name__ == "__main__":
    sys.exit(main())
