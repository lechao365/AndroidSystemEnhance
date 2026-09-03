"""verify 收据落盘：封装 cdp_receipt，按 verify 阶段汇总写 data/verify-results/。

用法（无子命令）：
  模式 A（apply 拉起，随批次）:
    ws_report.py --batch-file <cdp> [--target <12hex起点HEAD>] \
        --result pass|fail|skip --build ... --board ... \
        --acceptance-file "<自描述验收产物 JSON>" --unit-test-file "<自描述单测产物 JSON>" \
        --elapsed <秒> --summary "<一句话>" \
        [--body <正文文件>（CDP 原文+失败现场，必传见 SKILL）]
   模式 B（独立触发）:
    ws_report.py --target <12hex|dev|main> [--prefix manual|revert] \
        --result ... （同上；batch_id = <prefix>-<10位时间戳>）
  三指标结构化存档：--metrics "<JSON 对象>"（如性能采集的
  {"throughput_evs":328,"drain_ms_per_event":6.4,"daemon_rss_kb":5516}），
  写入收据 metrics 字段 + trend 行尾追加，供跨批 diff；缺省不写。
链路耗时打点：--timings-file <cdp_timing.py start/mark 原始打点文件>，经
   compute_segments 计算段耗时写入收据 timings 字段供 emit 定位耗时瓶颈；
   缺失/非法仅 warn 不阻断（诊断数据非验收证据）。
   兜底段语义（finish 两义）：compute_segments 末段名 "finish" 是"末个
   mark 到算段时刻"的兜底段，与 cdp_timing finish 子命令同名不同义。该段
   含自检/编排空转，不细分无法归因（15 笔 -s 收据兜底段 0.26~361.9s 乱跳
   而自检恒 11s 档）。本脚本解析打点前自发 mark report，收窄为纯写收据；
   selfcheck.py 跑完自发 mark apply_selfcheck，分离自检耗时。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# 本文件位于 harness/skills/workspace-verify/，parents[1] = harness/skills
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "cross-device" / "lib" / "python"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "lib"))
from cdp_parse import (SOFT_ERRORS, batch_id_from_text, parse_batch,  # noqa: E402
                       validate_batch)
from cdp_paths import log_apply_dir  # noqa: E402
from cdp_receipt import Receipt, append_trend, write_receipt  # noqa: E402
from paths import env_path  # noqa: E402


_HEX12_RE = re.compile(r"^[0-9a-f]{12}$")

# 链路已知段中带 verify 前缀的五段（verify_sync/build/push/unit_test/acceptance）：
# none 模式（-s/文档批）无 verify 环节，missing 判定按 verify_mode 取应有段集时
# 从 KNOWN_SEGMENTS 去掉这五段，避免 -s 批永远报 verify 段缺失（方向 1 收窄）。
_VERIFY_PREFIX_SEGMENTS = frozenset((
    "verify_sync", "verify_build", "verify_push",
    "verify_unit_test", "verify_acceptance",
))


def _mark_report(timings_file, batch_id):
    """自发 report 打点：解析打点前 mark 本批"收据解析+落盘"起点。

    兜底段语义：compute_segments 末段名 finish（与 finish 子命令同名
    两义——前者是"末个 mark 到算段时刻"的兜底段，后者是归档子命令），
    其耗时 = 末个 mark 到算段时刻，含自检与编排空转。15 笔 -s 收据兜底段
    在 0.26~361.9s 间乱跳而自检恒 11s 档即因此（收据在 push 之前落盘）。
    自发 mark report 后兜底段收窄为"report → 算段时刻"= 纯写收据耗时。
    目标文件与 timings 探测同源：显式 --timings-file 优先，未传自动探测
    log_apply_dir()/timings-<batch_id>.json；文件缺失/非法仅 warn 不阻断
    （打点诊断数据，非收据证据本身）。直接编辑文件（cdp_timing mark 仅
    支持 --batch 定位，无法覆盖显式 --timings-file 场景）。
    """
    target = timings_file
    if not target and batch_id:
        probe = log_apply_dir() / f"timings-{batch_id}.json"
        if probe.is_file():
            target = str(probe)
    if not target:
        return
    try:
        p = Path(target)
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("非 JSON 对象")
        data.setdefault("marks", []).append(
            {"name": "report", "wall": time.time()})
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(p)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"warn: report 打点失败（不阻断）: {e}", file=sys.stderr)


def _resolve_timings(timings_file, batch_id, verify_mode="board"):
    """解析链路耗时打点，返回 (timings_json_str, elapsed_int|None)。

    显式 --timings-file 优先；未传时自动探测 log_apply_dir() 下
    timings-<batch_id>.json（cdp_paths 绝对路径，与 cdp_timing.py 写入同源，
    认 CDP_PROJECT_ROOT，不依赖 cwd），存在即用。
    缺失/非法仅 warn 降级（timings 置空，elapsed 不推导），不阻断主流程。
    elapsed 从 wall_end 减 wall_start 取整（start/mark 结构 wall_end 缺省
    按当前时刻兜底），供 --elapsed 缺省时填写 elapsed_s（显式传参优先）。
    missing 判定按 verify_mode 取应有段集：none 模式（-s/文档批）无 verify
    环节，去掉 verify_* 五段；board 模式（上板验证批）为全表（方向 1 收窄）。
    """
    if not timings_file and batch_id:
        probe = log_apply_dir() / f"timings-{batch_id}.json"
        if probe.is_file():
            timings_file = str(probe)
            print(f"NOTE: 自动探测到打点文件: {probe}", file=sys.stderr)
    if not timings_file:
        print(f"warn: 未传 --timings-file 且未探测到 timings-{batch_id}.json，"
              "timings 置空", file=sys.stderr)
        return "", None
    try:
        from cdp_timing import CONDITIONAL_SEGMENTS, KNOWN_SEGMENTS, \
            compute_segments
        t = json.loads(Path(timings_file).read_text(encoding="utf-8"))
        if not isinstance(t, dict):
            raise ValueError("非 JSON 对象")
        if isinstance(t.get("segments"), list) and t["segments"]:
            segments = t["segments"]
        elif isinstance(t.get("marks"), list):
            segments = compute_segments(t)
        else:
            raise ValueError("缺 segments/marks（非 cdp_timing 打点结构）")
        wall_start = t.get("start_wall") or t.get("wall_start")
        wall_end = t.get("wall_end") or time.time()
        # 缺段可见性：应有段集在两种模式下均先减 CONDITIONAL_SEGMENTS
        # （edit_validate/gen_manifest/edit_plan/edit_retry 未产出不判缺），
        # none 模式再减 verify_* 五段（无 verify 环节）；缺失者以 missing 键
        # 写入收据 timings（emit 一眼看出哪些链路段没打点）；多余段
        # （finish/push 等表外名）不删，耗时原样保留可归因。
        names = {s.get("name") for s in segments
                 if isinstance(s, dict) and s.get("name")}
        expected = KNOWN_SEGMENTS - CONDITIONAL_SEGMENTS
        if verify_mode == "none":
            expected = expected - _VERIFY_PREFIX_SEGMENTS
        missing = sorted(expected - names)
        out = {
            "batch_id": t.get("batch_id", ""),
            "wall_start": wall_start,
            "wall_end": wall_end,
            "segments": segments,
            "missing": missing,
        }
        elapsed = None
        if isinstance(wall_start, (int, float)) and isinstance(wall_end, (int, float)):
            elapsed = int(wall_end - wall_start)
        return json.dumps(out, ensure_ascii=False, sort_keys=True), elapsed
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"warn: --timings-file 读取失败，timings 置空: {e}", file=sys.stderr)
        return "", None


def _trend_timing(timings_json, elapsed):
    """由收据 timings 提取 trend 行尾 timing JSON：{elapsed_s, segs（段名→秒数）}。

    timings 为空（无打点）或非法时返回空串（append_trend 不追加）；有打点
    则恒带 elapsed_s 与 segs 两键（segs 可能为空映射），供 emit 从 trend
    直读各批耗时瓶颈。
    """
    if not timings_json:
        return ""
    segs = {}
    try:
        t = json.loads(timings_json)
        for s in t.get("segments") or []:
            if isinstance(s, dict) and s.get("name"):
                segs[s["name"]] = round(float(s.get("elapsed_s", 0)), 3)
    except (json.JSONDecodeError, TypeError, ValueError):
        return ""
    return json.dumps({"elapsed_s": elapsed, "segs": segs},
                      ensure_ascii=False, separators=(",", ":"))


def _resolve_cases(cases_arg, batch_id):
    """解析收据 cases 字段（本次实跑用例标签）。

    显式 --case 优先；未传时自动探测 log_apply_dir()/cases-<batch_id>.json
    （cdp_paths 绝对路径，与 _resolve_timings/timings 探测同源；该文件由
    ws_acceptance 验收后写入本次实跑标签）。探测到即补全，缺失仅 warn
    降级（返回原值），不阻断——空 cases 阻断语义由调用方按
    verify_mode/result 组合判定（board+pass 空 cases 拒写）。
    """
    if (cases_arg or "").strip():
        return cases_arg
    if not batch_id:
        return cases_arg
    probe = log_apply_dir() / f"cases-{batch_id}.json"
    if not probe.is_file():
        print(f"warn: 未传 --case 且未探测到 cases-{batch_id}.json，"
              "cases 置空", file=sys.stderr)
        return cases_arg
    try:
        data = json.loads(probe.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not (data.get("cases") or "").strip():
            raise ValueError("cases 字段为空或非对象")
        print(f"NOTE: 自动探测到 cases 文件: {probe}", file=sys.stderr)
        return data["cases"].strip()
    except (OSError, json.JSONDecodeError, ValueError) as e:
        print(f"warn: cases 探测读取失败，置空: {e}", file=sys.stderr)
        return cases_arg


def _validate_acceptance_pass(acceptance):
    """result=pass 时验收证据门禁：解析 acceptance JSON，overall 须为 pass 且
    无 fail 项，否则拒写（堵手填假绿混过 promote——仅查有无不看内容是洞）。

    兼容两种结构（ws_acceptance.run 输出 {"overall","items"} 与历史数组格式
    [{...}]）：overall 缺失的数组格式按「存在 fail 项」判定。
    返回 (parsed, err)：err 非 None 时拒写（parsed 为 None）。
    """
    if not acceptance.strip():
        return None, "result=pass 必须传 --acceptance（逐项验收 JSON）"
    try:
        data = json.loads(acceptance)
    except (ValueError, json.JSONDecodeError) as e:
        return None, f"--acceptance 须为合法 JSON（解析失败: {e}）"
    if isinstance(data, dict):
        if data.get("overall") != "pass":
            return None, (f"acceptance overall 非 pass（实际 {data.get('overall')!r}），"
                          "拒绝写 pass 收据")
        items = data.get("items") or []
    elif isinstance(data, list):
        items = data
    else:
        return None, "--acceptance 须为 JSON 对象或数组"
    for it in items:
        if isinstance(it, dict) and it.get("status") == "fail":
            return None, "acceptance 含 fail 项（假绿），拒绝写 pass 收据"
    return data, None


def _norm_text(text: str) -> str:
    """规范化空白用于文本比对（产物 input_summary 与批次验收文本）。"""
    return " ".join((text or "").split())


def _validate_acceptance_file(path, expect_summary):
    """result=pass 时验收证据门禁（方向 1/3）：只接受自描述验收产物文件。

    校验：文件存在且为合法 JSON、run_id 非空、input_summary 与本批验收文本
    一致、单调时间表达新鲜度（start<end，不用固定墙钟，长编译/重试不误伤）、
    overall=pass 且无 fail 项。返回 (parsed, err)：err 非 None 拒写。
    """
    if not (path or "").strip():
        return None, "result=pass 必须传 --acceptance-file（自描述验收产物 JSON 路径）"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        return None, f"--acceptance-file 读取失败（{e}）"
    except (ValueError, json.JSONDecodeError) as e:
        return None, f"--acceptance-file 须为合法 JSON（解析失败: {e}）"
    if not isinstance(data, dict):
        return None, "--acceptance-file 须为 JSON 对象（自描述验收产物）"
    if not (data.get("run_id") or "").strip():
        return None, "--acceptance-file 缺 run_id（产物身份缺失），拒绝 PASS"
    summary = (data.get("input_summary") or "").strip()
    if not summary:
        return None, "--acceptance-file 缺 input_summary（输入摘要缺失），拒绝 PASS"
    # 输入摘要与本批一致：验收文本为「无」/空（-s 批本无验收）时跳过比对，
    # 真 -sv 批（验收非「无」）强制一致，防陈旧/错批产物
    expect = _norm_text(expect_summary)
    if expect and expect != "无" and _norm_text(summary) != expect:
        return None, ("--acceptance-file 输入摘要与本批验收文本不一致，拒绝 PASS"
                      "（陈旧/错批产物）")
    # 单调时间表达新鲜度（方向 3）：start<end，不用固定墙钟判新旧
    st, en = data.get("start_monotonic"), data.get("end_monotonic")
    if not isinstance(st, (int, float)) or not isinstance(en, (int, float)) \
            or not st < en:
        return None, "--acceptance-file 单调时间异常（start/end 缺失或未递增），拒绝 PASS"
    # 方向 6：复用既有判定（overall=pass 且无 fail 项），内嵌经校验的 JSON
    parsed, err = _validate_acceptance_pass(json.dumps(data, ensure_ascii=False))
    if err:
        return None, err
    return data, None


def _validate_unit_test_file(path, acceptance_run_id):
    """result=pass 时单测证据门禁（方向 2）：产物 run_id 与验收产物一致，
    且每个 target 全绿（rc==0 且 failed==0）。返回 (data, err)。"""
    if not (path or "").strip():
        return None, "result=pass 必须传 --unit-test-file（自描述单测产物 JSON 路径）"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as e:
        return None, f"--unit-test-file 读取失败（{e}）"
    except (ValueError, json.JSONDecodeError) as e:
        return None, f"--unit-test-file 须为合法 JSON（解析失败: {e}）"
    if not isinstance(data, dict) or "targets" not in data:
        return None, "--unit-test-file 须为 JSON 对象且含 targets（自描述单测产物）"
    rid = (data.get("run_id") or "").strip()
    if not rid:
        return None, "--unit-test-file 缺 run_id，拒绝 PASS"
    if acceptance_run_id and rid != acceptance_run_id:
        return None, "--unit-test-file run_id 与验收产物不一致（非同批产物），拒绝 PASS"
    for t in data.get("targets") or []:
        if not isinstance(t, dict):
            return None, "--unit-test-file 含非法 target 项，拒绝 PASS"
        if t.get("rc") != 0 or t.get("failed") != 0:
            return None, (f"--unit-test-file target {t.get('name')!r} 非全绿"
                          f"（rc={t.get('rc')} failed={t.get('failed')}），拒绝 PASS")
    return data, None


def _resolve_target(target: str):
    """把 --target 解析为 12hex commit，返回 (resolved, err)。

    dev/main 等描述经 git rev-parse 换算；promote 门禁以 verified_commit 比对
    HEAD^，解析失败不得写空串蒙混（比对不等空串恒失败），err 非 None 时
    调用方必须拒绝写收据（照模式 A 校验失败同款返 2）。
    """
    if not target or _HEX12_RE.match(target):
        return target, None
    try:
        r = subprocess.run(["git", "rev-parse", "--short=12", target],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=10)
    except (OSError, subprocess.TimeoutExpired) as e:
        return "", f"无法解析 --target {target!r}（git 执行失败: {e}）"
    if r.returncode != 0:
        return "", f"无法解析 --target {target!r}（git rev-parse 退出 {r.returncode}）"
    return r.stdout.strip(), None


def _sanitize(text: str) -> str:
    """简单脱敏：workspace 绝对路径 → <KEY> 占位符，家目录绝对路径 → ~。

    workspace 路径先于家目录正则替换（其本身含 /home/<user>/，若先脱家目录
    会破坏原始路径导致占位符失效）。
    """
    for key in ("KERNEL_WS", "AOSP_WS"):
        val = env_path(key)
        if val:
            text = re.sub(re.escape(val), f"<{key}>", text)
    text = re.sub(r"/home/[A-Za-z0-9_.-]+", "~", text)
    text = re.sub(r"[A-Za-z]:\\+Users\\+[A-Za-z0-9_.-]+", "~", text)
    return text


def main(argv=None):
    ap = argparse.ArgumentParser(description="verify 收据落盘")
    ap.add_argument("--batch-file", help="CDP 批次文件（模式 A）")
    ap.add_argument("--target", help="验证目标：12hex commit（模式 B 亦可用 dev/main 描述）")
    ap.add_argument("--prefix", choices=["manual", "revert"], default="manual",
                    help="模式 B 的 batch_id 前缀（revert 恢复验证用 revert）")
    ap.add_argument("--result", choices=["pass", "fail", "skip", "revert"], required=True)
    ap.add_argument("--build", choices=["pass", "fail", "skip"], default="skip")
    ap.add_argument("--board", choices=["pass", "fail", "skip"], default="skip")
    ap.add_argument("--acceptance", default="")
    ap.add_argument("--acceptance-file", default="",
                    help="自描述验收产物 JSON 路径（PASS 只接受此来源；run_id/"
                         "输入摘要/单调时间校验，缺失或不一致即拒 PASS）")
    ap.add_argument("--unit-test-file", default="",
                    help="自描述单测产物 JSON 路径（PASS 必需；run_id 与验收产物"
                         "一致且每个 target 全绿）")
    ap.add_argument("--elapsed", type=int, default=None,
                    help="耗时秒数；缺省从 timings 的 wall_end-wall_start 推导"
                         "（推导不出则 0），显式传参优先")
    ap.add_argument("--summary", default="")
    ap.add_argument("--case", default="",
                    help="本次实际验收用例标签（逗号分隔；写入收据 cases 字段，"
                         "供 baseline_register 推导 evidence-scope）")
    ap.add_argument("--selfcheck", default="",
                    help="自检摘要文本（-s 批次必带：pytest harness -q 与 "
                         "check_skill_refs 输出；含 failed 非零或缺 skipped 计数即拒写）")
    ap.add_argument("--metrics", default="",
                    help="三指标结构化 JSON 对象（写入收据 metrics 字段与 trend 行尾）")
    ap.add_argument("--timings-file", default="",
                    help="链路耗时打点文件路径（cdp_timing.py finish 产物；写入收据 "
                         "timings 字段供 emit 定位耗时瓶颈；缺失/非法仅 warn 不阻断）")
    ap.add_argument("--body", help="正文文件路径（CDP 原文/失败现场），经脱敏写入")
    args = ap.parse_args(argv)

    if args.metrics:
        # 校验为合法 JSON 对象并规范化（排序键便于 diff），非法直接拒写
        try:
            m = json.loads(args.metrics)
            if not isinstance(m, dict):
                raise ValueError("非 JSON 对象")
            args.metrics = json.dumps(m, ensure_ascii=False, sort_keys=True)
        except (ValueError, json.JSONDecodeError) as e:
            print(f"error: --metrics 须为合法 JSON 对象: {e}", file=sys.stderr)
            return 2

    if not args.batch_file and not args.target:
        print("error: 模式 A（--batch-file）与模式 B（--target）必选其一", file=sys.stderr)
        return 2

    if args.batch_file:
        if not args.body:
            print("error: 模式 A 必须传 --body（CDP 原文+失败现场）", file=sys.stderr)
            return 2
        if not Path(args.body).is_file():
            print(f"error: --body 文件不存在: {args.body}", file=sys.stderr)
            return 2
        text = Path(args.batch_file).read_text(encoding="utf-8")
        code, errs = validate_batch(text, role="apply")
        if code != 0:
            # apply 角色下 SOFT_ERRORS（如验收规则违规 17）降级为 WARN 不阻断，
            # 与 cdp_parse.main 的降级语义一致（硬失败会卡死首个 -sv）
            softened = code in SOFT_ERRORS
            for e in errs:
                level = "warn" if softened else "error"
                print(f"{level}: 批次校验失败: {e}", file=sys.stderr)
            if not softened:
                return 2
        b = parse_batch(text)
        if b.mode == "sv" and not args.acceptance and not args.acceptance_file:
            # -sv 批次必须带验收逐项证据，否则 promote 时 baseline 证据链有洞
            # （PASS 走产物文件 --acceptance-file，fail 仍可用 --acceptance 直传）
            print("error: 模式 A -sv 批次必须传 --acceptance/--acceptance-file"
                  "（步骤 5 逐项验收结果），否则 baseline 证据链有洞", file=sys.stderr)
            return 2
        batch_id = batch_id_from_text(text)
        batch_base = b.base
        verify_mode = "board" if b.mode == "sv" else "none"
        # verified_commit = 验证起点 HEAD（= 该批 commit 的 parent）；
        # apply 链路中 HEAD 未动（编辑未提交），故等于 base；显式 --target 优先
        # （模式 A 同走 _resolve_target：非 12hex 描述须换算，失败拒写）
        verified, terr = _resolve_target(args.target or b.base)
        if terr:
            print(f"error: {terr}", file=sys.stderr)
            return 2
    else:
        batch_id = f"{args.prefix}-{time.strftime('%y%m%d%H%M')}"
        batch_base = args.target or ""
        # 模式 B：--board skip（如 revert 恢复验证未上板）时 verify_mode 取 none
        verify_mode = "board" if args.board != "skip" else "none"
        # dev/main 等描述须解析为 12hex，否则 promote 门禁比 HEAD^ 恒失败
        verified, terr = _resolve_target(args.target or "")
        if terr:
            print(f"error: {terr}", file=sys.stderr)
            return 2

    # 自发 report 打点：解析打点前 mark 本批收据解析+落盘起点，使兜底段
    # （末个 mark 到算段时刻）收窄为纯写收据耗时（自检/编排空转不再混入）
    _mark_report(args.timings_file, batch_id)
    # 链路耗时打点：显式 --timings-file 优先，未传自动探测
    # log_apply_dir()/timings-<batch_id>.json（cdp_paths 绝对路径与
    # cdp_timing 写入同源，认 CDP_PROJECT_ROOT）；
    # --elapsed 缺省从 timings 的 wall_end-wall_start 推导（显式传参优先）
    args.timings, derived_elapsed = _resolve_timings(args.timings_file, batch_id,
                                                     verify_mode)
    if args.elapsed is None and derived_elapsed is not None:
        args.elapsed = derived_elapsed
    if args.elapsed is None:
        args.elapsed = 0

    # cases 补全：显式 --case 优先，未传自动探测 cases-<batch_id>.json
    # （ws_acceptance 验收后写入本次实跑标签，与 timings 探测同源）
    args.case = _resolve_cases(args.case, batch_id)
    # board+pass 空 cases 拒写：空 cases 会让 prepare 的 evidence-scope
    # 推导无源而卡死（2026-09-02 BL-20260902-01 被迫回填 7833c640079a），
    # 堵住源头比事后改历史收据可靠——收据一经落盘即证据，禁事后修改
    if args.result == "pass" and verify_mode == "board" \
            and not (args.case or "").strip():
        print("error: verify_mode=board 且 result=pass 必须带 cases"
              "（--case 或 cases-<batch_id>.json 探测），空 cases 会使 "
              "prepare evidence-scope 推导死锁，拒绝写收据", file=sys.stderr)
        return 2

    # 验收证据门禁：result=pass 只接受自描述验收产物文件（--acceptance-file），
    # 校验 run_id/输入摘要/单调时间且整体通过，否则拒写；单测产物（--unit-test-file）
    # 为必需且 run_id 一致、每 target 全绿。通过后单行化落盘——header 逐行
    # key-value，多行 JSON 会被 from_text 只读首行截断，单行化保证 apply_done
    # 能读全（方向 6：收据内嵌经校验的 acceptance JSON，保 _acceptance_passed 不断）
    if args.result == "pass":
        if args.acceptance:
            print("error: result=pass 已改为只接受 --acceptance-file"
                  "（自描述验收产物），--acceptance 直传 JSON 已废弃", file=sys.stderr)
            return 2
        parsed, acc_err = _validate_acceptance_file(args.acceptance_file,
                                                    b.acceptance if args.batch_file else "")
        if acc_err:
            print(f"error: {acc_err}", file=sys.stderr)
            return 2
        ures, ut_err = _validate_unit_test_file(args.unit_test_file,
                                                (parsed or {}).get("run_id"))
        if ut_err:
            print(f"error: {ut_err}", file=sys.stderr)
            return 2
        args.acceptance = json.dumps(parsed, ensure_ascii=False,
                                     separators=(",", ":"))

    # 自检证据（-s 批次必带，堵零验证通道）：对照 -sv 缺 --acceptance 返 2 的既有约束，
    # result=skip 而 selfcheck 为空即拒写。自检门禁以退出码为主判据（方向 1-5）：
    #   - 缺 pytest_rc/refs_rc 任一即返 2（rc 不可见则自检不可信）
    #   - 任一 rc 非零即返 2（pytest 崩溃/悬空引用均带 rc，文本可能无 failed/skipped）
    # failed 文本匹配与 skipped 计数保留作冗余（rc 全 0 后的补充防线）
    if args.result == "skip" and not args.selfcheck.strip():
        print("error: result=skip 必须传 --selfcheck（自检摘要：pytest harness -q 与 "
              "check_skill_refs 输出，含 pytest_rc/refs_rc），否则零验证通道敞开",
              file=sys.stderr)
        return 2
    if args.selfcheck.strip():
        rcs = {}
        for key in ("pytest_rc", "refs_rc"):
            m = re.search(rf"{re.escape(key)}=(\d+)", args.selfcheck)
            if not m:
                print(f"error: --selfcheck 缺 {key}（退出码为主判据，文本匹配仅冗余）",
                      file=sys.stderr)
                return 2
            rcs[key] = int(m.group(1))
        if rcs["pytest_rc"] != 0 or rcs["refs_rc"] != 0:
            print(f"error: --selfcheck 存在非零退出码（pytest_rc={rcs['pytest_rc']} "
                  f"refs_rc={rcs['refs_rc']}），自检未通过拒绝写收据", file=sys.stderr)
            return 2
        # 冗余文本防线（rc 全 0 后的补充）：rc 为 0 而文本仍含 failed 非零/
        # 悬空引用字样即矛盾——两工具已败却报 rc=0，拒写防伪造。
        # pytest 摘要为 "<n> failed, <n> passed, <n> skipped in ..."（数字在前），
        # 兼容 failed=3 / skipped: 2 的等号/冒号形态
        if re.search(r"\b([1-9]\d*)\s*failed\b", args.selfcheck) or \
                re.search(r"\bfailed\s*[=,: ]+\s*([1-9]\d*)", args.selfcheck):
            print("error: --selfcheck 含 failed 非零（带红落地，拒绝写收据）",
                  file=sys.stderr)
            return 2
        if re.search(r"悬空引用", args.selfcheck):
            print("error: --selfcheck 含悬空引用字样（引用完整性未通过，"
                  "拒绝写收据）", file=sys.stderr)
            return 2
        if not (re.search(r"\b\d+\s*skipped\b", args.selfcheck)
                or re.search(r"\bskipped\s*[=,: ]+\s*\d+", args.selfcheck)):
            print("error: --selfcheck 缺 skipped 计数（平台跳过的测试数须显式可见）",
                  file=sys.stderr)
            return 2

    body = ""
    if args.body and Path(args.body).is_file():
        body = _sanitize(Path(args.body).read_text(encoding="utf-8"))

    # selfcheck 单行化（header 逐行 key-value，多行正文信息须并入一行才可见）：
    # "531 passed in 27.9s | skipped=0 | OK: ..."，保证 skipped 计数随收据显式落地
    args.selfcheck = " | ".join(l for l in args.selfcheck.splitlines() if l.strip())

    r = Receipt(batch_id=batch_id, batch_base=batch_base,
                verified_commit=verified,
                verify_mode=verify_mode, result=args.result,
                build=args.build, push_board=args.board,
                acceptance=args.acceptance, elapsed_s=args.elapsed,
                summary=args.summary, metrics=args.metrics,
                timings=args.timings, cases=args.case,
                selfcheck=args.selfcheck)
    path = write_receipt(r, body or args.summary)
    append_trend(time.strftime("%Y-%m-%d %H:%M:%S"), batch_id, args.result,
                 f"build={args.build} board={args.board} "
                 f"acc={args.acceptance.splitlines()[0][:40] if args.acceptance else '-'}",
                 args.summary[:40], args.metrics,
                 timing=_trend_timing(args.timings, args.elapsed))
    print(f"receipt: {path}")
    print(f"batch_id: {batch_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())