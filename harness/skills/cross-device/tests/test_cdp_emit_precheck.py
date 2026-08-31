import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_emit_precheck
import cdp_receipt


class TestEmitPrecheck(unittest.TestCase):
    """上批已推送判定三分支：可达已推送放行 / 不可达放行记因 / 未推送拒批。

    fixture 为真实 git 仓（precheck 全程走 git 命令）：本地 dev + 手工
    update-ref refs/remotes/origin/dev 模拟远端；--no-pull 干跑不碰网络。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
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
        os.environ.pop("CDP_PROJECT_ROOT")

    def _git(self, *args, check=True):
        r = subprocess.run(["git", "-C", str(self.root), *args],
                           capture_output=True, text=True)
        if check:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def _head12(self):
        return self._git("rev-parse", "--short=12", "HEAD").stdout.strip()

    def _commit_all(self):
        # 收据落盘后入库提交，保证 status --porcelain 干净（precheck 门禁）
        self._git("add", "-A")
        self._git("commit", "--allow-empty", "-m", "receipt")

    def _write_receipt(self, batch_id, verified_commit):
        r = cdp_receipt.Receipt(batch_id=batch_id,
                                verified_commit=verified_commit,
                                batch_base="ad76b9e1b93d",
                                verify_mode="board", result="pass")
        cdp_receipt.write_receipt(r, "test fixture")

    # ── 分支 1：verified_commit 可达且已被 origin/dev 前进覆盖 → 放行 ──
    def test_reachable_pushed_pass(self):
        sha = self._head12()
        self._write_receipt("111111111111", sha)
        self._commit_all()
        self._git("commit", "--allow-empty", "-m", "next")
        self._git("update-ref", "refs/remotes/origin/dev", "HEAD")
        ok, reason, detail = cdp_emit_precheck.precheck(self.root, do_pull=False)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")
        self.assertEqual(detail, "")

    # ── 分支 2：verified_commit 本地不可达 → 放行且 reason 记无法判定 ──
    def test_unreachable_commit_pass_with_reason(self):
        self._write_receipt("222222222222", "deadbeefdead")
        self._commit_all()
        self._git("update-ref", "refs/remotes/origin/dev", "HEAD")
        ok, reason, detail = cdp_emit_precheck.precheck(self.root, do_pull=False)
        self.assertTrue(ok)
        self.assertIn("verified_commit", reason)
        self.assertIn("不可达", reason)
        self.assertIn("无法判定", reason)
        self.assertEqual(detail, "222222222222")

    # ── 分支 3：origin/dev 仍停在 verified_commit → 拒批"未推送" ──
    def test_unpushed_rejected(self):
        # 收据经 .gitignore 不入库（模拟已验证但收据未推送：HEAD/origin/dev
        # 停在 verified_commit，树保持干净），equality 分支必须拒批
        (self.root / ".gitignore").write_text("data/\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "--allow-empty", "-m", "gitignore")
        sha = self._head12()
        self._write_receipt("333333333333", sha)
        self._git("update-ref", "refs/remotes/origin/dev", "HEAD")
        ok, reason, _ = cdp_emit_precheck.precheck(self.root, do_pull=False)
        self.assertFalse(ok)
        self.assertIn("未推送", reason)
        self.assertIn("333333333333", reason)


if __name__ == "__main__":
    unittest.main()
