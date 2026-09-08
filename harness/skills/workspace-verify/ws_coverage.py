#!/usr/bin/env python3
# ============================================================
# ws_coverage.py — 覆盖率采集（P1-A，一期只记录不门禁）
# 设计目的：把 native_coverage: true 插桩从"摆设"变为收据证据——测试真跑
#   后采集 llvm 覆盖数据，产出覆盖率 JSON 随收据入库跨批可 diff。
# 一期语义（对齐 lcview-perf"只报数不设门禁"先例）：status 三态——
#   ok（有覆盖数据并算出）/ partial（有产物但 lcov 失败）/ unavailable
#   （无覆盖产物，如实标注不假绿）。不做阈值门禁。
# 降级路径：设备侧 .gcda 回传不可行时（无 adb/无 llvm-cov）→ 编译期静态
#   覆盖不可得 → status=unavailable 并注明原因，不阻断主流程。
# 用法：python3 ws_coverage.py [--product rpi5] [--out <aosp out>]
#   [--result-file <json>]
# 退出码：0 采集完成（含降级）/ 1 采集异常（不应发生） / 2 参数错误
# ============================================================

import argparse
import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path


def _run_lcov(args):
    """运行 lcov（llvm-cov 前端），返回 (rc, stdout)。缺失返 (1, "")。"""
    try:
        r = subprocess.run(["lcov"] + args, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=300)
        return r.returncode, r.stdout
    except FileNotFoundError:
        return 1, ""
    except subprocess.TimeoutExpired:
        return 1, ""


def _iter_gcda(products: list[Path]):
    """在 products 下找 .gcda（设备侧回传产物）与 .profraw（编译期插桩）。"""
    for base in products:
        if not base.is_dir():
            continue
        for p in base.rglob("*.gcda"):
            yield p
        for p in base.rglob("*.profraw"):
            yield p


def collect(out: Path, product: str = "rpi5") -> dict:
    """采集覆盖率，返回自描述 dict（status/targets/reason）。

    targets: {name: lines_pct}——gcda 所在 nativetest 目录名即 target 名。
    """
    product_dir = out / "target" / "product" / product
    gcda_files = list(_iter_gcda([product_dir]))
    if not gcda_files:
        return {"status": "unavailable",
                "reason": "无 .gcda/.profraw 覆盖产物（未启用 native_coverage "
                          "插桩或设备侧未回传），如实标注不门禁",
                "targets": {}}
    targets = {}
    status = "ok"
    for p in gcda_files:
        # gcda 所在 <nativetest>/<name>/ 目录名 = target 名
        name = p.parent.name if p.parent.parent.name in (
            "nativetest", "nativetest64", "testcases") else p.parent.name
        rc, out_text = _run_lcov([
            "--capture", "--directory", str(p.parent),
            "--output-file", "/dev/null", "--summary"])
        if rc == 0:
            m = re.search(r"lines?\.\.\.\.\.\.\s*([\d.]+)%", out_text)
            targets[name] = float(m.group(1)) if m else None
        else:
            status = "partial"
            targets[name] = None
    return {"status": status, "targets": targets, "reason": ""}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="覆盖率采集（P1-A 只记录不门禁）")
    ap.add_argument("--product", default="rpi5")
    ap.add_argument("--out", default="", help="AOSP out 目录")
    ap.add_argument("--result-file", default="",
                   help="自描述覆盖率产物 JSON 路径（原子写）")
    args = ap.parse_args(argv)
    out = Path(args.out) if args.out else Path("").resolve()
    data = collect(out, args.product)
    data["run_id"] = os.environ.get("CDP_RUN_ID") or uuid.uuid4().hex
    if args.result_file:
        from verify_common import atomic_write_json  # noqa: E402
        atomic_write_json(Path(args.result_file), data)
    print(json.dumps(data, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
