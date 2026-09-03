#!/usr/bin/env python3
# ============================================================
# lcview_check.py — lcview 业务数据板端校验器（host 侧执行）
# 所属模块：workspace-verify — 业务验证用例资产
# 设计目的：通过 adb 拉取 /data/vendor/lechao_lcview/logs/ 下 JSONL
#   与 schema，在 host 侧完成业务数据正确性校验。设备侧无 python3，
#   复杂解析（合法 JSON / schema 匹配 / 增量基线）全部在 host 完成；
#   设备侧只做 ls/stat/date 等 toybox 支持的最小操作。
#
# 模式：
#   files      — 存在至少 1 个非空 .jsonl（业务事件已落盘）
#   valid_json — 全部记录行均为合法 JSON
#   schema     — 每条记录 id∈schema 且 f 字段数 == schema 定义
#   invalid    — invalid_records.log 为空（无坏记录）
#   fresh      — 最近 .jsonl 的 mtime 距今 < --window 秒（服务持续写入）
#   ts         — 记录时间戳与设备时钟偏差 < --skew 秒（可选，无记录跳过）
#   baseline   — 记录当前各文件行数与全局最新 ts 到 --baseline（供 delta）
#   delta      — 对比基线，统计新增记录；--event 限定事件 id；
#                --vid/--pid 校验 usb_probe 字段匹配
#   conserve   — 守恒判据：窗口内内核产生 ≈ 磁盘 JSONL 落盘（两拍直读采样，
#                在途差值 = 产生增量 - 落盘增量，不超界且不为负——防丢记录/
#                重复落盘回归，替代人工核算；磁盘行数不受 daemon 重启影响）
#   perf       — 性能采集（脚本化统一负载）：dd 读块设备 --load-mb MB（默认 64，
#                与性能基线负载一致）→ 三指标：事件吞吐（内核 total_records 直读
#                增量 / dd 实测耗时）、平均落盘延迟（jsonl 达标 drain 时间 /
#                jsonl 行数增量，100ms 直读采样，不含任何人工 sleep 且不受心跳
#                周期绑架）、daemon RSS（/proc VmHWM 峰值）。
#                只报数不设门禁，供跨批基线对照。
#
# 退出码：0 校验通过 / 1 校验失败 / 2 设备不可达或参数错误
# ============================================================

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

# 设备定位复用 ws_adb_connect（勿自建 adb 层）：host_port 默认 rp5.local:5555，
# 支持 LC_VERIFY_ADB_HOST/PORT 环境变量覆盖，mDNS 发现逻辑不在此重复实现
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ws_adb_connect import (ensure_connected as ws_connected,  # noqa: E402
                            host_port, run_adb)

ADB_TARGET = host_port()
# 实际连接端点：ensure_connected 可能经 mDNS 发现非静态端点，后续 -s 统一用它
_EP = ADB_TARGET
LOGS_DIR = "/data/vendor/lechao_lcview/logs"
SCHEMA_REMOTE = "/vendor/etc/lcview_events.json"


def _default_baseline(env_name="LCVIEW_BASELINE_FILE"):
    """按轮次隔离的基线路径（方向 4）：轮次编排层经环境变量注入按轮次唯一
    路径（hostcmd 侧 ${LCVIEW_BASELINE_FILE:-...} 同步透传），防跨轮基线串扰
    （A 轮写的基线被 B 轮 delta 误读）；未设置时回退固定默认（独立 CLI/单测）。"""
    return os.environ.get(env_name) or "/tmp/lcview_baseline.json"


BASELINE_DEFAULT = _default_baseline()


def adb(args, timeout=60):
    """执行 adb 命令，返回 (stdout, returncode)。"""
    return run_adb(["-s", _EP] + args, timeout=timeout)


def ensure_connected():
    """adb 连接（复用 ws_adb_connect mDNS→静态 fallback）+ root + 探活。

    仅连静态 host_port 会撞 PIT-1（静态 IP 漂移后连不上）；root 重启 adbd
    后需重新连接，探活失败视为设备不可达。
    """
    global _EP
    ep = ws_connected()
    if not ep:
        print(f"ERROR: 设备不可达（mDNS 与静态 {ADB_TARGET} 均失败）")
        sys.exit(2)
    _EP = ep
    run_adb(["-s", ep, "root"])
    time.sleep(2)
    ep = ws_connected()
    if not ep:
        print(f"ERROR: 设备 {ADB_TARGET} root 后重连失败")
        sys.exit(2)
    _EP = ep


def device_now():
    """设备侧当前 epoch 秒；失败返回 None。"""
    out, rc = adb(["shell", "date +%s"])
    if rc == 0 and out.strip().isdigit():
        return int(out.strip())
    return None


def pull_logs(tmp):
    """拉取 logs 目录下全部 .jsonl 到本地，返回本地路径列表；adb 异常返 -1。

    ls 的 rc 必须判（adb 超时 -1 透传，不得当"无日志文件"假绿）；pull 失败
    不得静默跳过（拉不全的"全部"不可信），同样返 -1 透传。
    """
    out, rc = adb(["shell", f"ls {LOGS_DIR}"])
    if rc == -1:
        return -1
    if rc != 0:
        # ls 自身失败（目录不存在等）：无文件可拉，按无日志处理
        return []
    files = [f for f in out.split() if f.endswith(".jsonl")]
    pulled = []
    for f in files:
        local = os.path.join(tmp, f)
        _, prc = adb(["pull", f"{LOGS_DIR}/{f}", local])
        if prc != 0:
            return -1
        pulled.append(local)
    return pulled


def pull_schema(tmp):
    """从板上下载当前部署的 schema（与板端一致，避免本地归档漂移）。"""
    local = os.path.join(tmp, "lcview_events.json")
    _, rc = adb(["pull", SCHEMA_REMOTE, local])
    if rc != 0:
        return None
    try:
        with open(local, encoding="utf-8") as fp:
            return json.load(fp)
    except (OSError, json.JSONDecodeError):
        return None


def load_all(pulled):
    """解析全部记录。返回 (records, bad_lines)。
    records: [{ts, id, fields, file}]；bad_lines: [(file, lineno)]。"""
    records, bad_lines = [], []
    for p in pulled:
        with open(p, encoding="utf-8", errors="replace") as fp:
            for ln, line in enumerate(fp, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    records.append({
                        "ts": obj.get("ts"),
                        "id": obj.get("id"),
                        "fields": obj.get("f"),
                        "file": os.path.basename(p),
                    })
                except json.JSONDecodeError:
                    bad_lines.append((os.path.basename(p), ln))
    return records, bad_lines


def schema_field_count(schema, event_id):
    """按事件 id 查 schema 字段数；未定义返回 None。"""
    if not schema:
        return None
    for ev in schema.get("events", []):
        if ev.get("id") == event_id:
            return len(ev.get("fields", []))
    return None


# ============================================================
# 各模式实现
# ============================================================

def mode_files(tmp, _args):
    pulled = pull_logs(tmp)
    if pulled == -1:
        return -1  # adb 超时透传，不得当"无日志"假绿
    nonempty = [p for p in pulled if os.path.getsize(p) > 0]
    print(f"jsonl 文件 {len(pulled)} 个，非空 {len(nonempty)} 个")
    for p in sorted(nonempty):
        print(f"  {os.path.basename(p)}: {os.path.getsize(p)}B")
    if not nonempty:
        print("ERROR: 无任何非空 jsonl（业务事件未落盘）")
        return 1
    return 0


def mode_valid_json(tmp, _args):
    pulled = pull_logs(tmp)
    if pulled == -1:
        return -1  # adb 超时透传
    records, bad = load_all(pulled)
    print(f"记录 {len(records)} 条，坏行 {len(bad)} 条")
    for name, ln in bad[:10]:
        print(f"  BAD: {name}:{ln}")
    if not records:
        # 零记录 ≠ 合法零坏行：无数据可校验即假绿，须判红
        print("ERROR: 无任何记录可校验（业务事件未落盘）")
        return 1
    if bad:
        print("ERROR: 存在非合法 JSON 行")
        return 1
    return 0


def mode_schema(tmp, _args):
    pulled = pull_logs(tmp)
    if pulled == -1:
        return -1  # adb 超时透传
    schema = pull_schema(tmp)
    if schema is None:
        print("ERROR: schema 拉取失败")
        return 1
    records, _ = load_all(pulled)
    if not records:
        # 零记录须判红：无数据可校验 schema，与"全记录匹配"区分
        print("ERROR: 无任何记录可校验（业务事件未落盘）")
        return 1
    # 允许的事件 id 集合（schema 定义）
    allowed = {ev.get("id") for ev in schema.get("events", [])}
    mism = []
    for r in records:
        if r["id"] not in allowed:
            mism.append(f"{r['file']}: id={r['id']} 不在 schema({sorted(allowed)})")
            continue
        expect = schema_field_count(schema, r["id"])
        got = len(r["fields"]) if isinstance(r["fields"], list) else -1
        if expect is not None and got != expect:
            mism.append(f"{r['file']}: id={r['id']} 字段数 {got} != schema {expect}")
    print(f"记录 {len(records)} 条，schema 不匹配 {len(mism)} 条")
    for m in mism[:10]:
        print(f"  MISMATCH: {m}")
    if mism:
        print("ERROR: 存在 schema 不匹配记录")
        return 1
    return 0


def mode_invalid(tmp, _args):
    out, rc = adb(["shell", f"stat -c '%s' {LOGS_DIR}/invalid_records.log 2>/dev/null"])
    if rc == -1:
        return -1  # adb 超时透传给 main 判定，不得假绿"视为空通过"
    if rc != 0:
        # stat 失败（目录/文件不存在等）：无法确认坏记录状态，不得静默通过
        print(f"ERROR: invalid_records.log 不可读（stat rc={rc}），无法确认坏记录状态")
        return 1
    size = out.strip()
    if not size.isdigit():
        print(f"ERROR: invalid_records.log 大小非数字（{size!r}），无法确认坏记录状态")
        return 1
    if int(size) > 0:
        # 正向验证：展示坏记录内容（证明判红非误报）
        body, rrc = adb(["shell", f"head -c 300 {LOGS_DIR}/invalid_records.log"])
        print(f"ERROR: invalid_records.log 非空（{size}B），存在坏记录:")
        for line in body.splitlines()[:5]:
            print(f"  {line[:120]}")
        if rrc != 0:
            print(f"  （坏记录内容读取失败 rc={rrc}）")
        return 1
    print("invalid_records.log 为空（无坏记录）")
    return 0


def mode_fresh(tmp, args):
    window = args.window or 600
    now = device_now()
    if now is None:
        print("ERROR: 无法读取设备时钟")
        return 1
    # 设备侧 stat 取各文件 mtime（epoch 秒），取最新
    out, rc = adb(["shell",
                   f"stat -c '%Y %n' {LOGS_DIR}/*.jsonl 2>/dev/null"])
    if rc == -1:
        return -1  # adb 超时透传给 main 判定，不得误判"无文件"
    mtimes = []
    for line in out.splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2 and parts[0].isdigit():
            mtimes.append((int(parts[0]), parts[1]))
    if not mtimes:
        print("ERROR: 无 .jsonl 文件可判新鲜度")
        return 1
    newest = max(mtimes)
    age = now - newest[0]
    print(f"最新文件 {newest[1]} mtime_age={age}s（窗口 {window}s）")
    if age > window:
        print(f"ERROR: 最新写入距今 {age}s 超窗（服务可能停止写入）")
        return 1
    return 0


def mode_ts(tmp, args):
    skew = args.skew or 600
    now = device_now()
    if now is None:
        print("ERROR: 无法读取设备时钟")
        return 1
    pulled = pull_logs(tmp)
    if pulled == -1:
        return -1  # adb 超时透传
    records, _ = load_all(pulled)
    # --baseline 限定时只判基线 max_ts 之后的新记录（trigger 用，须显式传）：
    # 全历史会命中时钟校准回拨前的 501 条未来记录永久判红；
    # 未显式传（warn 卫生检查）时判全历史
    if getattr(args, "baseline_explicit", False):
        if not os.path.exists(args.baseline):
            print(f"ERROR: 基线文件缺失 {args.baseline}")
            return 1
        with open(args.baseline, encoding="utf-8") as fp:
            base_ts = json.load(fp).get("max_ts", 0)
        records = [r for r in records
                   if isinstance(r["ts"], (int, float)) and r["ts"] > base_ts]
        if not records:
            print("ERROR: 基线之后无新记录可判时间戳")
            return 1
    # 排除未来 ts（与基线同源防御）：校准回拨前记录不得当"最新记录"；
    # 滤阈由 skew 派生（硬编码 300 < skew 600 时会把判红对象本身滤掉架空）
    tss, filtered = _trusted_tss(records, now, skew)
    if filtered > 0:
        if not getattr(args, "baseline_explicit", False):
            # warn 全历史卫生检查：未来记录即判红（PIT-5 特征，暴露历史污染）
            print(f"ERROR: 检测到 {filtered} 条时钟回拨前未来记录"
                  f"（ts > now+{skew}s，PIT-5 特征，判红）")
            return 1
        # trigger 场景（--baseline 显式限定）：未来记录属本周期之前的历史
        # 污染（时钟校准回拨遗留），只报告不判红——ts 判据只针对本轮新记录
        print(f"NOTE: 忽略 {filtered} 条基线前历史未来记录（校准回拨遗留，"
              f"卫生检查见 lcview-pipeline-warn）")
    if not tss:
        # 零可信记录须判红：无数据可判时间戳，与"记录偏差在窗内"区分
        print("ERROR: 无记录可判时间戳（业务事件未落盘）")
        return 1
    newest_ns = max(tss)
    age_s = abs(newest_ns / 1e9 - now)
    print(f"最新记录 ts={int(newest_ns)}ns，与设备时钟偏差 {age_s:.1f}s（skew {skew}s）")
    if age_s > skew:
        print("ERROR: 记录时间戳与设备时钟偏差超窗")
        return 1
    return 0


def _trusted_tss(records, now, future_skew=600):
    """返回 (trusted, filtered_count)：排除超过设备时钟 future_skew 秒的未来 ts。

    设备时钟被校准回拨（PIT-5，如超前 7h 后 date -u 修正）后，校准前写入的
    记录 ts 是"未来时间"，会污染 baseline max_ts 致 delta 恒 0（新事件 ts
    永远小于基线）。滤阈由调用方 skew 派生（默认 600 = ts skew 默认）：
    硬编码 300 小于 skew 600 时，超 300s 的未来记录被滤掉后余下恒过——
    恰是 PIT-5（超前 7h）特征，等于把判红对象架空。
    now 为 None（读不到时钟）时不过滤（调用方须自行判红）。
    """
    out = []
    filtered = 0
    for r in records:
        ts = r["ts"]
        if not isinstance(ts, (int, float)):
            continue
        if now is not None and ts / 1e9 > now + future_skew:
            filtered += 1
            continue
        out.append(ts)
    return out, filtered


def mode_baseline(tmp, args):
    """记录各文件行数与全局最新 ts 到基线文件（供 delta 增量判定）。"""
    pulled = pull_logs(tmp)
    if pulled == -1:
        return -1  # adb 超时透传
    if not pulled:
        # 零文件不得写基线：max_ts 落 0 会让 delta 把历史记录全当新增（假绿）
        print("ERROR: 无 .jsonl 文件可写基线（业务事件未落盘）")
        return 1
    records, _ = load_all(pulled)
    line_count = {}
    for p in pulled:
        with open(p, encoding="utf-8", errors="replace") as fp:
            line_count[os.path.basename(p)] = sum(1 for _ in fp)
    # 排除未来 ts：设备时钟校准回拨后，校准前记录的 ts 会污染 max_ts；
    # 设备时钟读不到须判红（静默不过滤等于放弃护栏）
    now = device_now()
    if now is None:
        print("ERROR: 无法读取设备时钟")
        return 1
    trusted, _ = _trusted_tss(records, now, args.skew or 600)
    if not trusted:
        # 全部记录被滤空（仅剩时钟回拨前旧记录）不得写基线：
        # max_ts 落 0 会让 delta 把历史全当新增（假绿，绕过上面零文件护栏）
        print("ERROR: 无可信时间戳可写基线（业务事件未落盘或仅剩时钟回拨前旧记录）")
        return 1
    max_ts = max(trusted)
    baseline = {"max_ts": max_ts, "line_count": line_count}
    with open(args.baseline, "w", encoding="utf-8") as fp:
        json.dump(baseline, fp, ensure_ascii=False, indent=2)
    print(f"基线已写入 {args.baseline}: max_ts={max_ts}, 文件数={len(line_count)}")
    return 0


def mode_delta(tmp, args):
    """对比基线：统计新增记录（ts > 基线 max_ts）；--event 限定事件；
    --vid/--pid 校验 usb_probe 的 f=[idx,vid,pid,vendor,product] 匹配。"""
    if not os.path.exists(args.baseline):
        print(f"ERROR: 基线文件不存在 {args.baseline}（先跑 baseline 模式）")
        return 1
    with open(args.baseline, encoding="utf-8") as fp:
        base = json.load(fp)
    base_ts = base.get("max_ts", 0)

    pulled = pull_logs(tmp)
    if pulled == -1:
        return -1  # adb 超时透传
    records, _ = load_all(pulled)
    # 排除未来 ts（与基线同源防御）：校准回拨前的旧记录不得当新增；
    # 设备时钟读不到须判红（静默不过滤等于放弃护栏）
    now = device_now()
    if now is None:
        print("ERROR: 无法读取设备时钟")
        return 1
    trusted, _ = _trusted_tss(records, now, args.skew or 600)
    trusted = set(trusted)
    new_records = [r for r in records
                   if isinstance(r["ts"], (int, float)) and r["ts"] > base_ts
                   and r["ts"] in trusted]

    print(f"基线 max_ts={base_ts}，新增记录 {len(new_records)} 条")
    for r in new_records[:10]:
        print(f"  NEW: id={r['id']} ts={r['ts']} file={r['file']}")

    if not new_records:
        print("ERROR: 无新增记录（事件源未产生新事件）")
        return 1

    if args.event is not None:
        hit = [r for r in new_records if r["id"] == args.event]
        print(f"事件 id={args.event} 新增 {len(hit)} 条")
        if not hit:
            print(f"ERROR: 新增记录中无事件 {args.event}")
            return 1
        # 校验字段匹配（usb_probe f=[device_index, vid, pid, vendor, product]）
        if args.vid is not None or args.pid is not None:
            for r in hit:
                f = r["fields"]
                if (isinstance(f, list) and len(f) >= 3 and
                        int(f[1]) == args.vid and int(f[2]) == args.pid):
                    print(f"  字段匹配: f={f}")
                    return 0
            print(f"ERROR: 事件 {args.event} 无 vid={args.vid}/pid={args.pid} 匹配记录")
            return 1
    return 0


def mode_conserve(tmp, args):
    """守恒判据：窗口内内核产生 ≈ 磁盘 JSONL 落盘（AIDL 注释同源）。

    守恒式 = 内核累计产生 total_records（overrun 被驱逐未落盘）≈ 磁盘 JSONL
    落盘行数。v4（恢复负向判红，两段式采样）：
    v2 只查 ring 与 HAL buffered 两处积压——docstring 却仍写守恒，且 FileWriter
    六条 DROP 全静默时两处皆空反判绿（重构首要风险正是丢记录）；
    v3 改回比较"产生 vs 落盘"，但负向（落盘>产生）被当追赶期整体放行——
    重复落盘/计数异常从此不可检测（心跳 dropped 计丢弃不计重复，不能替代）；
    零记录守卫亦放宽为 produced==0 且 landed==0（静止态有负载时形同虚设）。
    v4 两段式采样：
    - 静止确认段：两拍直读（间隔 --conserve-sample-s）增量归零即确认窗口
      起点无积压（外部负载已排干）；增量非零则起点有积压（追赶期）
    - 负载采样段：--conserve-load-mb 触发 dd 读块设备自造负载后两拍采样，
      produced = Δtotal - Δoverrun（内核产生增量，去被驱逐）
      landed   = ΔJSONL 行数（wc -l，磁盘持久计数）
      in_flight = produced - landed
    - produced == 0：负载窗口无事件产生 → 判红（防假绿，与 valid_json/
      schema 零记录判红先例一致；自造负载下应产生 > 0）
    - in_flight > --in-flight：产生未落盘积压 → 判红
    - in_flight < -(--in-flight)：起点无积压（静止确认通过）时落盘超过产生
      即重复落盘/计数异常 → 判红；仅起点有积压（追赶期，landed 含窗口前
      产生的补落盘）才放行
    磁盘行数不受 daemon 重启（jsonl_records 进程内归零）影响，窗口增量亦不受
    跨 boot 历史影响；DROP 分类计数（心跳 drop_* 字段）供判红后定位丢在哪条。
    """
    limit = args.in_flight or 16
    interval = args.conserve_sample_s or 5
    s1 = kernel_stats()
    if s1 is None:
        print(f"ERROR: 无法直读内核计数（cat {STATS_SYSFS} 失败，"
              f"内核未带 sysfs 导出？）")
        return 1
    l1 = jsonl_line_count()
    if l1 is None:
        print("ERROR: 无法直读 JSONL 行数（wc -l 失败）")
        return 1
    time.sleep(interval)
    s2 = kernel_stats()
    if s2 is None:
        print(f"ERROR: 无法直读内核计数（cat {STATS_SYSFS} 失败）")
        return 1
    l2 = jsonl_line_count()
    if l2 is None:
        print("ERROR: 无法直读 JSONL 行数（wc -l 失败）")
        return 1
    # 静止确认段：前段两拍增量归零 → 窗口起点无积压（外部负载已排干）；
    # 增量非零 → 起点有积压（追赶期），负向判红须放行（landed 含补落盘）
    rest_produced = (s2[0] - s1[0]) - (s2[1] - s1[1])
    rest_landed = l2 - l1
    at_rest = rest_produced == 0 and rest_landed == 0
    print(f"静止确认: 前段增量 产生={rest_produced} 落盘={rest_landed} "
          f"（{'起点无积压' if at_rest else '起点有积压(追赶期)'}）")
    # 负载采样段：自造短负载（--conserve-load-mb > 0）——外部 dd 时序不可控
    # （事件几秒内全部落盘，两拍采样窗口可能错过），自带负载保证窗口内
    # produced>0，守恒有数据可校验；默认 0 保持纯只读（跟随外部负载场景）
    if args.conserve_load_mb:
        dev = args.block_dev or "/dev/block/sda"
        _, rc = adb(["shell",
                     f"dd if={dev} of=/dev/null bs=1M count={args.conserve_load_mb} 2>/dev/null"],
                    timeout=args.dd_timeout or 120)
        if rc == -1:
            return -1  # adb 超时透传
        if rc != 0:
            print(f"ERROR: conserve 负载 dd 执行失败 rc={rc}（块设备 {dev} 不可读？）")
            return 1
    time.sleep(interval)
    s3 = kernel_stats()
    if s3 is None:
        print(f"ERROR: 无法直读内核计数（cat {STATS_SYSFS} 失败）")
        return 1
    l3 = jsonl_line_count()
    if l3 is None:
        print("ERROR: 无法直读 JSONL 行数（wc -l 失败）")
        return 1
    produced = (s3[0] - s2[0]) - (s3[1] - s2[1])
    landed = l3 - l2
    in_flight = produced - landed
    print(f"负载窗口 {interval}s: 内核产生增量={produced}，落盘增量={landed}，"
          f"在途差值={in_flight}（上限 {limit}）")
    if produced == 0:
        # 负载窗口无事件产生须判红：自造负载下应产生 > 0，无数据可校验
        # 守恒，与"守恒成立"区分（同 valid_json / schema 零记录判红先例）
        print("ERROR: 负载窗口内无事件产生（内核 total_records 无增量，"
              "自造负载未生效？），守恒无从校验")
        return 1
    if in_flight < -limit:
        if at_rest:
            # 起点无积压仍落盘超过产生 → 真异常：重复落盘/计数异常（心跳
            # dropped 计丢弃不计重复，不能替代本判据）
            print(f"ERROR: 在途差值 {in_flight} < -{limit}（起点无积压仍落盘 "
                  f"{landed} 超过产生 {produced}，存在重复落盘/计数异常）")
            return 1
        # 追赶期放行：窗口起点有积压使落盘增量 > 产生增量合法（高吞吐后的
        # drain 追赶期，landed 含窗口前产生的补落盘）
        print(f"NOTE: 负向在途 {in_flight} 属追赶期（起点有积压，落盘 {landed} "
              f"> 产生 {produced} 为补落盘），放行")
    if in_flight > limit:
        print(f"ERROR: 在途差值 {in_flight} 超界 {limit}（产生未落盘积压，"
              f"HAL/daemon 消费可能停滞）")
        return 1
    print(f"OK: 守恒成立（负载窗口内产生 {produced} ≈ 落盘 {landed}）")
    return 0


def kernel_stats():
    """直读内核三计数 (total_records, overrun, ring_usage_bytes)；失败返回 None。

    与 kernel_total 同源（STATS_SYSFS 只读导出，单次 cat 全量解析，减少
    adb 往返）：conserve 窗口采样需 total 与 overrun 配对，perf 只取 total。
    """
    out, rc = adb(["shell", f"cat {STATS_SYSFS}"])
    if rc == -1:
        return None
    vals = {}
    for line in out.splitlines():
        for key in ("total_records", "overrun", "ring_usage_bytes"):
            m = re.search(key + r"=(\d+)", line)
            if m and key not in vals:
                vals[key] = int(m.group(1))
    if len(vals) == 3:
        return (vals["total_records"], vals["overrun"], vals["ring_usage_bytes"])
    return None


def daemon_rss_kb():
    """daemon 进程峰值 RSS（/proc/<pid>/status VmHWM，kB）；失败返回 -1。

    RSS 取自进程峰值（VmHWM），与基线口径一致（内存增长回归探测）；
    pidof 定位失败或 /proc 读取失败返回 -1，由调用方判红（指标不全
    不能当完整基线）。
    """
    out, rc = adb(["shell", "pidof lechao_lcview"])
    if rc == -1:
        return -1  # adb 超时透传
    if rc != 0 or not out.strip():
        print("ERROR: 无法定位 daemon 进程（pidof lechao_lcview 失败）")
        return -1
    pid = out.strip().split()[0]
    out, rc = adb(["shell", f"cat /proc/{pid}/status"])
    if rc != 0:
        print("ERROR: 无法读取 daemon /proc/<pid>/status")
        return -1
    for line in out.splitlines():
        if line.startswith("VmHWM:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1])
    print("ERROR: /proc/<pid>/status 无 VmHWM 字段")
    return -1


# 内核 total_records 直读节点：sysfs 只读导出（lcview_main.c lcview_stats_show）。
# 设备节点 /dev/vendor_lechao_lcview 有单打开限制（HAL 常驻占用，并发 open 返
# EBUSY），ioctl 直读不可行；sysfs 文件 cat 即得实时计数，不参与单打开语义。
STATS_SYSFS = "/sys/class/lcview/vendor_lechao_lcview/lcview_stats"


def kernel_total():
    """直读内核 total_records（cat sysfs 只读节点）；失败返回 None。

    直读替代心跳观测：心跳每 30 loop 约 28s 才更新，远粗于 2.5s 的 dd 负载窗，
    drain 被心跳周期绑架（实测 30.5s 恰为一个心跳周期）；sysfs cat 实时、
    无该粒度偏差。adb 超时或解析失败返回 None（调用方判红，不静默通过）。
    """
    s = kernel_stats()
    return s[0] if s else None


def jsonl_line_count():
    """直读 logs 目录全部 .jsonl 总行数（wc -l，设备侧计数，不传输内容）。

    wc -l 在设备侧统计（只回传文件名+数字），避免每 100ms 全量 pull 的传输
    开销；adb 超时返回 None（调用方判红）。
    """
    out, rc = adb(["shell", f"wc -l {LOGS_DIR}/*.jsonl 2>/dev/null"])
    if rc == -1:
        return None  # adb 超时透传
    total = 0
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1] == "total":
            try:
                return int(parts[0])
            except ValueError:
                return None
        if len(parts) == 2 and parts[0].isdigit():
            total += int(parts[0])
    return total


def mode_perf(tmp, args):
    """性能采集（固定负载，脚本化，只报数不设门禁）。

    统一负载 dd 读块设备 --load-mb MB（默认 64，与性能基线 dd 64MB 一致，
    消除自动化用例 4MB 与基线 64MB 负载不一的对照失真）：
    - 事件吞吐 = 内核 total_records 直读增量 / dd 实测耗时（events/s）
    - 平均落盘延迟 = jsonl 达标 drain 时间 / jsonl_records 增量（ms/event）：
      drain 时间从 dd 完成时刻起算，按 --perf-sample-ms（默认 100ms）直读
      JSONL 行数与内核计数，直至 jsonl 落盘达标（jsonl 增量 >= total 增量），
      不含任何人工 sleep，也不受心跳 28s 周期绑架（原心跳观测致延迟随负载
      翻倍而机械减半、drain 恒为一个心跳周期）
    - daemon RSS = /proc VmHWM 峰值（kB）
    输出 human 可读行 + METRICS JSON 行（供上层结构化存档，跨批可 diff）。
    """
    load_mb = args.load_mb or 64
    dev = args.block_dev or "/dev/block/sda"
    sample_s = (args.perf_sample_ms or 100) / 1000.0

    total0 = kernel_total()
    if total0 is None:
        print(f"ERROR: 无法直读内核计数（cat {STATS_SYSFS} 失败，"
              f"内核未带 sysfs 导出？）")
        return 1
    jsonl0 = jsonl_line_count()
    if jsonl0 is None:
        print("ERROR: 无法直读 JSONL 行数（wc -l 失败）")
        return 1

    # dd 计时（host 侧单调钟，不含任何人工 sleep）
    t0 = time.monotonic()
    _, rc = adb(["shell",
                 f"dd if={dev} of=/dev/null bs=1M count={load_mb} 2>/dev/null"],
                timeout=args.dd_timeout or 300)
    dd_s = time.monotonic() - t0
    if rc == -1:
        return -1  # adb 超时透传
    if rc != 0:
        print(f"ERROR: dd 负载执行失败 rc={rc}（块设备 {dev} 不可读？）")
        return 1
    if dd_s <= 0:
        # 单调钟下 dd_s<=0 说明负载从未真正执行（计时异常/空转），
        # 出数会得到 throughput=inf 的假基线，判红并提示负载未执行
        print("ERROR: dd 计时非正（dd_s<=0），负载未执行或计时异常，判红防假基线")
        return 1

    # drain 计时起点 = dd 完成时刻；直读按 100ms 采样——先等内核计数出现增量
    # （dd 产生事件已计入），再等 jsonl 落盘达标（与心跳观测 28s 粒度解耦）
    drain_t0 = time.monotonic()
    total, jsonl = total0, jsonl0
    seen_total = False
    while True:
        t = kernel_total()
        if t is not None:
            total = t
        j = jsonl_line_count()
        if j is not None:
            jsonl = j
        if total - total0 > 0:
            seen_total = True
        if seen_total and jsonl - jsonl0 >= total - total0:
            break  # 内核计数已反映 dd 事件且 jsonl 落盘达标
        if time.monotonic() - drain_t0 > (args.perf_timeout or 60):
            print("ERROR: 内核计数未出现增量或 jsonl 落盘未达标（drain 超时，"
                  "daemon 消费停滞？）")
            return 1
        time.sleep(sample_s)
    drain_s = time.monotonic() - drain_t0

    total_delta = total - total0
    jsonl_delta = jsonl - jsonl0
    if total_delta <= 0 or jsonl_delta <= 0:
        print("ERROR: 无事件增量（dd 未产生传输事件，块设备未就绪？）")
        return 1
    throughput = total_delta / dd_s
    latency_ms = drain_s / jsonl_delta * 1000

    rss_kb = daemon_rss_kb()
    if rss_kb < 0:
        return 1  # RSS 指标不全不能当完整基线（内部已打印原因）

    metrics = {
        "load_mb": load_mb,
        "dd_s": round(dd_s, 3),
        "throughput_evs": round(throughput, 1),
        "drain_ms_per_event": round(latency_ms, 3),
        "daemon_rss_kb": rss_kb,
        "total_delta": total_delta,
        "jsonl_delta": jsonl_delta,
    }
    print(f"性能采集（负载 {load_mb}MB，dd {dd_s:.3f}s）: "
          f"事件吞吐={throughput:.1f} events/s，平均落盘延迟={latency_ms:.3f} "
          f"ms/event，daemon RSS={rss_kb} kB（drain {drain_s:.3f}s）")
    print("METRICS " + json.dumps(metrics, ensure_ascii=False))
    return 0


MODES = {
    "files": mode_files,
    "valid_json": mode_valid_json,
    "schema": mode_schema,
    "invalid": mode_invalid,
    "fresh": mode_fresh,
    "ts": mode_ts,
    "baseline": mode_baseline,
    "delta": mode_delta,
    "conserve": mode_conserve,
    "perf": mode_perf,
}


def main(argv=None):
    ap = argparse.ArgumentParser(description="lcview 业务数据板端校验器")
    ap.add_argument("--mode", required=True, choices=sorted(MODES),
                    help="校验模式")
    ap.add_argument("--window", type=int, default=600,
                    help="fresh 模式写入新鲜窗口（秒）")
    ap.add_argument("--skew", type=int, default=600,
                    help="ts 模式时间偏差容忍（秒）")
    ap.add_argument("--event", type=int, default=None,
                    help="delta 模式限定事件 id")
    ap.add_argument("--vid", type=int, default=None,
                    help="delta 模式校验 usb_probe vid")
    ap.add_argument("--pid", type=int, default=None,
                    help="delta 模式校验 usb_probe pid")
    ap.add_argument("--baseline", default=BASELINE_DEFAULT,
                    help="baseline/delta 模式基线文件路径")
    ap.add_argument("--in-flight", type=int, default=16,
                    help="conserve 模式在途差值上限（条），默认 16——按实测收紧"
                         "（dd 64MB 后峰值 3、静止 0，留 5 倍余量；原 512 稀释"
                         "灵敏度约百倍）")
    ap.add_argument("--conserve-sample-s", type=int, default=5,
                    help="conserve 模式两拍直读采样间隔（秒），默认 5（窗口内"
                         "比较产生 vs 落盘增量，免疫 daemon 重启）")
    ap.add_argument("--conserve-load-mb", type=int, default=0,
                    help="conserve 模式窗口内主动触发 dd 读负载（MB），默认 0=不触发"
                         "（纯只读）；>0 时 s1 采样后 dd 读 --block-dev 保证窗口内"
                         "有事件产生，供守恒校验（外部 dd 时序不可控场景）")
    ap.add_argument("--load-mb", type=int, default=64,
                    help="perf 模式 dd 读负载（MB），默认 64（与性能基线一致）")
    ap.add_argument("--block-dev", default="/dev/block/sda",
                    help="perf 模式 dd 读块设备路径")
    ap.add_argument("--perf-timeout", type=int, default=60,
                    help="perf 模式 jsonl 落盘 drain 轮询超时（秒）")
    ap.add_argument("--perf-sample-ms", type=int, default=100,
                    help="perf 模式直读采样间隔（毫秒），默认 100（不受心跳周期绑架）")
    ap.add_argument("--dd-timeout", type=int, default=300,
                    help="perf 模式 dd 执行 adb 超时（秒）")
    args = ap.parse_args(argv)
    # 记录 --baseline 是否显式传（ts 模式只在显式时做基线限定）
    args.baseline_explicit = "--baseline" in (argv if argv is not None
                                              else sys.argv[1:])

    ensure_connected()
    with tempfile.TemporaryDirectory(prefix="lcview_check_") as tmp:
        rc = MODES[args.mode](tmp, args)
    # run_adb 已把 adb 超时吞成 rc=-1（不再抛 TimeoutExpired），按 -1 判定
    if rc == -1:
        print("ERROR: adb 执行超时")
        return 1
    return 0 if rc == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
