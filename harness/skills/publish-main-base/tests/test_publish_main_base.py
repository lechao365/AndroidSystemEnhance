import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "publish_main_base.sh"

# 工作树恒脏（status --porcelain 非空）；rev-parse --short=12 <sha>^ 返回 verified000000
MOCK_GIT = """#!/usr/bin/env bash
case "$1" in
  status) echo " M dirty.txt" ;;
  rev-parse)
    if [ "$2" = "--short=12" ]; then echo "verified000000"; else echo "0123456789abcdef"; fi ;;
  log) echo "修复(skills): 测试提交" ;;
  *) exit 0 ;;
esac
"""

# 最近内容提交父(PARENTMOCK) != verified_commit(verified000000) → check_class=NEED_VERIFY
MOCK_GIT_NEED_VERIFY = """#!/usr/bin/env bash
case "$1" in
  status) echo " M dirty.txt" ;;
  rev-parse)
    if [ "$2" = "--short=12" ]; then echo "PARENTMOCK"; else echo "0123456789abcdef"; fi ;;
  log) echo "修复(skills): 测试提交" ;;
  *) exit 0 ;;
esac
"""

# python3 收据查询：返回 relpath/result/verified_commit 三行
MOCK_PY = """#!/usr/bin/env bash
echo "data/verify-results/mock-receipt.md"
echo "pass"
echo "verified000000"
"""

# known-issues 门禁模拟：--task 指定（python3 - <task>）时判红/放行
MOCK_PY_GATE_FAIL = """#!/usr/bin/env bash
if [ "$1" = "-" ] && [ -n "$2" ]; then
  echo "KI-1: origin=introduced blocking=True status=open"
  exit 1
fi
echo "data/verify-results/mock-receipt.md"
echo "pass"
echo "verified000000"
"""

MOCK_PY_GATE_PASS = """#!/usr/bin/env bash
if [ "$1" = "-" ] && [ -n "$2" ]; then
  echo "known-issues 门禁通过（task=$2 无未解决阻塞问题）"
  exit 0
fi
echo "data/verify-results/mock-receipt.md"
echo "pass"
echo "verified000000"
"""


@unittest.skipUnless(shutil.which("bash"), "需要 bash 解释器（Windows 环境跳过）")
class TestSyncModifyToMainBase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        bin_dir = Path(self._tmp.name) / "bin"
        bin_dir.mkdir()
        for name, content in (("git", MOCK_GIT), ("python3", MOCK_PY)):
            p = bin_dir / name
            p.write_text(content, encoding="utf-8")
            p.chmod(p.stat().st_mode | stat.S_IEXEC)
        self._env = dict(os.environ)
        self._env["PATH"] = f"{bin_dir}{os.pathsep}{self._env['PATH']}"

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, *args):
        return subprocess.run(["bash", str(SCRIPT), *args],
                              capture_output=True, text=True, env=self._env)

    def test_check_only_skips_dirty_tree_precheck(self):
        # check-only 干跑不做 add/commit/squash，脏树无害：跳过预检仍通过
        r = self._run("--check-only")
        self.assertEqual(r.returncode, 0)
        self.assertIn("前置校验通过", r.stdout)
        self.assertNotIn("工作树非空", r.stderr)

    def test_prepare_still_rejects_dirty_tree(self):
        # prepare 真实登记/推送，脏树必须拒绝（预检未废）
        r = self._run("--prepare")
        self.assertEqual(r.returncode, 1)
        self.assertIn("工作树非空", r.stderr)

    def _swap_python3(self, content):
        p = Path(self._tmp.name) / "bin" / "python3"
        p.write_text(content, encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def _swap_git(self, content):
        p = Path(self._tmp.name) / "bin" / "git"
        p.write_text(content, encoding="utf-8")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    def test_check_outputs_need_verify_class(self):
        # --check 失败分类：最近内容提交父 != verified_commit → NEED_VERIFY（编排进验证路径）
        self._swap_git(MOCK_GIT_NEED_VERIFY)
        r = self._run("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("check_class=NEED_VERIFY", r.stderr)

    def test_check_outputs_ki_blocked_class(self):
        # --check 失败分类：known-issues 门禁失败 → KI_BLOCKED（编排终止禁止 promote）
        self._swap_python3(MOCK_PY_GATE_FAIL)
        r = self._run("--check", "--task", "lcview-refactor")
        self.assertEqual(r.returncode, 1)
        self.assertIn("check_class=KI_BLOCKED", r.stderr)

    def test_check_only_rejects_open_blocking_issues(self):
        # known-issues 门禁：目标任务存在 origin=introduced/blocking 且 status!=fixed 者即拒
        self._swap_python3(MOCK_PY_GATE_FAIL)
        r = self._run("--check-only", "--task", "lcview-refactor")
        self.assertEqual(r.returncode, 1)
        self.assertIn("known-issues 门禁未通过", r.stderr)

    def test_check_only_passes_without_blocking_issues(self):
        # 门禁放行路径：无未解决阻塞问题仍走通既有前置校验
        self._swap_python3(MOCK_PY_GATE_PASS)
        r = self._run("--check-only", "--task", "lcview-refactor")
        self.assertEqual(r.returncode, 0)
        self.assertIn("前置校验通过", r.stdout)


if __name__ == "__main__":
    unittest.main()
