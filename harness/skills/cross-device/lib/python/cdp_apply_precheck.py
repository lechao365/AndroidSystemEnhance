"""apply 侧 precheck：分支 dev / 工作树干净 / HEAD==origin/dev / base 匹配。

与 emit 侧 cdp_emit_precheck.py 同款结构（方向 3）：apply 机执行批次编辑
前的前置门禁机器化——SKILL 步骤 2 的门禁（git branch --show-current 须为
dev、git status --porcelain 须为空）与步骤 3 的 base 拒批（--expect-base
比对）统一由本脚本判定，输出 JSON {ok, reason, detail, base} 供编排层
（cross-device-apply SKILL）与人工核对。base 拒批 exit 18 语义由
cdp_parse 承担，此处 base 匹配校验为同源双保险（防批次 base 与本地
HEAD 漂移）。

角色门禁：require_role("apply")——apply 侧命令不得在 emit 设备误跑。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[5]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from harness.lib.role_guard import require_role  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def precheck(expect_base=None, root=None, check_origin=True):
    """apply 前置校验：返回 (ok, reason, detail)。fail-closed——git 命令
    失败（非零退出）不得按空 stdout 放行，显式检查 returncode。"""
    root = Path(root) if root else Path.cwd()
    branch_r = _git(root, "branch", "--show-current")
    if branch_r.returncode != 0:
        return False, "git branch 失败（分支不可判，拒绝编辑）", \
            (branch_r.stderr or "").strip()[:200]
    branch = branch_r.stdout.strip()
    if branch != "dev":
        return False, f"当前分支 {branch!r} 非 dev（apply 仅限 dev 分支编辑）", ""
    status_r = _git(root, "status", "--porcelain")
    if status_r.returncode != 0:
        return False, "git status 失败（工作树状态不可判，拒绝编辑）", \
            (status_r.stderr or "").strip()[:200]
    if status_r.stdout.strip():
        return False, "工作树不干净（未提交改动将干扰批次编辑）", ""
    if check_origin:
        head_r = _git(root, "rev-parse", "--short=12", "HEAD")
        origin_r = _git(root, "rev-parse", "--short=12", "origin/dev")
        if head_r.returncode != 0:
            return False, "git rev-parse HEAD 失败（HEAD 不可判，拒绝编辑）", \
                (head_r.stderr or "").strip()[:200]
        if origin_r.returncode != 0:
            return False, "git rev-parse origin/dev 失败（origin 不可判）", \
                (origin_r.stderr or "").strip()[:200]
        head = head_r.stdout.strip()
        origin = origin_r.stdout.strip()
        if head != origin:
            return False, f"本地 HEAD({head}) != origin/dev({origin})（先拉平）", ""
        if expect_base and head != expect_base.lower():
            return False, f"expect-base({expect_base}) != 本地 HEAD({head})（批次 base 拒批）", ""
        return True, "", ""
    # 不查 origin：仍须 base 匹配本地 HEAD（--no-origin-check 仅用于离线/测试）
    head_r = _git(root, "rev-parse", "--short=12", "HEAD")
    if head_r.returncode != 0:
        return False, "git rev-parse HEAD 失败（HEAD 不可判，拒绝编辑）", \
            (head_r.stderr or "").strip()[:200]
    head = head_r.stdout.strip()
    if expect_base and head != expect_base.lower():
        return False, f"expect-base({expect_base}) != 本地 HEAD({head})（批次 base 拒批）", ""
    return True, "", ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="apply precheck")
    ap.add_argument("--expect-base", default=None,
                    help="批次 base（12hex，须等于本地 HEAD 前 12 位）")
    ap.add_argument("--no-origin-check", action="store_true",
                    help="跳过 origin/dev 比对（离线/测试）")
    ap.add_argument("--root", default=None, help="仓库根（测试注入）")
    args = ap.parse_args(argv)
    # 角色机器化门禁：apply precheck 为 apply 侧命令（批次编辑前置），
    # 参数解析后、副作用前拦截非 apply 设备
    require_role("apply")
    ok, reason, detail = precheck(expect_base=args.expect_base,
                                  root=args.root,
                                  check_origin=not args.no_origin_check)
    out = {"ok": ok, "reason": reason, "detail": detail[:100]}
    if ok:
        r = _git(Path(args.root) if args.root else Path.cwd(),
                 "rev-parse", "--short=12", "HEAD")
        out["base"] = r.stdout.strip() if r.returncode == 0 else ""
    print(json.dumps(out, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
