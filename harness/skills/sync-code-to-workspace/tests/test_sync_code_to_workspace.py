"""sync_code_to_workspace 单元测试

覆盖核心逻辑：EXTRA 删除语义（Bug 1/2）、check-only 失败退出码（Bug 3）、
verify 扫描错误保护（Bug 4）、project.list 缺失（Bug 5）、apply 格式行告警（Bug 7）
以及 _select_all / verify 四分类等既有行为。
"""

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import sync_code_to_workspace as sw


def _git(cwd, *args):
    r = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
    return r.stdout.strip()


def _make_git_repo() -> Path:
    """初始化带 base.txt 的临时 git 仓库，返回路径。"""
    d = Path(tempfile.mkdtemp())
    _git(d, "init", "-q")
    _git(d, "config", "user.email", "t@t")
    _git(d, "config", "user.name", "t")
    (d / "base.txt").write_text("base", encoding="utf-8")
    _git(d, "add", "base.txt")
    _git(d, "commit", "-qm", "init")
    return d


class TestDoSyncExtra(unittest.TestCase):
    """Bug 1/2：EXTRA-NEW-TRACKED 须同时清理 index 与工作树。"""

    def test_extra_new_tracked_removes_index_and_worktree(self):
        repo = _make_git_repo()
        f = repo / "new_tracked.txt"
        f.write_text("x", encoding="utf-8")
        _git(repo, "add", "new_tracked.txt")
        with mock.patch.object(sw, "_kernel_ws", return_value=str(repo)):
            ok = sw._do_sync_extra("kernel", "new_tracked.txt", "EXTRA-NEW-TRACKED")
        self.assertTrue(ok)
        self.assertFalse(f.exists())
        self.assertEqual(_git(repo, "status", "--porcelain"), "")

    def test_extra_new_tracked_aosp_scope_removes_index_and_worktree(self):
        ws = Path(tempfile.mkdtemp())
        scope_dir = ws / "device" / "brcm" / "rpi5"
        scope_dir.mkdir(parents=True)
        _git(scope_dir, "init", "-q")
        _git(scope_dir, "config", "user.email", "t@t")
        _git(scope_dir, "config", "user.name", "t")
        (scope_dir / "base.txt").write_text("base", encoding="utf-8")
        _git(scope_dir, "add", "base.txt")
        _git(scope_dir, "commit", "-qm", "init")
        f = scope_dir / "new_tracked.txt"
        f.write_text("x", encoding="utf-8")
        _git(scope_dir, "add", "new_tracked.txt")
        with mock.patch.object(sw, "_aosp_ws", return_value=str(ws)):
            ok = sw._do_sync_extra("aosp:device/brcm/rpi5",
                                   "new_tracked.txt", "EXTRA-NEW-TRACKED")
        self.assertTrue(ok)
        self.assertFalse(f.exists())
        self.assertEqual(_git(scope_dir, "status", "--porcelain"), "")

    def test_extra_modified_checkout_restores(self):
        repo = _make_git_repo()
        (repo / "base.txt").write_text("modified", encoding="utf-8")
        base = _git(repo, "rev-parse", "HEAD")
        with mock.patch.object(sw, "_kernel_ws", return_value=str(repo)), \
             mock.patch.object(sw, "_find_upstream_base", return_value=base):
            ok = sw._do_sync_extra("kernel", "base.txt", "EXTRA-MODIFIED")
        self.assertTrue(ok)
        self.assertEqual((repo / "base.txt").read_text(encoding="utf-8"), "base")

    def test_extra_new_untracked_removes_worktree(self):
        repo = _make_git_repo()
        f = repo / "untracked.txt"
        f.write_text("u", encoding="utf-8")
        with mock.patch.object(sw, "_kernel_ws", return_value=str(repo)):
            ok = sw._do_sync_extra("kernel", "untracked.txt", "EXTRA-NEW-UNTRACKED")
        self.assertTrue(ok)
        self.assertFalse(f.exists())
        self.assertEqual(_git(repo, "status", "--porcelain"), "")

    def test_unknown_category_returns_false(self):
        repo = _make_git_repo()
        with mock.patch.object(sw, "_kernel_ws", return_value=str(repo)):
            ok = sw._do_sync_extra("kernel", "x.txt", "EXTRA-UNKNOWN")
        self.assertFalse(ok)


class TestSelectAll(unittest.TestCase):
    """--auto 全选：仅 '-' 行翻转为 '+'，注释/空行/已 '+' 保持。"""

    def test_only_dash_marked_lines_flipped(self):
        p = Path(tempfile.mkdtemp()) / "plan.tsv"
        p.write_text("# header\n\n"
                     "+\tMODIFIED-DIVERGED\tkernel\ta.c\tcheckout\tx\n"
                     "-\tNEW-MISMATCH\tkernel\tb.c\trestore\ty\n",
                     encoding="utf-8")
        sw._select_all(str(p))
        content = p.read_text(encoding="utf-8")
        self.assertIn("# header", content)
        self.assertIn("+\tMODIFIED-DIVERGED\tkernel\ta.c\tcheckout\tx", content)
        self.assertIn("+\tNEW-MISMATCH\tkernel\tb.c\trestore\ty", content)
        self.assertNotIn("\n-\t", content)


class TestVerifyAfterApply(unittest.TestCase):
    """落盘校验四分类：FIXED / KEPT / RESIDUAL / NEW-DIFF。"""

    def _run_verify(self, orig_text: str, new_text: str) -> tuple[bool, str]:
        tmp = Path(tempfile.mkdtemp())
        orig = tmp / "plan.tsv"
        orig.write_text(orig_text, encoding="utf-8")
        new_plan = tmp / "new.tsv"
        new_plan.write_text(new_text, encoding="utf-8")
        verify_out = tmp / "verify.tsv"

        def _write_new(out):
            Path(out).write_text(new_plan.read_text(encoding="utf-8"))
            return 0

        with mock.patch.object(sw, "_gen_plan_silent", side_effect=_write_new), \
             mock.patch.object(sw, "_artifact_path", return_value=str(verify_out)):
            ok = sw._verify_after_apply(str(orig))
        return ok, verify_out.read_text(encoding="utf-8")

    def test_fixed_kept_residual_newdiff(self):
        orig = ("+\tMODIFIED-DIVERGED\tkernel\ta.c\tcheckout\tx\n"
                "-\tNEW-MISMATCH\tkernel\tb.c\trestore\ty\n"
                "+\tEXTRA-MODIFIED\taosp:dev\tc.c\tsync\tz\n")
        new = ("+\tMODIFIED-DIVERGED\tkernel\ta.c\tcheckout\tx\n"
               "+\tNEW-MISMATCH\tkernel\tb.c\trestore\ty\n"
               "+\tNEW-MISMATCH\taosp:dev\td.c\trestore\tw\n")
        ok, content = self._run_verify(orig, new)
        self.assertFalse(ok)
        self.assertIn("FIXED\taosp:dev\tc.c", content)
        self.assertIn("KEPT\tkernel\tb.c", content)
        self.assertIn("RESIDUAL\tkernel\ta.c", content)
        self.assertIn("NEW-DIFF\taosp:dev\td.c", content)

    def test_all_fixed_returns_true(self):
        orig = ("+\tMODIFIED-DIVERGED\tkernel\ta.c\tcheckout\tx\n"
                "-\tNEW-MISMATCH\tkernel\tb.c\trestore\ty\n")
        new = ("+\tNEW-MISMATCH\tkernel\tb.c\trestore\ty\n")
        ok, content = self._run_verify(orig, new)
        self.assertTrue(ok)
        self.assertIn("FIXED\tkernel\ta.c", content)
        self.assertIn("KEPT\tkernel\tb.c", content)
        self.assertNotIn("RESIDUAL\t", content)
        self.assertNotIn("NEW-DIFF\t", content)

    def test_scan_error_fails_verify(self):
        """Bug 4：verify 重扫出现错误时须判定失败，不得把失败当成功。"""
        tmp = Path(tempfile.mkdtemp())
        orig = tmp / "plan.tsv"
        orig.write_text("+\tMODIFIED-DIVERGED\tkernel\ta.c\tcheckout\tx\n",
                        encoding="utf-8")
        with mock.patch.object(sw, "_gen_plan_silent", return_value=1), \
             mock.patch.object(sw, "_artifact_path",
                               return_value=str(tmp / "verify.tsv")):
            ok = sw._verify_after_apply(str(orig))
        self.assertFalse(ok)


class TestGenPlanSilent(unittest.TestCase):
    """Bug 4：_gen_plan_silent 须返回扫描错误计数（供 verify 判定）。"""

    def test_returns_error_count(self):
        out = str(Path(tempfile.mkdtemp()) / "plan.tsv")
        repo = _make_git_repo()
        with mock.patch.object(sw, "_kernel_ws", return_value=str(repo)), \
             mock.patch.object(sw, "_aosp_ws", return_value=""), \
             mock.patch.object(sw, "_scan_kernel_modified", return_value=(0, 2)), \
             mock.patch.object(sw, "_scan_kernel_new", return_value=(0, 0)), \
             mock.patch.object(sw, "_scan_extra_kernel", return_value=(0, 0)):
            rc = sw._gen_plan_silent(out)
        self.assertEqual(rc, 2)

    def test_zero_errors_when_clean(self):
        out = str(Path(tempfile.mkdtemp()) / "plan.tsv")
        with mock.patch.object(sw, "_kernel_ws", return_value=""), \
             mock.patch.object(sw, "_aosp_ws", return_value=""):
            rc = sw._gen_plan_silent(out)
        self.assertEqual(rc, 0)


class TestScanAospModified(unittest.TestCase):
    """Bug 5：project.list 缺失须报错，不得静默返回空。"""

    def test_project_list_missing_reports_error(self):
        ws = Path(tempfile.mkdtemp())
        (ws / ".repo").mkdir()
        out = str(Path(tempfile.mkdtemp()) / "out.tsv")
        with mock.patch.object(sw, "_aosp_ws", return_value=str(ws)), \
             mock.patch.object(sw, "log_error") as le:
            m, e = sw._scan_aosp_modified(out)
        self.assertEqual(e, 1)
        le.assert_called_once()


class TestScanKernelModified(unittest.TestCase):
    """Bug 6：git diff 失败不得静默当作空 diff（误标缺失 code 定制）。"""

    def _make_code_with_diff(self):
        code = Path(tempfile.mkdtemp())
        dfile = code / "kernel" / "modified" / "drivers" / "a.c.diff"
        dfile.parent.mkdir(parents=True)
        dfile.write_text("diff --git a/drivers/a.c b/drivers/a.c\n", encoding="utf-8")
        return code

    def test_git_diff_failure_reports_error(self):
        code = self._make_code_with_diff()
        repo = _make_git_repo()
        out = Path(tempfile.mkdtemp()) / "out.tsv"
        out.touch()
        failed = mock.Mock(returncode=-1, stdout="", stderr="boom")
        with mock.patch.object(sw, "_patch_root", return_value=code), \
             mock.patch.object(sw, "_kernel_ws", return_value=str(repo)), \
             mock.patch.object(sw, "_find_upstream_base", return_value="HEAD"), \
             mock.patch.object(sw, "_git_run", return_value=failed):
            m, e = sw._scan_kernel_modified(str(out))
        self.assertEqual(e, 1)
        self.assertEqual(out.read_text(encoding="utf-8"), "")

    def test_normal_diff_ok(self):
        code = self._make_code_with_diff()
        repo = _make_git_repo()
        out = str(Path(tempfile.mkdtemp()) / "out.tsv")
        real = sw._git_run
        with mock.patch.object(sw, "_patch_root", return_value=code), \
             mock.patch.object(sw, "_kernel_ws", return_value=str(repo)), \
             mock.patch.object(sw, "_find_upstream_base", return_value="HEAD"):
            m, e = sw._scan_kernel_modified(out)
        self.assertEqual(e, 0)


class TestApplyPlan(unittest.TestCase):
    """Bug 7：格式错误行须告警，不静默跳过。"""

    def test_malformed_line_logs_warning(self):
        tmp = Path(tempfile.mkdtemp())
        plan = tmp / "plan.tsv"
        plan.write_text("+\tMODIFIED-DIVERGED\tkernel\ta.c\tcheckout\tx\n"
                        "+\tBADLINE\n",
                        encoding="utf-8")
        with mock.patch.object(sw, "_do_checkout_patch", return_value=True), \
             mock.patch.object(sw, "log_warn") as lw:
            ok = sw._apply_plan(str(plan))
        self.assertTrue(ok)
        self.assertTrue(any("格式异常" in c[0][0] for c in lw.call_args_list))


class TestMainCheckOnly(unittest.TestCase):
    """Bug 3：--check-only 扫描失败须非 0 退出（exit 3）。"""

    def _run_main(self):
        repo = _make_git_repo()
        ws_a = Path(tempfile.mkdtemp())
        (ws_a / ".repo").mkdir()
        code_root = Path(tempfile.mkdtemp())
        (code_root / ".git").mkdir()
        def _exit(code=0):
            raise SystemExit(code)

        with mock.patch.object(sys, "argv", ["sync_code_to_workspace.py", "--check-only"]), \
             mock.patch.object(sw, "_kernel_ws", return_value=str(repo)), \
             mock.patch.object(sw, "_aosp_ws", return_value=str(ws_a)), \
             mock.patch.object(sw, "_patch_root", return_value=code_root), \
             mock.patch.object(sw, "_gen_plan", return_value=1), \
             mock.patch.object(sw, "_git_check", return_value=True), \
             mock.patch.object(sw, "harness_init"), \
             mock.patch.object(sw, "harness_exit", side_effect=_exit):
            try:
                sw.main()
            except SystemExit as e:
                return e.code
        return None

    def test_check_only_scan_failure_exits_3(self):
        self.assertEqual(self._run_main(), 3)


if __name__ == "__main__":
    unittest.main()
