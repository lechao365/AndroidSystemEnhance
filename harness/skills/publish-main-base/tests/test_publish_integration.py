"""publish_main_base.sh 集成测试：真实 git 仓库（origin 裸仓库 + work 工作仓）。

覆盖：BH 回溯跳过登记元/文档提交、prepare 严格拒绝文档提交、文档提交夹带非 docs/ 拒绝、
promote 强制 --task、prepare source_commit 取内容提交与 candidate 去重、promote 全流程、
push main 失败时 rollback 清 main squash commit。
"""
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]          # .../publish-main-base
SCRIPT = SKILL / "publish_main_base.sh"
HARNESS = Path(__file__).resolve().parents[3]        # .../harness
CDP_PY = HARNESS / "skills" / "cross-device" / "lib" / "python"

RECEIPT_REL = "data/verify-results/20260830-000000-inttest.md"
BID = "BL-inttest-01"


@unittest.skipUnless(shutil.which("bash") and shutil.which("git"),
                     "需要 bash 与 git（本环境为 Linux）")
class TestSyncModifyIntegration(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.origin = self.root / "origin.git"
        self.work = self.root / "work"
        subprocess.run(["git", "init", "--bare", "-q", str(self.origin)], check=True)
        subprocess.run(["git", "init", "-q", "-b", "dev", str(self.work)], check=True)
        self._git(["config", "user.email", "t@t"])
        self._git(["config", "user.name", "t"])
        self._git(["remote", "add", "origin", str(self.origin)])
        # harness 最小结构（复制而非符号链接，避免 __file__.resolve() 指向真实仓库）
        dst_skill = self.work / "harness/skills/publish-main-base"
        dst_skill.mkdir(parents=True)
        shutil.copy(str(SCRIPT), str(dst_skill / "publish_main_base.sh"))
        shutil.copy(str(SKILL / "baseline_register.py"),
                    str(dst_skill / "baseline_register.py"))
        dst_cdp = self.work / "harness/skills/cross-device/lib/python"
        dst_cdp.mkdir(parents=True)
        for f in ("cdp_receipt.py", "cdp_paths.py", "cdp_issue.py"):
            shutil.copy(str(CDP_PY / f), str(dst_cdp / f))
        cfg = self.work / "harness/config"
        cfg.mkdir(parents=True)
        (cfg / "baseline-status.yaml").write_text(
            "# baseline 状态登记\nbaselines: []\n", encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    # ── git 原语 ──────────────────────────────────────────────────────────
    def _git(self, args, check=True):
        return subprocess.run(["git", *args], cwd=self.work,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", check=check)

    def _git_out(self, args):
        return self._git(args).stdout.strip()

    def _origin(self, args, check=True):
        return subprocess.run(["git", "-C", str(self.origin), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace", check=check)

    def _commit_all(self, msg):
        self._git(["add", "-A"])
        self._git(["commit", "-q", "-m", msg])

    def _run_script(self, *args):
        env = dict(os.environ)
        env["CDP_PROJECT_ROOT"] = str(self.work)
        # 防 python 导入生成 __pycache__ 污染工作树（git status 非空会使预检拒绝）
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(["bash", str(SCRIPT), *args], cwd=self.work,
                              capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)

    # ── 场景构建 ──────────────────────────────────────────────────────────
    def _base_repo(self):
        """M0：README + baseline-status.yaml(baselines: [])；建 main 并推送 origin。"""
        (self.work / "README.md").write_text("base\n", encoding="utf-8")
        self._commit_all("base M0")
        vc = self._git_out(["rev-parse", "--short=12", "HEAD"])
        self._git(["branch", "main"])
        self._git(["push", "-q", "-u", "origin", "main"])
        self._git(["push", "-q", "-u", "origin", "dev"])
        return vc

    def _write_receipt(self, vc, batch_id="inttest", cases=""):
        content = (
            f"- schema_version: 1\n- batch_id: {batch_id}\n"
            f"- batch_base: {vc}\n- verified_commit: {vc}\n"
            "- verify_mode: board\n- result: pass\n- build: pass\n"
            "- push_board: pass\n- acceptance: ok\n- elapsed_s: 0\n"
            f"- summary: integration\n- metrics: \n- cases: {cases}\n"
            "\n## body\n\nintegration test\n")
        p = self.work / RECEIPT_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return RECEIPT_REL

    def _content_commit(self, vc, cases=""):
        """B：内容提交（修改 README + 收据随批入库）。"""
        (self.work / "README.md").write_text("content B\n", encoding="utf-8")
        self._write_receipt(vc, cases=cases)
        self._commit_all("feat: 内容提交 B")
        return self._git_out(["rev-parse", "--short=12", "HEAD"])

    def _register_commit(self, source_commit):
        """C：登记 candidate 提交（构建(baseline): 前缀）。"""
        yaml_text = (
            "# baseline 状态登记\n"
            "baselines:\n"
            f"- baseline_id: {BID}\n"
            "  status: candidate\n"
            "  source_branch: dev\n"
            f"  source_commit: {source_commit}\n"
            f"  sync_manifest: {RECEIPT_REL}\n"
            "  build_result: PASS\n  package_result: PASS\n  board_verify: PASS\n"
            "  evidence:\n"
            "    build_result: PASS\n    package_result: PASS\n    board_verify: PASS\n"
            f"    sync_manifest: {RECEIPT_REL}\n")
        (self.work / "harness/config/baseline-status.yaml").write_text(
            yaml_text, encoding="utf-8")
        self._git(["add", "harness/config/baseline-status.yaml"])
        self._git(["commit", "-q", "-m",
                   f"构建(baseline): 登记 candidate（receipt={Path(RECEIPT_REL).name}）"])
        return self._git_out(["rev-parse", "--short=12", "HEAD"])

    def _doc_commit(self, only_docs=True):
        """D：文档提交（文档( 前缀；only_docs=False 时夹带非 docs/ 文件）。"""
        d = self.work / "docs"
        d.mkdir(parents=True, exist_ok=True)
        (d / "design.md").write_text("# design\n", encoding="utf-8")
        if not only_docs:
            c = self.work / "code"
            c.mkdir(parents=True, exist_ok=True)
            (c / "x.txt").write_text("code\n", encoding="utf-8")
        self._commit_all("文档(docs): 同步设计文档")

    def _push_dev(self):
        self._git(["push", "-q", "origin", "dev"])

    def _msg_file(self):
        # 置于 work 之外，避免 untracked 文件污染工作树预检
        p = self.root / "msg.txt"
        p.write_text("feat: promote 批次（基线 BL-inttest-01）\n", encoding="utf-8")
        return str(p)

    # ── 用例 ──────────────────────────────────────────────────────────────
    def test_check_only_ok_with_meta_and_doc(self):
        # 回溯跳过登记元 + 文档提交，PARENT=verified_commit 通过；输出跳过详情
        vc = self._base_repo()
        b12 = self._content_commit(vc)
        self._register_commit(b12)
        self._doc_commit()
        self._push_dev()
        r = self._run_script("--check-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("前置校验通过", r.stdout)
        self.assertIn("跳过登记元提交: 1", r.stdout)
        self.assertIn("跳过文档提交: 1", r.stdout)

    def test_promote_full_flow_with_doc_commit(self):
        # 完整 promote：文档提交 + 登记元提交并存，squash 进 main，dev 重建
        vc = self._base_repo()
        b12 = self._content_commit(vc)
        self._register_commit(b12)
        self._doc_commit()
        self._push_dev()
        r = self._run_script("--promote", "--baseline-id", BID,
                             "--message-file", self._msg_file(),
                             "--task", "lcview-refactor", "--approved-by", "tester")
        self.assertEqual(r.returncode, 0, r.stderr)
        # dev 重建：work HEAD == origin/main == origin/dev
        main_sha = self._origin(["rev-parse", "main"]).stdout.strip()
        dev_sha = self._origin(["rev-parse", "dev"]).stdout.strip()
        self.assertEqual(main_sha, dev_sha)
        self.assertEqual(self._git_out(["rev-parse", "HEAD"]), main_sha)
        self.assertEqual(self._git_out(["branch", "--show-current"]), "dev")
        # main 内容：文档 + 收据 + promoted 登记
        self._origin(["show", "main:docs/design.md"], check=True)
        self._origin(["show", f"main:{RECEIPT_REL}"], check=True)
        yaml_main = self._origin(["show", "main:harness/config/baseline-status.yaml"]).stdout
        self.assertIn("status: promoted", yaml_main)
        self.assertIn("approved_by: tester", yaml_main)

    def test_prepare_rejects_doc_commit(self):
        # prepare 严格模式：dev 已存在文档提交即拒
        vc = self._base_repo()
        b12 = self._content_commit(vc)
        self._register_commit(b12)
        self._doc_commit()
        self._push_dev()
        r = self._run_script("--prepare", "--task", "lcview-refactor",
                                "--evidence-scope", "lcview-liveness")
        self.assertEqual(r.returncode, 1)
        self.assertIn("prepare 前 dev 已存在 1 个文档提交", r.stderr)

    def test_doc_commit_with_code_rejected(self):
        # 文档提交夹带非 docs/ 改动：拒绝（防未验证代码随 squash 混入 main）
        vc = self._base_repo()
        b12 = self._content_commit(vc)
        self._register_commit(b12)
        self._doc_commit(only_docs=False)
        self._push_dev()
        r = self._run_script("--check-only")
        self.assertEqual(r.returncode, 1)
        self.assertIn("含非 docs/ 改动", r.stderr)

    def test_promote_without_task_passes(self):
        # promote 不传 --task 也能走通：强制已删，known-issues 门禁在共用段
        # 缺省推断（空登记 → empty-registry 放行），不再要求人工申报
        vc = self._base_repo()
        b12 = self._content_commit(vc)
        self._register_commit(b12)
        self._push_dev()
        r = self._run_script("--promote", "--baseline-id", BID,
                             "--message-file", self._msg_file())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("promote 完成", r.stdout)

    def test_prepare_source_commit_and_dedup(self):
        # source_commit 取内容提交（BH 回溯后），重复 prepare 复用 candidate
        vc = self._base_repo()
        b12 = self._content_commit(vc, cases="lcview-liveness")
        r = self._run_script("--prepare", "--task", "lcview-refactor",
                                "--evidence-scope", "lcview-liveness")
        self.assertEqual(r.returncode, 0, r.stderr)
        yaml_text = (self.work / "harness/config/baseline-status.yaml").read_text(
            encoding="utf-8")
        self.assertIn("status: candidate", yaml_text)
        self.assertIn(f"source_commit: {b12}", yaml_text)
        # 第二次 prepare：复用，不新增记录
        r2 = self._run_script("--prepare", "--task", "lcview-refactor",
                                "--evidence-scope", "lcview-liveness")
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("candidate 复用", r2.stdout)
        yaml_text2 = (self.work / "harness/config/baseline-status.yaml").read_text(
            encoding="utf-8")
        self.assertEqual(yaml_text2.count("baseline_id:"), 1)
        # 登记提交已推送 origin/dev
        self._origin(["show", "dev:harness/config/baseline-status.yaml"], check=True)

    def test_rollback_cleans_main_on_push_fail(self):
        # push main 失败（pre-receive 拒绝）→ rollback：main 无 squash 残留、dev 回退
        vc = self._base_repo()
        b12 = self._content_commit(vc)
        self._register_commit(b12)
        self._doc_commit()
        self._push_dev()
        hook = self.origin / "hooks/pre-receive"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(hook.stat().st_mode | 0o111)
        r = self._run_script("--promote", "--baseline-id", BID,
                             "--message-file", self._msg_file(),
                             "--task", "lcview-refactor")
        self.assertEqual(r.returncode, 2)
        # main 本地 reset 回 origin/main：无残留 squash commit
        origin_main = self._git_out(["rev-parse", "origin/main"])
        self.assertEqual(self._git_out(["rev-parse", "main"]), origin_main)
        self.assertEqual(self._git_out(["rev-parse", "dev"]),
                         self._git_out(["rev-parse", "origin/dev"]))
        # baseline 回 candidate；dev 上登记提交仍在（squash 未发生）
        yaml_text = (self.work / "harness/config/baseline-status.yaml").read_text(
            encoding="utf-8")
        self.assertIn("status: candidate", yaml_text)


if __name__ == "__main__":
    unittest.main()
