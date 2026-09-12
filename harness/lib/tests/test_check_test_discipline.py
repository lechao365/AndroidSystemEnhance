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

    def test_new_unittest_skip_variants_reported(self):
        # tst-01 红灯：unittest skip 装饰器（skip / skipUnless / skipIf——
        # 本仓最惯用的掩盖修法，此前正则漏网）→ 违规。
        # 模式字面量拆分书写，防 discipline 守卫扫 diff 新增行自触发
        s = "skip"
        variants = (
            "@unittest." + s + "('屏蔽')",
            "@unittest.skip" + "Unless(False, '屏蔽')",
            "@unittest.skip" + "If(True, '屏蔽')",
        )
        for deco in variants:
            with self.subTest(deco=deco):
                out = self._edit(
                    "import unittest\n\n" + deco + "\ndef test_f():\n"
                    "    pass\n")
                self.assertTrue(any("skip" in o for o in out))

    def test_new_module_pytestmark_skip_reported(self):
        # tst-01 红灯：模块级 pytestmark 赋值 pytest.mark 之 skip（含列表
        # 包裹写法）此前不命中 → 违规（模式字面量拆分防守卫自触发）
        skip_call = "pytest.mark." + "skip" + "('屏蔽')"
        for mark in ("pytestmark = " + skip_call,
                     "pytestmark = [" + skip_call + "]"):
            with self.subTest(mark=mark):
                out = self._edit(
                    "import pytest\n\n" + mark + "\n\ndef test_f():\n"
                    "    pass\n")
                self.assertTrue(any("skip" in o for o in out))

    def test_new_sleep_reported(self):
        # 新增 time.sleep（sleep 重试掩盖竞态）→ 违规
        out = self._edit(
            "import time\n\ndef test_f():\n    time.sleep(2)\n")
        self.assertTrue(any("sleep" in o for o in out))

    def test_non_test_file_not_scanned(self):
        # 非测试文件（无 tests/ 且非 test 命名）新增 xfail 不扫（守卫面=测试，
        # xfail/skip/sleep 禁令只管测试文件）；但方向 1 门禁会判"生产改动须带
        # 测试"——此处验证两条语义分离：违规是"须带对应 tests"，不是 xfail
        src = self.repo / "harness" / "lib" / "foo.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("import pytest\nxfail = pytest.mark.xfail\n")
        out = ctd.scan(self.repo)
        self.assertTrue(any("行为性改动" in o and "foo.py" in o for o in out), out)
        self.assertFalse(any("xfail" in o for o in out), out)

    # ── 方向 1：harness/lib|skills 下 .py 行为性改动须带对应 tests/ 改动 ──
    def test_production_change_without_test_reported(self):
        # 红灯：harness/lib 下生产 .py 新增，同批次无对应 tests/ 改动 → 判红
        # （skills 加 73 行 0 测试照样绿的漏洞，7.5 清单最后一条实质门禁）
        src = self.repo / "harness" / "lib" / "foo.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("def f():\n    return 1\n")
        out = ctd.scan(self.repo)
        self.assertTrue(
            any("foo.py" in o and "行为性改动" in o
                and "test_foo.py" in o for o in out), out)

    def test_skills_production_change_without_test_reported(self):
        # 红灯：harness/skills 下生产 .py 改动（skill 加 73 行 0 测试场景）
        src = self.repo / "harness" / "skills" / "ws_x" / "ws_x.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("def f():\n    return 1\n")
        out = ctd.scan(self.repo)
        self.assertTrue(any("ws_x.py" in o and "行为性改动" in o for o in out), out)

    def test_production_change_with_test_ok(self):
        # 生产 .py 与对应 tests/ 文件同批改动 → 放行
        src = self.repo / "harness" / "lib" / "foo.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("def f():\n    return 2\n")
        tst = self.repo / "harness" / "lib" / "tests" / "test_foo.py"
        tst.write_text("def test_f():\n    assert True\n")
        self.assertEqual(ctd.scan(self.repo), [])

    def test_production_change_exempt_ok(self):
        # 豁免通道：纯重构/文档注释改动的生产 .py 登记豁免 → 放行（防卡死）
        exempt = self.repo / "harness" / "config" / "test-delete-exempt.txt"
        exempt.parent.mkdir(parents=True, exist_ok=True)
        exempt.write_text("harness/lib/foo.py\n")
        src = self.repo / "harness" / "lib" / "foo.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("def f():\n    return 3\n")
        self.assertEqual(ctd.scan(self.repo), [])

    def test_non_harness_py_not_scanned(self):
        # 守卫面只 harness/lib 与 harness/skills；其余 .py（如 scripts/）不判
        src = self.repo / "scripts" / "tool.py"
        src.parent.mkdir(parents=True, exist_ok=True)
        src.write_text("def f():\n    return 1\n")
        self.assertEqual(ctd.scan(self.repo), [])

    def test_untracked_new_test_file_reported(self):
        # 方向 4 红灯：新增但未 git add 的测试文件（git diff --name-only HEAD
        # 不列未跟踪）含违禁修法 → 旧逻辑整文件漏判假绿；文件面并入未跟踪
        # （--others --exclude-standard）后必须判红。模式字面量拆分拼接，
        # 防本文件源码自触发 discipline 守卫
        cases = {
            "sleep": "import time\n\ndef test_f():\n    time." + "sleep" + "(2)\n",
            "xfail": "import pytest\n\n@pytest.mark." + "xfail" + "\n"
                     "def test_f():\n    pass\n",
        }
        for kind, content in cases.items():
            with self.subTest(kind=kind):
                f = self.repo / "harness" / "lib" / "tests" / "test_untracked.py"
                f.write_text(content)
                out = ctd.scan(self.repo)
                self.assertTrue(any(kind in o for o in out), out)

    def test_deleted_test_file_flagged_without_exempt(self):
        # 方向 2 红灯：删除测试文件（此前只扫新增行完全不可见，删测试换绿
        # 静默通过 discipline_rc=0）→ 未登记豁免即判红
        self.testfile.unlink()
        out = ctd.scan(self.repo)
        self.assertTrue(any("测试文件删除未登记豁免" in o for o in out), out)

    def test_exempt_allows_deleted_test_file(self):
        # 方向 2 豁免通道：删除的测试文件在豁免清单登记 → 放行（正常重构
        # 删测试不卡死；理由须随 commit message 说明）
        exempt = self.repo / "harness" / "config" / "test-delete-exempt.txt"
        exempt.parent.mkdir(parents=True, exist_ok=True)
        exempt.write_text(
            "# 正常重构豁免登记（理由随 commit message）\n"
            "harness/lib/tests/test_x.py\n")
        self.testfile.unlink()
        self.assertEqual(ctd.scan(self.repo), [])

    def test_case_count_net_decrease_flagged(self):
        # 方向 2 红灯：用例数净减（HEAD 3 用例 → 工作树 2 用例，删 1 加 0）
        # → 判红（删测试用例换绿静默流失，不再只盯新增行）
        self.testfile.write_text(
            "def test_a():\n    pass\n\n"
            "def test_b():\n    pass\n\n"
            "def test_c():\n    pass\n")
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-qm", "three")
        self.testfile.write_text(
            "def test_a():\n    pass\n\n"
            "def test_c():\n    pass\n")
        out = ctd.scan(self.repo)
        self.assertTrue(any("用例数净减" in o and "净减 1" in o for o in out), out)

    def test_case_count_equal_ok(self):
        # 用例数不净减（等价重命名 1→1）→ 放行
        self.testfile.write_text("def test_renamed():\n    pass\n")
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
