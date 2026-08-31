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
        self.head_vc = self._git("rev-parse", "--short=12", "HEAD").stdout.strip()
        self._write_receipt(self.parent_vc)

    def tearDown(self):
        os.environ.pop("CDP_PROJECT_ROOT")
        self._tmp.cleanup()
        if getattr(self, "_remote_tmp", None):
            self._remote_tmp.cleanup()

    def _setup_remote(self):
        """bare 远端 fixture：bare 仓放仓库树外（push 写 objects/refs 会弄脏树内
        工作区），origin=origin.git（main=c1，dev=c2），供 fetch/push e2e。"""
        if not getattr(self, "_remote_tmp", None):
            self._remote_tmp = tempfile.TemporaryDirectory()
        bare = Path(self._remote_tmp.name) / "origin.git"
        self._git("init", "--bare", str(bare))
        self._git("remote", "add", "origin", str(bare))
        self._git("branch", "main", self.parent_vc)
        self._git("push", "origin", "main")
        self._git("push", "origin", "dev")
        return bare

    def _git(self, *args, check=True):
        r = subprocess.run(["git", "-C", str(self.root), *args],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        if check:
            self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def _write_receipt(self, verified_commit, build="pass", push_board="pass",
                       batch_id="000000000001", result="pass", verify_mode="board"):
        d = self.root / "data" / "verify-results"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"20260831-100000-{batch_id}.md"
        p.write_text(
            f"- schema_version: 1\n- batch_id: {batch_id}\n"
            f"- batch_base: edd5748dc3c6\n- verified_commit: {verified_commit}\n"
            f"- verify_mode: {verify_mode}\n- result: {result}\n- build: {build}\n"
            f"- push_board: {push_board}\n- acceptance: t\n- elapsed_s: 1\n"
            f"- summary: fixture\n- metrics: \n- timings: \n"
            f"\n## body\n\nfixture\n", encoding="utf-8")
        return p

    def _run(self, *args):
        script = self.root / "harness" / "skills" / "publish-main-base" / "publish_main_base.sh"
        return subprocess.run(["bash", str(script), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              cwd=self.root, env=self._env)

    def _run_register(self, *args):
        return subprocess.run(
            [sys.executable,
             str(self.root / "harness" / "skills" / "publish-main-base" / "baseline_register.py"),
             *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=self.root, env=self._env)

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

    # ── 方向 5：promote 收紧与 ki_gate 证据链（bare 远端 e2e）────────────
    def _receipt_commit_c3(self, batch_id="000000000002", **kw):
        # 收据入库为 c3（内容提交，父=c2==VC），保证 promote/prepare 工作树干净
        self._write_receipt(self.head_vc, batch_id=batch_id, **kw)
        self._git("add", "-A")
        self._git("commit", "-m", "修复(test): 收据入库三")
        self._git("push", "origin", "dev")

    def _candidate_yaml(self):
        (self.root / "harness" / "config" / "baseline-status.yaml").write_text(
            "baselines:\n"
            f"  - baseline_id: BL-TEST-01\n"
            f"    status: candidate\n"
            f"    source_commit: {self.head_vc}\n"
            f"    sync_manifest: data/verify-results/20260831-100000-000000000002.md\n"
            f"    build_result: PASS\n"
            f"    package_result: PASS\n"
            f"    board_verify: SKIP\n"
            f"    evidence:\n"
            f"      ki_gate: pass\n",
            encoding="utf-8")

    def _promote(self, *extra):
        # message 文件放仓库树外，避免弄脏工作树（promote 前置要求树净）
        msg = Path(self._remote_tmp.name) / "promote-msg.txt"
        msg.write_text("构建(baseline): BL-TEST-01 基线晋升\n", encoding="utf-8")
        return self._run("--promote", "--baseline-id", "BL-TEST-01",
                         "--message-file", str(msg), "--task", "t1", *extra)

    def test_promote_rejects_non_board_receipt(self):
        # 方向 2 board 拒：result=pass 但 verify_mode=skip 且 dev 有 code/ 改动 → RECEIPT_FAIL
        self._setup_remote()
        (self.root / "code").mkdir()
        (self.root / "code" / "foo.txt").write_text("x\n", encoding="utf-8")
        self._receipt_commit_c3(verify_mode="skip")
        r = self._promote()
        self.assertEqual(r.returncode, 1)
        self.assertIn("check_class=RECEIPT_FAIL", r.stderr)
        self.assertIn("verify_mode=board", r.stderr)

    def test_promote_passes_zero_code_change(self):
        # 方向 2 零改动豁免：verify_mode=skip 但无 code/ 改动 → warn 豁免 + e2e promote 完成
        self._setup_remote()
        self._candidate_yaml()
        self._receipt_commit_c3(verify_mode="skip")
        r = self._promote()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("无 code/ 改动，豁免放行", r.stdout)
        self.assertIn("promote 完成", r.stdout)
        # 正常放行：verified tag 本地与远端均存在（方向 1）
        self.assertIn("verified/BL-TEST-01",
                      self._git("tag", "-l", "verified/BL-TEST-01").stdout)
        self.assertIn("refs/tags/verified/BL-TEST-01",
                      self._git("ls-remote", "origin",
                                "refs/tags/verified/BL-TEST-01").stdout)

    def test_promote_rejects_duplicate_tag(self):
        # 方向 1 同名 tag 拒：verified/BL-TEST-01 已存在 → 退 3（未发生任何变更）
        self._setup_remote()
        self._receipt_commit_c3()
        self._git("tag", "-a", "verified/BL-TEST-01", "-m", "pre-existing", "HEAD")
        r = self._promote()
        self.assertEqual(r.returncode, 3)
        self.assertIn("已存在", r.stderr)

    def test_promote_rejects_tree_mismatch(self):
        # 方向 2/3 树不等拒：meta 提交夹带 code/evil.txt → verify-tree 失败，
        # rollback 一并删除本地与远端 verified tag（方向 4），退 1
        self._setup_remote()
        self._receipt_commit_c3()
        self._candidate_yaml()
        (self.root / "code").mkdir()
        (self.root / "code" / "evil.txt").write_text("x\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "构建(baseline): 伪造元提交夹带")
        self._git("push", "origin", "dev")
        r = self._promote()
        self.assertEqual(r.returncode, 1)
        self.assertIn("树等价", r.stderr)
        self.assertIn("code/evil.txt", r.stderr)
        self.assertEqual(
            self._git("tag", "-l", "verified/BL-TEST-01").stdout.strip(), "")
        self.assertEqual(
            self._git("ls-remote", "origin",
                      "refs/tags/verified/BL-TEST-01").stdout.strip(), "")

    def test_prepare_without_task_records_ki_gate_not_run(self):
        # 方向 3/4：prepare 未传 --task → warn + KIGATE=not-run 写入 evidence
        self._setup_remote()
        self._receipt_commit_c3()
        r = self._run("--prepare")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("KIGATE=not-run", r.stderr)
        data = yaml.safe_load(
            (self.root / "harness" / "config" / "baseline-status.yaml").read_text(
                encoding="utf-8"))
        self.assertEqual(data["baselines"][0]["evidence"]["ki_gate"], "not-run")

    def test_prepare_with_task_records_ki_gate_pass(self):
        # 方向 3/4：--task 门禁通过（空登记合法）→ KIGATE=pass 写入 evidence
        self._setup_remote()
        self._receipt_commit_c3()
        r = self._run("--prepare", "--task", "t1")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("KIGATE=not-run", r.stderr)
        data = yaml.safe_load(
            (self.root / "harness" / "config" / "baseline-status.yaml").read_text(
                encoding="utf-8"))
        self.assertEqual(data["baselines"][0]["evidence"]["ki_gate"], "pass")


if __name__ == "__main__":
    unittest.main()
