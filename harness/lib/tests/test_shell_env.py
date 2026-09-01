"""shell_env 单测：find_bash 三态（PATH 命中/开关关/开关开由 git 推得）与 shim 可执行。"""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import shell_env  # noqa: E402


class TestFindBash(unittest.TestCase):
    def test_path_hit(self):
        # PATH 命中：直接返回 PATH 中的 bash
        with mock.patch("shell_env.shutil.which",
                        side_effect=lambda c: "/usr/bin/bash"
                        if c == "bash" else None):
            self.assertEqual(shell_env.find_bash(), "/usr/bin/bash")

    def test_switch_off_returns_none(self):
        # PATH 无 bash 且 LC_HARNESS_WIN_BASH 非 1 → None（不推 git 路径）
        os.environ.pop("LC_HARNESS_WIN_BASH", None)
        with mock.patch("shell_env.shutil.which", return_value=None) as wh:
            self.assertIsNone(shell_env.find_bash())
            self.assertEqual(wh.call_count, 1)  # 开关关：只查 bash，不查 git

    def test_switch_on_infers_from_git(self):
        # PATH 无 bash 且 LC_HARNESS_WIN_BASH=1 → 由 git 路径推 Git for Windows
        # 的 bin/bash.exe（git 在 <root>/cmd/ 或 <root>/bin/ 两布局均覆盖）
        os.environ["LC_HARNESS_WIN_BASH"] = "1"
        with mock.patch("shell_env.shutil.which",
                        side_effect=lambda c: "/c/Program Files/Git/cmd/git.exe"
                        if c == "git" else None):
            with mock.patch.object(Path, "is_file", return_value=True):
                self.assertEqual(shell_env.find_bash(),
                                 "/c/Program Files/Git/bin/bash.exe")

    def test_switch_on_git_missing_returns_none(self):
        # 开关开但 git 也不在 PATH → None
        os.environ["LC_HARNESS_WIN_BASH"] = "1"
        with mock.patch("shell_env.shutil.which", return_value=None):
            self.assertIsNone(shell_env.find_bash())


class TestWritePython3Shim(unittest.TestCase):
    def test_shim_written_executable(self):
        # 写 shim → 文件存在、置可执行位、能转发 sys.executable 执行
        with tempfile.TemporaryDirectory() as tmp:
            d = shell_env.write_python3_shim(Path(tmp) / "shim")
            self.assertTrue(d.is_dir())
            shim = d / ("python3.exe" if sys.platform == "win32" else "python3")
            self.assertTrue(shim.is_file())
            self.assertTrue(shim.stat().st_mode & stat.S_IEXEC)
            # 转发执行：python3 -c 输出当前解释器路径（正斜杠比对）
            r = subprocess.run([str(shim), "-c", "import sys; print(sys.executable)"],
                               capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip().replace("\\", "/"),
                             sys.executable.replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()