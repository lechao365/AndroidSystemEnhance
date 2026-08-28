#!/usr/bin/env python3
# ============================================================
# lciod_check.py — lciod 业务数据板端校验器（host 侧执行）
# 所属模块：workspace-verify — 业务验证用例资产
# 设计目的：通过 adb 在设备侧执行 lciod_probe（/system/bin，
#   内核 GET_STATS ioctl 取数工具），host 侧完成字段齐全性/
#   数值合法性/增量基线校验。设备侧无 python3，复杂解析全部
#   在 host 完成；设备侧只做单二进制最小操作。
#
# 模式：
#   stats    — 校验 probe 快照：≥1 设备、abi_version==2、字段齐全、
#              数值非负、vendor/product 非空（字段映射完整性回归点）
#   baseline — [--reset] 取快照存 --baseline（供 delta；
#              --reset 传给设备工具归零计数，delta 断言简化为绝对值）
#   delta    — 对比基线，--expect 字段必须严格增加（防假绿：
#              缺基线/缺字段/未增均判红）
#
# 退出码：0 校验通过 / 1 校验失败 / 2 设备不可达或参数错误
# ============================================================

import argparse
import json
import re
import sys
import time
from pathlib import Path

# 设备定位复用 ws_adb_connect（勿自建 adb 层）：host_port 默认 rp5.local:5555，
# 支持 LC_VERIFY_ADB_HOST/PORT 环境变量覆盖，mDNS 发现逻辑不在此重复实现
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from ws_adb_connect import (ensure_connected as ws_connected,  # noqa: E402
                            host_port, run_adb)

ADB_TARGET = host_port()
_EP = ADB_TARGET
BASELINE_DEFAULT = "/tmp/lciod_baseline.json"

# probe 输出必须齐全的字段（与 lciod_probe.c 输出、ioctl.h v2 ABI 对齐；
# 缺任一字段即字段映射回归，stats 模式判红）
REQUIRED_FIELDS = [
    "minor", "path", "vid", "pid", "vendor", "product",
    "read_bytes", "write_bytes", "read_ns", "write_ns",
    "read_cmds", "write_cmds",
    "error_count", "reset_count", "probe_count",
    "disconnect_count", "degrade_count",
    "current_rate", "peak_rate", "last_transport_latency_ns",
    "last_event_ts_ns", "last_update",
    "stall_count", "corrupt_count", "timeout_count",
    "last_event_type", "enabled", "flags", "event_drop_count",
    "abi_version",
]
# 非数值字段（vendor/product 可含空格引号包裹；path 为字符串）
_TEXT_FIELDS = {"path", "vendor", "product"}
_EXPECTED_ABI = "2"

# key=value 解析：vendor="SanDisk Corp" 等引号包裹值支持空格
_TOKEN_RE = re.compile(r'(\w+)=(?:"([^"]*)"|(\S+))')


def adb(args, timeout=60):
    """执行 adb 命令，返回 (stdout, returncode)。"""
    return run_adb(["-s", _EP] + args, timeout=timeout)


def ensure_connected():
    """adb 连接（复用 ws_adb_connect mDNS→静态 fallback）+ root + 探活。"""
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


def parse_probe_output(text):
    """解析 lciod_probe 输出（每设备一行 key=value）→ dict 列表。

    缺 minor/path 的残缺行抛 ValueError——残缺数据不得静默当有效
    快照（防假绿），由调用方转 exit 1。
    """
    devices = []
    for ln, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        fields = {}
        for m in _TOKEN_RE.finditer(line):
            fields[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
        if "minor" not in fields or "path" not in fields:
            raise ValueError(f"probe 行 {ln} 缺 minor/path: {line[:100]}")
        devices.append(fields)
    return devices


def run_probe(extra_args=None):
    """设备侧执行 lciod_probe，返回 stdout。失败/超时按语义退出。"""
    out, rc = adb(["shell", "lciod_probe"] + (extra_args or []))
    if rc == -1:
        # adb 超时透传：不得当"无输出"假绿（对齐 lcview_check files 模式）
        print("ERROR: adb 执行 lciod_probe 超时")
        sys.exit(2)
    if rc != 0:
        print(f"ERROR: lciod_probe 退出码 {rc}（open/ioctl 失败）")
        sys.exit(1)
    return out


def validate_devices(devices):
    """stats 模式校验 → 错误列表（空 = 通过）。

    判红项：零设备 / 字段缺失 / 数值字段非数字或负值 /
    vendor·product 为空 / abi_version != 2（镜像副本与内核真相源漂移）。
    """
    errors = []
    if not devices:
        return ["probe 输出为空：无 usbd 设备（内核驱动/HAL 未就绪？）"]
    numeric = [f for f in REQUIRED_FIELDS if f not in _TEXT_FIELDS]
    for i, dev in enumerate(devices):
        tag = f"device#{i}(minor={dev.get('minor', '?')})"
        missing = [f for f in REQUIRED_FIELDS if f not in dev]
        for f in missing:
            errors.append(f"{tag}: 缺字段 {f}")
        for f in numeric:
            if f in missing:
                continue
            try:
                if int(dev[f], 0) < 0:
                    errors.append(f"{tag}: {f} 为负值: {dev[f]}")
            except ValueError:
                errors.append(f"{tag}: {f} 非数字: {dev[f]}")
        # 累计错误/丢事件必须为 0：error_count/event_drop_count 为无符号计数，
        # 仅校验非负恒真，错误或丢事件累计再多仍判绿；加等于 0 断言（防假绿）
        for f in ("error_count", "event_drop_count"):
            if f in missing:
                continue
            try:
                if int(dev[f], 0) != 0:
                    errors.append(f"{tag}: {f}={dev[f]} != 0（累计错误/丢事件，链路异常）")
            except ValueError:
                pass  # 非数字已由 numeric 检查判红
        if not dev.get("vendor", "").strip():
            errors.append(f"{tag}: vendor 为空（内核未上报厂商串）")
        if not dev.get("product", "").strip():
            errors.append(f"{tag}: product 为空（内核未上报产品串）")
        if dev.get("abi_version") != _EXPECTED_ABI:
            errors.append(f"{tag}: abi_version={dev.get('abi_version')} != {_EXPECTED_ABI}")
        if dev.get("enabled") != "1":
            # 监控功能禁用（enabled=0）时统计仍存在但不再更新，
            # 不断言会导致"监控被禁用仍全绿"假绿（对齐 lcview logfield 故障可见性）
            errors.append(f"{tag}: enabled={dev.get('enabled')} != 1（设备监控被禁用）")
    return errors


def load_baseline(path):
    """读基线文件 → {minor_str: {field: int|str}}；不存在/损坏返回 None。

    数值字段归一为 int（支持 0x 前缀）；vendor/product 文本字段保留
    字符串（不参与 delta 对比，误设为 expect 时由 diff 的数字校验判红）。
    """
    try:
        with open(path, encoding="utf-8") as fp:
            raw = json.load(fp)
        out = {}
        for k, fields in raw.items():
            out[str(k)] = {f: (int(v, 0) if isinstance(v, str) else int(v))
                           if f not in _TEXT_FIELDS else v
                           for f, v in fields.items()}
        return out
    except (OSError, ValueError, AttributeError, json.JSONDecodeError):
        return None


def diff_devices(baseline, devices, expect_fields):
    """delta 对比 → (错误列表, 报告行列表)。

    expect_fields 中任一字段未严格增加即判红；设备在基线中缺失、
    expect 字段缺失同样判红（增量为 0 = 触发未生效，不得假绿）。
    baseline 结构与 load_baseline 返回一致：{minor_str: {field: value}}。
    """
    errors, report = [], []
    if not expect_fields:
        # 空 expect 时增量断言循环不执行、errors 恒空会直接判绿；
        # yaml 漏写 --expect 即核心增量断言全跳过，必须判红（防假绿）
        errors.append("delta 模式未指定 --expect 字段（核心增量断言全跳过）")
        return errors, report
    base = baseline if isinstance(baseline, dict) else {}
    if not base:
        errors.append("基线无设备数据（baseline 未写入或损坏）")
        return errors, report
    if not devices:
        errors.append("当前 probe 输出为空，无法对比")
        return errors, report
    for dev in devices:
        minor = str(dev.get("minor", "?"))
        tag = f"minor={minor}"
        if minor not in base:
            errors.append(f"{tag}: 设备不在基线中（基线后新出现？重跑 baseline）")
            continue
        for f in expect_fields:
            if f not in dev:
                errors.append(f"{tag}: 缺 expect 字段 {f}")
                continue
            try:
                now = int(dev[f], 0)
            except ValueError:
                errors.append(f"{tag}: {f} 非数字: {dev[f]}")
                continue
            before = base[minor].get(f)
            if before is None:
                errors.append(f"{tag}: 基线缺字段 {f}")
                continue
            # 类型归一防御：基线值须为数字（load_baseline 已转 int，
            # 此处兜底 str 形态，非数字判红不崩）
            try:
                before = int(before, 0) if isinstance(before, str) else int(before)
            except (TypeError, ValueError):
                errors.append(f"{tag}: 基线 {f} 非数字: {before}")
                continue
            delta = now - before
            report.append(f"{tag}: {f} {before} -> {now} (delta={delta})")
            if delta <= 0:
                errors.append(f"{tag}: {f} 未增加（delta={delta}），触发未生效")
    return errors, report


def main():
    ap = argparse.ArgumentParser(description="lciod 板端数据校验器（host 侧）")
    ap.add_argument("--mode", required=True,
                    choices=["stats", "baseline", "delta"])
    ap.add_argument("--reset", action="store_true",
                    help="baseline 模式：设备侧 lciod_probe --reset 归零计数")
    ap.add_argument("--expect", nargs="+", default=[],
                    help="delta 模式：必须严格增加的字段（如 read_bytes）")
    ap.add_argument("--baseline", default=BASELINE_DEFAULT,
                    help="baseline/delta 快照路径")
    args = ap.parse_args()

    ensure_connected()
    report = []  # delta 模式增量报告；stats/baseline 无 report（统一打印）

    if args.mode == "stats":
        devices = parse_probe_output(run_probe())
        errors = validate_devices(devices)
    elif args.mode == "baseline":
        probe_args = ["--reset"] if args.reset else []
        devices = parse_probe_output(run_probe(probe_args))
        errors = validate_devices(devices)
        if errors:
            # 零设备/字段不齐不得写基线（对齐 lcview baseline 防假绿原则）
            for e in errors:
                print(f"FAIL: {e}")
            print("ERROR: 快照校验未通过，拒绝写基线")
            sys.exit(1)
        snap = {d["minor"]: {f: d[f] for f in REQUIRED_FIELDS} for d in devices}
        Path(args.baseline).write_text(json.dumps(snap, indent=1), encoding="utf-8")
        print(f"OK: baseline 写入 {args.baseline}（{len(devices)} 设备"
              f"{'，已归零计数' if args.reset else ''}）")
        sys.exit(0)
    else:  # delta
        baseline = load_baseline(args.baseline)
        if baseline is None:
            print(f"ERROR: 基线不可读或损坏: {args.baseline}")
            sys.exit(1)
        devices = parse_probe_output(run_probe())
        # delta 取数后先跑 stats 校验：enabled=0/缺字段/abi 漂移等
        # 在 trigger 末步（diff 增量断言）被绕过，先校验不得假绿
        errors = validate_devices(devices)
        d_errors, report = diff_devices(baseline, devices, args.expect)
        errors += d_errors

    if errors:
        for e in errors:
            print(f"FAIL: {e}")
        print(f"ERROR: lciod {args.mode} 校验失败")
        sys.exit(1)
    for line in report:
        print(line)
    print(f"OK: lciod {args.mode} 校验通过（{len(devices)} 设备）")
    sys.exit(0)


if __name__ == "__main__":
    main()
