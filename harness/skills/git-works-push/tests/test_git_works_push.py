import os
import shutil
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "git_works_push.sh"

# ls-remote 空输出（exit 0）：触发 REMOTE_SHA 空值判定
MOCK_GIT_EMPTY = """#!/usr/bin/env bash
case "$1" in
  branch) echo dev ;;
  status) echo " M mock.txt" ;;
  add) exit 0 ;;
  commit) echo "mock commit ok"; exit 0 ;;
  push) exit 0 ;;
  ls-remote) exit 0 ;;
  rev-parse) echo "0123456789abcdef0123456789abcdef01234567" ;;
  *) exit 0 ;;
esac
"""

# ls-remote 正常输出且与本地 HEAD 一致：正常完成路径
MOCK_GIT_OK = """#!/usr/bin/env bash
case "$1" in
  branch) echo dev ;;
  status) echo " M mock.txt" ;;
  add) exit 0 ;;
  commit) echo "mock commit ok"; exit 0 ;;
  push) exit 0 ;;
  ls-remote) echo "0123456789abcdef0123456789abcdef01234567" ;;
  rev-parse)
    if [ "$2" = "--short" ]; then echo "0123456789ab"; else echo "0123456789abcdef0123456789abcdef01234567"; fi ;;
  *) exit 0 ;;
esac
"""


@unittest.skipUnless(shutil.which("bash"), "需要 bash 解释器（Windows 环境跳过）")
class TestGitWorksPush(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._msg = Path(self._tmp.name) / "msg.txt"
        self._msg.write_text("测试提交\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _env_with_mock_git(self, mock_git):
        bin_dir = Path(self._tmp.name) / "bin"
        bin_dir.mkdir(exist_ok=True)
        git = bin_dir / "git"
        git.write_text(mock_git, encoding="utf-8")
        git.chmod(git.stat().st_mode | stat.S_IEXEC)
        env = dict(os.environ)
        env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
        return env

    def test_remote_sha_empty_exits_2(self):
        # ls-remote 空输出（exit 0）→ 空值判定触发，不落到「疑似推送未生效」误导文案
        env = self._env_with_mock_git(MOCK_GIT_EMPTY)
        r = subprocess.run(["bash", str(SCRIPT), "--message-file", str(self._msg)],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("远端 dev 引用无输出", r.stderr)
        self.assertNotIn("疑似推送未生效", r.stderr)

    def test_remote_sha_match_exits_0(self):
        # ls-remote 与本地 HEAD 一致 → 正常完成
        env = self._env_with_mock_git(MOCK_GIT_OK)
        r = subprocess.run(["bash", str(SCRIPT), "--message-file", str(self._msg)],
                           capture_output=True, text=True, env=env)
        self.assertEqual(r.returncode, 0)
        self.assertIn("pushed: dev 0123456789ab", r.stdout)


if __name__ == "__main__":
    unittest.main()
