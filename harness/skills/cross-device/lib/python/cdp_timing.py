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
环境变量 CDP_BATCH_ID > log 目录唯一 timings 文件；均不可得时静默跳过
（返回 0，失败不阻断口径）。
退出码: 0 正常 / 2 参数错误 / 3 未 start 即 mark/finish（显式来源时）
打点文件: <project_root>/harness/log/cross-device/timings-<batch_id>.json
（gitignore 工作态；ws_report --timings-file 读原始打点文件经 compute_segments
计算段耗时并入收据 timings 字段——finish 仅归档/人工查看，不依赖其先跑）
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from cdp_parse import batch_id_from_text
from cdp_paths import log_apply_dir


def _timing_path(batch_id: str) -> Path:
    """打点文件路径：timings-<batch_id>.json（工作态目录）。"""
    return log_apply_dir() / f"timings-{batch_id}.json"


def _resolve_timing_path(args) -> tuple[Path | None, bool]:
    """解析打点文件路径，返回 (path, silent)。

    优先级：显式 --batch/--file > 环境变量 CDP_BATCH_ID > log 目录唯一
    timings 文件；均不可得时返回 (None, True)——静默跳过（脚本自动 mark
    拿不到 batch 属正常降级，调用方返回 0 不阻断，失败不阻断口径不变）。
    多文件且无 CDP_BATCH_ID 时同样静默跳过（防误标其他批次的打点文件，
    不再按文件名倒序盲取最新）。
    """
    batch = getattr(args, "batch", None)
    if batch:
        return _timing_path(batch), False
    if getattr(args, "file", None):
        return Path(args.file), False
    env_id = os.environ.get("CDP_BATCH_ID", "").strip()
    if env_id:
        return _timing_path(env_id), False
    files = sorted(log_apply_dir().glob("timings-*.json"))
    if len(files) == 1:
        return files[0], False
    if not files:
        print("info: 无打点文件且无 CDP_BATCH_ID，静默跳过（未 start）",
              file=sys.stderr)
    else:
        print(f"info: log 目录 {len(files)} 个打点文件且无 CDP_BATCH_ID，"
              "静默跳过（仅唯一文件自动识别）", file=sys.stderr)
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
    """由 start_wall + marks + 当前时刻计算相邻段耗时（ws_report 落收据复用）。

    首段 = 首个 mark - start_wall；末段 = 当前时刻 - 末个 mark；
    无 mark 或 start_wall 缺失时返回空列表（不崩，调用方按缺打点处理）。
    """
    start = data.get("start_wall")
    marks = data.get("marks") or []
    if start is None or not marks:
        return []
    segs = []
    prev = start
    for m in marks:
        segs.append({"name": m["name"], "elapsed_s": round(m["wall"] - prev, 3)})
        prev = m["wall"]
    segs.append({"name": "finish", "elapsed_s": round(_wall() - prev, 3)})
    return segs


def _cmd_start(batch_id: str) -> int:
    """start：初始化打点文件（覆盖重建，AI 可重打点）。"""
    data = {"batch_id": batch_id, "start_wall": _wall(), "marks": []}
    _save(_timing_path(batch_id), data)
    print(f"timing started: {_timing_path(batch_id)}")
    return 0


def _cmd_mark(path: Path, name: str) -> int:
    """mark：追加一个时间戳；未 start 返 3（AI 漏 start 可发现）。"""
    data = _load(path)
    if data is None:
        print(f"error: 未 start（缺打点文件 {path}），先执行 cdp_timing.py start", file=sys.stderr)
        return 3
    data.setdefault("marks", []).append({"name": name, "wall": _wall()})
    _save(path, data)
    print(f"mark: {name} @ {_wall():.3f}")
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
        return _cmd_mark(path, args.name)
    if args.cmd == "finish":
        return _cmd_finish(path)
    return 2


if __name__ == "__main__":
    sys.exit(main())
