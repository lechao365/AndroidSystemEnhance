#!/usr/bin/env python3
# ============================================================
# ws_package.py — 打包证据生产（mk_rpi5_full_image.sh mode 0 包装）
# 所属模块：workspace-verify — 打包证据（baseline package_result 生产者）
# 设计目的：发布硬门禁需要真实打包证据——本脚本前置校验三镜像齐备，
#   以 BLD-007 合规方式（显式传 TARGET_PRODUCT 与 ANDROID_PRODUCT_OUT，
#   并核验打包脚本 sudo 行同样显式传参）调 mk_rpi5_full_image.sh mode 0，
#   落自描述证据 JSON（镜像路径/sha256/字节、脚本 rc、耗时、原因），
#   原子写。镜像缺失或 sudo 环境不可用等失败一律如实记因，不产假证据
#   （证据 rc 非 0 → baseline_register 记 UNKNOWN，不声称 PASS）。
#   真跑前先做 sudo -n true 非交互探测（sudo_n 字段）：免密可用才执行，
#   不可用如实记因不执行——防非 tty 卡死（脚本内 sudo 提示密码）或错报。
# 证据消费方：baseline_register.py add-candidate（收据 package 字段优先，
#   保留 --package-evidence 与按 batch_id 探测兜底）；ws_report 把证据内嵌
#   收据 package 字段随收据入库可追溯。
# 用法：python3 ws_package.py [--mode 0] [--evidence-file <json>] [--timeout 900]
# 退出码：0 打包成功（script_rc==0）/ 1 失败（证据已如实落盘）/ 2 参数错误
# ============================================================

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import uuid
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
# 复用仓内路径工具：AOSP_WS 单一事实源（paths.conf，支持环境变量覆盖）
sys.path.insert(0, str(_SCRIPT_DIR.parents[1] / "lib"))
import paths  # noqa: E402

_SCRIPT = _SCRIPT_DIR.parents[1] / "scripts" / "mk_rpi5_full_image.sh"
# 证据目录：harness/log/workspace-verify/（gitignore 域，与 chain runs/ 同域）
_EVIDENCE_DIR = _SCRIPT_DIR.parents[1] / "log" / "workspace-verify"
_CROSS_DEVICE_LOG = _SCRIPT_DIR.parents[1] / "log" / "cross-device"

# 三镜像（mode 0 仅打包已有镜像，缺一即前置失败不产假证据）
_IMAGES = ("boot.img", "system.img", "vendor.img")
# 打包脚本 sudo 行的 BLD-007 合规标记：sudo 必须显式传两环境变量（禁 -E/裸 sudo）
_BLD007_RE = re.compile(
    r'sudo\s+TARGET_PRODUCT="\$\{TARGET_PRODUCT\}"'
    r'\s+ANDROID_PRODUCT_OUT="\$\{ANDROID_PRODUCT_OUT\}"')
# 打包脚本内部固定的 lunch 目标（aosp_rpi5-bp1a-userdebug）→ TARGET_PRODUCT
_TARGET_PRODUCT = "aosp_rpi5"

_OUTPUT_RE = "RaspberryVanillaAOSP15-*-rpi5.img"


def _atomic_write_json(path, data):
    """原子写 JSON：先落 tmp 再 os.replace，避免半写产物污染证据链。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    os.replace(tmp, p)


def _sha256(path):
    """流式 sha256（镜像 GB 级，不整读进内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _resolve_batch_id():
    """batch_id 三级回落精简版：CDP_BATCH_ID env > 唯一打点文件（同 ws_acceptance）。"""
    bid = os.environ.get("CDP_BATCH_ID", "").strip()
    if bid:
        return bid
    files = sorted(_CROSS_DEVICE_LOG.glob("timings-*.json"))
    if len(files) == 1:
        return files[0].stem[len("timings-"):]
    return None


def run_package(mode=0, evidence_file=None, timeout=900, aosp_ws=None,
                run=True):
    """执行打包并落自描述证据，返回 (rc, evidence)。

    run=False 仅做前置校验（镜像齐备性/BLD-007 标记）不真跑——供 dry 检查；
    生产路径恒 run=True（真跑一次，失败如实记因）。
    """
    run_id = os.environ.get("CDP_RUN_ID") or uuid.uuid4().hex
    batch_id = _resolve_batch_id()
    started_at = time.time()
    t0m = time.monotonic()
    aosp = aosp_ws or paths.env_path("AOSP_WS")
    evidence = {"run_id": run_id, "batch_id": batch_id, "mode": mode,
                "script": str(_SCRIPT), "images": [], "images_ok": False,
                "packaged_img": None, "script_rc": None, "ran": False,
                "sudo_bld007": False, "sudo_n": False, "error": ""}

    def _finish(rc):
        evidence["started_at"] = started_at
        evidence["ended_at"] = time.time()
        evidence["dur_s"] = round(time.monotonic() - t0m, 3)
        out = evidence_file or (_EVIDENCE_DIR /
                                f"package-{batch_id or run_id}.json")
        evidence["evidence_file"] = str(out)
        _atomic_write_json(out, evidence)
        return rc, evidence

    if not aosp:
        evidence["error"] = "AOSP_WS 未配置（harness/config/paths.conf 或环境变量）"
        return _finish(1)
    if not _SCRIPT.is_file():
        evidence["error"] = f"打包脚本不存在: {_SCRIPT}"
        return _finish(1)
    product_out = Path(aosp) / "out" / "target" / "product" / "rpi5"
    evidence["target_product"] = _TARGET_PRODUCT
    evidence["android_product_out"] = str(product_out)

    # BLD-007 前置核验：打包脚本 sudo 行须显式传 TARGET_PRODUCT 与
    # ANDROID_PRODUCT_OUT（禁 sudo -E/裸 sudo——sudo 清空环境变量）
    try:
        script_text = _SCRIPT.read_text(encoding="utf-8")
    except OSError as e:
        evidence["error"] = f"打包脚本不可读: {e}"
        return _finish(1)
    evidence["sudo_bld007"] = bool(_BLD007_RE.search(script_text))
    if not evidence["sudo_bld007"]:
        evidence["error"] = ("BLD-007 违规：打包脚本 sudo 行未显式传 "
                             "TARGET_PRODUCT/ANDROID_PRODUCT_OUT，拒绝执行")
        return _finish(1)

    # 前置校验三镜像齐备：缺一即如实记因不执行（不产假证据）
    missing = []
    for name in _IMAGES:
        p = product_out / name
        if p.is_file() and p.stat().st_size > 0:
            evidence["images"].append({"name": name, "path": str(p),
                                       "bytes": p.stat().st_size,
                                       "sha256": _sha256(p)})
        else:
            evidence["images"].append({"name": name, "path": str(p),
                                       "bytes": None, "sha256": None})
            missing.append(name)
    evidence["images_ok"] = not missing
    if missing:
        evidence["error"] = f"镜像缺失: {', '.join(missing)}（{product_out}）"
        return _finish(1)
    if not run:
        return _finish(0)

    # 非交互 sudo 前置探测（本批意图 4）：sudo -n true 免密可用才执行打包；
    # 不可用（需密码/无权限/超时）如实记因不执行——打包脚本内 sudo 在非
    # tty 下提示输密码会挂死整链，或把 sudo 失败误包装成打包失败/成功错报。
    sudo_ok = False
    sudo_detail = ""
    try:
        sp = subprocess.run(["sudo", "-n", "true"], capture_output=True,
                            text=True, encoding="utf-8", errors="replace",
                            timeout=30)
        sudo_ok = sp.returncode == 0
        sudo_detail = (sp.stderr or "").strip()[-200:]
    except subprocess.TimeoutExpired:
        sudo_detail = "sudo -n true 超时（>30s）"
    except OSError as e:
        sudo_detail = f"sudo 不可执行: {e}"
    evidence["sudo_n"] = sudo_ok
    if not sudo_ok:
        evidence["error"] = ("sudo 非交互探测失败（sudo -n true 不可用，需密码/"
                             f"无权限）：{sudo_detail or '未知原因'}，拒绝执行防"
                             "非 tty 卡死或错报")
        return _finish(1)

    # 真跑：显式传 TARGET_PRODUCT/ANDROID_PRODUCT_OUT（BLD-007 调用方侧），
    # mode 0 仅打包（不编译），输出捕获尾段供失败归因
    env = dict(os.environ)
    env["AOSP_ROOT"] = str(aosp)
    env["ANDROID_PRODUCT_OUT"] = str(product_out)
    env["TARGET_PRODUCT"] = _TARGET_PRODUCT
    try:
        proc = subprocess.run(["bash", str(_SCRIPT), "-mode", str(mode)],
                              env=env, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        evidence["ran"] = True
        evidence["error"] = f"打包超时（>{timeout}s），进程组已回收"
        return _finish(1)
    except OSError as e:
        evidence["error"] = f"打包脚本启动失败: {e}"
        return _finish(1)
    evidence["ran"] = True
    evidence["script_rc"] = proc.returncode
    evidence["output_tail"] = ((proc.stdout or "") + (proc.stderr or ""))[-2000:]
    if proc.returncode != 0:
        evidence["error"] = f"打包脚本 rc={proc.returncode}（详见 output_tail）"
        return _finish(1)
    # 成功：登记最新 SD 卡刷机镜像（路径/sha256/字节）
    outputs = sorted(product_out.glob(_OUTPUT_RE), key=lambda p: p.stat().st_mtime)
    if outputs:
        img = outputs[-1]
        evidence["packaged_img"] = {"path": str(img),
                                    "bytes": img.stat().st_size,
                                    "sha256": _sha256(img)}
    else:
        evidence["error"] = "打包 rc=0 但未找到 SD 卡刷机镜像（异常，如实记录）"
        return _finish(1)
    return _finish(0)


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="打包证据生产（mk_rpi5_full_image.sh mode 0 包装，"
                    "证据供 baseline_register 定 package_result）")
    ap.add_argument("--mode", type=int, choices=[0], default=0,
                    help="打包模式（证据生产仅支持 0=仅打包已有镜像）")
    ap.add_argument("--evidence-file", default=None,
                    help="自描述证据 JSON 路径（缺省 "
                         "harness/log/workspace-verify/package-<batch_id>.json）")
    ap.add_argument("--timeout", type=int, default=900,
                    help="打包超时秒数（mode 0 实测约 3 分钟）")
    args = ap.parse_args(argv)
    rc, evidence = run_package(mode=args.mode,
                               evidence_file=args.evidence_file,
                               timeout=args.timeout)
    print(json.dumps({k: evidence[k] for k in
                      ("run_id", "batch_id", "script_rc", "images_ok",
                       "error", "evidence_file") if k in evidence},
                     ensure_ascii=False))
    return rc


if __name__ == "__main__":
    sys.exit(main())
