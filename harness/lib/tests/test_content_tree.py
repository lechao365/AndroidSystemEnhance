"""批次 261f10265269 方向 1/3：content_tree 内容树计算。

真 git 仓验证：工作树模式（HEAD+全量含未跟踪）、引用模式（HEAD^{tree}
出发）、统一排除集合（EXCLUDE_PATHS）两侧可比。
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from content_tree import EXCLUDE_PATHS, content_tree, diff_paths  # noqa: E402

REPO_LIB = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("git"), "需要 git 解释器")
class TestContentTree(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        env = dict(os.environ)
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "t"
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "t@t"
        self._env = env
        self._git("init", "-q", "-b", "dev")
        (self.root / "a.txt").write_text("1\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "c1")

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args, check=True):
        r = subprocess.run(["git", *args], cwd=self.root, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           env=self._env)
        if check:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def _tree_paths(self, tree):
        out = self._git("ls-tree", "-r", "--name-only", tree).stdout
        return set(out.splitlines())

    def test_worktree_mode_includes_untracked(self):
        # 工作树模式 = HEAD + 全量未跟踪（排除集合内除外）
        (self.root / "b.txt").write_text("2\n", encoding="utf-8")
        tree = content_tree(repo_root=self.root)
        paths = self._tree_paths(tree)
        self.assertIn("a.txt", paths)
        self.assertIn("b.txt", paths)

    def test_excludes_unified_set(self):
        # EXCLUDE_PATHS 各前缀项均不进树（含收据目录与运行态 harness/log）
        for p in ("docs/x.md", "data/verify-results/r.md",
                  "data/baselines/s.md", "data/known-issues/k.md",
                  "harness/log/cross-device/t.json"):
            f = self.root / p
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text("x\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "c2")
        tree = content_tree(ref="HEAD", repo_root=self.root)
        paths = self._tree_paths(tree)
        self.assertEqual(paths, {"a.txt"})
        self.assertNotIn("harness/config/baseline-status.yaml", paths)
        # 默认排除集合语义：六类前缀
        self.assertEqual(len(EXCLUDE_PATHS), 6)

    def test_ref_mode_matches_worktree_after_receipt_commit(self):
        # 绑定语义核心：收据落盘时工作树树（排除后）== 收据随批提交后
        # HEAD 树（同集合排除）——多出的只有收据目录本身
        (self.root / "b.txt").write_text("2\n", encoding="utf-8")
        wt_tree = content_tree(repo_root=self.root)
        d = self.root / "data" / "verify-results"
        d.mkdir(parents=True)
        (d / "r.md").write_text("receipt\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "c2")
        head_tree = content_tree(ref="HEAD", repo_root=self.root)
        self.assertEqual(wt_tree, head_tree)

    def test_diff_paths_lists_difference(self):
        t1 = content_tree(repo_root=self.root)
        (self.root / "b.txt").write_text("2\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-qm", "c2")
        t2 = content_tree(ref="HEAD", repo_root=self.root)
        self.assertEqual(diff_paths(t1, t2, repo_root=self.root), ["b.txt"])

    def test_empty_repo_no_head(self):
        # 空仓（无 HEAD）：工作树模式从空 index 起步，仍可算树
        empty = Path(tempfile.mkdtemp(dir=self._tmp.name))
        r = subprocess.run(["git", "init", "-q", "-b", "dev"], cwd=empty,
                           capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(r.returncode, 0, r.stderr)
        (empty / "x.txt").write_text("x\n", encoding="utf-8")
        tree = content_tree(repo_root=empty)
        self.assertEqual(self._tree_paths(tree)
                         if False else subprocess.run(
                             ["git", "ls-tree", "-r", "--name-only", tree],
                             cwd=empty, capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             env=self._env).stdout.splitlines(), ["x.txt"])
        shutil.rmtree(empty, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
