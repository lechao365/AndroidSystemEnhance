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
        # PATH 无 bash 且 LC_HARNESS_WIN_BASH 非 1 → None（不推 git 路径）；
        # 上下文内 pop 开关键（patch.dict 退出时恢复外部值，不污染后续模块）
        with mock.patch.dict(os.environ, {"LC_HARNESS_WIN_BASH": "1"}):
            os.environ.pop("LC_HARNESS_WIN_BASH")
            with mock.patch("shell_env.shutil.which", return_value=None) as wh:
                self.assertIsNone(shell_env.find_bash())
                self.assertEqual(wh.call_count, 1)  # 开关关：只查 bash，不查 git

    def test_switch_on_infers_from_git(self):
        # PATH 无 bash 且 LC_HARNESS_WIN_BASH=1 → 由 git 路径推 Git for Windows
        # 的 bin/bash.exe（git 在 <root>/cmd/ 布局）；真实临时目录造文件，
        # 不 mock is_file；开关经 patch.dict 局部设置，退出即恢复
        with mock.patch.dict(os.environ, {"LC_HARNESS_WIN_BASH": "1"}):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "cmd").mkdir()
                (root / "bin").mkdir()
                (root / "cmd" / "git.exe").touch()
                (root / "bin" / "bash.exe").touch()
                with mock.patch("shell_env.shutil.which",
                                side_effect=lambda c: str(root / "cmd" / "git.exe")
                                if c == "git" else None):
                    self.assertEqual(shell_env.find_bash(),
                                     str(root / "bin" / "bash.exe"))

    def test_switch_on_bin_layout_infers_same_bash(self):
        # git 在 <root>/bin/ 布局：parent.parent 仍为 root，推得 root/bin/bash.exe
        with mock.patch.dict(os.environ, {"LC_HARNESS_WIN_BASH": "1"}):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / "bin").mkdir()
                (root / "bin" / "git.exe").touch()
                (root / "bin" / "bash.exe").touch()
                with mock.patch("shell_env.shutil.which",
                                side_effect=lambda c: str(root / "bin" / "git.exe")
                                if c == "git" else None):
                    self.assertEqual(shell_env.find_bash(),
                                     str(root / "bin" / "bash.exe"))

    def test_switch_on_git_missing_returns_none(self):
        # 开关开但 git 也不在 PATH → None
        with mock.patch.dict(os.environ, {"LC_HARNESS_WIN_BASH": "1"}):
            with mock.patch("shell_env.shutil.which", return_value=None):
                self.assertIsNone(shell_env.find_bash())


class TestWritePython3Shim(unittest.TestCase):
    def test_shim_written_executable(self):
        # 写 shim → 文件存在、置可执行位；转发执行经 bash（Windows 上无
        # 扩展名文件不可由 CreateProcess 直接执行，经 bash 与 shell 语义一致）
        bash = shell_env.find_bash()
        if not bash:
            self.skipTest("无 bash")
        with tempfile.TemporaryDirectory() as tmp:
            d = shell_env.write_python3_shim(Path(tmp) / "shim")
            self.assertTrue(d.is_dir())
            shim = d / ("python3.exe" if sys.platform == "win32" else "python3")
            self.assertTrue(shim.is_file())
            self.assertTrue(shim.stat().st_mode & stat.S_IEXEC)
            # 经 bash 执行 shim：python3 -c 输出当前解释器路径（正斜杠比对）
            argv = shell_env.bash_argv(
                shim, args=["-c", "import sys; print(sys.executable)"])
            self.assertIsNotNone(argv)
            r = subprocess.run(argv, capture_output=True, text=True,
                               encoding="utf-8")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip().replace("\\", "/"),
                             sys.executable.replace("\\", "/"))


if __name__ == "__main__":
    unittest.main()