#!/usr/bin/env python3
"""check_test_discipline 单测：测试改动禁新增 xfail/skip/sleep 重试。

真实 git 临时仓（dev 仓必有 git，禁以非 git 仓跳过测试）；改动 = 相对 HEAD
未提交工作树改动。"""
import contextlib
import io
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_test_discipline as ctd  # noqa: E402


def _git(repo: Path, *args):
    subprocess.run(["git", *args], cwd=repo, check=True,
                   capture_output=True, text=True, encoding="utf-8")


class TestDiscipline(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        self.testfile = self.repo / "harness" / "lib" / "tests" / "test_x.py"
        self.testfile.parent.mkdir(parents=True)
        self.testfile.write_text("def test_ok():\n    pass\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "base")

    def tearDown(self):
        self._tmp.cleanup()

    def _edit(self, content: str) -> list[str]:
        self.testfile.write_text(content)
        return ctd.scan(self.repo)

    def test_untouched_clean(self):
        # 无改动：零违规
        self.assertEqual(ctd.scan(self.repo), [])

    def test_new_xfail_reported(self):
        # 新增 @pytest.mark.xfail → 违规（IDLE-006：禁 xfail 掩盖）
        out = self._edit(
            "import pytest\n\ndef test_ok():\n    pass\n\n"
            "@pytest.mark.xfail\ndef test_f():\n    pass\n")
        self.assertTrue(any("xfail" in o for o in out))

    def test_new_skip_reported(self):
        # 新增 pytest.skip / skipif → 违规
        out = self._edit(
            "import pytest\n\ndef test_f():\n    pytest.skip('屏蔽')\n")
        self.assertTrue(any("skip" in o for o in out))

    def test_new_sleep_reported(self):
        # 新增 time.sleep（sleep 重试掩盖竞态）→ 违规
        out = self._edit(
            "import time\n\ndef test_f():\n    time.sleep(2)\n")
        self.assertTrue(any("sleep" in o for o in out))

    def test_non_test_file_not_scanned(self):
        # 非测试文件（无 tests/ 且非 test 命名）新增 xfail 不扫（守卫面=测试）
        src = self.repo / "harness" / "lib" / "foo.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("import pytest\nxfail = pytest.mark.xfail\n")
        self.assertEqual(ctd.scan(self.repo), [])


class TestDisciplineMain(unittest.TestCase):
    def test_non_git_repo_skips(self):
        # 非 git 仓：显式跳过（dev 仓恒为 git，此路径防误跑）
        with tempfile.TemporaryDirectory() as d:
            rc = ctd.main(["--repo", d])
        self.assertEqual(rc, 0)


class TestDisciplineFailClosed(unittest.TestCase):
    """lib-07 / lib-15：git 失败 fail-closed 判红 + sleep 禁令补分支。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@t")
        _git(self.repo, "config", "user.name", "t")
        testdir = self.repo / "harness" / "lib" / "tests"
        testdir.mkdir(parents=True)
        self.testfile = testdir / "test_x.py"
        self.testfile.write_text("def test_ok():\n    pass\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "base")

    def tearDown(self):
        self._tmp.cleanup()

    def test_git_failure_reported_red(self):
        # lib-07 红灯：.git 存在但 git diff 失败（_git_lines 返 None）→
        # 哨兵违规判红，不得静默当"无改动"假绿
        with mock.patch.object(ctd, "_git_lines", return_value=None):
            out = ctd.scan(self.repo)
        self.assertTrue(out)
        self.assertIn("git", out[0])
        err = io.StringIO()
        with mock.patch.object(ctd, "_git_lines", return_value=None), \
                contextlib.redirect_stderr(err):
            rc = ctd.main(["--repo", str(self.repo)])
        self.assertEqual(rc, 1)
        self.assertIn("判红", err.getvalue())

    def test_new_asyncio_sleep_reported(self):
        # lib-15 红灯：新增行 await asyncio 版 sleep 调用判红（此前正则漏
        # asyncio 场景，sleep 重试可换皮绕过守卫）。样例在 sleep 与括号间
        # 拆分拼接（源码行不含相邻调用字样），防本测试文件自身触发守卫
        self.testfile.write_text(
            "import asyncio\n\nasync def test_f():\n"
            "    await asyncio." + "sleep" + "(1)\n")
        out = ctd.scan(self.repo)
        self.assertTrue(any("sleep" in o for o in out))

    def test_new_bare_sleep_reported(self):
        # lib-15 红灯：行首裸 sleep 调用判红（此前正则要求前导非空字符，
        # 行首裸调漏网）。样例同上拆分拼接防自触发
        self.testfile.write_text("def test_f():\n    " + "sleep" + "(1)\n")
        out = ctd.scan(self.repo)
        self.assertTrue(any("sleep" in o for o in out))

    def test_sleep_pattern_branches(self):
        # sleep 正则分支直接验证：time.sleep / 行首裸调 / asyncio.sleep /
        # 非标识符前导裸调均命中；属性式非 sleep 前缀（attr.sleep 等）不误伤。
        # 样例一律 sleep 与括号拆开拼接（源码行防自触发）
        sleep_pat = next(p for p, k in ctd._BANNED if k == "sleep")
        self.assertIsNotNone(
            sleep_pat.search("    time." + "sleep" + "(2)"))
        self.assertIsNotNone(sleep_pat.search("    " + "sleep" + "(1)"))
        self.assertIsNotNone(
            sleep_pat.search(" await asyncio." + "sleep" + "(1)"))
        self.assertIsNotNone(sleep_pat.search("x " + "sleep" + "(1)"))
        self.assertIsNone(sleep_pat.search("    attr." + "sleep" + "(1)"))
        self.assertIsNone(sleep_pat.search("    monkeysleep(1)"))


if __name__ == "__main__":
    unittest.main()
