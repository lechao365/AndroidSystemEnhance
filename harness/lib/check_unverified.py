#!/usr/bin/env python3
# ============================================================
# check_unverified.py — push 前置未上板告警（code/ 改动未被 board 收据覆盖）
# 设计目的：git-works-push 推送含 code/ 业务源码的提交前，检测该 code 改动
#   是否已被最新 board 收据覆盖——六批 code 改动全 -s skip、发布前才发现
#   无 board 收据是 publish-main-base 卡点根因（2026-10-09 实测）。本检查器
#   把「未上板」提前到推送阶段告警，避免累积到发布才暴露。
# 覆盖判定与 promote 同口径（publish_main_base.sh 前置）：
#   - CODE_HEAD 为 verified_commit 祖先（code_head 改动已被验证点覆盖）视为覆盖
#   - 或 CODE_HEAD 的父 == verified_commit（验证起点在内容提交之前）视为覆盖
#   - 其余视为未覆盖 → 告警（warn 不阻断，commit_scope 已管提交面一致性，
#     本检查器是未上板前置提醒，不是硬拒——硬拒留给 promote）
# 用法：python3 harness/lib/check_unverified.py [--code-dir code]
# 退出码：0 覆盖（含无 code 改动）/ 1 有未上板 code 改动（warn 信息在 stdout）
# ============================================================

import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]


def _git(*args):
    # 全仓纪律（check_quotepath）：git 输出点一律带 -c core.quotepath=false，
    # 防非 ASCII 路径输出转义致解析失效（本检查器虽只取 sha，仍遵守统一约束）；
    # -C 锚定仓根（不依赖调用方 cwd=仓根，lib-14 同源）
    r = subprocess.run(["git", "-C", str(_REPO_ROOT),
                        "-c", "core.quotepath=false", *args],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return r.returncode, r.stdout.strip()


def _code_head():
    """dev 相对 origin/main 的最近 code/ 内容提交 sha（无返回 None）。"""
    rc, out = _git("log", "--format=%H", "origin/main..HEAD", "--", "code/")
    if rc != 0 or not out:
        return None
    return out.splitlines()[0]


def _latest_board_verified():
    """最新 board 收据 verified_commit（无返回 None）。"""
    # sys.path 用仓根推导的绝对路径（不依赖调用方 cwd=仓根）
    sys.path.insert(
        0, str(_REPO_ROOT / "harness" / "skills" / "cross-device"
               / "lib" / "python"))
    try:
        import cdp_receipt
    except ImportError:
        return None
    p, r, errs = cdp_receipt.latest_board_receipt()
    if errs or r is None:
        return None
    return r.verified_commit


def covered(code_head, verified_commit):
    """覆盖判定：is-ancestor 或 CODE_HEAD 父 == verified_commit。"""
    rc, _ = _git("merge-base", "--is-ancestor", code_head, verified_commit)
    if rc == 0:
        return True
    rc, parent = _git("rev-parse", "--short=12", code_head + "^")
    return rc == 0 and parent == verified_commit


def main(argv=None):
    code_head = _code_head()
    if not code_head:
        print("ok: dev 相对 origin/main 无 code/ 改动（或无法解析），无需上板覆盖")
        return 0
    verified = _latest_board_verified()
    if not verified:
        print(f"warn: dev 有 code/ 改动 {code_head[:12]}，但无最新 board 收据"
              "（从未上板验证）——发布前须 /workspace-verify 上板验证产收据",
              file=sys.stderr)
        return 1
    if covered(code_head, verified):
        print(f"ok: code/ 改动 {code_head[:12]} 已被最新 board 收据覆盖"
              f"（verified_commit={verified}）")
        return 0
    print(f"warn: code/ 改动 {code_head[:12]} 未被最新 board 收据覆盖"
          f"（verified_commit={verified}，非祖先也非父）——上板验证缺失，"
          "发布前 promote 会被 RECEIPT_FAIL 拒，建议先 /workspace-verify "
          "上板验证产收据", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
