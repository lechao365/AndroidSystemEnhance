import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[4]
REAL_SKILL_DIR = REPO_ROOT / "harness" / "skills" / "publish-main-base"
REAL_CDP_LIB = REPO_ROOT / "harness" / "skills" / "cross-device" / "lib" / "python"

sys.path.insert(0, str(REAL_CDP_LIB))
import cdp_issue  # noqa: E402 （fixture 进程内构造登记，路径经 CDP_PROJECT_ROOT 指向临时根）


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"),
                     "需要 bash 与 git 解释器（Windows 环境跳过）")
class TestSyncModifyToMainBase(unittest.TestCase):
    """真 git 仓 fixture：tempdir + git init 造提交链 c1→c2(HEAD)。

    脚本契约 = 从项目根运行（python3 相对路径），故 setUp 拷贝 harness 骨架
    （cross-device lib + publish-main-base）到临时根，cwd/env 均指向临时根；
    收据 verified_commit 默认取 c1（最近内容提交 c2 的父）→ 父等于 VC 放行。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = dict(os.environ)
        self._env["CDP_PROJECT_ROOT"] = str(self.root)
        os.environ["CDP_PROJECT_ROOT"] = str(self.root)
        # harness 骨架拷贝（脚本内 python3 相对路径与 cdp 模块导入均落在临时根）
        shutil.copytree(REAL_CDP_LIB,
                        self.root / "harness" / "skills" / "cross-device" / "lib" / "python")
        shutil.copytree(REAL_SKILL_DIR,
                        self.root / "harness" / "skills" / "publish-main-base")
        cfg = self.root / "harness" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "baseline-status.yaml").write_text("baselines: []\n", encoding="utf-8")
        # 真 git 仓：c1（内容）→ c2（内容，HEAD）
        self._git("init")
        self._git("symbolic-ref", "HEAD", "refs/heads/dev")
        self._git("config", "user.email", "t@example.com")
        self._git("config", "user.name", "t")
        self._git("config", "commit.gpgsign", "false")
        (self.root / "a.txt").write_text("1\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "修复(test): 内容提交一")
        self.parent_vc = self._git("rev-parse", "--short=12", "HEAD").stdout.strip()
        (self.root / "b.txt").write_text("2\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "修复(test): 内容提交二")
        self._write_receipt(self.parent_vc)

    def tearDown(self):
        os.environ.pop("CDP_PROJECT_ROOT")
        self._tmp.cleanup()

    def _git(self, *args, check=True):
        r = subprocess.run(["git", "-C", str(self.root), *args],
                           capture_output=True, text=True)
        if check:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def _write_receipt(self, verified_commit, build="pass", push_board="pass",
                       batch_id="000000000001"):
        d = self.root / "data" / "verify-results"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"20260831-100000-{batch_id}.md"
        p.write_text(
            f"- schema_version: 1\n- batch_id: {batch_id}\n"
            f"- batch_base: edd5748dc3c6\n- verified_commit: {verified_commit}\n"
            f"- verify_mode: board\n- result: pass\n- build: {build}\n"
            f"- push_board: {push_board}\n- acceptance: t\n- elapsed_s: 1\n"
            f"- summary: fixture\n- metrics: \n- timings: \n"
            f"\n## body\n\nfixture\n", encoding="utf-8")
        return p

    def _run(self, *args):
        script = self.root / "harness" / "skills" / "publish-main-base" / "publish_main_base.sh"
        return subprocess.run(["bash", str(script), *args],
                              capture_output=True, text=True,
                              cwd=self.root, env=self._env)

    def _run_register(self, *args):
        return subprocess.run(
            [sys.executable,
             str(self.root / "harness" / "skills" / "publish-main-base" / "baseline_register.py"),
             *args],
            capture_output=True, text=True, cwd=self.root, env=self._env)

    def _mk_issue(self, task="t1", status="open", origin="introduced", blocking=True):
        return cdp_issue.Issue(
            issue_id="KI-X", title="测试问题", discovered_in="38433d446f07",
            origin=origin, blocking=blocking,
            blocking_reason="r" if blocking else "", status=status, task=task,
            batch_id="000000000001")

    # ── 方向 4：真 git 仓两例（父等于 / 不等 VC）──────────────────────────
    def test_check_parent_equals_vc_passes(self):
        # 父(c1) == verified_commit(c1) → 前置校验通过
        r = self._run("--check-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("前置校验通过", r.stdout)
        self.assertIn(f"PARENT={self.parent_vc}", r.stdout)

    def test_check_outputs_need_verify_class(self):
        # 父(c1) != verified_commit（伪造）→ NEED_VERIFY（编排进验证路径）
        self._write_receipt("deadbeefdead", batch_id="000000000002")
        r = self._run("--check")
        self.assertEqual(r.returncode, 1)
        self.assertIn("check_class=NEED_VERIFY", r.stderr)

    # ── 方向 1：门禁经 baseline_register.py check-issues，非 0 → KI_BLOCKED ──
    def test_check_outputs_ki_blocked_class(self):
        cdp_issue.write_issue(self._mk_issue(), "现场")
        r = self._run("--check", "--task", "t1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("check_class=KI_BLOCKED", r.stderr)
        self.assertIn("known-issues 门禁未通过", r.stderr)

    def test_check_only_rejects_open_blocking_issues(self):
        # 目标任务存在 origin=introduced/blocking 且 status!=fixed 者即拒
        cdp_issue.write_issue(self._mk_issue(), "现场")
        r = self._run("--check-only", "--task", "t1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("known-issues 门禁未通过", r.stderr)

    def test_check_only_passes_without_blocking_issues(self):
        # fixed 且非 blocking 已闭环 → 门禁放行走通前置校验
        cdp_issue.write_issue(self._mk_issue(status="fixed", origin="pre-existing",
                                             blocking=False), "现场")
        r = self._run("--check-only", "--task", "t1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("前置校验通过", r.stdout)

    # ── 工作树预检（真 git status）───────────────────────────────────────
    def test_check_only_skips_dirty_tree_precheck(self):
        # check-only 干跑不做 add/commit/squash，脏树无害：跳过预检仍通过
        (self.root / "dirty.txt").write_text("x\n", encoding="utf-8")
        r = self._run("--check-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("前置校验通过", r.stdout)
        self.assertNotIn("工作树非空", r.stderr)

    def test_prepare_still_rejects_dirty_tree(self):
        # prepare 真实登记/推送，脏树必须拒绝（预检未废）
        (self.root / "dirty.txt").write_text("x\n", encoding="utf-8")
        r = self._run("--prepare")
        self.assertEqual(r.returncode, 1)
        self.assertIn("工作树非空", r.stderr)

    # ── 方向 5：check-issues 畸形判红与放行 ──────────────────────────────
    def test_check_issues_rejects_malformed_registry(self):
        # 畸形登记（task 含空白 → index 按空格切分错位，且无 index）有红即拒
        d = self.root / "data" / "known-issues"
        d.mkdir(parents=True, exist_ok=True)
        (d / "20260831-100000-000000000001-bad.md").write_text(
            "- schema_version: 1\n- issue_id: KI-BAD\n- title: t\n"
            "- discovered_in: 38433d446f07\n- origin: introduced\n"
            "- blocking: false\n- blocking_reason: \n- status: open\n"
            "- task: fix lcview bug\n- resolved_in: \n\n## body\n\nx\n",
            encoding="utf-8")
        r = self._run_register("check-issues", "--task", "t1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("task 含空白", r.stderr)
        self.assertIn("畸形登记", r.stderr)

    def test_check_issues_passes_valid_registry(self):
        # 合法登记（write_issue 同步 index，fixed 非阻塞）→ 门禁放行
        cdp_issue.write_issue(self._mk_issue(status="fixed", origin="pre-existing",
                                             blocking=False), "现场")
        r = self._run_register("check-issues", "--task", "t1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("known-issues 门禁通过", r.stdout)

    def test_check_issues_requires_task(self):
        r = self._run_register("check-issues")
        self.assertEqual(r.returncode, 1)
        self.assertIn("--task", r.stderr)

    # ── 方向 3：空 build/push_board 记 FAIL 不记 SKIP ────────────────────
    def _registered_evidence(self):
        cfg = self.root / "harness" / "config" / "baseline-status.yaml"
        return yaml.safe_load(cfg.read_text(encoding="utf-8"))["baselines"][0]

    def test_add_candidate_empty_build_registers_fail(self):
        # 收据 build/push_board 为空 → 登记为 FAIL（空值非合法 skip 证据）
        self._write_receipt(self.parent_vc, build="", push_board="",
                            batch_id="000000000003")
        r = self._run_register("add-candidate", "--source-commit", "abc123def456",
                               "--receipt-path",
                               "data/verify-results/20260831-100000-000000000003.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        b = self._registered_evidence()
        self.assertEqual(b["build_result"], "FAIL")
        self.assertEqual(b["package_result"], "FAIL")
        self.assertEqual(b["board_verify"], "FAIL")

    def test_add_candidate_explicit_skip_still_skip(self):
        # 显式 skip（-s 批次收据）保持 SKIP，不受空值从严影响
        self._write_receipt(self.parent_vc, build="skip", push_board="skip",
                            batch_id="000000000004")
        r = self._run_register("add-candidate", "--source-commit", "abc123def456",
                               "--receipt-path",
                               "data/verify-results/20260831-100000-000000000004.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        b = self._registered_evidence()
        self.assertEqual(b["build_result"], "SKIP")
        self.assertEqual(b["board_verify"], "SKIP")


if __name__ == "__main__":
    unittest.main()
