"""check_host_tests 检查器测试：host 单测 rc 判定 + 目录缺失/工具缺失判红。"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_host_tests as cht  # noqa: E402


class TestCheckHostTests(unittest.TestCase):
    def test_run_one_make_test_success(self):
        with mock.patch.object(cht.subprocess, "run") as m:
            m.return_value.returncode = 0
            rc, out = cht._run_make_test("LcView")
            self.assertEqual(rc, 0)
            self.assertIn("host_rc=0", out)

    def test_run_one_make_test_fail(self):
        with mock.patch.object(cht.subprocess, "run") as m:
            m.return_value.returncode = 2
            rc, out = cht._run_make_test("LcView")
            self.assertEqual(rc, 1)
            self.assertIn("host_rc=1", out)

    def test_clean_always_runs_after_test(self):
        with mock.patch.object(cht.subprocess, "run") as m:
            m.side_effect = [mock.Mock(returncode=0), mock.Mock(returncode=0)]
            cht._run_make_test("LcView")
            # 先 make test 后 make clean（清掉产物防污染 git status）
            self.assertEqual(m.call_count, 2)
            self.assertIn("clean", str(m.call_args_list[1]))

    def test_make_dir_missing_returns_one(self):
        rc, out = cht._run_make_test("__nonexistent_module__")
        self.assertEqual(rc, 1)
        self.assertIn("host_rc=1", out)


if __name__ == "__main__":
    unittest.main()
