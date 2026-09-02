#!/usr/bin/env python3
# ============================================================
# ws_upload_tests.py — 设备侧单测执行（workspace-verify 制度化环节）
# 所属模块：workspace-verify — 编译产物上板验证
# 设计目的：C++ 单测长期只编译不执行是 nextSeqFor 真 bug 未被发现的根因
#   （本批起制度化）：编译验证后必须设备真跑。本脚本从
#   harness/config/verify-cases.yaml modules 段的 test_targets 读测试目标
#   （覆盖 lcview + lciod 的 unit_test / hal_test），定位 nativetest 二进制
#   → adb push 到设备 → 执行 gtest → 汇总 pass/fail。
#   推送前校验产物新鲜度（test_src）：源码任一文件比二进制新即陈旧判红，
#   禁止推旧二进制报绿（曾首推旧测试二进制却报 PASS 114、未含新增用例）。
# 用法：python3 ws_upload_tests.py [--product rpi5] [--out <aosp out>]
#   [--test-targets lechao_lcview_unit_test ...]（默认从 verify-cases.yaml
#   读全部模块 test_targets）
# 退出码：0 全部通过 / 1 有失败或不可达 / 2 参数或配置错误
# ============================================================

import argparse
import os
import posixpath
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

import yaml

import ws_adb_connect as ac  # noqa: E402

# harness/config/verify-cases.yaml：test_targets 源（与 ws_acceptance 同路径解析）
_CASES_PATH = Path(__file__).resolve().parents[2] / "config" / "verify-cases.yaml"


def load_test_targets(cases_path):
    """从 verify-cases.yaml modules 段收集全部 test_targets（保持模块顺序）。

    返回 (targets, run_as_root, src_map, None)：targets 为列表，run_as_root 为
    需 root 执行的测试集合，src_map 为 {test_target: 源码目录相对 AOSP 根}；
    解析失败返回 (None, None, None, err)。
    """
    try:
        data = yaml.safe_load(Path(cases_path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as e:
        return None, None, None, f"verify-cases.yaml 读取失败: {e}"
    modules = data.get("modules") or {}
    targets = []
    run_as_root = set()
    src_map = {}
    for mod in modules.values():
        mod_targets = mod.get("test_targets") or []
        targets.extend(mod_targets)
        run_as_root.update(mod.get("test_targets_run_as_root") or [])
        src = mod.get("test_src")
        if src:
            for t in mod_targets:
                src_map[t] = src
    if not targets:
        return None, None, None, "verify-cases.yaml modules 段无 test_targets（未登记测试目标）"
    return targets, run_as_root, src_map, None


def binary_is_stale(binary, aosp_root, src_rel):
    """二进制是否陈旧：早于其源码目录内任一文件 mtime（推送产物与源码不一致）。

    设备单测通道曾首推旧测试二进制仍报 PASS（未含新增用例），制度化为推送前
    校验：源码任一文件比二进制新 → 二进制不含该改动 → 判红，禁止报绿。
    源码目录缺失或二进制不可 stat 时返回 False（不误伤，产物缺失由调用方判红）。
    """
    src_root = Path(aosp_root) / src_rel
    if not src_root.is_dir():
        return False
    try:
        bin_mtime = Path(binary).stat().st_mtime
    except OSError:
        return False
    newest = 0.0
    for p in src_root.rglob("*"):
        if p.is_file():
            try:
                newest = max(newest, p.stat().st_mtime)
            except OSError:
                continue
    return newest > bin_mtime


def find_binary(out, product, name):
    """定位 nativetest 二进制（data/nativetest64 或 testcases/<name>/arm64）。

    返回绝对路径；找不到返回 None（调用方判红：编译产物缺失不能当通过）。
    """
    base = Path(out) / "target" / "product" / product
    candidates = [
        base / "data" / "nativetest64" / name / name,
        base / "testcases" / name / "arm64" / name,
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    return None


def adb_run(ep, args, timeout=600):
    """adb -s <ep> <args...>；返回 (stdout, returncode)。

    errors="replace" 对齐 ws_adb_connect.run_adb：设备输出含非 UTF-8 字节
    （如中文/二进制噪声）时不得抛 UnicodeDecodeError 中断整个测试批次。
    """
    try:
        p = subprocess.run(["adb", "-s", ep] + args,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           timeout=timeout)
        return p.stdout + p.stderr, p.returncode
    except subprocess.TimeoutExpired:
        return "", -1


def ensure_user(ep, need_root):
    """切换 adbd 用户（幂等）：need_root 时 adb root，否则 adb unroot。

    adbd 已处于目标用户时 adb 不会重启服务（快速返回，输出 already
    running）；真实切换会重启 adbd——重启后须等待 + shell 探活确认在线
    （对齐 lcview_check root 后 sleep 2 + 重连模式，否则立即 push 会因
    adbd 未就绪偶发失败）。返回 (ok, detail)。
    """
    cmd = "root" if need_root else "unroot"
    out, rc = adb_run(ep, [cmd], timeout=60)
    if rc != 0:
        return False, f"adb {cmd} 失败 rc={rc}: {out.strip()}"
    if "already running" in out:
        return True, ""
    # adbd 已重启：等待 + 探活（adb 客户端会自动重连，探活确认可执行）
    time.sleep(2)
    for _ in range(3):
        _, prc = adb_run(ep, ["shell", "echo ok"], timeout=30)
        if prc == 0:
            return True, ""
        time.sleep(1)
    return False, f"adb {cmd} 重启 adbd 后探活失败"


def run_one(ep, out, product, name, verbose=False, aosp_root=None, src_rel=None):
    """push + 执行单个 nativetest，返回 (ok, detail)。"""
    binary = find_binary(out, product, name)
    if binary is None:
        return False, f"{name}: 编译产物缺失（{out}/target/product/{product} 下未找到）"
    if aosp_root and src_rel and binary_is_stale(binary, aosp_root, src_rel):
        return False, (f"{name}: 编译产物陈旧（早于源码 {src_rel}，可能未含新用例）"
                       "——须重新编译后再推送，禁止用旧二进制报绿")
    _, rc = adb_run(ep, ["push", binary, f"/data/local/tmp/{name}"], timeout=300)
    if rc != 0:
        return False, f"{name}: adb push 失败 rc={rc}"
    _, rc = adb_run(ep, ["shell", f"chmod +x /data/local/tmp/{name}"], timeout=60)
    if rc != 0:
        return False, f"{name}: chmod 失败 rc={rc}"
    out_text, rc = adb_run(ep, ["shell", f"/data/local/tmp/{name}"], timeout=600)
    failed = re.search(r"\[  FAILED  \]", out_text)
    summary = None
    m = re.search(r"\[==========\] (\d+) tests? from \d+ test suites? ran\. "
                  r"\((\d+) ms total\)", out_text)
    if m:
        summary = f"{m.group(1)} tests ran"
    if rc == 0 and not failed:
        # 用例数判红（方向 5）：解析不到汇总行或实跑用例数为 0 即 FAIL——
        # 空跑/汇总缺失禁止报绿（无法证明用例真实执行过）
        if m is None:
            return False, (f"{name}: FAIL 用例数解析不到（缺 gtest 汇总行"
                           "「[==========] N tests ... ran」），须确认实跑用例数")
        if int(m.group(1)) == 0:
            return False, f"{name}: FAIL 实跑用例数为 0（无用例被执行，禁止报绿）"
        detail = f"{name}: PASS（{summary}）"
        if verbose:
            detail += f"\n{out_text}"
        return True, detail
    detail = f"{name}: FAIL rc={rc}{('，有 FAILED 用例') if failed else ''}"
    detail += f"\n--- 输出摘录 ---\n{out_text[:2000]}"
    return False, detail


def _default_out():
    """AOSP out 默认路径：优先 paths.conf 的 AOSP_WS（单一事实源，
    harness/lib/paths.py 读取，AOSP_WS 环境变量可覆盖）；读取失败回退
    ~/workspace/aosp/out（不因路径服务异常阻断脚本）。
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "lib"))
        from paths import env_path
        aosp = env_path("AOSP_WS")
        if aosp:
            # posixpath 拼接：AOSP out 路径跨平台统一正斜杠（emit 侧 Windows
            # Path 会产出反斜杠，被传给 adb/设备侧时破坏路径）
            return posixpath.join(aosp, "out")
    except Exception:
        pass
    # 回退分支同样 posix 拼接（Windows Path 会产出反斜杠，传给 adb/设备侧破坏路径）
    return posixpath.join(str(Path.home()), "workspace", "aosp", "out")


def _mark_stage(name):
    """验证阶段自动打点：cdp_timing.py mark（batch 识别：CDP_BATCH_ID 环境变量
    > log 目录唯一 timings 文件；均缺时静默跳过返 0，失败不阻断口径）。"""
    timing = (Path(__file__).resolve().parents[1] / "cross-device"
              / "lib" / "python" / "cdp_timing.py")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(timing.parent) + os.pathsep + env.get("PYTHONPATH", "")
    try:
        r = subprocess.run([sys.executable, str(timing), "mark", "--name", name],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=10, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"warn: 打点 {name} 失败（不阻断）: {e}", file=sys.stderr)
        return
    if r.returncode != 0:
        print(f"warn: 打点 {name} 失败（不阻断）: {r.stderr.strip()}",
              file=sys.stderr)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", default="rpi5", help="AOSP 产品名（默认 rpi5）")
    ap.add_argument("--out", default=_default_out(),
                    help="AOSP out 目录（默认 paths.conf AOSP_WS/out）")
    ap.add_argument("--aosp-root", default=None,
                    help="AOSP 源码根目录（默认 out 的上一级；产物新鲜度校验用）")
    ap.add_argument("--cases", default=str(_CASES_PATH),
                    help="verify-cases.yaml 路径")
    ap.add_argument("--test-targets", nargs="+", default=None,
                    help="测试目标列表（默认从 verify-cases.yaml 读全部模块）")
    ap.add_argument("--verbose", action="store_true",
                    help="通过时也打印测试完整输出")
    args = ap.parse_args(argv)

    if args.test_targets:
        targets = args.test_targets
        run_as_root = set()
        src_map = {}
    else:
        targets, run_as_root, src_map, err = load_test_targets(args.cases)
        if err:
            print(f"ERROR: {err}")
            return 2

    aosp_root = args.aosp_root or str(Path(args.out).parent)

    ep = ac.ensure_connected(rescue_enabled=False)
    if not ep:
        print("ERROR: 设备不可达（ensure_connected 失败）")
        return 1
    print(f"设备端点: {ep}；待执行 {len(targets)} 个测试目标: {', '.join(targets)}"
          f"{('；需 root: ' + ', '.join(sorted(run_as_root))) if run_as_root else ''}")

    all_ok = True
    for name in targets:
        need_root = name in run_as_root
        ok, detail = ensure_user(ep, need_root)
        if not ok:
            all_ok = False
            print(f"  [FAIL] {name}: {detail}")
            continue
        ok, detail = run_one(ep, args.out, args.product, name, args.verbose,
                             aosp_root=aosp_root, src_rel=src_map.get(name))
        all_ok = all_ok and ok
        print(("  [OK]   " if ok else "  [FAIL] ") + detail)
    # 跑完恢复 shell 用户，避免 root 状态影响后续 verify 环节
    ensure_user(ep, False)
    print(f"\n设备侧单测{'全部通过' if all_ok else '存在失败'}：{len(targets)} 目标")
    # 脚本自动打点单测段（失败不阻断）
    _mark_stage("verify_unit_test")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
