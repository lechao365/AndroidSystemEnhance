"""验收标签解析与自动执行（含 CLI）。

标签语法（推荐格式）：svc:<svc> / log:<kw> / prop:<k>=<v> / file:<path> /
cmd:"<含空格的 shell>" 或 cmd:<无空格> / hostcmd:"<host 侧 shell>"（在
workspace-verify 目录执行，payload 相对路径以其为根，如
hostcmd:"cases/lcview_check.sh --mode files"）/ boot（裸词）/ logfresh:"锚点|秒数"
（以设备时钟回退 N 秒作 logcat 时间窗起点，窗内未命中锚点即判红——时效性判据，
daemon 卡死而进程存活时旧心跳不再判绿）；其余内容视为
自由文本（status='ai'，由 verify AI 现场判定）。
overall 语义（三态）：任一自动项 fail 即 fail；无 fail 但含未判定项（ai）则 ai；
全 pass 且无 ai 才 pass（未判定不算成功）。

CLI:
  ws_acceptance.py run <--acceptance "<验收文本>" | --case <标签> | --batch-file <cdp>> \
      [--ensure-boot] [--wait-ready] [--log-since <MM-DD HH:MM:SS.mmm>]
    → 验收文本三选一（互斥，全缺返 2）；--case 从 harness/config/verify-cases.yaml
      cases 段取标签（含空格/引号命令在此书写）；--batch-file 经 cdp_parse 解析批次
      取验收文本。内部 ensure_connected，逐项执行，输出 JSON；exit 0 通过 /
      1 设备不可达或 fail / 2 参数错误（验收来源缺失/互斥、--case 标签不存在、
      --log-since 格式非法、--wait-ready 且含 log: 却无 --log-since、或 overall=ai 未判定）
"""
import argparse
import json
import re
import shlex
import subprocess
import sys
import tempfile
import time
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

import ws_adb_connect as ac  # noqa: E402（ensure 连接/就绪/时钟/救援，编排层复用）

# 复用 CDP 解析（与 ws_report.py 同款路径注入）：parse_batch 取批次验收文本
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cross-device" / "lib" / "python"))
from cdp_parse import batch_id_from_text, parse_batch  # noqa: E402
import cdp_paths  # noqa: E402
import cdp_timing  # noqa: E402

# harness/config/verify-cases.yaml：--case 标签源（资产层，批次内禁引号用例集中维护）
_CASES_PATH = Path(__file__).resolve().parents[2] / "config" / "verify-cases.yaml"

# hostcmd 在 workspace-verify 目录执行：payload 相对路径（如 cases/lcview_check.sh）
# 以该目录为根，无需在用例资产中硬编码绝对路径
_VERIFY_ROOT = Path(__file__).resolve().parent

# log 支持引号包裹（含空格关键字，如 log:"LcView HAL: registered"）；
# cmd/hostcmd 支持引号包裹（含空格）；引号内支持反斜杠转义（\"），否则
# 含转义引号的命令（如 adb shell \"echo ...\"）会在转义处截断成坏标签；
# logfield 取 logcat 含锚点的最后一行按字段名提取数值比较（锚点|字段|比较符|数值
# [|进程名]），防 log: 子串匹配命中历史零值心跳的假绿；第 5 段进程名经 pidof 取 pid
# 后日志按该 pid 收窄（防旧进程心跳残留行被当新进程心跳），锚点未命中时每 5s 重取
# （绕缓存重取——缓存只服务同批同 key 多标签复用）到 90s 超时判红；logfresh 取设备
# 时钟回退 N 秒的
# logcat 时间窗（锚点|秒数），窗内未命中锚点即判红（时效性判据，自带时间窗不走
# 缓存，语义不变）；adb_logcat 按 (pid, 时间窗) 缓存：同批多标签只拉一次 5000 行，
# 每项执行 wall-clock 计时写 items elapsed_s（收据 acceptance JSON 可见）；
# boot 为裸词；其余标签不含空格
_TAG_RE = re.compile(
    r'(?:svc|log|prop|file|hostcmd):(?:"(?:\\.|[^"\\])*"|\S+)'
    r'|logfield:(?:"(?:\\.|[^"\\])*"|\S+)'
    r'|logfresh:(?:"(?:\\.|[^"\\])*"|\S+)'
    r'|cmd:(?:"(?:\\.|[^"\\])*"|\S+)|\bboot\b')
# --log-since 时间窗起点格式：MM-DD 或 YYYY-MM-DD 的 HH:MM:SS.mmm
_SINCE_RE = re.compile(r"^(?:\d{4}-)?\d{2}-\d{2} \d{2}:\d{2}:\d{2}\.\d{3}$")


def _parse_since_epoch(since):
    """--log-since 文本 → 本地时区 epoch 秒（float，含毫秒小数）。

    支持 MM-DD（无年，按当前年补全）与 YYYY-MM-DD 两种前缀；
    解析失败抛 ValueError（调用方转错误消息返 2/1）。
    """
    if since[4] != "-":  # "MM-DD ..."（无年）补当前年
        since = f"{datetime.now().year}-{since}"
    dt = datetime.strptime(since, "%Y-%m-%d %H:%M:%S.%f")
    local = dt.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return local.timestamp()


def convert_since_to_device(local_since, device_cmd):
    """把本地 --log-since 按设备时钟/时区换算为设备侧时间窗文本。

    --log-since 在本地（CST）书写，而设备时钟/时区常为 UTC（PIT-5 同源，
    已复发三次靠人工换算绕过）：本地时刻直接传 logcat 会落在设备"未来"，
    判据取回 0 字符恒红。换算 = 本地时刻 epoch + (设备 epoch - 本地 epoch)，
    再按设备时区（date +%z）格式化，保证时间窗落在设备真实时间域。
    device_cmd(cmd) -> (stdout, exit_code)。返回 (device_since, err)；
    err 非 None 时 device_since 为 None。
    """
    try:
        local_epoch = _parse_since_epoch(local_since)
    except ValueError:
        return None, f"--log-since 解析失败: {local_since!r}"
    now_epoch = time.time()
    out, rc = device_cmd("date +%s")
    if rc != 0 or not out.strip().isdigit():
        return None, "无法读取设备时钟（date +%s）"
    device_epoch = int(out.strip())
    out, rc = device_cmd("date +%z")
    if rc != 0:
        return None, "无法读取设备时区（date +%z）"
    m = re.match(r"([+-])(\d{2})(\d{2})", out.strip())
    if not m:
        return None, f"设备时区格式非法 {out.strip()!r}"
    sign = 1 if m.group(1) == "+" else -1
    offset = sign * (int(m.group(2)) * 3600 + int(m.group(3)) * 60)
    tz = timezone(timedelta(seconds=offset))
    device_since = local_epoch + (device_epoch - now_epoch)
    dt = datetime.fromtimestamp(device_since, tz=tz)
    fmt = "%Y-%m-%d %H:%M:%S.%f" if local_since[4] == "-" else "%m-%d %H:%M:%S.%f"
    return dt.strftime(fmt)[:-3], None


def parse_acceptance(text):
    """提取标签列表；无任何标签则整段视为单条自由文本。

    标签外残文本不得静默丢弃（曾致 lcview-trigger 的 USB 开关命令整体消失）：
    残余非空即抛 ValueError，由 main 捕获返 2。
    """
    text = text or ""
    tags = _TAG_RE.findall(text)
    if not tags and text.strip():
        return [text.strip()]
    residual = _TAG_RE.sub("", text)
    if residual.strip():
        raise ValueError(
            f"验收文本存在未识别残余（标签外内容会被静默丢弃）: {residual.strip()!r}")
    return tags


def _merge_overall(a, b):
    """三态汇总：任一 fail 即 fail；无 fail 有 ai 即 ai；全 pass 才 pass。"""
    if "fail" in (a, b):
        return "fail"
    if "ai" in (a, b):
        return "ai"
    return "pass"


def split_tag(tag):
    if tag == "boot":
        return "boot", ""
    if ":" in tag:
        kind, payload = tag.split(":", 1)
        if kind in ("cmd", "log", "hostcmd", "logfield", "logfresh") and payload.startswith('"') and payload.endswith('"'):
            payload = payload[1:-1]
            payload = payload.replace('\\"', '"')  # 反转义：\" → "
        return kind, payload
    return "text", tag


def resolve_acceptance(args, cases_path=_CASES_PATH):
    """解析验收文本来源（--acceptance / --case / --batch-file 三选一）。

    返回 (acceptance, err)：err 非 None 时 acceptance 为 None（err 为错误消息）。
    --batch-file 经 cdp_parse.parse_batch 取批次验收文本（-s 批次验收为「无」则拒绝）；
    --case 从 verify-cases.yaml cases 段取标签对应验收文本（值内可用引号），
    支持逗号分隔多用例（逐个查表拼接，任一缺失即拒）。
    """
    sources = [s for s in (args.acceptance, args.case, args.batch_file) if s]
    if len(sources) > 1:
        return None, "--acceptance / --case / --batch-file 互斥，只能选其一"
    if not sources:
        return None, "必传其一：--acceptance 验收文本 / --case 用例标签 / --batch-file 批次文件"
    if args.batch_file:
        try:
            text = Path(args.batch_file).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            return None, f"批次文件不可读或非 UTF-8: {e}"
        b = parse_batch(text)
        if not b.acceptance or b.acceptance == "无":
            return None, "--batch-file 批次验收为空或「无」（-s 批次无验收，须用 -sv 批次）"
        # 禁批次自带宿主命令：验收文本含 hostcmd:/cmd: 标签即拒（批次夹带
        # 宿主命令执行意图属越权；case 与 acceptance 两分支不变）
        if re.search(r"\bhostcmd\s*:|\bcmd\s*:", b.acceptance):
            return None, ("--batch-file 批次验收含 hostcmd:/cmd: 标签"
                          "（禁止批次自带宿主命令，验收走 case/manual 契约）")
        if b.acceptance.startswith("case:"):
            # 方向 3：case id 逐个查 verify-cases.yaml cases 段，任一未知即拒
            # （main 退 2）；manual 模式自由文本不查表。
            # KIR-002 当批修（2026-09-05）：查表后须展开为验收文本（镜像
            # --case 分支语义）——此前返回原串 "case:a,b"，执行器无 case: kind
            # 落自由文本 ai 项 → rc 2（三批 -sv 实证，收据 cases 源同步堵洞）
            ids = [i.strip() for i in b.acceptance[len("case:"):].split(",")
                   if i.strip()]
            try:
                data = yaml.safe_load(Path(cases_path).read_text(encoding="utf-8")) or {}
                cases = data.get("cases") or {}
            except (OSError, yaml.YAMLError) as e:
                return None, f"verify-cases.yaml 读取失败: {e}"
            missing = [i for i in ids if i not in cases]
            if missing:
                opts = ", ".join(sorted(cases)) or "无"
                return None, (f"验收 case id {', '.join(missing)} 不存在于 "
                              f"verify-cases.yaml cases 段（可选: {opts}）")
            try:
                return " ".join(_case_text(cases[i]) for i in ids), None
            except ValueError as e:
                return None, str(e)
        # 非 case: 批次验收（manual:/自由标签文本）原样返回（不查表）
        return b.acceptance, None
    if args.case:
        try:
            data = yaml.safe_load(Path(cases_path).read_text(encoding="utf-8")) or {}
            cases = data.get("cases") or {}
        except (OSError, yaml.YAMLError) as e:
            return None, f"verify-cases.yaml 读取失败: {e}"
        # --case 支持逗号分隔多用例：逐个查表拼接，任一缺失即整批拒绝（不部分拼接）
        labels = [c.strip() for c in args.case.split(",") if c.strip()]
        if not labels:
            return None, "--case 标签为空（逗号分隔后无有效标签）"
        missing = [c for c in labels if c not in cases]
        if missing:
            opts = ", ".join(sorted(cases)) or "无"
            return None, (f"用例标签 {', '.join(missing)} 不存在于 verify-cases.yaml cases 段"
                          f"（可选: {opts}）")
        try:
            return " ".join(_case_text(cases[c]) for c in labels), None
        except ValueError as e:
            return None, str(e)
    return args.acceptance, None


def _exec_annotate(detail, code):
    """code==-1（adb 执行超时）时在 detail 标注，与正常命令失败（exit>0）区分。"""
    return f"{detail}（adb 执行超时）" if code == -1 else detail


def _clip_body(body, code):
    """detail 截断口径：通过取头部 200 控收据体积；失败取尾部 400——错误
    信息通常在输出末尾，头部截断会遮蔽 ERROR 尾行致取证困难（2026-09-05
    recover seq 假红实拍：失败现场只见前 200 字符，根因行被吞）。"""
    return body[-400:] if code != 0 else body[:200]


def execute_tag(tag, adb_exec, adb_logcat, host_env=None, endpoint=None):
    """adb_exec(cmd)->(body, exit_code)；adb_logcat()->str。返回 (status, detail)。

    status: pass | fail | ai（自由文本由 AI 判定）；exit_code=-1 表示 adb 超时。
    host_env: hostcmd 分支子进程环境（方向 2 按 run_id 导出基线文件路径，
    实现轮次隔离；为 None 时沿用调用进程环境，行为不变）。
    endpoint（wsv2-02）：非空时 logfresh 的 logcat 定向 -s <endpoint>（多
    serial 残留防 more than one device 假红）；缺省 None 保持无 -s 旧行为
    （单 serial / 测试直调）。
    """
    kind, payload = split_tag(tag)
    if kind == "svc":
        # payload 经 shlex.quote 防注入（svc 名含空格/特殊字符时安全落入 getprop 参数）
        body, code = adb_exec(f"getprop init.svc.{shlex.quote(payload)}")
        return ("pass" if code == 0 and body.strip() == "running" else "fail",
                _exec_annotate(f"init.svc.{payload}={body.strip()!r} exit={code}", code))
    if kind == "log":
        out = adb_logcat()
        hit = payload in out
        return ("pass" if hit else "fail",
                f"logcat {'命中' if hit else '未命中'} 关键字 {payload!r}（取回 {len(out)} 字符）")
    if kind == "logfield":
        # 锚点|字段名|比较符|数值[|进程名]：取 logcat 含锚点的最后一行按字段名提取
        # 数值比较——log: 子串匹配会命中历史零值心跳（5000 行缓冲），
        # 故障累计后仍 PASS 的假绿由此防。
        # 第 5 段进程名（5 段写法）：经 pidof 取 pid 后日志按该 pid 收窄
        # （logcat --pid），防旧进程心跳残留行被当新进程心跳（E1 首跑认旧
        # 进程心跳根因：logfield 取末行不按进程归属筛）；4 段写法不变。
        parts = payload.split("|")
        if len(parts) not in (4, 5):
            return "fail", (f"logfield 语法错误（须 锚点|字段|比较符|数值"
                            f"[|进程名]）: {payload!r}")
        anchor, field, op, expect_s = [p.strip() for p in parts[:4]]
        proc = parts[4].strip() if len(parts) == 5 else ""
        if len(parts) == 5 and not proc:
            return "fail", f"logfield: 第 5 段进程名为空: {payload!r}"
        if proc:
            # 5 段写法：pidof 取 pid（空或非数字判红），日志按 pid 收窄；
            # 锚点未命中（新进程首心跳未到）时每 5s 重取（重新 pidof + logcat）
            # 到 90s 超时判红——不得回落全量筛（回落即旧进程行重新混入假绿）；
            # 首次调用走缓存（同批同 key 多标签复用，如 liveness 5 条同 pid
            # logfield 只拉一次），轮询重试 force=True 绕缓存重取（走缓存永远
            # 读首拉旧内容死等到 90s 超时判红——缓存只服务复用，轮询须实时）
            deadline = time.monotonic() + 90
            first = True
            while True:
                body, code = adb_exec(f"pidof {shlex.quote(proc)}")
                pid = body.strip()
                if code != 0 or not pid.isdigit():
                    return "fail", (f"logfield: 进程 {proc!r} pidof 为空或非数字"
                                    f" {pid!r}（exit={code}）")
                out = adb_logcat(pid, force=not first)
                first = False
                lines = [ln for ln in out.splitlines() if anchor in ln]
                if lines:
                    break
                if time.monotonic() >= deadline:
                    return "fail", (f"logfield: 进程 {proc!r}(pid={pid}) 90s 内"
                                    f"未命中锚点 {anchor!r}（未回落全量筛）")
                time.sleep(5)
        else:
            out = adb_logcat()
            lines = [ln for ln in out.splitlines() if anchor in ln]
            if not lines:
                return "fail", f"logfield: logcat 未命中锚点 {anchor!r}"
        last = lines[-1]
        m = re.search(rf"{re.escape(field)}=(-?\d+)", last)
        if not m:
            return "fail", f"logfield: 锚点末行无字段 {field}（{last[:120]}）"
        actual = int(m.group(1))
        try:
            expect = int(expect_s)
        except ValueError:
            return "fail", f"logfield: 期望值非数字 {expect_s!r}"
        ok = {"=": actual == expect, "!=": actual != expect,
              ">": actual > expect, "<": actual < expect,
              ">=": actual >= expect, "<=": actual <= expect}.get(op, None)
        if ok is None:
            return "fail", f"logfield: 未知比较符 {op!r}（须 = != > < >= <=）"
        return ("pass" if ok else "fail",
                f"logfield {field}={actual} {op} {expect}（锚点末行: {last[:120]}）")
    if kind == "logfresh":
        # 锚点|秒数：取设备 date 回退 N 秒作 logcat 时间窗起点，窗内未命中
        # 锚点即判红——log:/logfield 取末行不看时效，daemon 卡死而进程存活时
        # 旧心跳仍判绿（假绿精确条件：进程活着但采集链路死了）。
        # 时间窗文本按设备时区格式化后直接透传 build_logcat_cmd（-t <since>），
        # 不解析时间戳（复用既有命令构造，PIT-5 同源防护）。
        # 方向 8：未命中每 5s 重取（重新取设备时间算滑动窗），到 window 秒
        # 超时判红——reboot 后 daemon 恢复心跳/clock_sync 时钟域切换（PIT-5
        # 回拨后校准）时，首拉窗可能落在心跳恢复前或旧时钟域，等一个心跳
        # 周期即命中，避免 -sv 重启后首轮验收必判红；超时仍按 window 保证
        # 时效语义（最终判红 = window 内确无锚点心跳，非等待放大判据）。
        parts = payload.split("|")
        if len(parts) != 2:
            return "fail", f"logfresh 语法错误（须 锚点|秒数）: {payload!r}"
        anchor, window_s = [p.strip() for p in parts]
        try:
            window = int(window_s)
        except ValueError:
            return "fail", f"logfresh 秒数非数字: {window_s!r}"
        deadline = time.monotonic() + window
        while True:
            body, code = adb_exec("date +%s")
            if code != 0 or not body.strip().isdigit():
                return "fail", "logfresh: 无法读取设备时钟（date +%s）"
            since_epoch = int(body.strip()) - window
            out, code = adb_exec("date +%z")
            if code != 0:
                return "fail", "logfresh: 无法读取设备时区（date +%z）"
            m = re.match(r"([+-])(\d{2})(\d{2})", out.strip())
            if not m:
                return "fail", f"logfresh: 设备时区格式非法 {out.strip()!r}"
            sign = 1 if m.group(1) == "+" else -1
            offset = sign * (int(m.group(2)) * 3600 + int(m.group(3)) * 60)
            tz = timezone(timedelta(seconds=offset))
            dt = datetime.fromtimestamp(since_epoch, tz=tz)
            since_text = dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            try:
                r = subprocess.run(ac.build_logcat_cmd(None, 5000,
                                                       since=since_text,
                                                       endpoint=endpoint),
                                   capture_output=True, text=True,
                                   encoding="utf-8", errors="replace",
                                   timeout=60)
                out = r.stdout
            except subprocess.TimeoutExpired:
                return "fail", "logfresh: logcat 执行超时"
            if anchor in out:
                return ("pass",
                        f"logfresh: 设备时钟回退 {window}s（窗起点 {since_text}），"
                        f"窗内命中 锚点 {anchor!r}（取回 {len(out)} 字符）")
            if time.monotonic() >= deadline:
                return ("fail",
                        f"logfresh: 设备时钟回退 {window}s（窗起点 {since_text}），"
                        f"窗内未命中 锚点 {anchor!r}（取回 {len(out)} 字符）")
            time.sleep(5)
    if kind == "prop":
        k, _, v = payload.partition("=")
        body, code = adb_exec(f"getprop {shlex.quote(k)}")
        # code 必须为 0 且取值相等才 pass（超时 -1 或命令失败即使输出恰好相等也判 fail）
        return ("pass" if code == 0 and body.strip() == v else "fail",
                _exec_annotate(f"{k}={body.strip()!r} 期望={v}", code))
    if kind == "file":
        body, code = adb_exec(f"ls -la {shlex.quote(payload)}")
        return ("pass" if code == 0 else "fail",
                _exec_annotate(body.strip()[:200], code))
    if kind == "cmd":
        # cmd 分支按设计是 shell 串（引号/管道语义需保留），不包裹
        body, code = adb_exec(payload)
        return ("pass" if code == 0 else "fail",
                _exec_annotate(_clip_body(body.strip(), code), code))
    if kind == "hostcmd":
        # host 侧执行（不经 adb）：payload 为 shell 串，cwd 落在 workspace-verify，
        # 相对路径（如 cases/lcview_check.sh）以其为根，用例资产不再硬编码绝对路径；
        # env 按 run_id 注入 LCVIEW_BASELINE_FILE / LCIOD_BASELINE_FILE（轮次隔离）
        host_kwargs = {"shell": True, "capture_output": True, "text": True,
                       "encoding": "utf-8", "errors": "replace",
                       "cwd": str(_VERIFY_ROOT), "timeout": 180}
        if host_env is not None:
            host_kwargs["env"] = host_env
        try:
            r = subprocess.run(payload, **host_kwargs)
            body = (r.stdout + r.stderr).strip()
            return ("pass" if r.returncode == 0 else "fail",
                    _exec_annotate(_clip_body(body, r.returncode), r.returncode))
        except subprocess.TimeoutExpired:
            return "fail", "hostcmd 执行超时"
        except OSError as e:
            return "fail", f"hostcmd 执行失败: {e}"
    if kind == "boot":
        body, code = adb_exec("getprop sys.boot_completed")
        return ("pass" if code == 0 and body.strip() == "1" else "fail",
                _exec_annotate(f"sys.boot_completed={body.strip()!r}", code))
    # 自由文本：交 AI 判定
    return "ai", payload


def run_acceptance(acceptance_text, adb_exec, adb_logcat, ensure_boot=False,
                   on_item=None, host_env=None, deadline=None, endpoint=None):
    """执行全部条目，返回 (overall, items)。overall ∈ pass|fail|ai。

    ensure_boot=True 且标签无 boot 时自动追加（兑现 workspace-verify SKILL L20：
    模式 B 设备存活是恢复的最低判据）。
    on_item(n) 可选回调：每项完成后调用（n 为已完成项数，1-based），
    供调用方逐项打点（case 级耗时归因）；无回调时行为与之前一致。
    host_env 透传 execute_tag（hostcmd 子进程环境，方向 2 轮次隔离基线）。
    deadline（monotonic 时刻，方向 4）：非 None 时逐项前检查墙钟，超时中断
    剩余项并落 __timeout__ 标记项（生命周期收尾顺序仍完整执行）。
    endpoint（wsv2-02）：透传 execute_tag，logfresh 的 logcat 定向 -s。
    """
    items = []
    tags = parse_acceptance(acceptance_text)
    if ensure_boot and "boot" not in tags:
        tags = tags + ["boot"]
    if not tags:
        # 空验收（空文本/未提取到任何标签）判红并附说明项：未判定不算成功，
        # 防空验收静默返回 pass 的假绿（三态语义下无标签即无证据）
        return "fail", [{"tag": "", "status": "fail",
                         "detail": "验收为空：未提取到任何标签/判据，按 fail 处理"
                                   "（防空验收假绿；-sv 批次须有非「无」验收）"}]
    for tag in tags:
        # 每项 wall-clock 计时（monotonic，轮询/拉取耗时含入）→ items 可见，
        # 收据 acceptance JSON 可读最慢项（定位耗时瓶颈，非判据本身）
        if deadline is not None and time.monotonic() > deadline:
            items.append({"tag": "__timeout__", "status": "fail",
                          "detail": f"用例墙钟超 timeout_s 上限，中断剩余 "
                                    f"{len(tags) - len(items)} 项（方向 4：超时"
                                    "按生命周期同一顺序收尾）"})
            break
        start = time.monotonic()
        status, detail = execute_tag(tag, adb_exec, adb_logcat,
                                     host_env=host_env, endpoint=endpoint)
        items.append({"tag": tag, "status": status, "detail": detail,
                      "elapsed_s": round(time.monotonic() - start, 3)})
        if on_item:
            on_item(len(items))
    auto = [i for i in items if i["status"] in ("pass", "fail")]
    if any(i["status"] == "fail" for i in auto):
        return "fail", items
    if any(i["status"] == "ai" for i in items):
        return "ai", items
    return "pass", items


# ── 副作用用例生命周期（2026-09-03，方向 1~4）─────────────────────────
# cases 段值新形态（dict）：{acceptance, setup_snapshot, teardown, timeout_s}
# —— str 旧形态继续支持（无生命周期）。执行固定顺序：
#   setup_snapshot → 判据（timeout_s 中断）→ first_error 时 ws_forensics
#   只读取证 → teardown（只恢复实际改变的状态，失败标 device_dirty）。

# teardown 命令模板占位符：${SNAPSHOT_i} 替换为 setup 快照第 i 项输出
_SNAPSHOT_REF_RE = re.compile(r"\$\{SNAPSHOT_(\d+)\}")


def _case_text(val):
    """cases 段值取验收文本：str 旧形态原样；dict 新形态取 acceptance 键。"""
    if isinstance(val, dict):
        v = (val.get("acceptance") or "").strip()
        if not v:
            raise ValueError("用例为 dict 形态但缺 acceptance 字段")
        return v
    return val


def _load_lifecycle(cases_path, label):
    """取单 case 生命周期（方向 1）：返回 dict 或 None（str 旧形态/缺省）。

    dict 形态且缺 acceptance 时抛 ValueError（资产书写错误须暴露，
    不静默降级为无生命周期——副作用用例无 teardown 会留脏态）。
    """
    try:
        data = yaml.safe_load(Path(cases_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        raise ValueError(f"verify-cases.yaml 读取失败: {e}") from e
    cases = data.get("cases") or {}
    if label not in cases:
        raise ValueError(f"用例标签 {label!r} 不存在于 verify-cases.yaml cases 段")
    val = cases[label]
    if isinstance(val, dict):
        if not (val.get("acceptance") or "").strip():
            raise ValueError(f"用例 {label!r} 为 dict 形态但缺 acceptance 字段")
        return val
    return None


def _run_host_cmd(cmd, host_env=None):
    """生命周期命令执行（host 侧 shell，语义同 execute_tag hostcmd 分支）。"""
    kwargs = {"shell": True, "capture_output": True, "text": True,
              "encoding": "utf-8", "errors": "replace",
              "cwd": str(_VERIFY_ROOT), "timeout": 60}
    if host_env is not None:
        kwargs["env"] = host_env
    try:
        r = subprocess.run(cmd, **kwargs)
        return (r.stdout + r.stderr), r.returncode
    except (subprocess.TimeoutExpired, OSError):
        return "", -1


def _take_snapshot(cmds, host_env=None):
    """执行快照命令列表，返回输出值列表；任一失败返 None（状态不可知）。"""
    vals = []
    for c in cmds:
        body, rc = _run_host_cmd(c, host_env)
        if rc != 0:
            return None
        vals.append(body.strip())
    return vals


def _expand_teardown(cmds, snapshot):
    """teardown 模板展开：${SNAPSHOT_i} → setup 快照第 i 项输出。"""
    out = []
    for c in cmds:
        out.append(_SNAPSHOT_REF_RE.sub(
            lambda m: snapshot[int(m.group(1))] if int(m.group(1)) < len(snapshot)
            else m.group(0), c))
    return out


def _restore_state(setup_cmds, teardown_cmds, snapshot, host_env=None):
    """teardown（方向 3）：只恢复本轮实际改变的状态。

    重读 setup 快照命令对比——未改变即跳过（不写设备）；有变才执行
    teardown 命令（模板展开快照值），恢复后重读复核。返回
    (device_dirty, detail)：恢复命令失败 / 重读失败 / 复核仍不符 → dirty。
    """
    if not setup_cmds or snapshot is None:
        return False, "无快照（未登记 setup_snapshot），无 teardown 责任面"
    current = _take_snapshot(setup_cmds, host_env)
    if current is None:
        return True, "teardown 重读快照失败（状态不可知），按 device_dirty 处理"
    if current == snapshot:
        return False, "状态未改变，teardown 跳过（只恢复实际改变的状态）"
    for c in _expand_teardown(teardown_cmds or [], snapshot):
        body, rc = _run_host_cmd(c, host_env)
        if rc != 0:
            return True, (f"teardown 命令失败 rc={rc}: {c[:120]} "
                          f"→ {body.strip()[:120]}")
    restored = _take_snapshot(setup_cmds, host_env)
    if restored != snapshot:
        return True, "teardown 执行后重读快照仍与初值不符（device_dirty）"
    return False, "已恢复到初值"


def _run_forensics(ep, since_epoch, items, first_error):
    """first_error 时调 ws_forensics 只读取证（方向 2 固定顺序第二环）。

    失败 items 序列化写临时文件传 stdout_file（host 侧失败现场随取证落盘）；
    取证失败仅 warn 不阻断（收尾顺序继续 teardown）。
    """
    try:
        import ws_forensics as wf
    except (ImportError, OSError) as e:
        print(f"warn: 取证模块不可用（不阻断）: {e}", file=sys.stderr)
        return None
    try:
        tmp = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                          encoding="utf-8")
        try:
            tmp.write(json.dumps({"first_error": first_error, "items": items},
                                 ensure_ascii=False, indent=2))
            tmp.close()
            _, run_dir = wf.collect(ep=ep, since_epoch=since_epoch,
                                    stdout_file=tmp.name)
            print(f"NOTE: 失败取证落盘: {run_dir}")
            return str(run_dir)
        finally:
            # 任何退出路径（含 collect 抛异常）都清理临时文件，防 /tmp 泄漏
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
    except (OSError, subprocess.SubprocessError, ValueError) as e:
        print(f"warn: 取证失败（不阻断）: {e}", file=sys.stderr)
        return None


def run_case_lifecycle(acceptance_text, lifecycle, adb_exec, adb_logcat,
                       ep=None, ensure_boot=False, on_item=None, host_env=None,
                       since_epoch=0):
    """副作用用例生命周期编排（方向 2 固定顺序）。

    顺序：setup_snapshot → 判据执行（timeout_s 中断）→ first_error 时
    ws_forensics 只读取证 → teardown → 返回。
    返回 (overall, items, meta)：meta = {"device_dirty": bool,
    "timed_out": bool, "teardown_detail": str, "forensics_dir": str|None}。
    """
    meta = {"device_dirty": False, "timed_out": False,
            "teardown_detail": "", "forensics_dir": None}
    setup_cmds = lifecycle.get("setup_snapshot") or []
    snapshot = None
    if setup_cmds:
        snapshot = _take_snapshot(setup_cmds, host_env)
        if snapshot is None:
            # 快照失败 = 设备状态不可知，teardown 无法保证 → 判红收尾
            # （仍走取证与收据，不执行 teardown——无初值可恢复）
            items = [{"tag": "setup_snapshot", "status": "fail",
                      "detail": "快照命令失败（设备状态不可知，teardown 无法"
                                "保证），按 fail 处理"}]
            meta["forensics_dir"] = _run_forensics(ep, since_epoch, items,
                                                   "setup_snapshot 失败")
            meta["device_dirty"] = True
            meta["teardown_detail"] = "无初值快照，跳过 teardown"
            return "fail", items, meta
    timeout_s = lifecycle.get("timeout_s")
    deadline = time.monotonic() + int(timeout_s) if timeout_s else None
    overall, items = run_acceptance(acceptance_text, adb_exec, adb_logcat,
                                    ensure_boot=ensure_boot, on_item=on_item,
                                    host_env=host_env, deadline=deadline,
                                    endpoint=ep)
    meta["timed_out"] = any(i.get("tag") == "__timeout__" for i in items)
    first_error = next((f"{i['tag']}: {i['detail']}" for i in items
                        if i["status"] == "fail"), "")
    if first_error:
        meta["forensics_dir"] = _run_forensics(ep, since_epoch, items,
                                               first_error)
    dirty, tdetail = _restore_state(setup_cmds, lifecycle.get("teardown") or [],
                                    snapshot, host_env)
    meta["device_dirty"] = dirty
    meta["teardown_detail"] = tdetail
    return overall, items, meta


def _mark_stage(name, batch_id=None, zero=False):
    """验证阶段自动打点：进程内直调 cdp_timing.main mark（batch 识别：显式
    batch_id > 环境变量 CDP_BATCH_ID > log 目录唯一 timings 文件；均缺时
    静默跳过返 0，失败不阻断口径）。验收段每项一发 mark（30+ 次），子进程
    版每次 0.1~0.3s 启动开销串行叠加，进程内直调消除（_backfill_zero_marks
    同款先例）。zero=True 记零 mark（跳过段占位，段耗时 0）。"""
    args = ["mark", "--name", name]
    if batch_id:
        args += ["--batch", batch_id]
    if zero:
        args += ["--zero"]
    try:
        rc = cdp_timing.main(args)
    except SystemExit as e:
        rc = e.code
    except Exception as e:
        print(f"warn: 打点 {name} 失败（不阻断）: {e}", file=sys.stderr)
        return
    if rc not in (0, None):
        print(f"warn: 打点 {name} rc={rc}（不阻断）", file=sys.stderr)


def _resolve_batch_id(batch_id):
    """batch 识别回落：显式 batch_id > 环境变量 CDP_BATCH_ID >
    current-batch.json 指针 > log 目录唯一 timings 文件（多文件静默跳过
    防误标其他批次）——薄壳委托 verify_common.resolve_batch_id_fallback
    （批次四 B6 统一口径：与 cdp_timing mark 落点/产物命名同源）。
    返回 batch_id 或 None。"""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "lib"))
    from verify_common import resolve_batch_id_fallback
    return resolve_batch_id_fallback(batch_id)


def _batch_case_labels(batch_file):
    """批次验收为 case: 前缀时提取 id 串（--case 缺省时的实跑标签源）。

    KIR-002 当批修配套：--batch-file 模式下 args.case 为空，实跑标签须
    从批次验收行提取，否则 cases-<batch_id>.json 缺失 → report PASS 探测
    空 cases 拒写。非 case: 前缀/批次不可读均返空串（静默降级不阻断）。
    """
    if not batch_file:
        return ""
    try:
        b = parse_batch(Path(batch_file).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return ""
    acc = (b.acceptance or "").strip()
    return acc[len("case:"):].strip() if acc.startswith("case:") else ""


def _write_cases(batch_id, cases_text):
    """本次实跑 case 标签落盘：log_apply_dir()/cases-<batch_id>.json。

    供 ws_report 未显式传 --case 时自动探测补全（与 timings 探测同源），
    杜绝 board pass 收据 cases 为空致 prepare evidence-scope 推导死锁
    （2026-09-02 BL-20260902-01 发布被迫回填 7833c640079a 的教训）。
    batch 识别三级回落（显式 > 环境变量 > 唯一打点文件）；均不可得或
    无实跑标签时静默跳过（不阻断，-s 无标签批/独立 CLI 属正常降级）。
    原子写：临时文件 + replace（对齐 cdp_timing 惯例，中断不留半写态）。
    """
    bid = _resolve_batch_id(batch_id)
    if not bid or not (cases_text or "").strip():
        return
    p = cdp_paths.log_apply_dir() / f"cases-{bid}.json"
    try:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps({"batch_id": bid,
                                   "cases": cases_text.strip()},
                                  ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(p)
        print(f"NOTE: 实跑 case 标签落盘: {p}", file=sys.stderr)
    except OSError as e:
        print(f"warn: cases 落盘失败（不阻断）: {e}", file=sys.stderr)


# 标准五段中的前四段（sync/build/push/unit_test）：跳过时补零 mark 占位，
# 保证收据 timings 段完整可归因（缺段 vs 0 耗时语义不同：缺段=去向不明）
_STANDARD_ZERO_SEGMENTS = ("verify_sync", "verify_build", "verify_push",
                           "verify_unit_test")


def _backfill_zero_marks(batch_id):
    """标准四段缺失时补零 mark（跳过段记 0 耗时，收据段完整可归因）。

    上一批收据只有 verify_start 与 verify_acceptance 两段即因四段跳过时
    未发 mark；验收是最末验证阶段，由它兜底补齐。batch_id 缺失（三级
    回落皆不可得）时跳过——自动识别不可靠时不写，防误标其他批次。
    """
    if not batch_id:
        return
    try:
        p = cdp_paths.log_apply_dir() / f"timings-{batch_id}.json"
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    have = {m.get("name") for m in (data.get("marks") or [])}
    for seg in _STANDARD_ZERO_SEGMENTS:
        if seg not in have:
            cdp_timing.main(["mark", "--batch", batch_id, "--name", seg,
                             "--zero"])


def _resolve_run_batch_id(batch_file):
    """main 内 batch_id 解析三级回落：batch-file 显式解析 > CDP_BATCH_ID >
    log 目录唯一 timings 文件（复用 _resolve_batch_id 口径）。

    --case/--acceptance 模式此前 batch_id 恒 None，_backfill_zero_marks
    直接 return——标准段跳过时 verify_build 永远 missing（0904 三批实证）。
    回落识别后补零/mark 必落本批打点文件；多打点文件时 _resolve_batch_id
    静默跳过，防误标其他批次。读取失败按未提供处理（回落继续）。
    """
    if batch_file:
        try:
            bid = batch_id_from_text(
                Path(batch_file).read_text(encoding="utf-8"))
            if bid:
                return bid
        except (OSError, UnicodeDecodeError):
            pass
    return _resolve_batch_id(None)


def _hostcmd_env(run_id):
    """按 run_id 构造 hostcmd 子进程环境：导出轮次隔离的基线文件路径。

    方向 2：run_id 每次执行唯一，LCVIEW_BASELINE_FILE / LCIOD_BASELINE_FILE
    随之唯一——baseline/delta 同轮同路径、跨轮隔离（原固定 /tmp 路径使轮次
    隔离实际未生效）。
    """
    env = dict(os.environ)
    env["LCVIEW_BASELINE_FILE"] = f"/tmp/lcview_baseline_{run_id}.json"
    env["LCIOD_BASELINE_FILE"] = f"/tmp/lciod_baseline_{run_id}.json"
    return env


def _device_serial(adb_exec):
    """设备身份标识（产物用）：ro.serialno → ro.boot.serialno → eth0 MAC 依次
    回落；三者皆空返回 (None, "")（调用方判红）。eth0 MAC 须命令成功且非空。"""
    for prop in ("ro.serialno", "ro.boot.serialno"):
        body, _ = adb_exec(f"getprop {prop}")
        v = (body or "").strip()
        if v:
            return v, f"getprop {prop}"
    body, rc = adb_exec("cat /sys/class/net/eth0/address")
    mac = (body or "").strip()
    if rc == 0 and mac:
        return mac, "eth0 MAC"
    return None, ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="验收执行器")
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("run")
    p.add_argument("--acceptance", default=None, help="验收文本（含标签）")
    p.add_argument("--case", default=None,
                   help="验收用例标签（从 harness/config/verify-cases.yaml cases 段取，"
                        "支持逗号分隔多用例，含空格/引号命令在此书写）")
    p.add_argument("--batch-file", default=None,
                   help="CDP 批次文件（经 cdp_parse 解析取验收文本，-sv 批次用）")
    p.add_argument("--ensure-boot", action="store_true",
                   help="验收无 boot 标签时自动追加 boot（模式 B 默认）")
    p.add_argument("--wait-ready", action="store_true",
                   help="连接后轮询 sys.boot_completed 就绪（步骤 4 有 reboot 时必传）")
    p.add_argument("--log-since", default=None,
                   help="logcat 时间窗起点（MM-DD HH:MM:SS.mmm），代 -t 行数避免命中旧日志")
    p.add_argument("--result-file", default=None,
                   help="自描述验收产物 JSON 路径（原子写：run_id/输入摘要/"
                        "设备序列号/设备指纹/起止单调时间/逐项结果/总判定）")
    args = ap.parse_args(argv)

    acceptance, err = resolve_acceptance(args)
    if err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    try:
        parse_acceptance(acceptance)
    except ValueError as e:
        # 标签外残文本静默丢弃的防护：含残余（如被转义引号截断的命令）直接拒
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.log_since and not _SINCE_RE.match(args.log_since):
        # 非法时间窗起点会让 adb 静默返空 → 假 fail；格式不符直接拒
        print(f"error: --log-since 须为 MM-DD 或 YYYY-MM-DD 的 HH:MM:SS.mmm: "
              f"{args.log_since!r}", file=sys.stderr)
        return 2
    if args.wait_ready and not args.log_since:
        # 假绿精确条件强制拦截：reboot 后 log 窗口若含旧日志会命中上轮关键字。
        # log: 子串与 logfield: 锚点末行累计值同源风险（logfield 同样可能
        # 取到 reboot 前的旧累计行），须一并拦截。
        tags = parse_acceptance(acceptance)
        if any(t.startswith(("log:", "logfield:")) for t in tags):
            print("error: --wait-ready 且验收含 log:/logfield: 标签时必须传 "
                  "--log-since（log 窗口须从 reboot 时刻起，否则命中旧日志假绿）",
                  file=sys.stderr)
            return 2

    # batch_id 解析三级回落（batch-file > CDP_BATCH_ID > 唯一 timings 文件）：
    # --case 模式此前恒 None 致 _backfill_zero_marks 直接 return，标准段
    # 跳过时 verify_build 永远 missing（0904 三批实证）；回落识别与
    # _mark_stage 同口径，多打点文件时静默跳过防误标其他批次
    batch_id = _resolve_run_batch_id(args.batch_file)

    ep = ac.ensure_connected()
    if not ep:
        # 编排层接线 rescue（激活第三级通道）：mDNS/静态失败后以
        # rescue_enabled=True 重试一次——重启 adbd 有副作用，仅此失败路径触发；
        # 失败现场由 ensure_connected 内部 [rescue] 打印
        ep = ac.ensure_connected(rescue_enabled=True)
    if not ep:
        print(json.dumps({"overall": "fail", "error": "设备不可达",
                          "items": []}, ensure_ascii=False))
        return 1
    _mark_stage("verify_acceptance_connect", batch_id)
    if args.wait_ready and not ac.ensure_ready(endpoint=ep):
        print(json.dumps({"overall": "fail",
                          "error": "设备未就绪（sys.boot_completed 超时，按不可达处理）",
                          "items": []}, ensure_ascii=False))
        return 1
    if args.wait_ready:
        _mark_stage("verify_acceptance_wait_ready", batch_id)
    # 时钟校准触发条件：--wait-ready（reboot 场景）或验收含 ts/fresh 判据
    # （时间敏感判据须设备时钟可信，否则 skew 大必判红——曾因只挂 wait_ready
    #  门禁，无 reboot 的 fresh/ts 用例从不校准时钟而恒红）
    need_clock = args.wait_ready or any(
        "--mode fresh" in p or "--mode ts" in p
        for _, p in (split_tag(t) for t in parse_acceptance(acceptance))) or any(
        t.startswith("logfresh") for t in parse_acceptance(acceptance))
    if need_clock:
        # 就绪后校准设备时钟：偏差超阈值自动 root 修正（PIT-5 复发防护，
        # 避免 ts/fresh 等时间敏感验收因时钟漂移误判）
        ok, detail = ac.clock_sync(endpoint=ep)
        if not ok:
            print(json.dumps({"overall": "fail",
                              "error": f"设备时钟修正失败: {detail}",
                              "items": []}, ensure_ascii=False))
            return 1
        _mark_stage("verify_acceptance_clock_sync", batch_id)

    def adb_exec(cmd):
        try:
            # exec 带 -s 定向本端点：多 serial 残留时缺 -s 会被 adb 以
            # "more than one device" 拒绝（假红），贯穿 ensure_connected 的 ep
            r = subprocess.run(ac.build_exec_cmd(cmd, endpoint=ep),
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               timeout=60)
            return ac.parse_exec_output(r.stdout)
        except subprocess.TimeoutExpired:
            return "", -1

    # --log-since 由本地（CST）书写而设备时钟/时区多为 UTC：直接传会让
    # logcat 时间窗落在设备"未来"取回 0 字符判红（PIT-5 同源，复发三次），
    # 按设备时钟/时区换算后再用；换算失败按设备不可达处理（判据不可信）
    device_since = args.log_since
    if args.log_since:
        device_since, err = convert_since_to_device(args.log_since, adb_exec)
        if err:
            print(json.dumps({"overall": "fail", "error": err, "items": []},
                             ensure_ascii=False))
            return 1
        print(f"NOTE: --log-since 本地 {args.log_since} → 设备 {device_since}"
              f"（按设备时钟/时区换算）")
        _mark_stage("verify_acceptance_since_convert", batch_id)

    # logcat 缓存：key=(pid, device_since)——同批多标签（如 liveness 的 log:
    # + 5 条同 pid logfield）只拉一次 5000 行，避免各拉一遍拖慢验收段；
    # force=True 绕过缓存重取（logfield 5 段轮询语义须实时，走缓存永远
    # 读旧内容死等到 90s 超时判红）；超时（""）不缓存，下次重拉
    # （缓存空串会放大单次故障影响面，判据语义不得放宽）
    _logcat_cache = {}

    def adb_logcat(pid=None, force=False):
        key = (pid, device_since)
        if not force and key in _logcat_cache:
            return _logcat_cache[key]
        try:
            r = subprocess.run(ac.build_logcat_cmd(None, 5000,
                                                   since=device_since,
                                                   pid=pid, endpoint=ep),
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=60)
            out = r.stdout
        except subprocess.TimeoutExpired:
            return ""
        _logcat_cache[key] = out
        return out

    # case 级打点：每项完成记 verify_acceptance_acc_<n>（n 为 1-based 序号），
    # 段耗时 = 相邻 mark 差，hostcmd 含 dd/sleep 的大头逐项可见
    def mark_case(n):
        _mark_stage(f"verify_acceptance_acc_{n}", batch_id)

    # 方向 1：run_id 提前到执行前生成，沿用到 hostcmd 环境与产物（不再收尾处
    # 新生成，保证 hostcmd 基线路径与产物 run_id 同源）
    # 方向 8：CDP_RUN_ID 环境变量优先（编排层每轮注入唯一值），缺省回退
    # uuid4——push/unit_test/acceptance 三产物须同轮同 run_id（ws_report
    # 按 run_id 一致核验同批产物，各自 uuid4 必失配，-sv 真机全链路首次
    # 暴露）；轮次隔离由编排层每轮换新 CDP_RUN_ID 维持
    run_id = os.environ.get("CDP_RUN_ID") or uuid.uuid4().hex
    host_env = _hostcmd_env(run_id)
    t_start = time.monotonic()
    # 生命周期编排（方向 1/2/4）：--case 单标签且资产为 dict 形态时启用
    # （setup_snapshot/teardown/timeout_s）。
    # 方向 5：批文件模式 case_labels 亦从批次验收行 case: 前缀提取（此前仅
    # --case 生效，批模式 args.case 空致 lifecycle 恒 None、teardown 恒不跑、
    # device_dirty 恒假）——批次 case:a 与 --case a 语义等价，同样启用
    # 生命周期。
    # 多 case 逐 case 跑（批次 7d41df8e24bf 方向 1）：此前多 case 批整体跳过
    # lifecycle，dict 形态 case 的 teardown 不跑而 device_dirty=False（假
    # 干净）——改逐 case 编排（case 间状态隔离，各跑各的 setup_snapshot →
    # 判据 → 取证 → teardown）；str 旧形态未登记 setup_snapshot（无 teardown
    # 责任面，与 _restore_state 无快照同口径），不参与 device_dirty 汇总；
    # 任一 case teardown 恢复失败（dirty=true）优先透传。
    case_labels = [c.strip() for c in (
        args.case or _batch_case_labels(args.batch_file)).split(",") if c.strip()]
    # 资产层预解析（参数/资产书写错误显式返 2/1 不落盘，与既有错误语义一致；
    # 不并入最外层 try——finally 落盘不应改写此类显式返回的语义）
    lifecycle = None
    case_vals = []
    if len(case_labels) == 1:
        try:
            lifecycle = _load_lifecycle(_CASES_PATH, case_labels[0])
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
    elif len(case_labels) > 1:
        try:
            data = yaml.safe_load(_CASES_PATH.read_text(encoding="utf-8")) or {}
            cases_tbl = data.get("cases") or {}
        except (OSError, yaml.YAMLError) as e:
            print(f"error: verify-cases.yaml 读取失败: {e}", file=sys.stderr)
            return 1
        for label in case_labels:
            val = cases_tbl.get(label)
            try:
                acc_c = _case_text(val)
            except ValueError as e:
                print(f"error: {e}", file=sys.stderr)
                return 2
            case_vals.append((label, val, acc_c))
    # ── 执行主体（方向 3：逐 case 崩溃不中断整链；最外层 finally 必落盘）──
    # 单 case 执行体抛非预期异常（子命令异常/设备操作抛错等）曾中断整链致后续
    # case 不跑、result-file 不写（上板结果缺失又无失败现场 = 假证据/污染）。
    # 处理：case 循环体包 try/except，崩溃 case 记 fail + device_dirty=True
    # （设备态不可信，teardown 未跑）继续跑后续；执行主体任何未预期崩溃经
    # except 兜底记 fail/dirty；finally 统一落盘收尾，杜绝无 result-file 返回。
    overall, items = "fail", []
    device_dirty, teardown_detail, forensics_dir = True, "", None
    # 实跑 case 标签累积（方向 3）：逐 case 进入执行时记录，区别于请求标签
    # ——中断/异常提前退出时只落已实跑 case，防 cases json 记录未跑标签污染
    # report evidence-scope 推导；teardown_parts 同理跨分支累积，中断分支
    # 保留已跑部分（方向 4）
    ran_labels, teardown_parts = [], []
    try:
        if lifecycle:
            ran_labels.append(case_labels[0])
            overall, items, life_meta = run_case_lifecycle(
                acceptance, lifecycle, adb_exec, adb_logcat, ep=ep,
                ensure_boot=args.ensure_boot, on_item=mark_case, host_env=host_env,
                since_epoch=int(time.time()))
            device_dirty = life_meta["device_dirty"]
            teardown_detail = life_meta["teardown_detail"]
            forensics_dir = life_meta["forensics_dir"]
        elif len(case_labels) > 1:
            overall, items = "pass", []
            device_dirty = False
            forensics_dir = None
            for label, val, acc_c in case_vals:
                ran_labels.append(label)
                done = len(items)
                try:
                    if isinstance(val, dict):
                        ov, it, meta = run_case_lifecycle(
                            acc_c, val, adb_exec, adb_logcat, ep=ep,
                            ensure_boot=args.ensure_boot,
                            on_item=lambda n, off=done: mark_case(off + n),
                            host_env=host_env, since_epoch=int(time.time()))
                        if meta["device_dirty"] is True:
                            device_dirty = True
                        if forensics_dir is None and meta["forensics_dir"]:
                            forensics_dir = meta["forensics_dir"]
                        tdetail = meta["teardown_detail"]
                    else:
                        # str 旧形态未登记 setup_snapshot：无 teardown 责任面，
                        # device_dirty 落 False（与 _restore_state 无快照即无
                        # teardown 责任面同口径），不参与汇总标 dirty
                        ov, it = run_acceptance(
                            acc_c, adb_exec, adb_logcat,
                            ensure_boot=args.ensure_boot,
                            on_item=lambda n, off=done: mark_case(off + n),
                            host_env=host_env, endpoint=ep)
                        tdetail = "无生命周期资产（str 旧形态，无 setup_snapshot，无 teardown 责任面）"
                except Exception as e:
                    # 逐 case 崩溃（方向 3）：非预期异常不得中断整批 case 循环
                    # ——该 case 记 fail 并置 device_dirty=True（设备态不可信，
                    # teardown 未跑），继续跑后续 case；崩溃现场随 item 与
                    # teardown_detail 落收据（与既有 teardown 失败标 dirty 同
                    # 一口径：设备态不可信即 dirty）
                    ov = "fail"
                    it = [{"tag": "__crash__", "status": "fail",
                           "detail": f"case {label!r} 执行崩溃: {e!r}"}]
                    device_dirty = True
                    tdetail = (f"case 执行崩溃（{e!r}），teardown 未跑，"
                               "按 device_dirty 处理")
                teardown_parts.append(f"[{label}] {tdetail}")
                overall = _merge_overall(overall, ov)
                items.extend(it)
            teardown_detail = "；".join(teardown_parts)
        else:
            if case_labels:
                ran_labels.append(case_labels[0])
            overall, items = run_acceptance(acceptance, adb_exec, adb_logcat,
                                            ensure_boot=args.ensure_boot,
                                            on_item=mark_case, host_env=host_env,
                                            endpoint=ep)
            if case_labels:
                # 单 str case 未登记 setup_snapshot：无 teardown 责任面（与
                # _restore_state 无快照同口径），device_dirty 落 False
                device_dirty = False
                teardown_detail = "无生命周期资产（str 旧形态，无 setup_snapshot，无 teardown 责任面）"
            else:
                device_dirty = False
                teardown_detail = ""
            forensics_dir = None
    except Exception as e:
        # 执行主体兜底（方向 3）：case 循环外的未预期崩溃（如单 case 生命周期
        # 编排抛异常）也不得无 result-file 返回——记 fail/dirty 后落 finally
        # 统一收尾；异常类型与消息打印 stderr 留失败现场
        print(f"error: 验收执行主体异常（已按 fail/dirty 收尾，result-file "
              f"仍落盘）: {e!r}", file=sys.stderr)
        overall = "fail"
        items = [{"tag": "__crash__", "status": "fail",
                  "detail": f"验收执行主体未预期崩溃: {e!r}"}]
        device_dirty = True
        teardown_detail = f"验收执行主体异常，设备态不可信: {e!r}"
        forensics_dir = None
    except BaseException as e:
        # 中断路径（方向 1，2026-09-08）：KeyboardInterrupt/SystemExit 等
        # 非 Exception 中断不得被 finally 内 return 吞成 pass/正常退出假证据
        # ——先记 fail 与 device_dirty 真（中断点 teardown 未保证、后续 case
        # 未跑，产物不得 overall pass），再 re-raise 让中断继续向上传播；
        # finally 只落盘不 return，失败现场随 fail 产物落盘。
        # 方向 4：已跑 teardown_parts 保留不整体覆盖——中断前已完成的逐 case
        # teardown 明细仍随收据可见，中断说明拼在其后（未跑部分归中断项）
        print(f"error: 验收执行被中断（{e!r}），已记 fail/dirty 收尾，"
              f"中断继续向上传播", file=sys.stderr)
        overall = "fail"
        items = items + [{"tag": "__interrupt__", "status": "fail",
                          "detail": f"验收执行被中断（未跑完）: {e!r}"}]
        device_dirty = True
        ran_detail = "；".join(teardown_parts)
        teardown_detail = ((ran_detail + "；") if ran_detail else "") + \
            f"验收被中断（{e!r}），teardown 未保证，按 dirty 处理"
        raise
    finally:
        # 收尾只落盘 + 打点，绝不 return（方向 1）：finally 内 return 会
        # (a) 吞掉传播中的中断/异常成假证据；(b) 使失败现场产物缺失。返回码
        # 统一改到 try 末尾（下方按 overall 出口）。_device_serial/getprop
        # 各包 try/except（方向 2）：设备身份获取失败记 unknown 不阻断落盘，
        # 判红经 overall=fail 表达而不再裸退（产物仍落盘留失败现场）。
        identity_notes = []
        if args.result_file:
            # 方向 1/3：自描述验收产物——run_id/输入摘要/设备序列号/设备指纹/
            # 起止单调时间/逐项结果/总判定；原子写防半截文件被当证据
            try:
                serial, serial_src = _device_serial(adb_exec)
            except Exception as e:
                serial, serial_src = None, ""
                identity_notes.append(f"设备序列号获取异常: {e!r}")
            if not serial:
                # 方向 3：设备身份标识三者皆空即判红（产物身份不可信），但
                # 不再裸 return——身份不可信记入 fail 产物留失败现场
                serial, serial_src = "unknown", ""
                identity_notes.append("设备身份标识获取失败（ro.serialno/"
                                      "ro.boot.serialno/eth0 MAC 皆空），判红")
            try:
                fprint = adb_exec("getprop ro.build.fingerprint")[0].strip()
            except Exception as e:
                fprint = ""
                identity_notes.append(f"设备指纹获取异常: {e!r}")
            if not fprint:
                # 设备指纹获取失败（空串）→ 判红（与 serial 全空判红同口径）：
                # 指纹是产物设备身份证据，空值产物不可信；记入 fail 产物留现场
                fprint = "unknown"
                identity_notes.append("设备指纹获取失败"
                                      "（ro.build.fingerprint 为空），判红")
            if identity_notes:
                overall = "fail"
                items = items + [{"tag": "__identity__", "status": "fail",
                                  "detail": "；".join(identity_notes)}]
            result = {
                "run_id": run_id,
                "input_summary": acceptance,
                "device_serial": serial,
                "device_serial_source": serial_src,
                # 方向 4：身份标识只认基镜像固化值，增量推送不改变，不能识别增量部署
                "identity_note": "设备身份标识只认基镜像（序列号/MAC 为烧录固化值），"
                                 "增量推送不改变该标识，不能用于识别增量部署",
                "device_fingerprint": fprint,
                "start_monotonic": round(t_start, 3),
                "end_monotonic": round(time.monotonic(), 3),
                "items": items,
                "overall": overall,
                # 方向 3：teardown 只恢复本轮实际改变的状态，失败即标 dirty
                #（ws_report 透传收据 header device_dirty）
                "device_dirty": device_dirty,
                "teardown_detail": teardown_detail,
                "forensics_dir": forensics_dir,
            }
            p = Path(args.result_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            # 统一原子写原语（批次四收敛：tmp 带 pid 防并发互写）
            sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "lib"))
            from verify_common import atomic_write_json
            atomic_write_json(p, result)
        print(json.dumps({"overall": overall, "items": items,
                          "device_dirty": device_dirty,
                          "teardown_detail": teardown_detail},
                         ensure_ascii=False, indent=2))
        # 本次实跑 case 标签落盘（逐 case 进入执行时累积 ran_labels，非请求
        # 标签全集：中断/异常提前退出只落已实跑 case，供 ws_report 自动探测
        # 补全，防 board pass 收据 cases 空/含未跑标签污染 evidence-scope）
        _write_cases(batch_id, ",".join(ran_labels))
        # 标准四段缺失补零（跳过段记 0，收据段完整可归因）+ 验收总段打点
        # （失败不阻断，结果 pass/fail 均记）
        _backfill_zero_marks(batch_id)
        _mark_stage("verify_acceptance", batch_id)
    # 返回码统一出口（方向 1）：原在 finally 内 return——中断传播中 finally
    # 若 return 会把 KeyboardInterrupt 吞成正常退出（假证据链），故返回码移到
    # try/except/finally 之后按 overall 判定；KeyboardInterrupt 路径已 re-raise，
    # 走不到此处，中断原样逃逸。
    if overall == "fail":
        return 1
    if overall == "ai":
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())