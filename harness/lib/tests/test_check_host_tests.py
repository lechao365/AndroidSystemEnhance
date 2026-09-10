"""check_host_tests 检查器测试：host 单测 rc 判定 + 副本隔离 + main 判红。

副本隔离：_run_make_test 在仓内 gitignored 副本（harness/log/host-tests/
<module>）内跑 make，产物不落 code 工作树。测试一律用临时仓构造模块，
不触碰真实仓与真实 workspace。
"""

import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_host_tests as cht  # noqa: E402

_MAKEFILE = "test:\n\t@true\nclean:\n\t@true\n"


def _make_repo(root: Path, *modules):
    """构造最小仓：<repo>/code/rpi5/kernel/new/vendor/lechao/<module>/tests/Makefile。"""
    repo = Path(root)
    for module in modules:
        d = (repo / "code" / "rpi5" / "kernel" / "new"
             / "vendor" / "lechao" / module / "tests")
        d.mkdir(parents=True)
        (d / "Makefile").write_text(_MAKEFILE, encoding="utf-8")
    return repo


class TestCheckHostTests(unittest.TestCase):
    def setUp(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        self.repo = _make_repo(td.name, "LcView", "LcIod")
        self.src_tests = (self.repo / "code" / "rpi5" / "kernel" / "new"
                          / "vendor" / "lechao" / "LcView" / "tests")

    def test_run_one_make_test_success(self):
        with mock.patch.object(cht.subprocess, "run") as m:
            m.return_value.returncode = 0
            rc, out = cht._run_make_test("LcView", self.repo)
            self.assertEqual(rc, 0)
            self.assertIn("host_rc=0", out)

    def test_run_one_make_test_fail(self):
        with mock.patch.object(cht.subprocess, "run") as m:
            m.return_value.returncode = 2
            rc, out = cht._run_make_test("LcView", self.repo)
            self.assertEqual(rc, 1)
            self.assertIn("host_rc=1", out)

    def test_clean_always_runs_after_test(self):
        with mock.patch.object(cht.subprocess, "run") as m:
            m.side_effect = [mock.Mock(returncode=0), mock.Mock(returncode=0)]
            cht._run_make_test("LcView", self.repo)
            # 先 make test 后 make clean（清掉副本产物）
            self.assertEqual(m.call_count, 2)
            self.assertIn("clean", str(m.call_args_list[1]))

    def test_make_dir_missing_returns_one(self):
        rc, out = cht._run_make_test("__nonexistent_module__", self.repo)
        self.assertEqual(rc, 1)
        self.assertIn("host_rc=1", out)

    # ── 副本隔离（方向 2）：make 在 gitignored 副本内跑、产物不落 code 树 ──
    def test_make_runs_in_staged_copy_not_source(self):
        # make test/clean 的 cwd 必须落在副本 harness/log/host-tests/lechao/
        # <module>/tests（方向 1 起拷整个 vendor/lechao，含顶层
        # kernel_lechao_log.h 供 LcView 调用点 -I../.. 编译），绝不落在
        # code 源码树；跑完副本被整体回收（源码 tests 目录不受影响）
        cwds = []

        def _fake_run(cmd, **kw):
            cwds.append(str(kw["cwd"]))
            return mock.Mock(returncode=0, stdout="ok\n")

        with mock.patch.object(cht.subprocess, "run", side_effect=_fake_run):
            rc, out = cht._run_make_test("LcView", self.repo)
        self.assertEqual(rc, 0)
        stage_tests = (self.repo / "harness" / "log" / "host-tests"
                       / "lechao" / "LcView" / "tests")
        for cwd in cwds:
            self.assertEqual(Path(cwd), stage_tests)
        # 副本收尾回收、源码树无产物无残留
        self.assertFalse((self.repo / "harness" / "log" / "host-tests"
                          / "lechao").exists())
        self.assertTrue((self.src_tests / "Makefile").is_file())

    # ── main 判红（方向 2）：make 缺失 / 超时 / rc 非零三种场景 ────────────
    def _run_main_capture(self, fake_run):
        buf = io.StringIO()
        with mock.patch.object(cht.subprocess, "run", side_effect=fake_run):
            with contextlib.redirect_stdout(buf):
                rc = cht.main(["--repo", str(self.repo)])
        return rc, buf.getvalue()

    def test_main_red_when_make_missing(self):
        # make 命令不可用（subprocess 抛 FileNotFoundError）→ 全模块判红
        rc, out = self._run_main_capture(FileNotFoundError)
        self.assertEqual(rc, 1)
        self.assertIn("make 未安装", out)
        self.assertIn("FAIL", out)

    def test_main_red_when_make_timeout(self):
        # make test 超时（内层 TimeoutExpired）→ 短路判红带归因（超时路径
        # 在 clean 前返回，每模块只消耗一次 subprocess 调用）
        te = cht.subprocess.TimeoutExpired("make test", 300)
        rc, out = self._run_main_capture([te, te])
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("make test 超时"), 2)
        self.assertIn("FAIL", out)

    def test_main_red_when_make_rc_nonzero(self):
        # make test 编译/运行失败返回非零 → 判红透出 host_rc=1
        bad = mock.Mock(returncode=2)
        ok = mock.Mock(returncode=0)
        rc, out = self._run_main_capture([bad, ok, bad, ok])
        self.assertEqual(rc, 1)
        self.assertEqual(out.count("host_rc=1"), 2)
        self.assertIn("FAIL", out)


if __name__ == "__main__":
    unittest.main()
