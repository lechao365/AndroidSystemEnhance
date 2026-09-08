"""check_ruff 检查器测试：rc 判定 + 结论行 + 工具缺失 fail-closed。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import check_ruff as cr  # noqa: E402


class TestCheckRuff(unittest.TestCase):
    def test_scan_compliant_returns_zero(self):
        rc, out = cr._run_ruff("harness/lib/check_hot_path_scan.py")
        # 目标文件应已 ruff 合规（Phase 1 Task 2 清零）
        self.assertEqual(rc, 0)
        self.assertIn("ruff_rc=0", out)

    def test_violation_returns_one(self):
        # 违规临时文件须落在 ruff scope 外（/tmp）：写在 harness/lib/tests/
        # 会落进并行 check_ruff 的扫描范围（selfcheck spawn 的 check_ruff
        # 默认 --scope harness），违规文件与并行扫描竞态成 flake
        # （批次 7d41df8e24bf 方向 5）。
        with tempfile.TemporaryDirectory() as d:
            tmp = Path(d) / "_ruff_violation_tmp.py"
            tmp.write_text("def f():\n    y = 1\n    return 0\nprint(f())\n",
                           encoding="utf-8")
            rc, out = cr._run_ruff(str(tmp))
            self.assertEqual(rc, 1)
            self.assertIn("ruff_rc=1", out)

    def test_ruff_missing_fail_closed(self):
        # 工具缺失按失败判红（fail-closed：无法检查不得静默绿）；
        # PATH 与用户 bin 均不可得才判红
        with mock.patch.object(cr.shutil, "which", return_value=None), \
                mock.patch.object(cr.Path, "home",
                                  return_value=Path("/nonexistent-home")):
            rc, out = cr._run_ruff("harness/lib/selfcheck.py")
            self.assertEqual(rc, 1)
            self.assertIn("ruff_rc=1", out)

    def test_ruff_path_missing_but_user_bin_found(self):
        # PATH 不含 ruff（systemd 托管环境）但 ~/.local/bin/ruff 存在时，
        # 回退用户 bin 落点继续检查，不误判"未安装"
        tmp = Path(tempfile.mkdtemp())
        ruff_dir = tmp / ".local" / "bin"
        ruff_dir.mkdir(parents=True)
        fake = ruff_dir / "ruff"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)
        with mock.patch.object(cr.shutil, "which", return_value=None), \
                mock.patch.object(cr.Path, "home", return_value=tmp):
            with mock.patch.object(cr.subprocess, "run") as m:
                m.return_value = mock.Mock(returncode=0, stdout="")
                rc, out = cr._run_ruff("harness/lib/selfcheck.py")
                self.assertEqual(rc, 0)
                self.assertIn("ruff_rc=0", out)
                used = m.call_args.args[0]
                self.assertEqual(used[0], str(fake))

    def test_ruff_user_bin_exec_failure_still_red(self):
        # 回退用户 bin 后执行仍失败（违规/异常）→ 照常判红
        tmp = Path(tempfile.mkdtemp())
        ruff_dir = tmp / ".local" / "bin"
        ruff_dir.mkdir(parents=True)
        fake = ruff_dir / "ruff"
        fake.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)
        with mock.patch.object(cr.shutil, "which", return_value=None), \
                mock.patch.object(cr.Path, "home", return_value=tmp), \
                mock.patch.object(cr.subprocess, "run") as m:
            m.return_value = mock.Mock(returncode=1, stdout="violation\n")
            rc, out = cr._run_ruff("harness/lib/selfcheck.py")
            self.assertEqual(rc, 1)
            self.assertIn("ruff_rc=1", out)


if __name__ == "__main__":
    unittest.main()
