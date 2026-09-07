"""cross-device 链路耗时打点：apply/verify 共用，粗粒度起步、逐阶段 mark。

核心语义：把 apply 链路（precheck/编辑/verify/收据/push）与 verify 内部
（同步/编译/推送/单测/验收）的 wall-clock 耗时从 AI 会话上下文（易丢失、
不可度量）转移到打点文件持久化，最终随收据落盘 data/verify-results，
供 emit 侧读 timings 字段定位耗时瓶颈。

用法（AI 仅需 start / finish 两个入口，其余阶段由脚本自发 mark）:
    cdp_timing.py start --batch <12hex> | --batch-file <cdp 批次文件>
    cdp_timing.py mark --name <阶段名>        # 手动补打（一般无需）
    cdp_timing.py finish [--file <path>]      # 计算相邻段耗时并落盘
自发打点公共入口 emit_mark(name, ...)（进程内直调，供 selfcheck/ws_report/
ws_push/ws_upload_tests/cdp_parse/git-works-push 收敛打点胶水，B8/C-1）：
    resolve_batch_id()  # CDP_BATCH_ID env > current-batch.json
    read_marks(batch_id=None)
    emit_mark(name, dur_s=None, zero=False, batch_id=None, timings_file=None)
mark/finish 的 batch 识别（脚本自动打点依赖）：显式 --batch/--file >
环境变量 CDP_BATCH_ID > current-batch.json（start 落盘记录当前批次指针，
多文件共存仍定位本批）；该级取不到时 stderr warn 后返 0（取消静默跳过，
失败不阻断口径）。
退出码: 0 正常 / 2 参数错误 / 3 未 start 即 mark/finish（显式来源时）
打点文件: <project_root>/harness/log/cross-device/timings-<batch_id>.json
（gitignore 工作态；ws_report --timings-file 读原始打点文件经 compute_segments
计算段耗时并入收据 timings 字段——finish 仅归档/人工查看，不依赖其先跑）

时间轴（B4）：mark 同时记录 wall（epoch）与 mono（monotonic）双时间戳，
compute_segments 在 start 与全部 marks 均带 mono 时优先走单调轴（免 NTP
校时/时钟回拨扭曲段耗时），任一缺失回退 wall 轴（旧打点文件兼容）。

兜底段语义（重要，finish 两义）：compute_segments 的末段名固定为 "finish"
——它是"末个 mark 到算段时刻"的兜底段，与 finish **子命令**（归档命令）
同名不同义。该段耗时 = 末个 mark 之后的所有未打点活动（如 -s 批次的
selfcheck、编排空转、收据写盘），不细分无法归因。定位耗时需在阶段
边界自发 mark：selfcheck.py 跑完发 apply_selfcheck、ws_report 解析打点
前发 report，使兜底段收窄为纯写收据。
"""
import argparse
import contextlib
import json
import os
import re
import sys
import time
from pathlib import Path

from cdp_parse import batch_id_from_text
from cdp_paths import log_apply_dir


@contextlib.contextmanager
def _locked(path: Path):
    """timings 文件级跨进程互斥（方向 3：并发 mark 丢段）。

    编排器（ws_verify_chain）注入 CDP_BATCH_ID 后，各子脚本独立进程并发
    自发 mark 同一 timings-<batch_id>.json——read→改→写 无临界区时后写者
    覆盖先写者（last-writer-wins），段被吞。flock 包住整个区间防丢段；
    非阻塞重试至多 10s，拿不到锁降级不加锁直写（打点属诊断面，防 mark 链
    卡死）；无 fcntl 平台（非 POSIX）直接降级。
    """
    try:
        import fcntl
    except ImportError:
        yield
        return
    lock_path = Path(f"{path}.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a")
    try:
        deadline = time.monotonic() + 10.0
        while True:
            try:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except (BlockingIOError, OSError):
                if time.monotonic() >= deadline:
                    break  # 拿锁超时降级不加锁直写
                time.sleep(0.05)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except (OSError, ValueError):
            pass
        fh.close()


# 链路阶段名常量表：apply/verify 已知链路段。mark 表外名仅 stderr warn
# 不阻断（仍记录），供 emit 侧定位耗时瓶颈时识别未知段（方向 5 固化）。
# edit_validate/gen_manifest：编辑阶段细分（diff 校验器/清单重生成各自
# 自发 mark），把 edit 段内的机械校验耗时单独归因（方向 2 增）。
# edit_plan/edit_retry：编辑打点约定（读完方向发 edit_plan、自愈重试前发
# edit_retry，方向 3 增）。
# edit_item：分方向编辑打点（B2），apply 每完成一个方向 mark 一次，
# 同名自动 #N 序号，单方向耗时在收据 segments 逐项可见。
# verify_start：verify 链（ws_verify_chain）起跑自发 mark（收据落盘前发生，
# -sv 收据 timings 应含该段）。
# report_post：report mark 之后至收据落盘的尾部工作段（B5，ws_report 在
# content_tree/commit_scope 完成后自发直写）——覆盖门禁段之后的尾部开销，
# 此前该段工作不落任何段不可归因。
KNOWN_SEGMENTS = frozenset([
    "precheck", "edit",
    "edit_validate", "gen_manifest", "edit_plan", "edit_retry", "edit_item",
    "verify_sync", "verify_build", "verify_push",
    "verify_unit_test", "verify_acceptance", "apply_selfcheck", "report",
    "report_post", "verify_start",
    "push", "push_commit", "push_remote", "verify_end",
])

# 条件段：仅在特定条件满足时打点（非每批必出）——edit_validate（跑过 diff
# 校验器）、gen_manifest（跑过清单重生成）、edit_plan（编辑规划）、edit_retry
# （自愈重试）、edit_item（分方向编辑，B2）。missing 判定（ws_report）应把
# 条件段排除在应有段集之外：未产出不判缺（方向 1 定）。
# 收据落盘后段（push/push_commit/push_remote/verify_end）：mark 发生在收据
# 之后（git-works-push 细分自发 add/commit/remote、verify 链终结自发），
# 收据 timings 天然不含——同样排除在应有段集之外，防 -sv 收据假 missing。
CONDITIONAL_SEGMENTS = frozenset([
    "edit_validate", "gen_manifest", "edit_plan", "edit_retry", "edit_item",
    "push", "push_commit", "push_remote", "verify_end",
])

# batch_id 合法形态（12 位小写 hex，对齐 cdp_issue 写时防御）：start/mark
# 显式传入非法值即拒（防路径注入 timings-../../x.json，P2-1）
BATCH_ID_RE = re.compile(r"^[0-9a-f]{12}$")

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
    from cdp_paths import atomic_write_text
    atomic_write_text(_current_batch_path(),
                      json.dumps({"batch_id": batch_id}, ensure_ascii=False) + "\n")


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
    中断保护：无 wall_end 的文件（批次中断未 finish）归档前补写
    status=aborted + 按已有 marks 终算 segments（P1：12/77 批次打点因
    中断缺 wall_end/segments 成残缺数据——归档时刻即数据终点，段可归因
    到中断点，emit 侧不再看到空 segments 的黑盒）。
    """
    d = log_apply_dir()
    archive = d / "archive"
    moved = 0
    for f in sorted(d.glob("timings-*.json")):
        if f.name == f"timings-{current_batch_id}.json":
            continue
        archive.mkdir(parents=True, exist_ok=True)
        data = _load(f)
        if data and not data.get("wall_end"):
            data["wall_end"] = _wall()
            data["status"] = "aborted"  # 中断批：段终算到归档时刻（非正常收尾）
            data["segments"] = compute_segments(data)
            _save(archive / f.name, data)
            f.unlink()
            print(f"info: 中断打点已按已有 marks 终算归档（aborted）: {f.name}",
                  file=sys.stderr)
        else:
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
        if not BATCH_ID_RE.match(batch):
            print(f"error: batch_id 非法（须 12 位小写 hex）: {batch!r}",
                  file=sys.stderr)
            return None, True
        return _timing_path(batch), False
    if getattr(args, "file", None):
        return Path(args.file), False
    env_id = os.environ.get("CDP_BATCH_ID", "").strip()
    if env_id:
        # env 来源与 --batch 同防（CDP-07）：BATCH_ID_RE 校验，非法拒用
        # warn 后回落下一级（current-batch.json），不落非法路径（防注入）
        if not BATCH_ID_RE.match(env_id):
            print(f"warn: CDP_BATCH_ID 非法（须 12 位小写 hex），拒用回落: "
                  f"{env_id!r}", file=sys.stderr)
        else:
            return _timing_path(env_id), False
    cur = _read_current_batch()
    if cur:
        return _timing_path(cur), False
    print("warn: 无显式 --batch/--file、无 CDP_BATCH_ID 且 current-batch.json "
          "缺失（未 start），自动 mark/finish 跳过", file=sys.stderr)
    return None, True


def _load(path: Path):
    """读打点文件；缺失/损坏/结构非法返回 None（mark 未 start 时据此报 3）。

    结构防御（P2-11）：顶层非 JSON 对象或 marks 非 list 一律按损坏处理，
    不让 _cmd_mark 对畸形结构 AttributeError 裸栈崩溃。
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("marks", []), list):
        return None
    return data


def _save(path: Path, data) -> None:
    """原子写：走 cdp_paths.atomic_write_text 统一原语（tmp 带 pid 防并发
    互写 + replace，中断不留半写态；对齐 append_trend 惯例）。"""
    from cdp_paths import atomic_write_text
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _wall() -> float:
    """epoch 秒小数（高精度 wall clock）。"""
    return time.time()


def _mono() -> float:
    """monotonic 秒（不受 NTP 校时/时钟回拨影响，B4）。"""
    return time.monotonic()


def compute_segments(data) -> list[dict]:
    """由 start + marks + 当前时刻计算段耗时（ws_report 落收据复用）。

    首段 = 首个 mark - start；末段 = 当前时刻 - 末个 mark；
    无 mark 或 start 缺失时返回空列表（不崩，调用方按缺打点处理）。

    时间轴（B4）：start 带 start_mono 且全部 marks 带 mono 时走单调轴
    （免 NTP 校时/时钟回拨扭曲段耗时），否则回退 wall 轴（旧文件兼容）。
    wall 轴下负 interval（时钟回拨）截 0，不产出负耗时段。

    归因修正（方向 2）：mark 带 dur_s（自测真实耗时）时该段耗时取 dur_s，
    相邻差额（interval）减去 dur_s 后的余量是段前未被 mark 覆盖的未打点
    活动（如自检前的编排空转），另落 gap_before_<name> 段，余量小于
    GAP_THRESHOLD 不落段；无 dur_s 或 dur_s 非法（非数值/越界）时回退旧
    算法（整段差额归后一个 mark 名）。gap 段在 name 段之前（时间序）。
    被 GAP_THRESHOLD 吞掉的余量累计进末尾 unattributed 段（B5）：segments
    求和与总时长不再静默差一截，emit 侧可校验归因完整性（无吞余时不出该段）。

    方向 5：dur_s 超 interval（自测真实耗时大于相邻间隔 = 数据矛盾，如打点
    间隔内混入未打点活动/时钟异常）时**不静默回退**——该段仍按 interval
    落耗时（保时间序），额外落 <name>_dur_exceed 异常段（elapsed_s=dur_s-
    interval，带 reason），emit 侧可见异常而不误判为段 0。

    同名段名（方向 4）：同一 mark 名第 n 次出现时段名为 name#n（首次不加
    序号），返工轮次在收据段表可见可数；mark 记录本身 name 不变。
    """
    start_wall = data.get("start_wall")
    marks = data.get("marks") or []
    if start_wall is None or not marks:
        return []
    start_mono = data.get("start_mono")
    use_mono = (isinstance(start_mono, (int, float))
                and all(isinstance(m.get("mono"), (int, float)) for m in marks))

    def _t(m):
        return m["mono"] if use_mono else m["wall"]

    def _now():
        return _mono() if use_mono else _wall()

    segs = []
    prev = start_mono if use_mono else start_wall
    seen: dict[str, int] = {}
    eaten = 0.0
    for m in marks:
        name = m["name"]
        seen[name] = seen.get(name, 0) + 1
        seg_name = name if seen[name] == 1 else f"{name}#{seen[name]}"
        interval = max(_t(m) - prev, 0.0)
        dur = m.get("dur_s")
        if isinstance(dur, (int, float)) and 0 <= dur <= interval:
            gap = interval - dur
            if gap >= GAP_THRESHOLD:
                segs.append({"name": f"gap_before_{seg_name}",
                             "elapsed_s": round(gap, 3)})
            else:
                eaten += gap
            segs.append({"name": seg_name, "elapsed_s": round(dur, 3)})
        elif isinstance(dur, (int, float)) and dur > interval:
            # 方向 5：dur_s 超 interval（自测耗时大于相邻间隔=数据矛盾）不静默
            # 回退——段按 interval 落（保时间序），另落 dur_exceed 异常段暴露
            # 超出的真实耗时，emit 侧可见而非误判段 0
            segs.append({"name": seg_name, "elapsed_s": round(interval, 3)})
            segs.append({"name": f"{seg_name}_dur_exceed",
                         "elapsed_s": round(dur - interval, 3),
                         "reason": "dur_s>interval"})
        else:
            segs.append({"name": seg_name, "elapsed_s": round(interval, 3)})
        prev = _t(m)
    segs.append({"name": "finish", "elapsed_s": round(max(_now() - prev, 0.0), 3)})
    if eaten > 0:
        segs.append({"name": "unattributed", "elapsed_s": round(eaten, 3)})
    return segs


def _cmd_start(batch_id: str) -> int:
    """start：初始化打点文件（覆盖重建，AI 可重打点）。

    落 current-batch.json 记 batch_id（自动 mark/finish 定位本批指针），
    并把工作态目录已有的历史 timings 移入 archive/ 子目录。
    batch_id 写时校验（P2-1）：非 12 位小写 hex 即拒（防路径注入）。
    """
    if not isinstance(batch_id, str) or not BATCH_ID_RE.match(batch_id):
        print(f"error: batch_id 非法（须 12 位小写 hex）: {batch_id!r}",
              file=sys.stderr)
        return 2
    data = {"batch_id": batch_id, "start_wall": _wall(), "start_mono": _mono(),
            "marks": []}
    with _locked(_timing_path(batch_id)):
        _save(_timing_path(batch_id), data)
        _archive_previous_timings(batch_id)
        _write_current_batch(batch_id)
    print(f"timing started: {_timing_path(batch_id)}")
    return 0


def _do_mark(path: Path, name: str, zero: bool = False, dur_s=None,
             quiet: bool = False) -> int:
    """mark 主体：追加一个时间戳；未 start 返 3（AI 漏 start 可发现）。

    zero=True 记零 mark：wall/mono 取最近 mark（无 mark 则 start）同一
    时刻——跳过段（如无编译/无上板时的 sync/build/push/unit_test）以 0
    耗时占位，收据 timings 段完整可归因（缺段 vs 0 耗时语义不同：缺段=
    去向不明）。

    dur_s（方向 1）：调用方自测该段的真实墙钟耗时，写入 mark 记录供
    compute_segments 归因（该段耗时取 dur_s，差额余量落 gap_before_<name>）。

    quiet=True：stdout 不打 mark 行（emit_mark 进程内直调时防污染调用方
    stdout，对 ws_report 等 stdout 敏感脚本安全）；stderr 告警保留。
    """
    with _locked(path):
        data = _load(path)
        if data is None:
            if not quiet:
                print(f"error: 未 start（缺打点文件 {path}），先执行 cdp_timing.py start",
                      file=sys.stderr)
            return 3
        marks = data.get("marks") or []
        if zero:
            if marks:
                wall, mono = marks[-1]["wall"], marks[-1].get("mono")
            else:
                wall, mono = data.get("start_wall"), data.get("start_mono")
            if wall is None:
                print("error: 无 start_wall 且无 marks，无法记零", file=sys.stderr)
                return 3
        else:
            wall, mono = _wall(), _mono()
        mark = {"name": name, "wall": wall}
        if isinstance(mono, (int, float)):
            mark["mono"] = mono
        if dur_s is not None:
            mark["dur_s"] = round(float(dur_s), 3)
        data.setdefault("marks", []).append(mark)
        # 段名表校验剥序号（方向 4）：AI 显式传 name#n 时按基础名比对
        if _base_seg_name(name) not in KNOWN_SEGMENTS:
            print(f"warn: 段名 {name!r} 不在常量表 "
                  f"（{', '.join(sorted(KNOWN_SEGMENTS))}），仅告警不阻断",
                  file=sys.stderr)
        _save(path, data)
    if not quiet:
        print(f"mark: {name} @ {wall:.3f}" + ("（零耗时占位）" if zero else ""))
    return 0


def _cmd_mark(path: Path, name: str, zero: bool = False, dur_s=None) -> int:
    """mark CLI 入口：同 _do_mark（stdout 打 mark 行，供人工/调试可见）。"""
    return _do_mark(path, name, zero=zero, dur_s=dur_s, quiet=False)


def resolve_batch_id() -> str | None:
    """当前活跃批次识别（公开 API，收口 selfcheck 等对私有函数的依赖）：
    CDP_BATCH_ID 环境变量 > current-batch.json 指针 > None。"""
    env_id = os.environ.get("CDP_BATCH_ID", "").strip()
    if env_id:
        return env_id
    return _read_current_batch()


def _resolve_mark_target(batch_id=None, timings_file=None) -> Path | None:
    """emit_mark 的打点文件定位：显式 timings_file > batch_id >
    CDP_BATCH_ID > current-batch.json；无来源返回 None（静默跳过）。"""
    if timings_file:
        return Path(timings_file)
    bid = batch_id or resolve_batch_id()
    if bid and BATCH_ID_RE.match(bid):
        return _timing_path(bid)
    return None


def read_marks(batch_id=None) -> list | None:
    """读当前批次 marks 列表（公开 API）；无活跃批/损坏返 None。

    供 selfcheck._ensure_edit_close_mark 等判读打点状态（替代直接调
    _read_current_batch/_load/_timing_path 私有三件套，B8）。
    """
    bid = batch_id or resolve_batch_id()
    if not bid or not BATCH_ID_RE.match(bid):
        return None
    data = _load(_timing_path(bid))
    if data is None:
        return None
    marks = data.get("marks")
    return marks if isinstance(marks, list) else None


def emit_mark(name: str, dur_s=None, zero: bool = False, batch_id=None,
              timings_file=None) -> bool:
    """进程内自发打点公共入口（打点胶水收敛，B8/C-1）。

    供 selfcheck/ws_report/ws_push/ws_upload_tests/cdp_parse/gen_manifest
    直接 import 调用（替代各处 subprocess 调 cdp_timing.py mark 的复制
    胶水：消除 Python 解释器启动 + 模块导入开销，每链 ~5-10 次 spawn）。
    stdout 静默（quiet）不污染调用方输出；失败仅返 False，不抛异常不阻断
    （打点诊断数据，非业务结果本身）。

    定位：显式 timings_file > batch_id > CDP_BATCH_ID > current-batch.json；
    无活跃批（emit 侧独立自测等）返 False（与旧胶水"静默跳过"口径一致）。
    """
    path = _resolve_mark_target(batch_id=batch_id, timings_file=timings_file)
    if path is None:
        return False
    try:
        return _do_mark(path, name, zero=zero, dur_s=dur_s, quiet=True) == 0
    except Exception as e:  # 打点失败绝不阻断调用方主流程
        print(f"warn: emit_mark({name}) 失败（不阻断）: {e}", file=sys.stderr)
        return False


def _cmd_finish(path: Path) -> int:
    """finish：计算相邻段耗时并落盘输出 JSON（缺段不崩，输出已有段）。

    保留 start_wall/marks 原始字段（ws_report --timings-file 两种结构皆可读，
    后续 mark 仍可追加）；仅新增 wall_end + segments。
    读→算→写整体包进 _locked（CDP-06）：与并发 mark 交错时无锁的旧快照
    覆盖写会吞 mark（finish 落盘的 marks 是加锁前快照，mark 写回被覆盖），
    锁内保持单一 _load/_save 原子区间。
    """
    with _locked(path):
        data = _load(path)
        if data is None:
            print(f"error: 未 start（缺打点文件 {path}），先执行 cdp_timing.py start",
                  file=sys.stderr)
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
    p_mark.add_argument("--batch", default=None,
                        help="12 位 batch_id（缺省：CDP_BATCH_ID 环境变量 > current-batch.json）")
    p_mark.add_argument("--zero", action="store_true",
                        help="记零 mark：wall 取最近 mark 同刻（跳过段占位，段耗时 0）")
    p_mark.add_argument("--dur-s", type=float, default=None,
                        help="自测真实耗时秒数（写入 mark，compute_segments 归因："
                             "该段耗时取 dur_s，差额余量落 gap_before_<name>；与 --zero 互斥）")

    p_finish = sub.add_parser("finish", help="计算段耗时并落盘")
    p_finish.add_argument("--file", default=None, help="打点文件路径（缺省同 mark 三级回落）")
    p_finish.add_argument("--batch", default=None,
                          help="12 位 batch_id（缺省：CDP_BATCH_ID 环境变量 > current-batch.json）")

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
