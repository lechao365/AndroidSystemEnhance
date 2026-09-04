"""内容树计算（批次 261f10265269 方向 1/3）：发布内容与验证内容绑定。

verified_tree = 收据落盘时刻、排除统一集合后的内容树（git 树对象 id，
内容寻址天然可复算）；promote 侧对 dev HEAD^{tree} 以同一算法同一集合
计算后直接比对树 id，绑定"晋升的确切内容 == 验证的确切内容"。

排除集合两侧统一（ws_report 收据侧 / publish promote 侧共用 EXCLUDE_PATHS）：
- harness/config/baseline-status.yaml：promote 自身写入的登记条目，不排除
  则恒判红；
- docs/：文档同步提交；
- data/baselines/、data/known-issues/：promote 生成的证据快照与清算删除，
  对齐 verify-tree 树等价断言的排除范围；
- data/verify-results/：收据与 trend.md 自引用豁免（收据随批入库后仍在
  dev 树中，promote 侧同排除才可比）；
- harness/log/：运行态目录（打点/收据临时态/promote 状态文件），与主仓
  .gitignore 同语义——测试仓无 gitignore 时 add -A 会把它收进树，不排除
  则绑定比对被运行态文件干扰。

实现：临时 index（GIT_INDEX_FILE 指向临时文件）上 read-tree + add -A +
rm --cached 排除项 + write-tree，不触碰用户 index；write-tree 产出真实
树对象，git diff --name-only 可直接用于归因差异路径。
"""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

# 两侧统一的排除集合（前缀匹配：目录带 / 结尾，文件全名）
EXCLUDE_PATHS = (
    "harness/config/baseline-status.yaml",
    "docs/",
    "data/baselines/",
    "data/known-issues/",
    "data/verify-results/",
    "harness/log/",
)


def _run_git(args, env, cwd):
    r = subprocess.run(["git", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=env, cwd=cwd)
    if r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {r.stderr.strip()}")
    return r.stdout.strip()


def content_tree(exclude=EXCLUDE_PATHS, ref=None, repo_root=None):
    """计算排除 exclude 前缀项后的内容树 id（40hex）；失败抛 RuntimeError。

    ref None → 工作树模式（HEAD 为基 + 工作树全量含未跟踪，gitignore 照排）；
    ref 给定 → 从该引用的树出发（promote 侧对 dev HEAD 用）。
    """
    cwd = str(repo_root) if repo_root else os.getcwd()
    env = dict(os.environ)
    fd, tmp_index = tempfile.mkstemp(prefix="cdp-index-")
    os.close(fd)
    os.unlink(tmp_index)  # read-tree 需可写路径，占位删除由 git 重建
    env["GIT_INDEX_FILE"] = tmp_index
    try:
        if ref:
            _run_git(["read-tree", f"{ref}^{{tree}}"], env, cwd)
        else:
            has_head = subprocess.run(
                ["git", "rev-parse", "--verify", "-q", "HEAD"],
                capture_output=True, env=env, cwd=cwd).returncode == 0
            if has_head:
                _run_git(["read-tree", "HEAD"], env, cwd)
            else:
                _run_git(["read-tree", "--empty"], env, cwd)
        _run_git(["add", "-A"], env, cwd)
        for path in exclude:
            _run_git(["rm", "--cached", "-r", "-q", "--ignore-unmatch",
                      "--", path], env, cwd)
        return _run_git(["write-tree"], env, cwd)
    finally:
        if os.path.exists(tmp_index):
            os.unlink(tmp_index)


def diff_paths(tree_a, tree_b, repo_root=None):
    """两树对象的差异路径列表（归因用）；任一树不可解析返 None。"""
    cwd = str(repo_root) if repo_root else os.getcwd()
    r = subprocess.run(["git", "diff", "--name-only", tree_a, tree_b],
                       capture_output=True, text=True,
                       encoding="utf-8", errors="replace", cwd=cwd)
    if r.returncode != 0:
        return None
    return [ln for ln in r.stdout.splitlines() if ln]


def main(argv=None):
    args = argv if argv is not None else sys.argv[1:]
    ref = None
    exclude = list(EXCLUDE_PATHS)
    i = 0
    while i < len(args):
        if args[i] == "--tree":
            i += 1
            if i >= len(args):
                print("error: --tree 需引用参数", file=sys.stderr)
                return 3
            ref = args[i]
        elif args[i] == "--exclude":
            i += 1
            if i >= len(args):
                print("error: --exclude 需路径参数", file=sys.stderr)
                return 3
            exclude.append(args[i])
        else:
            print(f"error: 未知参数 {args[i]}", file=sys.stderr)
            return 3
        i += 1
    try:
        print(content_tree(tuple(exclude), ref=ref))
        return 0
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
