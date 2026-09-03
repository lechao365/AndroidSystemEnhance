import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "lib"))
from shell_env import bash_argv, find_bash, write_python3_shim  # noqa: E402

BASH = find_bash()

SCRIPT = Path(__file__).resolve().parents[1] / "git_works_push.sh"
# 仓内钩子目录（git_works_push.sh 幂等接线的目标，方向 3）：
# parents: [0]=tests [1]=git-works-push [2]=skills [3]=harness [4]=仓根
HOOKS_DIR = Path(__file__).resolve().parents[4] / ".githooks"
COMMIT_MSG_HOOK = HOOKS_DIR / "commit-msg"

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
        self._msg.write_text("新增(cross-device): 测试提交\n", encoding="utf-8")
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
        # python3 shim 目录（Windows 无 python3 命令，脚本内调用经 shim
        # 转发到当前解释器）；目录经 bash_argv 前置目录传入（shell 内 PATH
        # 前置，绕开 bin/bash.exe 启动期强插 mingw64/usr 到 PATH 最前）
        shim_dir = write_python3_shim(Path(self._tmp.name) / "shim")
        return bin_dir, shim_dir

    def _run(self, *args, mock_git=MOCK_GIT_OK):
        bin_dir, shim_dir = self._env_with_mock_git(mock_git)
        argv = bash_argv(SCRIPT, args, prepend_dirs=[shim_dir, bin_dir])
        if argv is None:
            # find_bash 返 None（本机无 bash）→ skip 单测，防 None 进 subprocess
            # 变 TypeError（Windows 未设 LC_HARNESS_WIN_BASH 时触发）
            self.skipTest("无 bash（find_bash 返 None）")
        env = dict(os.environ)
        return subprocess.run(argv,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)

    @pytest.mark.slow_ok("脚本内 ls-remote 重试 3 次各 sleep 1s，真实子进程语义")
    def test_remote_sha_empty_exits_2(self):
        # ls-remote 空输出（exit 0）→ 空值判定触发，不落到「疑似推送未生效」误导文案
        # （实测 ~3.05s 贴线，slow guard 显式豁免示范）
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

    def test_english_prefix_rejected_exits_1(self):
        # 方向 6：英文前缀（feat/fix 等）提交信息一律拒绝，防提交风格漂移
        for subject in ("feat(harness): 英文前缀提交\n",
                        "fix: 无 scope 英文前缀\n",
                        "feat(harness) 缺冒号\n"):
            msg = Path(self._tmp.name) / "msg_en.txt"
            msg.write_text(subject, encoding="utf-8")
            r = self._run("--message-file", str(msg))
            self.assertEqual(r.returncode, 1)
            self.assertIn("中文type", r.stderr)
            self.assertIn("英文前缀拒绝", r.stderr)

    def test_non_type_cn_prefix_rejected_exits_1(self):
        # 中文但非词表 type（超出 新增/修复/重构/文档/构建/杂项）→ 拒绝
        msg = Path(self._tmp.name) / "msg_bad_type.txt"
        msg.write_text("优化(harness): 非词表 type\n", encoding="utf-8")
        r = self._run("--message-file", str(msg))
        self.assertEqual(r.returncode, 1)
        self.assertIn("英文前缀拒绝", r.stderr)

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


@unittest.skipUnless(BASH and shutil.which("git"), "需要 bash 与 git（真 git 验证钩子接线）")
class TestCommitMsgHook(unittest.TestCase):
    """方向 3：commit-msg 钩子（中文前缀校验）+ git_works_push.sh 幂等接线。

    用真 git 在临时仓验证：脚本启动把 core.hooksPath 指向仓内 .githooks
    （幂等），裸 git commit 路径同样被钩子拦截（此前脚本内校验只覆盖
    git_works_push.sh 自身提交路径，可绕过）。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._repo = Path(self._tmp.name) / "repo"
        self._repo.mkdir()
        env = dict(os.environ)
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "t"
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "t@t"
        self._env = env
        r = subprocess.run(["git", "-c", "init.defaultBranch=dev", "init"],
                           cwd=self._repo, capture_output=True, text=True,
                           encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        # 初始空提交（接线前，钩子未生效；--allow-empty 在空仓无 HEAD 会失败）
        r = subprocess.run(["git", "commit", "--allow-empty", "-m", "init"],
                           cwd=self._repo, capture_output=True, text=True,
                           encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args):
        return subprocess.run(["git", *args], cwd=self._repo,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              env=self._env)

    def _run_script(self, *args):
        argv = bash_argv(SCRIPT, list(args))
        if argv is None:
            self.skipTest("无 bash（find_bash 返 None）")
        return subprocess.run(argv, cwd=self._repo, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              env=self._env)

    def test_hook_file_exists_and_executable(self):
        # 钩子随仓入库且可执行（core.hooksPath 接线后按此文件触发）
        self.assertTrue(COMMIT_MSG_HOOK.is_file())
        self.assertTrue(os.access(COMMIT_MSG_HOOK, os.X_OK))

    def test_hook_accepts_cn_prefix_and_rejects_english(self):
        # 钩子与 git_works_push.sh 内校验同一词表：中文 type 过、英文前缀拒
        good = Path(self._tmp.name) / "good.txt"
        good.write_text("修复(harness): 中文前缀提交\n", encoding="utf-8")
        r = subprocess.run(["bash", str(COMMIT_MSG_HOOK), str(good)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0)
        bad = Path(self._tmp.name) / "bad.txt"
        bad.write_text("feat(harness): english prefix\n", encoding="utf-8")
        r = subprocess.run(["bash", str(COMMIT_MSG_HOOK), str(bad)],
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 1)
        self.assertIn("英文前缀拒绝", r.stderr)

    def test_script_sets_hooks_path_idempotent(self):
        # git_works_push.sh 启动幂等设 core.hooksPath（dry-run 即完成接线：
        # 设置在 dry-run 分支之前）；两次运行同值
        for _ in range(2):
            r = self._run_script("--dry-run")
            self.assertEqual(r.returncode, 0, r.stderr)
            got = self._git("config", "core.hooksPath").stdout.strip()
            self.assertTrue(got)
            self.assertEqual(Path(got).resolve(), HOOKS_DIR.resolve())

    def test_hook_blocks_bare_git_commit(self):
        # 接线后裸 git commit 也过钩子（堵绕过口子）：英文前缀 commit 被拒、
        # 中文前缀 commit 成功
        r = self._run_script("--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        bad = self._repo / "msg_en.txt"
        bad.write_text("feat(x): english prefix\n", encoding="utf-8")
        r = self._git("commit", "--allow-empty", "-F", str(bad))
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("英文前缀拒绝", r.stderr)
        good = self._repo / "msg_cn.txt"
        good.write_text("新增(harness): 中文前缀提交\n", encoding="utf-8")
        r = self._git("commit", "--allow-empty", "-F", str(good))
        self.assertEqual(r.returncode, 0)


@unittest.skipUnless(BASH and shutil.which("git"),
                     "需要 bash 与 git 解释器（Windows 环境跳过）")
class TestCommitFaceNarrow(unittest.TestCase):
    """提交面收窄 + 凭据扫描（真 git 仓）。

    git add -A 收窄为 add -u + 未跟踪白名单（data/{verify-results,baselines,
    known-issues}），名单外拒绝并列出；暂存新增行凭据扫描（低熵 psk 赋值
    形态）命中即拒，占位符放行。push 无远端必失败（rc 2），但 commit 已在
    push 前创建——rc 与 HEAD 内容共同断言。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._repo = Path(self._tmp.name) / "repo"
        self._repo.mkdir()
        env = dict(os.environ)
        env["GIT_AUTHOR_NAME"] = env["GIT_COMMITTER_NAME"] = "t"
        env["GIT_AUTHOR_EMAIL"] = env["GIT_COMMITTER_EMAIL"] = "t@t"
        self._env = env
        r = subprocess.run(["git", "-c", "init.defaultBranch=dev", "init"],
                           cwd=self._repo, capture_output=True, text=True,
                           encoding="utf-8", env=env)
        self.assertEqual(r.returncode, 0, r.stderr)
        (self._repo / "f.txt").write_text("v1\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "init")
        self._base = self._git("rev-parse", "HEAD").stdout.strip()
        self._msg = self._repo / "msg.txt"
        self._msg.write_text("新增(test): 提交面收窄验证\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _git(self, *args, check=True):
        r = subprocess.run(["git", *args], cwd=self._repo,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=self._env)
        if check:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def _run_script(self, *args):
        argv = bash_argv(SCRIPT, list(args))
        if argv is None:
            self.skipTest("无 bash（find_bash 返 None）")
        return subprocess.run(argv, cwd=self._repo, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              env=self._env)

    def test_untracked_outside_allowlist_rejected_and_listed(self):
        # 方向 1：名单外未跟踪文件拒绝并列出，commit 不创建
        (self._repo / "notes.txt").write_text("运行态产物\n", encoding="utf-8")
        self._git("add", "-u")
        r = self._run_script("--message-file", str(self._msg))
        self.assertEqual(r.returncode, 1)
        self.assertIn("白名单", r.stderr)
        self.assertIn("notes.txt", r.stderr)
        self.assertEqual(self._git("rev-parse", "HEAD").stdout.strip(), self._base)

    def test_allowlist_receipt_and_tracked_changes_committed(self):
        # 方向 1：已跟踪修改全收 + 白名单内未跟踪收据随批入库（push 失败 rc 2，
        # 但 commit 已创建且含两处改动）
        (self._repo / "f.txt").write_text("v2\n", encoding="utf-8")
        receipt = self._repo / "data" / "verify-results"
        receipt.mkdir(parents=True)
        (receipt / "20260101-000000-ca64a314af81.md").write_text("r\n", encoding="utf-8")
        r = self._run_script("--message-file", str(self._msg))
        self.assertEqual(r.returncode, 2)  # 无 origin，push 失败；commit 已创建
        show = self._git("show", "--name-only", "--format=").stdout
        self.assertIn("f.txt", show)
        self.assertIn("data/verify-results/20260101-000000-ca64a314af81.md", show)

    def test_allowlist_source_dir_new_file_committed(self):
        # 方向 1 补充：源码目录（harness/ 等）内新增未跟踪文件放行——
        # 批次新增实现/测试文件必须能入库；根目录散文件仍被拦（见上例）
        new_file = self._repo / "harness" / "skills" / "git-works-push" / "new_helper.sh"
        new_file.parent.mkdir(parents=True, exist_ok=True)
        new_file.write_text("#!/usr/bin/env bash\n", encoding="utf-8")
        r = self._run_script("--message-file", str(self._msg))
        self.assertEqual(r.returncode, 2)
        self.assertIn("harness/skills/git-works-push/new_helper.sh",
                      self._git("show", "--name-only", "--format=").stdout)

    def test_staged_low_entropy_psk_rejected(self):
        # 方向 2：暂存新增行低熵 psk 赋值命中即拒（BL-20260624-01 教训）。
        # 键名拼接构造：本测试文件自身过 git_works_push.sh 凭据扫描时，
        # 源码行不得含完整 psk 赋值形态（fixture 假凭据，运行时拼出完整行）
        psk_line = 'v1\nwifi.p' + 'sk="RealPass1234"\n'
        (self._repo / "f.txt").write_text(psk_line, encoding="utf-8")
        r = self._run_script("--message-file", str(self._msg))
        self.assertEqual(r.returncode, 1)
        self.assertIn("凭据", r.stderr)
        self.assertIn("psk", r.stderr)
        self.assertEqual(self._git("rev-parse", "HEAD").stdout.strip(), self._base)

    def test_placeholder_psk_allowed(self):
        # 方向 2：占位符白名单放行（push 失败 rc 2，commit 已创建且含占位行）
        (self._repo / "f.txt").write_text('v1\npsk = "PLACEHOLDER_VALUE"\n', encoding="utf-8")
        r = self._run_script("--message-file", str(self._msg))
        self.assertEqual(r.returncode, 2)
        self.assertIn("PLACEHOLDER_VALUE", self._git("show", "--format=", "HEAD").stdout)


if __name__ == "__main__":
    unittest.main()
