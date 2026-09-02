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
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
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

    # ── 分支 4：祖先判定不成立但收据文件被 origin/dev 跟踪 → 放行 ──
    def test_unpushed_by_ancestry_but_receipt_tracked_pass(self):
        # 祖先判定不成立（origin/dev 停在 verified_commit，equality 分支）时
        # 回落检查最新收据文件是否已被 origin/dev 跟踪：收据随 commit 入库
        # （squash/rebase 重写历史后 verified_commit 不再可达），被跟踪则视为
        # 已推送放行
        sha = self._head12()
        self._write_receipt("444444444444", sha)
        self._commit_all()  # 收据入库提交 → data/verify-results 被 dev 跟踪
        self._git("update-ref", "refs/remotes/origin/dev", "HEAD")
        ok, reason, detail = cdp_emit_precheck.precheck(self.root, do_pull=False)
        self.assertTrue(ok, reason)
        self.assertEqual(reason, "")
        self.assertEqual(detail, "")

    # ── KIR-005 存量告警：open/scheduled 条数阈值 8，只告警不阻断 ──
    def _write_index(self, n_open, extra_status=()):
        d = self.root / "data" / "known-issues"
        d.mkdir(parents=True, exist_ok=True)
        lines = [f"KI-{i:04d} batch-test false t{i} open" for i in range(1, n_open + 1)]
        lines += [f"KI-{9000 + i:04d} batch-test false t{i} {s}"
                  for i, s in enumerate(extra_status, start=1)]
        (d / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_warns_7_open_no_warning(self):
        self._write_index(7)
        self.assertEqual(cdp_emit_precheck.known_issues_warns(self.root), [])

    def test_warns_8_open_alerts(self):
        self._write_index(8)
        warns = cdp_emit_precheck.known_issues_warns(self.root)
        self.assertEqual(len(warns), 8)
        self.assertEqual(warns[0], "KI-0001")
        self.assertEqual(warns[-1], "KI-0008")

    def test_warns_fixed_and_wontfix_not_counted(self):
        # 6 条 open + fixed/wontfix 各 1 条 = 6 < 8，不告警
        self._write_index(6, extra_status=("fixed", "wontfix"))
        self.assertEqual(cdp_emit_precheck.known_issues_warns(self.root), [])

    def test_warns_index_missing_no_crash(self):
        self.assertEqual(cdp_emit_precheck.known_issues_warns(self.root), [])

    # ── precheck 领先告警：origin/main..origin/dev 提交数 > 1 即告警 ──
    def _mk_lead(self, n):
        # 造 n 笔领先提交：main 锚在 init，dev 领先 n 笔（update-ref 模拟远端）
        for _ in range(n):
            self._git("commit", "--allow-empty", "-m", "lead")
        self._git("update-ref", "refs/remotes/origin/main", f"HEAD~{n}")
        self._git("update-ref", "refs/remotes/origin/dev", "HEAD")

    def test_lead_one_no_warning(self):
        # 领先 1 笔（正常节奏）→ 无告警
        self._mk_lead(1)
        self.assertEqual(cdp_emit_precheck.lead_warns(self.root), [])

    def test_lead_two_alerts(self):
        # 领先 2 笔（批量连续 apply 中间提交无验证证据）→ 告警串提示先发布基线
        self._mk_lead(2)
        warns = cdp_emit_precheck.lead_warns(self.root)
        self.assertEqual(len(warns), 1)
        self.assertIn("领先 main 2 笔", warns[0])
        self.assertIn("/publish-main-base", warns[0])

    def test_lead_main_missing_no_crash(self):
        # 无 origin/main（新仓未推 main）→ rev-list 报错返空不崩
        self._git("update-ref", "refs/remotes/origin/dev", "HEAD")
        self.assertEqual(cdp_emit_precheck.lead_warns(self.root), [])


if __name__ == "__main__":
    unittest.main()
