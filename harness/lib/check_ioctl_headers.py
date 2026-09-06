#!/usr/bin/env python3
# ============================================================
# check_ioctl_headers.py — 内核/AOSP 拷贝同步头一致性 gate（S8）
# 设计目的：lechao 的 ioctl 头采用"内核为源、AOSP 拷贝同步"的共享
#   方式（CXX-001/LCD-004 契约），历史上靠人工同步，布局单侧漂移会
#   导致用户态解析错位（静态编译不报错）。本脚本对结构体/枚举的
#   类型+字段签名做规范化对比（排除注释、空白与用户态 typedef 适配
#   等合法差异），漂移时 rc=1 给出首个差异点。
# 对比对象：
#   内核 LcIod/lciod_usbd-ioctl.h
#   AOSP  hal/vendor_lechao_usbd-ioctl.h
# 用法：python3 harness/lib/check_ioctl_headers.py [--repo <code 仓根>]
# 退出码：0 签名一致 / 1 漂移 / 2 文件缺失
# ============================================================

import argparse
import hashlib
import re
import sys
from pathlib import Path

# 内核头 → AOSP 拷贝头（相对 code 仓根）
HEADER_PAIRS = [
    ("rpi5/kernel/new/vendor/lechao/LcIod/lciod_usbd-ioctl.h",
     "rpi5/aosp/new/vendor/lechao/services/lechao_lciod/hal/vendor_lechao_usbd-ioctl.h"),
]

# 提取 struct/enum 块：`struct NAME {` 或 `enum NAME {` 起步至配对 `};`
BLOCK_RE = re.compile(r"((?:struct|enum)\s+\w+\s*\{[^}]*\}\s*;)", re.S)


def normalize_block(block: str) -> list[str]:
    """块内容规范化为'类型 标识符'行序列：
    去行内注释、压空白；类型 token 归一（__uN→uN 等，兼容两侧
    typedef 适配层）；忽略 #ifdef 内的 typedef 适配行。"""
    sig = []
    for line in block.splitlines():
        line = re.sub(r"/\*.*?\*/", "", line)          # 行内块注释
        line = line.split("//")[0]                      # 行注释
        line = line.strip().rstrip(";").strip()
        if not line or line in ("struct {", "enum {", "{", "}", "};"):
            continue
        head = line.split("{")[0].strip()
        if head.startswith(("struct", "enum")):
            continue                                    # 块声明行本身
        # 归一化类型 token
        line = re.sub(r"\b__u(\d+)\b", r"u\1", line)
        line = re.sub(r"\b__s(\d+)\b", r"s\1", line)
        line = re.sub(r"\b__be(\d+)\b", r"be\1", line)
        line = re.sub(r"\s+", " ", line)
        if line:
            sig.append(line)
    return sig


def extract_signatures(text: str) -> dict[str, list[str]]:
    """返回 {struct/enum 名: 规范化签名行}；数组字段保留 [N] 后缀。"""
    out = {}
    for m in BLOCK_RE.finditer(text):
        block = m.group(1)
        name_m = re.match(r"(?:struct|enum)\s+(\w+)", block)
        if not name_m:
            continue
        name = name_m.group(1)
        sig = normalize_block(block)
        if sig:                                        # 空块（前向声明）不收
            out[name] = sig
    return out


def compare(k_path: Path, a_path: Path) -> tuple[int, str]:
    if not k_path.is_file() or not a_path.is_file():
        return 2, f"文件缺失: {'内核' if not k_path.is_file() else 'AOSP'} {k_path if not k_path.is_file() else a_path}"
    ksig = extract_signatures(k_path.read_text(encoding="utf-8", errors="replace"))
    asig = extract_signatures(a_path.read_text(encoding="utf-8", errors="replace"))
    problems = []
    for name in sorted(set(ksig) | set(asig)):
        if name not in ksig:
            problems.append(f"仅 AOSP 侧存在: {name}")
        elif name not in asig:
            problems.append(f"仅内核侧存在: {name}")
        elif ksig[name] != asig[name]:
            khash = hashlib.sha256("\n".join(ksig[name]).encode()).hexdigest()[:12]
            ahash = hashlib.sha256("\n".join(asig[name]).encode()).hexdigest()[:12]
            problems.append(f"签名漂移: {name} (kernel={khash} aosp={ahash})")
            for i, (kl, al) in enumerate(zip(ksig[name], asig[name])):
                if kl != al:
                    problems.append(f"  首差异行{i}: kernel='{kl}' aosp='{al}'")
                    break
    if problems:
        return 1, "\n".join(problems)
    names = ", ".join(sorted(ksig)) or "(无结构/枚举)"
    return 0, f"一致: {names}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=str(Path(__file__).resolve().parents[2] / "code"),
                    help="code 仓根（默认脚本相对推断）")
    args = ap.parse_args()
    repo = Path(args.repo)
    rc_total, fail_msgs = 0, []
    for k_rel, a_rel in HEADER_PAIRS:
        rc, msg = compare(repo / k_rel, repo / a_rel)
        tag = {0: "OK", 1: "漂移", 2: "缺失"}[rc]
        print(f"[{tag}] {k_rel} vs {a_rel}\n  {msg}")
        if rc != 0:
            rc_total = max(rc_total, 1 if rc == 1 else 2)
            fail_msgs.append(msg)
    return rc_total


if __name__ == "__main__":
    sys.exit(main())
