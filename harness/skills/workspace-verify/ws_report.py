"""verify 收据落盘：封装 cdp_receipt，按 verify 阶段汇总写 data/verify-results/。

用法（无子命令）：
  模式 A（apply 拉起，随批次）:
    ws_report.py --batch-file <cdp> [--target <12hex起点HEAD>] \
        --result pass|fail|skip --build ... --board ... \
        --acceptance "<逐项结果>" --elapsed <秒> --summary "<一句话>" \
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
"""
import argparse
import json
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
from cdp_receipt import Receipt, append_trend, write_receipt  # noqa: E402
from paths import env_path  # noqa: E402


_HEX12_RE = re.compile(r"^[0-9a-f]{12}$")


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
    ap.add_argument("--elapsed", type=int, default=0)
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

    if args.timings_file:
        # 读链路耗时打点（cdp_timing.py 采集），兼容两种结构：
        #   - 原始 start/mark 结构（有 marks）→ compute_segments 计算段耗时
        #   - finish 归档结构（有 segments）→ 直接用（AI 先 finish 再落收据也 OK）
        # 诊断数据缺失/非法仅 warn 降级（timings 置空），不阻断 push 主流程——
        # 区别于 --acceptance 的返 2 拒写（那是 promote 证据链，缺了有洞）
        try:
            from cdp_timing import compute_segments
            t = json.loads(Path(args.timings_file).read_text(encoding="utf-8"))
            if not isinstance(t, dict):
                raise ValueError("非 JSON 对象")
            if isinstance(t.get("segments"), list) and t["segments"]:
                segments = t["segments"]
            elif isinstance(t.get("marks"), list):
                segments = compute_segments(t)
            else:
                raise ValueError("缺 segments/marks（非 cdp_timing 打点结构）")
            out = {
                "batch_id": t.get("batch_id", ""),
                "wall_start": t.get("start_wall") or t.get("wall_start"),
                "wall_end": t.get("wall_end") or time.time(),
                "segments": segments,
            }
            args.timings = json.dumps(out, ensure_ascii=False, sort_keys=True)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            print(f"warn: --timings-file 读取失败，timings 置空: {e}", file=sys.stderr)
            args.timings = ""
    else:
        args.timings = ""

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
        if b.mode == "sv" and not args.acceptance:
            # -sv 批次必须带验收逐项证据，否则 promote 时 baseline 证据链有洞
            print("error: 模式 A -sv 批次必须传 --acceptance（步骤 5 逐项验收结果），"
                  "否则 baseline 证据链有洞", file=sys.stderr)
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
        # 冗余文本防线：pytest 摘要为 "<n> failed, <n> passed, <n> skipped in ..."
        # （数字在前），兼容 failed=3 / skipped: 2 的等号/冒号形态
        if re.search(r"\b([1-9]\d*)\s*failed\b", args.selfcheck) or \
                re.search(r"\bfailed\s*[=,: ]+\s*([1-9]\d*)", args.selfcheck):
            print("error: --selfcheck 含 failed 非零（带红落地，拒绝写收据）",
                  file=sys.stderr)
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
                 args.summary[:40], args.metrics)
    print(f"receipt: {path}")
    print(f"batch_id: {batch_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())