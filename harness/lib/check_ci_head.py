#!/usr/bin/env python3
# ============================================================
# check_ci_head.py — push 前置 CI 门禁（gh 未装未认证 → curl 免认证）
# 设计目的：git-works-push 推送 dev 前核对 GitHub Actions run 的
#   conclusion——CI 已失败仍推送会带红入库。gh CLI 已确认未装未认证，
#   改用 curl 免认证。
# 接口（方向 2 修正）：弃 /commits/<sha>/check-runs（实测 total_count 恒 0，
#   真结论在 /actions/runs），改查 /repos/{slug}/actions/runs?head_sha=<sha>。
#   推送前置核对两个 sha：待推送 HEAD（重推场景有历史 run）与上一个已推送
#   提交（origin/dev HEAD——新 HEAD 未推送时 GitHub 无其 run，422/空即
#   放行，须以已推送提交的 CI 状态为主判据）。
# 判定语义（fail-open 于 API 抖动，fail-closed 于真实失败结论）：
#   - workflow_runs 中 conclusion 含 failure/cancelled/timed_out → 阻断
#   - 无 run 记录（新 commit 未推送 / head_sha 无匹配）→ 放行
#   - 网络失败 / 429 限流 / 5xx / 未知响应 / 响应非 JSON → 降级告警不阻断
#   - 404（仓库非公开/不存在）→ 登记放弃并写明，不阻断（不静默）
# 用法：python3 harness/lib/check_ci_head.py --head <sha> --prev-head <sha>
#   [--repo-slug <owner/repo>]
# 退出码：0 放行（含降级）/ 1 CI 有失败结论阻断 / 2 参数错误
# ============================================================

import argparse
import json
import re
import subprocess
import sys

# 判定为「CI 失败」的 conclusion 集（其余结论一律放行，包括 neutral/
# skipped/started/pending/null 等未定性状态）
_BAD_CONCLUSIONS = {"failure", "cancelled", "timed_out"}

# 免费额度限流（HTTP 429/403 限流头），公共仓免认证 60 次/小时
_API_LIMIT_CODES = ("403", "429")


def _repo_slug_from_remote(repo: str = "."):
    """从 git remote.origin.url 推断 owner/repo（ssh 与 https 两种格式）。"""
    try:
        r = subprocess.run(["git", "-C", repo, "remote", "get-url", "origin"],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    url = (r.stdout or "").strip()
    if not r.returncode == 0 or not url:
        return None
    m = re.search(r"(?:github\.com[:/])([^/\s]+/[^/\s]+?)(?:\.git)?$", url)
    return m.group(1) if m else None


def _curl(url: str):
    """curl 免认证 GET；返回 (http_code, body)；网络失败返回 (None, err)。"""
    try:
        r = subprocess.run(["curl", "-sS", "-w", "\n%{http_code}", url],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=30)
    except (OSError, subprocess.SubprocessError) as e:
        return None, str(e)
    lines = (r.stdout or "").rsplit("\n", 1)
    body = lines[0] if lines else ""
    code = lines[-1].strip() if len(lines) > 1 else ""
    return code, body


def _check_sha_runs(sha: str, slug: str, label: str):
    """查单个 sha 的 actions runs；返回 rc（0 放行，1 阻断）。"""
    if not sha:
        return 0
    url = (f"https://api.github.com/repos/{slug}/actions/runs"
           f"?head_sha={sha}&per_page=10")
    code, body = _curl(url)
    if code is None:
        print(f"warn: check_ci_head 网络失败（{body}）——API 抖动降级告警，"
              f"不阻断推送", file=sys.stderr)
        return 0
    if code == "404":
        print(f"warn: check_ci_head 仓库 {slug} 非公开或不可达（404）"
              f"——登记放弃 CI 前置，本推送不查 CI（非静默）", file=sys.stderr)
        return 0
    if code in _API_LIMIT_CODES or code.startswith("5"):
        print(f"warn: check_ci_head API 限流/服务端异常（HTTP {code}）"
              f"——降级告警，不阻断推送", file=sys.stderr)
        return 0
    if code != "200":
        print(f"warn: check_ci_head API 未知响应（HTTP {code}）"
              f"——降级告警，不阻断推送", file=sys.stderr)
        return 0
    try:
        data = json.loads(body)
    except ValueError as e:
        print(f"warn: check_ci_head API 响应非 JSON（{e}）"
              f"——降级告警，不阻断推送", file=sys.stderr)
        return 0
    runs = data.get("workflow_runs") or []
    if not runs:
        print(f"warn: check_ci_head {label} 无 actions run 记录"
              f"（新提交未推送或无匹配 head_sha）——放行", file=sys.stderr)
        return 0
    bad = sorted({r.get("conclusion") for r in runs
                  if r.get("conclusion") in _BAD_CONCLUSIONS})
    if bad:
        print(f"error: CI run 存在失败结论 {bad}（{label}，共 {len(runs)}"
              f" run）——阻断推送，修复 CI 或登记放弃后重试", file=sys.stderr)
        return 1
    ids = [r.get("id") for r in runs[:3]]
    print(f"OK: CI run {ids}（{label}，{len(runs)} run）结论全部非失败")
    return 0


def check_ci(head_sha: str, prev_head_sha: str, repo_slug: str):
    """核对 HEAD 与上一个已推送提交的 actions runs；返回 rc（0/1）。"""
    rc = _check_sha_runs(prev_head_sha, repo_slug, "上一个已推送提交")
    if rc != 0:
        return rc
    return _check_sha_runs(head_sha, repo_slug, "待推送 HEAD")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="push 前置 CI 门禁（curl 免认证查 actions runs conclusion）")
    ap.add_argument("--head", required=True, help="待推送 HEAD sha")
    ap.add_argument("--prev-head", default=None,
                    help="上一个已推送提交 sha（缺省取 origin/dev HEAD）")
    ap.add_argument("--repo-slug", default=None,
                    help="GitHub owner/repo（缺省从 git remote.origin.url 推断）")
    ap.add_argument("--repo", default=".",
                    help="git 仓库根（remote/prev-head 推断用；测试注入）")
    args = ap.parse_args(argv)
    for name in ("head", "prev-head"):
        val = getattr(args, name.replace("-", "_"))
        if val and not re.fullmatch(r"[0-9a-fA-F]{7,40}", val):
            print(f"error: --{name} 非法（须 7~40 位 hex）: {val}", file=sys.stderr)
            return 2
    slug = args.repo_slug or _repo_slug_from_remote(args.repo)
    if not slug:
        print("error: 无法解析 GitHub owner/repo（remote.origin.url 缺失或非"
              " github 域）——CI 前置无法执行，阻断", file=sys.stderr)
        return 2
    prev = args.prev_head
    if not prev:
        try:
            r = subprocess.run(["git", "-C", args.repo, "rev-parse",
                                "origin/dev"], capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=30)
            prev = r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            prev = None
    return check_ci(args.head, prev, slug)


if __name__ == "__main__":
    sys.exit(main())
