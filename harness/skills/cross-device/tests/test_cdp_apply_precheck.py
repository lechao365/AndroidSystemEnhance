"""cdp_apply_precheck 单测：apply 前置门禁（分支 dev / 工作树干净 /
HEAD==origin/dev / base 匹配）。

fixture 为真实 git 仓（precheck 全程走 git 命令）：本地 dev + 手工
update-ref refs/remotes/origin/dev 模拟远端。
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_apply_precheck


class TestApplyPrecheck(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._git("init")
        self._git("symbolic-ref", "HEAD", "refs/heads/dev")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")
        self._git("config", "commit.gpgsign", "false")
        (self.root / "README.md").write_text("init\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "init")

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args, check=True):
        r = subprocess.run(["git", "-C", str(self.root), *args],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        if check:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def _head12(self):
        return self._git("rev-parse", "--short=12", "HEAD").stdout.strip()

    def _sync_origin(self):
        self._git("update-ref", "refs/remotes/origin/dev", "HEAD")

    def test_clean_dev_base_match_ok(self):
        # 干净 dev + HEAD==origin/dev + base 匹配 → ok，base 输出本地 HEAD
        self._sync_origin()
        head12 = self._head12()
        ok, reason, _ = cdp_apply_precheck.precheck(
            expect_base=head12, root=self.root)
        self.assertTrue(ok, reason)

    def test_non_dev_branch_rejected(self):
        # 分支非 dev → 拒（apply 仅限 dev 编辑）
        self._git("checkout", "-qb", "other")
        ok, reason, _ = cdp_apply_precheck.precheck(root=self.root)
        self.assertFalse(ok)
        self.assertIn("非 dev", reason)

    def test_dirty_worktree_rejected(self):
        # 工作树不干净 → 拒
        (self.root / "dirty.txt").write_text("x\n", encoding="utf-8")
        ok, reason, _ = cdp_apply_precheck.precheck(root=self.root)
        self.assertFalse(ok)
        self.assertIn("工作树不干净", reason)

    def test_head_diverged_from_origin_rejected(self):
        # 本地 HEAD != origin/dev → 拒（先拉平）
        self._sync_origin()
        (self.root / "new.txt").write_text("y\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "local ahead")
        ok, reason, _ = cdp_apply_precheck.precheck(root=self.root)
        self.assertFalse(ok)
        self.assertIn("!= origin/dev", reason)

    def test_expect_base_mismatch_rejected(self):
        # base 匹配：expect-base != 本地 HEAD → 拒（批次 base 拒批）
        self._sync_origin()
        ok, reason, _ = cdp_apply_precheck.precheck(
            expect_base="0" * 12, root=self.root)
        self.assertFalse(ok)
        self.assertIn("base 拒批", reason)

    def test_no_origin_check_skips_origin_but_keeps_base(self):
        # --no-origin-check：跳过 origin/dev 比对，但 base 匹配仍强制
        ok, _, _ = cdp_apply_precheck.precheck(
            expect_base=self._head12(), root=self.root,
            check_origin=False)
        self.assertTrue(ok)
        ok, reason, _ = cdp_apply_precheck.precheck(
            expect_base="0" * 12, root=self.root, check_origin=False)
        self.assertFalse(ok)
        self.assertIn("base 拒批", reason)


if __name__ == "__main__":
    unittest.main()
