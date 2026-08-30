"""emit 侧 precheck：pull / 工作树干净 / HEAD==origin/dev / 上批已推送。

判定「上批已推送」（spec §5.2）：读最新详情 verified_commit →
merge-base --is-ancestor(verified_commit, origin/dev) 且
origin/dev HEAD（short=12） != verified_commit。
无收据（首轮）视为通过。--no-pull 用于干跑（不执行网络操作）。
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cdp_paths import project_root  # noqa: E402
from cdp_receipt import read_latest_receipt  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args],
                          capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=120)


def precheck(root=None, do_pull=True):
    root = Path(root) if root else project_root()
    try:
        if do_pull:
            # --ff-only 防自锁：本地落后时只允许快进，绝不自动 merge 产生分歧
            r = _git(root, "pull", "--ff-only")
            if r.returncode != 0:
                return False, "git pull 失败", r.stderr.strip()[:200]
    except subprocess.TimeoutExpired:
        return False, "git pull 超时", ""
    if _git(root, "status", "--porcelain").stdout.strip():
        return False, "工作树不干净", ""
    head = _git(root, "rev-parse", "HEAD").stdout.strip()
    origin = _git(root, "rev-parse", "origin/dev").stdout.strip()
    if head != origin:
        return False, "本地 HEAD != origin/dev", ""
    # 上批已推送判定（sha 统一 short=12 比较，防 40 位 vs 12 位恒不等）
    # 只依赖详情收据（latest），不依赖 trend.md——trend 是展示性文件，缺失/损坏
    # 不得让"上批已推送"闸门静默失效（严格生产者）；verified_commit 缺失（旧收据）
    # 时无法判定，保持放行兼容。
    latest = read_latest_receipt(root / "data" / "verify-results")
    if latest and latest.verified_commit:
        r = _git(root, "merge-base", "--is-ancestor",
                 latest.verified_commit, "origin/dev")
        origin_head12 = _git(root, "rev-parse", "--short=12",
                             "origin/dev").stdout.strip()
        if r.returncode != 0 or origin_head12 == latest.verified_commit:
            return False, f"上批({latest.batch_id})未推送", ""
    return True, "", ""


def main(argv=None):
    ap = argparse.ArgumentParser(description="emit precheck")
    ap.add_argument("--no-pull", action="store_true", help="干跑：不执行 git pull")
    args = ap.parse_args(argv)
    ok, reason, detail = precheck(do_pull=not args.no_pull)
    print(json.dumps({"ok": ok, "reason": reason, "detail": detail[:100]},
                     ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())