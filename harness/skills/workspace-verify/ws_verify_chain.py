#!/usr/bin/env python3
# ============================================================
# ws_verify_chain.py — 上板验证确定性三步串联（sync→push→unit_test）
# 所属模块：workspace-verify — 编译产物上板验证
# 设计目的：三步间原为 AI 编排往返（收据 gap_before_verify_* 三段 ~55s/批）。
#   本脚本把确定性步骤串联为单次执行：逐段透传 stdout、rc 逐段门禁、
#   失败即停（后续步骤不执行），末尾输出自描述 JSON（run_id/逐段 rc 与
#   耗时/overall/skipped）。打点仍由各子脚本自发 mark（verify_sync/
#   verify_push/verify_unit_test 段口径不变）；acceptance 留在编排层
#   （参数动态、失败需 AI 归因）。
# 用法：python3 ws_verify_chain.py [--product rpi5] [--out <aosp out>]
#   [--result-file <json>]
# 退出码：0 三步全过 / 1 某步失败（JSON 标注停在哪步）/ 2 参数错误
# ============================================================

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
_SYNC = _SCRIPT_DIR.parent / "sync-code-to-workspace" / "sync_code_to_workspace.py"

# 链式步骤名序列（可注入单测）：真实 argv 由 _build_argv 按步骤名构造
_CHAIN_STEPS = ("sync", "push", "unit_test")


def _build_argv(name, product, out):
    """步骤名 → 子脚本 argv（各子脚本 --product/--out 均为真实支持的参数）。"""
    if name == "sync":
        # code→workspace 同步与 AOSP out 无关，仅 --auto
        return [sys.executable, str(_SYNC), "--auto"]
    if name == "push":
        cmd = [sys.executable, str(_SCRIPT_DIR / "ws_push.py"),
               "--product", product]
    elif name == "unit_test":
        cmd = [sys.executable, str(_SCRIPT_DIR / "ws_upload_tests.py"),
               "--product", product]
    else:
        raise ValueError(f"未知链式步骤: {name}")
    if out:
        cmd += ["--out", out]
    return cmd


def run_chain(product="rpi5", out=None, result_file=None):
    """顺序执行三步，返回 (rc, result_dict)。失败即停，余步记入 skipped。"""
    steps, skipped = [], []
    overall = "pass"
    for name in _CHAIN_STEPS:
        argv = _build_argv(name, product, out)
        t0 = time.monotonic()
        # 不 capture：子脚本 stdout/stderr 直通终端，rc 真实
        proc = subprocess.run(argv)
        steps.append({"name": name, "rc": proc.returncode,
                      "dur_s": round(time.monotonic() - t0, 3)})
        if proc.returncode != 0:
            overall = "fail"
            done = {s["name"] for s in steps}
            skipped = [n for n in _CHAIN_STEPS if n not in done]
            break
    result = {"run_id": os.environ.get("CDP_RUN_ID") or uuid.uuid4().hex,
              "steps": steps, "skipped": skipped, "overall": overall}
    if result_file:
        # 原子写：先落 tmp 再 os.replace，避免半写产物污染证据链
        p = Path(result_file)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        os.replace(tmp, p)
    return (0 if overall == "pass" else 1), result


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="上板验证确定性三步串联（sync→push→unit_test）")
    ap.add_argument("--product", default="rpi5")
    ap.add_argument("--out", default=None, help="AOSP out 目录（透传）")
    ap.add_argument("--result-file", default=None,
                    help="自描述链式产物 JSON（原子写）")
    args = ap.parse_args(argv)
    rc, result = run_chain(args.product, args.out, args.result_file)
    print(json.dumps(result, ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
