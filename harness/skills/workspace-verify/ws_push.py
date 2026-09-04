#!/usr/bin/env python3
# ============================================================
# ws_push.py — 编译产物推送上板（workspace-verify 步骤 4 脚本化）
# 所属模块：workspace-verify — 编译产物上板验证
# 设计目的：verify-cases.yaml modules 段的 push 映射此前无任何消费者，
#   推送长期按 SKILL 步骤 4 手敲 adb push，映射与实际推送易漂移（漏推
#   sepolicy/vintf 产物同源清刷必炸风险）。本脚本把推送、回读校验、
#   生效门禁、自描述产物内聚为单一入口，映射成为执行的唯一事实源。
# 功能：
#   1) 读 verify-cases.yaml modules 段 push 映射（--modules 过滤，默认
#      全部模块），按映射逐项 adb push 编译产物（本地源 = AOSP out
#      target/product/<product> 下与 dst 同相对路径的产物）。
#   2) 每项推送后回读设备侧 SHA256 / 字节数 / SELinux 上下文，与本地产物
#      比对：SHA256 与字节数精确比对；SELinux 上下文 host 无 SELinux 域，
#      按有效性校验（非 unlabeled 且标准 u:object_r:*:s0 格式），
#      --expect-context 可对指定 dst 追加精确比对。任一不符判红。
#   3) 落自描述产物 --result-file：run_id + 逐项源/目标路径 + 三项校验值
#      （checks: sha256/bytes/context），原子写防半截文件被当证据。
#   4) 生效门禁：命中 sepolicy（dst 含 /selinux/）或 vintf（dst 含
#      /vintf/）或 init rc（dst 以 .rc 结尾）且推送成功的项，强制 reboot
#      并等待 sys.boot_completed 启动完成；重启/就绪失败判红。无跳过
#      开关（接口层杜绝"跳过即判红"被参数绕过）。推送失败的生效项不
#      触发重启（产物未写入，重启无意义，失败本身已判红）。
#   5) verify_push 打点在实际推送循环完成后（此前 ws_adb_connect ensure
#      连接成功即打，量不到推送；现移至本脚本推送环节终点，失败不阻断）。
# 用法：python3 ws_push.py [--product rpi5] [--out <aosp out>]
#   [--cases <verify-cases.yaml>] [--modules lcview lciod]
#   [--expect-context "/vendor/bin/x=u:object_r:x:s0,..."]
#   [--result-file <json>]
# 退出码：0 推送+回读+生效门禁全过 / 1 推送或校验或重启失败 / 2 参数或配置错误
# ============================================================

import argparse
import hashlib
import json
import os
import posixpath
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

import yaml

import ws_adb_connect as ac  # noqa: E402

# harness/config/verify-cases.yaml：push 映射源（与 ws_upload_tests 同路径解析）
_CASES_PATH = Path(__file__).resolve().parents[2] / "config" / "verify-cases.yaml"

# 标准 SELinux 上下文形态（u:object_r:<type>:s0）
_CONTEXT_RE = re.compile(r"^u:object_r:\S+:s0$")

# 可注入睡眠点（方向 1）：reboot_and_wait 的实时等待（重启 settle 8s /
# 轮询间隔 5s）统一经此下发；单测 patch 本符号消除真实等待——该等待曾使
# 每次自检多花 15~28s 且随 xdist 分发波动
_sleep = time.sleep


def load_push_map(cases_path, modules=None):
    """从 verify-cases.yaml modules 段收集 push 映射（保持模块顺序）。

    返回 (items, err)：items 为 [{"module", "dst"}] 保序列表；modules 非
    None 时仅保留指定模块名。解析失败/无映射返回 (None, err)。
    """
    try:
        data = yaml.safe_load(Path(cases_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        return None, f"verify-cases.yaml 读取失败: {e}"
    mods = data.get("modules") or {}
    items = []
    for name, mod in mods.items():
        if modules is not None and name not in modules:
            continue
        for entry in (mod.get("push") or []):
            module = entry.get("module")
            dsts = entry.get("dst") or []
            if not module or not dsts:
                return None, f"verify-cases.yaml modules.{name}.push 段缺 module 或 dst"
            for dst in dsts:
                items.append({"module": module, "dst": dst})
    if not items:
        return None, "verify-cases.yaml modules 段无 push 映射（未登记推送项）"
    return items, None


def _default_out():
    """AOSP out 默认路径：优先 paths.conf 的 AOSP_WS（单一事实源，
    harness/lib/paths.py 读取，AOSP_WS 环境变量可覆盖）；读取失败回退
    ~/workspace/aosp/out（不因路径服务异常阻断脚本）。"""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "lib"))
        from paths import env_path
        aosp = env_path("AOSP_WS")
        if aosp:
            # posixpath 拼接：AOSP out 路径跨平台统一正斜杠（Windows Path
            # 会产出反斜杠，被传给 adb/设备侧时破坏路径）
            return posixpath.join(aosp, "out")
    except Exception:
        pass
    return posixpath.join(str(Path.home()), "workspace", "aosp", "out")


def resolve_source(out, product, dst):
    """按映射 dst 定位本地编译产物：$OUT/target/product/<product> 下与
    dst 同相对路径（分区镜像布局 system/ vendor/ 对应设备分区）。
    返回绝对路径；不存在返回 None（调用方判红：产物缺失不能当通过）。"""
    local = Path(out) / "target" / "product" / product / dst.lstrip("/")
    return str(local) if local.is_file() else None


def adb_run(ep, args, timeout=600):
    """adb -s <ep> <args...>；返回 (stdout+stderr, returncode)。

    errors="replace" 对齐 ws_adb_connect.run_adb：设备输出含非 UTF-8 字节
    时不得抛 UnicodeDecodeError 中断整个推送批次。
    """
    try:
        p = subprocess.run(["adb", "-s", ep] + args,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout)
        return p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        return "", -1


def local_fingerprint(path):
    """本地产物指纹：SHA256 + 字节数（回读比对基准）。"""
    data = Path(path).read_bytes()
    return {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}


def readback_device(ep, dst):
    """回读设备侧三项：SHA256 / 字节数 / SELinux 上下文。

    三项独立 exec（任一失败不掩盖其余项证据）；返回
    {"sha256", "bytes", "context", "err"}——err 非空即回读不可信（判红）。
    """
    out, rc = adb_run(ep, ["shell", f"sha256sum {dst}"], timeout=60)
    sha = None
    if rc == 0:
        parts = out.strip().split()
        if parts and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            sha = parts[0]
    out, rc = adb_run(ep, ["shell", f"stat -c %s {dst}"], timeout=60)
    nbytes = None
    if rc == 0 and out.strip().isdigit():
        nbytes = int(out.strip())
    out, rc = adb_run(ep, ["shell", f"ls -Zd {dst}"], timeout=60)
    ctx = None
    if rc == 0:
        # toybox ls -Zd 每行形如 "u:object_r:vendor_file:s0 /vendor/bin/x"
        # （顺序可能变体），按标准形态正则抽取而非按列位解析
        m = re.search(r"u:object_r:\S+:s0", out)
        if m:
            ctx = m.group(0)
    return {"sha256": sha, "bytes": nbytes, "context": ctx}


def check_context(device_ctx, expect=None):
    """SELinux 上下文校验（方向 2 三项之一）。

    host 侧无 SELinux 域无法为本地产物算上下文，默认按有效性校验：
    非 unlabeled（未打标 = file_contexts 缺失/未生效）且标准
    u:object_r:*:s0 格式；expect 非空时追加精确比对（判红明示差异）。
    返回 (ok, detail)。
    """
    if not device_ctx:
        return False, "设备侧 SELinux 上下文回读为空"
    if "unlabeled" in device_ctx:
        return False, f"SELinux 上下文为 unlabeled（未打标）: {device_ctx}"
    if not _CONTEXT_RE.match(device_ctx):
        return False, f"SELinux 上下文非标准形态（u:object_r:*:s0）: {device_ctx}"
    if expect and device_ctx != expect:
        return False, f"SELinux 上下文与期望不符: 期望 {expect}，实际 {device_ctx}"
    return True, device_ctx


def verify_item(local_path, device, expect_ctx=None):
    """回读三项与本地产物比对（方向 2）：任一不符即判红。

    返回 (ok, checks, detail)：checks 为 {"sha256","bytes","context"} 的
    pass/fail 值（自描述产物的三项校验值）。
    """
    local = local_fingerprint(local_path)
    checks = {}
    details = []
    checks["sha256"] = "pass" if device["sha256"] == local["sha256"] else "fail"
    if checks["sha256"] == "fail":
        details.append(f"SHA256 不符: 本地 {local['sha256']}，设备 {device['sha256']}")
    checks["bytes"] = "pass" if device["bytes"] == local["bytes"] else "fail"
    if checks["bytes"] == "fail":
        details.append(f"字节数不符: 本地 {local['bytes']}，设备 {device['bytes']}")
    ok_ctx, ctx_detail = check_context(device["context"], expect_ctx)
    checks["context"] = "pass" if ok_ctx else "fail"
    if not ok_ctx:
        details.append(ctx_detail)
    ok = all(v == "pass" for v in checks.values())
    detail = "ok" if ok else "；".join(details)
    return ok, checks, detail, local


def needs_reboot(dst):
    """生效类产物判定（方向 4）：sepolicy（含 file_contexts）或 vintf 或
    init rc——写完必须重启才能生效的项。"""
    return "/selinux/" in dst or "/vintf/" in dst or dst.endswith(".rc")


def reboot_and_wait(ep, boot_timeout=240):
    """强制重启并等待启动完成（方向 4 生效门禁）。

    adb reboot 后 adbd 断开属预期（rc 不判）；循环 ensure_connected（不
    走串口救援）+ ensure_ready 轮询 sys.boot_completed，总超时
    boot_timeout 秒。返回 (ok, detail)。
    """
    adb_run(ep, ["reboot"], timeout=60)
    _sleep(8)  # 等 adbd 断开并开始重启
    deadline = time.monotonic() + boot_timeout
    last_err = ""
    while time.monotonic() < deadline:
        ep2 = ac.ensure_connected(rescue_enabled=False)
        if ep2:
            if ac.ensure_ready(timeout=15, poll_interval=5):
                return True, f"reboot 后启动完成（endpoint={ep2}）"
            last_err = "已连接但 sys.boot_completed 未就绪"
        else:
            last_err = "reboot 后 adb 未恢复在线"
        _sleep(5)
    return False, f"reboot 后启动未完成（{boot_timeout}s 超时）: {last_err}"


def ensure_root_remount(ep):
    """推送前置：adb root + adb remount（原 SKILL 步骤 4 手敲环节内聚）。

    root 切换会重启 adbd，探活确认后再 remount（对齐 ws_upload_tests
    ensure_user 模式）。返回 (ok, detail)。
    """
    out, rc = adb_run(ep, ["root"], timeout=60)
    if rc != 0:
        return False, f"adb root 失败 rc={rc}: {out.strip()}"
    if "already" not in out:
        time.sleep(2)
        for _ in range(3):
            _, prc = adb_run(ep, ["shell", "echo ok"], timeout=30)
            if prc == 0:
                break
            time.sleep(1)
        else:
            return False, "adb root 重启 adbd 后探活失败"
    out, rc = adb_run(ep, ["remount"], timeout=120)
    if rc != 0:
        return False, f"adb remount 失败 rc={rc}: {out.strip()}"
    return True, ""


def _atomic_write_json(path, data):
    """原子写 JSON 产物（方向 3）：先写临时文件再 os.replace，防半截文件被当证据。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def _mark_stage(name, dur_s=None):
    """验证阶段自动打点：cdp_timing.py mark（batch 识别：CDP_BATCH_ID 环境变量
    > log 目录唯一 timings 文件；均缺时静默跳过返 0，失败不阻断口径）。

    dur_s（方向 1）：调用方自测脚本内实测秒数，mark 段耗时取 dur_s，相邻差额
    减去 dur_s 后的余量落 gap_before_<name>——脚本启动前的 AI 活动时间不再
    污染段口径（上批 sync 段记 71.2s 而脚本自报 13.9s，段口径不可信则后续
    提速无从测量）。
    """
    timing = (Path(__file__).resolve().parents[1] / "cross-device"
              / "lib" / "python" / "cdp_timing.py")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(timing.parent) + os.pathsep + env.get("PYTHONPATH", "")
    args = [sys.executable, str(timing), "mark", "--name", name]
    if dur_s is not None:
        args += ["--dur-s", str(round(float(dur_s), 3))]
    try:
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=10, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"warn: 打点 {name} 失败（不阻断）: {e}", file=sys.stderr)
        return
    if r.returncode != 0:
        print(f"warn: 打点 {name} 失败（不阻断）: {r.stderr.strip()}",
              file=sys.stderr)


def _parse_expect_context(text):
    """解析 --expect-context："/dst=ctx,/dst2=ctx2" → {dst: ctx}。"""
    expect = {}
    for pair in (text or "").split(","):
        pair = pair.strip()
        if not pair:
            continue
        if "=" not in pair:
            raise ValueError(f"--expect-context 项缺 = 分隔: {pair}")
        dst, ctx = pair.split("=", 1)
        expect[dst.strip()] = ctx.strip()
    return expect


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="编译产物推送上板（verify-cases.yaml push 映射驱动）")
    ap.add_argument("--product", default="rpi5", help="AOSP 产品名（默认 rpi5）")
    ap.add_argument("--out", default=_default_out(),
                    help="AOSP out 目录（默认 paths.conf AOSP_WS/out）")
    ap.add_argument("--cases", default=str(_CASES_PATH),
                    help="verify-cases.yaml 路径")
    ap.add_argument("--modules", nargs="+", default=None,
                    help="仅推送指定模块（默认全部模块的 push 映射）")
    ap.add_argument("--expect-context", default=None,
                    help="追加 SELinux 上下文精确比对（格式 /dst=ctx,/dst=ctx）")
    ap.add_argument("--result-file", default=None,
                    help="自描述推送产物 JSON 路径（原子写；--out 已被 AOSP "
                         "输出目录占用，不可复用该名）")
    args = ap.parse_args(argv)

    # 方向 1：脚本自报实测时长基准（verify_push 打点传 --dur-s）
    _t0 = time.monotonic()

    try:
        expect_ctx = _parse_expect_context(args.expect_context)
    except ValueError as e:
        print(f"ERROR: {e}")
        return 2

    items, err = load_push_map(args.cases, set(args.modules) if args.modules else None)
    if err:
        print(f"ERROR: {err}")
        return 2

    ep = ac.ensure_connected(rescue_enabled=False)
    if not ep:
        print("ERROR: 设备不可达（ensure_connected 失败）")
        return 1
    print(f"设备端点: {ep}；待推送 {len(items)} 项（映射源: {args.cases}）")

    ok_root, detail = ensure_root_remount(ep)
    if not ok_root:
        print(f"ERROR: {detail}")
        _write_result(args.result_file, ep, [], None, "fail")
        return 1

    all_ok = True
    results = []
    for item in items:
        dst = item["dst"]
        source = resolve_source(args.out, args.product, dst)
        if source is None:
            all_ok = False
            results.append({"module": item["module"], "source": None, "dst": dst,
                            "push_ok": False,
                            "checks": {"sha256": "fail", "bytes": "fail",
                                       "context": "fail"},
                            "detail": f"编译产物缺失（{args.out}/target/product/"
                                      f"{args.product}{dst}）"})
            print(f"  [FAIL] {item['module']} -> {dst}: 编译产物缺失")
            continue
        # 方向 2 幂等：推送前先回读设备侧，与本地 SHA256+字节全等则跳过
        # 该项（不计入重启判定——产物未写入，重启无意义）；回读失败
        # （sha256/bytes 任一缺失）仍按原路推。上批 push 226.3s 中约 214s
        # 是全等产物触发的强制重启，幂等跳过直接消除该段。
        local = local_fingerprint(source)
        device = readback_device(ep, dst)
        if (device["sha256"] and device["bytes"] is not None
                and device["sha256"] == local["sha256"]
                and device["bytes"] == local["bytes"]):
            pushed = False
        else:
            pushed = True
        if pushed:
            _, rc = adb_run(ep, ["push", source, dst], timeout=300)
            if rc != 0:
                all_ok = False
                results.append({"module": item["module"], "source": source, "dst": dst,
                                "push_ok": False, "pushed": True,
                                "checks": {"sha256": "fail", "bytes": "fail",
                                           "context": "fail"},
                                "detail": f"adb push 失败 rc={rc}"})
                print(f"  [FAIL] {item['module']} -> {dst}: adb push 失败 rc={rc}")
                continue
            device = readback_device(ep, dst)
        ok, checks, detail, _ = verify_item(source, device,
                                            expect_ctx.get(dst))
        all_ok = all_ok and ok
        results.append({"module": item["module"], "source": source, "dst": dst,
                        "push_ok": True, "pushed": pushed, "local": local,
                        "device": device, "checks": checks, "detail": detail})
        tag = "SKIP" if not pushed else ("OK" if ok else "FAIL")
        print(f"  [{tag}] {item['module']} -> {dst}: {detail}"
              f"（sha256={checks['sha256']} bytes={checks['bytes']} "
              f"context={checks['context']}）")

    # verify_push 打点：实际推送循环完成后（方向 5；此前在 ensure 连接成功
    # 即打，量不到推送）。含失败——失败也是推送环节的终点。方向 1：传
    # --dur-s 脚本自报实测秒数（脚本启动至今，未归属时间落 gap_before_）。
    _mark_stage("verify_push", dur_s=time.monotonic() - _t0)

    # 生效门禁（方向 4）：命中 sepolicy/vintf/rc 且实际推送成功 → 强制重启并
    # 等待启动完成；跳过即判红（无跳过开关），重启/就绪失败判红。
    # 方向 2：跳过项（与设备全等未推送）不计入重启判定——产物未写入无需重启。
    reboot_info = None
    if any(r["push_ok"] and r.get("pushed", True) and needs_reboot(r["dst"])
           for r in results):
        ok_boot, detail = reboot_and_wait(ep)
        reboot_info = {"required": True, "ok": ok_boot, "detail": detail}
        print(f"  生效门禁: 强制重启 {'通过' if ok_boot else '判红'}——{detail}")
        all_ok = all_ok and ok_boot

    _write_result(args.result_file, ep, results, reboot_info,
                  "pass" if all_ok else "fail")
    print(f"\n推送{'全部通过' if all_ok else '存在失败'}：{len(items)} 项")
    return 0 if all_ok else 1


def _write_result(path, ep, results, reboot_info, overall):
    """落自描述推送产物（方向 3）：run_id + endpoint + 逐项源/目标路径 +
    三项校验值 + 生效门禁段，原子写。path 为空跳过。"""
    if not path:
        return
    _atomic_write_json(path, {
        "run_id": os.environ.get("CDP_RUN_ID") or uuid.uuid4().hex,
        "endpoint": ep,
        "items": results,
        "reboot": reboot_info,
        "overall": overall,
    })


if __name__ == "__main__":
    sys.exit(main())
