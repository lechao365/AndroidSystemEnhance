import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from shell_env import find_bash, write_python3_shim  # noqa: E402

BASH = find_bash()

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

# push 被拒（non-fast-forward / [rejected]）：触发 push 失败分类提示
MOCK_GIT_PUSH_REJECTED = """#!/usr/bin/env bash
case "$1" in
  branch) echo dev ;;
  status) echo " M mock.txt" ;;
  add) exit 0 ;;
  commit) echo "mock commit ok"; exit 0 ;;
  push) echo " ! [rejected] dev -> dev (non-fast-forward)"; exit 1 ;;
  ls-remote) echo "0123456789abcdef0123456789abcdef01234567" ;;
  rev-parse) echo "0123456789abcdef0123456789abcdef01234567" ;;
  *) exit 0 ;;
esac
"""

# 工作树干净：触发 "working tree clean"（exit 4）
MOCK_GIT_CLEAN = """#!/usr/bin/env bash
case "$1" in
  branch) echo dev ;;
  status) exit 0 ;;
  *) exit 0 ;;
esac
"""

MOCK_BASELINE = """baselines:
- baseline_id: BL-20261234-01
  status: promoted
"""


@unittest.skipUnless(BASH, "需要 bash 解释器（Windows 环境跳过）")
class TestGitWorksPush(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._msg = Path(self._tmp.name) / "msg.txt"
        self._msg.write_text("测试提交\n", encoding="utf-8")
        self._baseline = Path(self._tmp.name) / "baseline-status.yaml"
        self._baseline.write_text(MOCK_BASELINE, encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _env_with_mock_git(self, mock_git):
        bin_dir = Path(self._tmp.name) / "bin"
        bin_dir.mkdir(exist_ok=True)
        git = bin_dir / "git"
        git.write_text(mock_git, encoding="utf-8")
        git.chmod(git.stat().st_mode | stat.S_IEXEC)
        # PATH 前置 python3 shim（Windows 无 python3 命令，脚本内调用经 shim
        # 转发到当前解释器）再前置 mock git 目录
        shim_dir = write_python3_shim(Path(self._tmp.name) / "shim")
        env = dict(os.environ)
        env["PATH"] = f"{shim_dir}{os.pathsep}{bin_dir}{os.pathsep}{env['PATH']}"
        return env

    def _run(self, *args, mock_git=MOCK_GIT_OK):
        env = self._env_with_mock_git(mock_git)
        return subprocess.run([BASH, str(SCRIPT), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)

    def test_remote_sha_empty_exits_2(self):
        # ls-remote 空输出（exit 0）→ 空值判定触发，不落到「疑似推送未生效」误导文案
        r = self._run("--message-file", str(self._msg), mock_git=MOCK_GIT_EMPTY)
        self.assertEqual(r.returncode, 2)
        self.assertIn("远端 dev 引用无输出", r.stderr)
        self.assertNotIn("疑似推送未生效", r.stderr)

    def test_remote_sha_match_exits_0(self):
        # ls-remote 与本地 HEAD 一致 → 正常完成
        r = self._run("--message-file", str(self._msg))
        self.assertEqual(r.returncode, 0)
        self.assertIn("pushed: dev 0123456789ab", r.stdout)

    def test_baseline_not_registered_exits_1(self):
        # subject 首行声明未登记 BL → 拒绝提交
        msg = Path(self._tmp.name) / "msg_bl_unreg.txt"
        msg.write_text("构建(baseline): BL-20990101-01 晋升 promoted\n", encoding="utf-8")
        r = self._run("--message-file", str(msg), "--baseline-status", str(self._baseline))
        self.assertEqual(r.returncode, 1)
        self.assertIn("未在登记表登记", r.stderr)

    def test_baseline_registered_exits_0(self):
        # subject 首行声明已登记 BL → 正常提交
        msg = Path(self._tmp.name) / "msg_bl_reg.txt"
        msg.write_text("构建(baseline): BL-20261234-01 晋升 promoted\n", encoding="utf-8")
        r = self._run("--message-file", str(msg), "--baseline-status", str(self._baseline))
        self.assertEqual(r.returncode, 0)
        self.assertIn("pushed: dev", r.stdout)

    def test_baseline_in_body_only_passes(self):
        # 正文提及未登记 BL（复盘/示例）不应误伤——仅提取 subject 首行
        msg = Path(self._tmp.name) / "msg_bl_body.txt"
        msg.write_text("修复(cross-device): 调整收据写入\n\n复盘 BL-20990101-01 被拒过程\n",
                       encoding="utf-8")
        r = self._run("--message-file", str(msg), "--baseline-status", str(self._baseline))
        self.assertEqual(r.returncode, 0)

    def test_push_rejected_exits_2_with_hint(self):
        # push non-fast-forward → 分类提示 pull --rebase
        r = self._run("--message-file", str(self._msg), mock_git=MOCK_GIT_PUSH_REJECTED)
        self.assertEqual(r.returncode, 2)
        self.assertIn("non-fast-forward", r.stderr)
        self.assertIn("pull --rebase", r.stderr)

    def test_clean_tree_exits_4(self):
        # 工作树无改动 → exit 4
        r = self._run("--message-file", str(self._msg), mock_git=MOCK_GIT_CLEAN)
        self.assertEqual(r.returncode, 4)
        self.assertIn("working tree clean", r.stderr)

    def test_missing_message_file_value_exits_3(self):
        # --message-file 缺值 → 参数错误
        r = self._run("--message-file")
        self.assertEqual(r.returncode, 3)

    def test_dry_run_exits_0(self):
        # dry-run 预览（完整 stat + untracked 清单），不执行 add/commit/push
        r = self._run("--dry-run")
        self.assertEqual(r.returncode, 0)
        self.assertIn("== dry-run", r.stdout)
        self.assertIn("== 未跟踪/新增文件", r.stdout)
        self.assertNotIn("pushed:", r.stdout)

    def test_push_only_exits_0(self):
        # push-only：跳过 normal 分支直接 push
        r = self._run("--push-only")
        self.assertEqual(r.returncode, 0)
        self.assertIn("pushed: dev", r.stdout)


if __name__ == "__main__":
    unittest.main()
