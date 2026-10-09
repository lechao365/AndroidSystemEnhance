"""check_unverified.py 单元测试：code/ 改动未上板告警检查。

覆盖：无 code 改动放行 / 有 code 改动但最新 board 收据覆盖放行 /
有 code 改动未覆盖告警（rc=1）/ 无最新 board 收据告警。
subprocess 与 latest_board_receipt 打桩（无真实 git 仓/收据依赖）。
"""

import sys
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_unverified as cu


class _Proc:
    def __init__(self, returncode, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _no_code_changes():
    """git log origin/main..HEAD -- code/ 返回空（无 code 改动）。"""
    return mock.patch.object(cu, "_git", return_value=(0, ""))


def _with_code_head(sha="abc123456789"):
    return mock.patch.object(cu, "_git", return_value=(0, sha + "\n" + sha))


class CheckUnverifiedTest(unittest.TestCase):
    def test_no_code_changes_ok(self):
        # 无 code/ 改动 → ok 放行 rc=0
        with _no_code_changes():
            self.assertEqual(cu.main([]), 0)

    def test_git_log_failure_ok(self):
        # git log 失败（无 origin/main 引用等）→ 无法确认改动，放行（fail-open：
        # 登记/推送非晋升，promote 阶段仍有覆盖硬门禁兜底）
        with mock.patch.object(cu, "_git", return_value=(1, "")):
            self.assertEqual(cu.main([]), 0)

    def test_code_changes_no_board_receipt_warns(self):
        # 有 code/ 改动但无最新 board 收据（从未上板验证）→ rc=1 告警
        with _with_code_head():
            with mock.patch.object(cu, "_latest_board_verified", return_value=None):
                self.assertEqual(cu.main([]), 1)

    def test_code_changes_covered_ok(self):
        # 有 code/ 改动且最新 board 收据 verified_commit 覆盖（is-ancestor）→ ok
        with _with_code_head():
            with mock.patch.object(cu, "_latest_board_verified",
                                   return_value="abc123456789"):
                with mock.patch.object(cu, "covered", return_value=True):
                    self.assertEqual(cu.main([]), 0)

    def test_code_changes_uncovered_warns(self):
        # 有 code/ 改动且最新 board 收据未覆盖 → rc=1 告警
        with _with_code_head():
            with mock.patch.object(cu, "_latest_board_verified",
                                   return_value="zzz999999999"):
                with mock.patch.object(cu, "covered", return_value=False):
                    self.assertEqual(cu.main([]), 1)

    def test_covered_is_ancestor(self):
        # covered：is-ancestor 成立即覆盖
        with mock.patch.object(cu, "_git", return_value=(0, "")):
            self.assertTrue(cu.covered("abc123", "def456"))

    def test_covered_parent_equal(self):
        # covered：CODE_HEAD 父 == verified_commit（验证起点在内容提交之前）
        with mock.patch.object(cu, "_git",
                               side_effect=[(1, ""), (0, "def456")]):
            self.assertTrue(cu.covered("abc123", "def456"))

    def test_covered_false(self):
        # 既非 is-ancestor 也非父等价 → 未覆盖
        with mock.patch.object(cu, "_git",
                               side_effect=[(1, ""), (0, "fff999")]):
            self.assertFalse(cu.covered("abc123", "def456"))


if __name__ == "__main__":
    unittest.main()
