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
sys.path.insert(0, str(REAL_SKILL_DIR))
sys.path.insert(0, str(REPO_ROOT / "harness" / "lib"))
from shell_env import find_bash, write_python3_shim  # noqa: E402
import cdp_issue  # noqa: E402 （fixture 进程内构造登记，路径经 CDP_PROJECT_ROOT 指向临时根）

BASH = find_bash()


@unittest.skipUnless(BASH and shutil.which("git"),
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
        # python3 shim 目录独立于 git 仓外（仓内脏文件会被 prepare 预检拒绝）
        self._shim_tmp = tempfile.TemporaryDirectory()
        self._env = dict(os.environ)
        self._env["CDP_PROJECT_ROOT"] = str(self.root)
        os.environ["CDP_PROJECT_ROOT"] = str(self.root)
        # harness 骨架拷贝（脚本内 python3 相对路径与 cdp 模块导入均落在临时根）
        shutil.copytree(REAL_CDP_LIB,
                        self.root / "harness" / "skills" / "cross-device" / "lib" / "python")
        shutil.copytree(REAL_SKILL_DIR,
                        self.root / "harness" / "skills" / "publish-main-base")
        # content_tree.py（promote 绑定比对）与 commit_scope.py 相对路径调用，
        # 临时根须有 harness/lib/
        lib_dst = self.root / "harness" / "lib"
        lib_dst.mkdir(parents=True, exist_ok=True)
        shutil.copy(REPO_ROOT / "harness" / "lib" / "content_tree.py", lib_dst)
        shutil.copy(REPO_ROOT / "harness" / "lib" / "commit_scope.py", lib_dst)
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
        self._shim_tmp.cleanup()
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
                       batch_id="000000000001", result="pass", verify_mode="board",
                       cases="", verified_tree=None):
        d = self.root / "data" / "verify-results"
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"20260831-100000-{batch_id}.md"
        # verified_tree 默认按当前工作树实算（content_tree CLI，排除统一集合），
        # 与 promote 侧 --tree HEAD 同算法——正常流程两树一致；绑定拒绝用例
        # 经参数注入假树
        if verified_tree is None:
            r = subprocess.run(
                [sys.executable, str(REPO_ROOT / "harness" / "lib" / "content_tree.py")],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=self.root, env=self._env)
            verified_tree = r.stdout.strip() if r.returncode == 0 else ""
        p.write_text(
            f"- schema_version: 1\n- batch_id: {batch_id}\n"
            f"- batch_base: edd5748dc3c6\n- verified_commit: {verified_commit}\n"
            f"- verified_tree: {verified_tree}\n"
            f"- verify_mode: {verify_mode}\n- result: {result}\n- build: {build}\n"
            f"- push_board: {push_board}\n- acceptance: t\n- elapsed_s: 1\n"
            f"- summary: fixture\n- metrics: \n- timings: \n- cases: {cases}\n"
            f"\n## body\n\nfixture\n", encoding="utf-8")
        return p

    def _run(self, *args):
        script = self.root / "harness" / "skills" / "publish-main-base" / "publish_main_base.sh"
        # PATH 前置 python3 shim（Windows 无 python3 命令，脚本内调用经 shim
        # 转发到当前解释器）——bash 用 find_bash 绝对路径，不依赖 PATH
        shim_dir = write_python3_shim(Path(self._shim_tmp.name))
        env = dict(self._env)
        env["PATH"] = f"{shim_dir}{os.pathsep}{env['PATH']}"
        return subprocess.run([BASH, str(script), *args],
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              cwd=self.root, env=env)

    def _run_register(self, *args):
        return subprocess.run(
            [sys.executable,
             str(self.root / "harness" / "skills" / "publish-main-base" / "baseline_register.py"),
             *args],
            capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=self.root, env=self._env)

    def _mk_issue(self, task="t1", status="open", origin="introduced", blocking=True,
                  issue_id="KI-X"):
        return cdp_issue.Issue(
            issue_id=issue_id, title="测试问题", discovered_in="38433d446f07",
            origin=origin, blocking=blocking,
            blocking_reason="r" if blocking else "", status=status, task=task,
            batch_id="000000000001")

    # ── 方向 3：rollback 状态推导（人工 --rollback 与失败点共用实现）────────
    BID_RB = "BL-20260903-01"

    def _write_promoted_yaml(self, status="promoted"):
        (self.root / "harness" / "config" / "baseline-status.yaml").write_text(
            "baselines:\n"
            f"- baseline_id: {self.BID_RB}\n"
            f"  status: {status}\n"
            "  source_commit: 0123456789ab\n", encoding="utf-8")

    def _state_file(self):
        p = self.root / "harness" / "log" / "cross-device" / f"promote-{self.BID_RB}.head"
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def test_rollback_drops_meta_commit_based_on_recorded_head(self):
        # 基准（promote 进入时 HEAD）已记状态文件；元提交恰前进一位 → 才 reset：
        # HEAD 回基准、revert-candidate 生效后随 reset 一并丢弃、状态文件清理
        self._write_promoted_yaml("candidate")
        self._git("add", "-A")
        self._git("commit", "-m", "构建(baseline): 登记")
        base = self._git("rev-parse", "HEAD").stdout.strip()
        self._write_promoted_yaml("promoted")   # promote 登记后随元提交入库
        (self.root / "c.txt").write_text("3\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "构建(baseline): 晋升元提交")
        self._state_file().write_text(base + "\n", encoding="utf-8")
        r = self._run("--rollback", "--baseline-id", self.BID_RB)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._git("rev-parse", "HEAD").stdout.strip(), base)
        self.assertFalse((self.root / "c.txt").exists())
        self.assertFalse(self._state_file().exists())

    def test_rollback_without_meta_commit_skips_reset_but_reverts(self):
        # 元提交未建（HEAD==基准）：不 reset（防误删）；登记已落（promoted）
        # 仍回退 candidate（revert 仅改工作区 yaml，不产生提交）
        base = self._git("rev-parse", "HEAD").stdout.strip()
        self._write_promoted_yaml("promoted")
        self._state_file().write_text(base + "\n", encoding="utf-8")
        r = self._run("--rollback", "--baseline-id", self.BID_RB)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._git("rev-parse", "HEAD").stdout.strip(), base)
        data = yaml.safe_load(
            (self.root / "harness" / "config" / "baseline-status.yaml")
            .read_text(encoding="utf-8"))
        self.assertEqual(data["baselines"][0]["status"], "candidate")
        self.assertFalse(self._state_file().exists())

    def test_rollback_without_state_file_skips_reset(self):
        # 基准缺失（状态文件丢失/人工环境）：不 reset，提示人工核查，rc 0
        self._write_promoted_yaml("candidate")
        self._git("add", "-A")
        self._git("commit", "-m", "修复(t): 无状态文件的额外提交")
        head = self._git("rev-parse", "HEAD").stdout.strip()
        r = self._run("--rollback", "--baseline-id", self.BID_RB)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._git("rev-parse", "HEAD").stdout.strip(), head)
        self.assertIn("跳过 dev reset", r.stdout + r.stderr)

    def test_rollback_candidate_status_skips_revert(self):
        # 推导一：登记未落（candidate）→ 不调 revert-candidate（无 reverted 输出）
        base = self._git("rev-parse", "HEAD").stdout.strip()
        self._write_promoted_yaml("candidate")
        self._state_file().write_text(base + "\n", encoding="utf-8")
        r = self._run("--rollback", "--baseline-id", self.BID_RB)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("reverted-candidate", r.stdout + r.stderr)

    # ── 方向 3（261f10265269）：promote 绑定 verified_tree（一致过路径由
    # test_promote_passes_code_covered_by_board_receipt 显式覆盖）──────────
    # ── 方向 3（261f10265269）：promote 绑定 verified_tree。流程对齐
    # test_promote_passes（board 收据 verified_commit=code_head、yaml 与收据
    # 随后入库），BOARD_OK 先过才达绑定比对；一致过路径由该用例显式覆盖。
    # 注：code/ 无改动的 promote 走豁免分支，不触发绑定比对（见零改动用例）
    def _board_chain(self, verified_tree):
        self._setup_remote()
        cdp_issue.write_issue(self._mk_issue(task="t1", origin="pre-existing",
                                             blocking=False), "现场")
        (self.root / "code").mkdir()
        (self.root / "code" / "foo.txt").write_text("x\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "修复(test): code 改动")
        self._git("push", "origin", "dev")
        code_head = self._git("rev-parse", "--short=12", "HEAD").stdout.strip()
        self._write_receipt(code_head, batch_id="000000000001",
                            cases="lcview-liveness", verify_mode="board",
                            verified_tree=verified_tree)
        (self.root / "harness" / "config" / "baseline-status.yaml").write_text(
            "baselines:\n"
            f"  - baseline_id: BL-TEST-01\n"
            f"    status: candidate\n"
            f"    source_commit: {code_head}\n"
            f"    sync_manifest: data/verify-results/20260831-100000-000000000001.md\n"
            f"    build_result: PASS\n"
            f"    package_result: PASS\n"
            f"    board_verify: PASS\n"
            f"    evidence:\n"
            f"      ki_gate: pass\n",
            encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "修复(test): board 收据与 candidate 入库")
        self._git("push", "origin", "dev")

    def test_promote_rejects_board_tree_mismatch(self):
        # 收据 verified_tree 与晋升内容树不一致（发布内容≠验证内容）→ 拒并列差异
        self._board_chain("0" * 40)
        r = self._promote()
        self.assertEqual(r.returncode, 1)
        self.assertIn("verified_tree 与晋升内容树不一致", r.stderr)

    def test_promote_rejects_missing_verified_tree(self):
        # 旧版收据缺 verified_tree → 无法证明绑定，拒（提示以当前工具重写）
        self._board_chain("")
        r = self._promote()
        self.assertEqual(r.returncode, 1)
        self.assertIn("缺 verified_tree", r.stderr)

    # ── 方向 4：真 git 仓两例（父等于 / 不等 VC）──────────────────────────
    def test_check_parent_equals_vc_passes(self):
        # 父(c1) == verified_commit(c1) → 前置校验通过
        r = self._run("--check-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("前置校验通过", r.stdout)
        self.assertIn(f"PARENT={self.parent_vc}", r.stdout)

    def test_rejects_mismatched_cdp_project_root(self):
        # 方向 4：CDP_PROJECT_ROOT 已设且不等于 git 顶层目录 → 收据查找前拒绝，
        # 防收据目录被环境变量改道（CDP_PROJECT_ROOT=root 时正常放行）
        r_ok = self._run("--check-only")
        self.assertEqual(r_ok.returncode, 0, r_ok.stderr)
        env = dict(self._env)
        env["CDP_PROJECT_ROOT"] = str(self.root / "elsewhere")
        r = subprocess.run(
            [BASH, str(self.root / "harness" / "skills" / "publish-main-base"
                       / "publish_main_base.sh"), "--check-only"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=self.root, env=env)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("CDP_PROJECT_ROOT", r.stderr)
        self.assertIn("git 顶层目录", r.stderr)
        self.assertIn("拒绝执行", r.stderr)

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
        # 空注册表（无活跃任务）→ 缺省推断 empty-registry 放行（不再要求必传）
        r = self._run_register("check-issues")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("task=empty-registry", r.stdout)

    def test_check_issues_infers_single_task(self):
        # 唯一活跃任务（非 fixed 条目）→ 缺省自动推断采用，无需人工传参
        cdp_issue.write_issue(self._mk_issue(task="lcview-refactor"), "现场")
        r = self._run_register("check-issues")
        self.assertEqual(r.returncode, 1)  # 推断出任务后按其门禁（open+introduced → 拒）
        self.assertIn("task=lcview-refactor", r.stderr)

    def test_check_issues_infers_multiple_tasks_rejects(self):
        # 多活跃任务 → 缺省推断歧义，报错列候选要求显式传 --task
        cdp_issue.write_issue(self._mk_issue(task="t1"), "现场")
        cdp_issue.write_issue(self._mk_issue(task="t2", issue_id="KI-Y"), "现场")
        r = self._run_register("check-issues")
        self.assertEqual(r.returncode, 1)
        self.assertIn("多值", r.stderr)
        self.assertIn("t1", r.stderr)
        self.assertIn("t2", r.stderr)

    def test_check_issues_rejects_typo_task(self):
        # 显式 --task 不在活跃集合内 → exit 3（防拼错静默通过）
        cdp_issue.write_issue(self._mk_issue(task="lcview-refactor"), "现场")
        r = self._run_register("check-issues", "--task", "lcview-rafactr")
        self.assertEqual(r.returncode, 3)
        self.assertIn("不在活跃任务集合", r.stderr)

    # ── 方向 3：空 build/push_board 记 FAIL 不记 SKIP ────────────────────
    def _registered_evidence(self):
        cfg = self.root / "harness" / "config" / "baseline-status.yaml"
        return yaml.safe_load(cfg.read_text(encoding="utf-8"))["baselines"][0]

    def test_add_candidate_empty_build_rejected(self):
        # 方向 4：收据 build/push_board 为空（记 FAIL）→ Python 层拒登记
        # （防绕过 shell 直调登记；基线证据须 pass/skip）
        self._write_receipt(self.parent_vc, build="", push_board="",
                            batch_id="000000000003", cases="lcview-liveness")
        r = self._run_register("add-candidate", "--source-commit", "abc123def456",
                               "--evidence-scope", "lcview-liveness",
                               "--receipt-path",
                               "data/verify-results/20260831-100000-000000000003.md")
        self.assertEqual(r.returncode, 1)
        self.assertIn("拒绝登记", r.stderr)

    def test_add_candidate_explicit_skip_still_skip(self):
        # 显式 skip（-s 批次收据）保持 SKIP，不受空值从严影响
        self._write_receipt(self.parent_vc, build="skip", push_board="skip",
                            batch_id="000000000004", cases="lcview-liveness")
        r = self._run_register("add-candidate", "--source-commit", "abc123def456",
                               "--evidence-scope", "lcview-liveness",
                               "--receipt-path",
                               "data/verify-results/20260831-100000-000000000004.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        b = self._registered_evidence()
        self.assertEqual(b["build_result"], "SKIP")
        self.assertEqual(b["board_verify"], "SKIP")

    # ── 方向 1：add-candidate 带病项记账（known_issues_carried）─────────
    def test_add_candidate_carried_written(self):
        # 显式传 --known-issues-carried → evidence 写入逗号分隔 issue_id
        self._write_receipt(self.parent_vc, batch_id="000000000005",
                            cases="lcview-liveness")
        r = self._run_register("add-candidate", "--source-commit", "abc123def456",
                               "--evidence-scope", "lcview-liveness",
                               "--known-issues-carried", "KI-001,KI-002",
                               "--receipt-path",
                               "data/verify-results/20260831-100000-000000000005.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        b = self._registered_evidence()
        self.assertEqual(b["evidence"]["known_issues_carried"], "KI-001,KI-002")

    def test_add_candidate_carried_missing_is_empty(self):
        # 缺参 → evidence 记空字符串（只记录不阻断，硬阻断会死锁）
        self._write_receipt(self.parent_vc, batch_id="000000000006",
                            cases="lcview-liveness")
        r = self._run_register("add-candidate", "--source-commit", "abc123def456",
                               "--evidence-scope", "lcview-liveness",
                               "--receipt-path",
                               "data/verify-results/20260831-100000-000000000006.md")
        self.assertEqual(r.returncode, 0, r.stderr)
        b = self._registered_evidence()
        self.assertEqual(b["evidence"]["known_issues_carried"], "")

    # ── 方向 2：carried_issue_ids 自动取 id（只收 open 与 scheduled）────
    def test_carried_issue_ids_only_open_scheduled(self):
        cdp_issue.write_issue(self._mk_issue(task="t1", origin="pre-existing",
                                             blocking=False, issue_id="KI-OPEN"),
                              "x")
        cdp_issue.write_issue(self._mk_issue(task="t1", origin="pre-existing",
                                             blocking=False, status="scheduled",
                                             issue_id="KI-SCHED"), "y")
        cdp_issue.write_issue(self._mk_issue(task="t1", status="fixed",
                                             origin="pre-existing", blocking=False,
                                             issue_id="KI-FIXED"), "z")
        cdp_issue.write_issue(self._mk_issue(task="t1", status="wontfix",
                                             origin="pre-existing", blocking=False,
                                             issue_id="KI-WONT"), "w")
        cdp_issue.write_issue(self._mk_issue(task="t2", origin="pre-existing",
                                             blocking=False, issue_id="KI-OTHER"),
                              "v")
        from baseline_register import carried_issue_ids
        self.assertEqual(set(carried_issue_ids("t1")), {"KI-OPEN", "KI-SCHED"})
        self.assertEqual(carried_issue_ids("t2"), ["KI-OTHER"])
        self.assertEqual(carried_issue_ids(""), [])
        self.assertEqual(set(carried_issue_ids("t1", self.root / "data" / "known-issues")),
                         {"KI-OPEN", "KI-SCHED"})

    # ── 方向 5：promote 收紧与 ki_gate 证据链（bare 远端 e2e）────────────
    def _receipt_commit_c3(self, batch_id="000000000002", **kw):
        # 收据入库为 c3（内容提交，父=c2==VC），保证 promote/prepare 工作树干净
        self._write_receipt(self.head_vc, batch_id=batch_id,
                            cases="lcview-liveness", **kw)
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
                         "--message-file", str(msg), "--task", "t1",
                         "--approved-by", "t", *extra)

    def test_promote_requires_approved_by(self):
        # 方向 6：--promote 缺 --approved-by → exit 3（审批凭据外部化，
        # 不再回落默认常量）
        self._setup_remote()
        cdp_issue.write_issue(self._mk_issue(task="t1", origin="pre-existing",
                                             blocking=False), "现场")
        self._candidate_yaml()
        self._receipt_commit_c3(verify_mode="skip")
        msg = Path(self._remote_tmp.name) / "promote-msg.txt"
        msg.write_text("构建(baseline): BL-TEST-01 基线晋升\n", encoding="utf-8")
        r = self._run("--promote", "--baseline-id", "BL-TEST-01",
                      "--message-file", str(msg))
        self.assertEqual(r.returncode, 3)
        self.assertIn("--approved-by", r.stderr)
        self.assertIn("审批凭据外部化", r.stderr)

    def test_promote_rejects_non_board_receipt(self):
        # 方向 2 board 拒：仅 skip 收据（board 收据不覆盖 code/ 改动）→ RECEIPT_FAIL
        self._setup_remote()
        (self.root / "code").mkdir()
        (self.root / "code" / "foo.txt").write_text("x\n", encoding="utf-8")
        self._receipt_commit_c3(verify_mode="skip")
        r = self._promote()
        self.assertEqual(r.returncode, 1)
        self.assertIn("check_class=RECEIPT_FAIL", r.stderr)
        self.assertIn("被最新 board 收据覆盖", r.stderr)

    def _candidate_yaml_pkg(self, package_result):
        """candidate 登记模板（可变 package_result，供方向 3 门禁两态用例）。"""
        (self.root / "harness" / "config" / "baseline-status.yaml").write_text(
            "baselines:\n"
            "  - baseline_id: BL-TEST-01\n"
            "    status: candidate\n"
            f"    source_commit: {self.head_vc}\n"
            "    sync_manifest: data/verify-results/20260831-100000-000000000002.md\n"
            "    build_result: PASS\n"
            f"    package_result: {package_result}\n"
            "    board_verify: PASS\n"
            "    evidence:\n"
            "      ki_gate: pass\n",
            encoding="utf-8")

    def _commit_meta(self):
        # 登记元提交（构建(baseline): 前缀，内容提交回溯会跳过）——candidate
        # yaml 写入工作树后须入库保持树净（promote 前置拒绝脏树）
        self._git("add", "-A")
        self._git("commit", "-m", "构建(baseline): BL-TEST-01 登记元提交")
        self._git("push", "origin", "dev")

    def _code_change_with_board_receipt(self):
        """code/ 改动 c3 + board 收据入库 c4（verified_commit=c3，覆盖链成立）。"""
        self._setup_remote()
        (self.root / "code").mkdir()
        (self.root / "code" / "foo.txt").write_text("x\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "修复(test): 代码改动三")
        self._git("push", "origin", "dev")
        code_head = self._git("rev-parse", "--short=12", "HEAD").stdout.strip()
        self._write_receipt(code_head, batch_id="000000000002",
                            cases="lcview-liveness", verify_mode="board")
        self._git("add", "-A")
        self._git("commit", "-m", "修复(test): 收据入库四")
        self._git("push", "origin", "dev")

    def test_promote_package_gate_blocks_unknown(self):
        # 方向 3（批次 ff33f92060ac）promote 硬门禁：动过 code（覆盖链/树绑定
        # 均成立）且 package_result=UNKNOWN 非 PASS → RECEIPT_FAIL 阻断
        self._code_change_with_board_receipt()
        self._candidate_yaml_pkg("UNKNOWN")
        self._commit_meta()
        r = self._promote()
        self.assertEqual(r.returncode, 1)
        self.assertIn("check_class=RECEIPT_FAIL", r.stderr)
        self.assertIn("promote 硬门禁", r.stderr)
        self.assertIn("package_result=UNKNOWN", r.stderr)
        self.assertIn("no-code-change 豁免不受限", r.stderr)

    def test_promote_package_gate_passes_with_pass(self):
        # 方向 3 正常路径：动过 code 且 package_result=PASS → 门禁放行，
        # promote 完成（同时证明覆盖链/树绑定 fixture 与门禁位置正确）
        self._code_change_with_board_receipt()
        self._candidate_yaml_pkg("PASS")
        self._commit_meta()
        r = self._promote()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("promote 完成", r.stdout)
        self.assertIn("verified/BL-TEST-01",
                      self._git("tag", "-l", "verified/BL-TEST-01").stdout)

    def test_prepare_evidence_anchor_uses_latest_board_receipt(self):
        # 缺陷修复：最新收据为 -s skip（cases 空），evidence 锚点须回溯最新
        # board 收据——candidate 的 evidence_scope/sync_manifest/build/board_verify
        # 均取 board 收据实测值（SKILL 阶段 3 语义），而非在 skip 收据上死锁
        self._setup_remote()
        cdp_issue.write_issue(self._mk_issue(task="t1", origin="pre-existing",
                                             blocking=False), "现场")
        # 先落 board 收据（有实测 cases），再落 skip 收据（最新，cases 空）
        self._write_receipt(self.parent_vc, batch_id="000000000001",
                            cases="lcview-liveness", verify_mode="board")
        self._write_receipt(self.head_vc, batch_id="000000000002",
                            cases="", verify_mode="skip")
        self._git("add", "-A")
        self._git("commit", "-m", "修复(test): board 与 skip 收据双落")
        self._git("push", "origin", "dev")
        r = self._run("--prepare", "--task", "t1")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = yaml.safe_load(
            (self.root / "harness" / "config" / "baseline-status.yaml").read_text(
                encoding="utf-8"))
        b = data["baselines"][0]
        # 证据锚点 = board 收据：scope 从其 cases 推导，收据路径指向它
        self.assertEqual(b["evidence_scope"], "lcview-liveness")
        self.assertIn("000000000001", b["sync_manifest"])
        self.assertEqual(b["build_result"], "PASS")
        self.assertEqual(b["board_verify"], "PASS")

    def test_promote_passes_code_covered_by_board_receipt(self):
        # 缺陷修复：code/ 改动已被较早 board 收据覆盖，仅最新收据被 -s skip
        # 批刷成非 board → promote 收紧回溯 board 收据判覆盖，放行（原
        # "最新收据须 board" 语义误拒该场景）
        self._setup_remote()
        cdp_issue.write_issue(self._mk_issue(task="t1", origin="pre-existing",
                                             blocking=False), "现场")
        (self.root / "code").mkdir()
        (self.root / "code" / "foo.txt").write_text("x\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "修复(test): code 改动")
        self._git("push", "origin", "dev")
        code_head = self._git("rev-parse", "--short=12", "HEAD").stdout.strip()
        # board 收据覆盖 code 改动提交（candidate yaml 一并入库，promote 要求树净）
        self._write_receipt(code_head, batch_id="000000000001",
                            cases="lcview-liveness", verify_mode="board")
        (self.root / "harness" / "config" / "baseline-status.yaml").write_text(
            "baselines:\n"
            f"  - baseline_id: BL-TEST-01\n"
            f"    status: candidate\n"
            f"    source_commit: {code_head}\n"
            f"    sync_manifest: data/verify-results/20260831-100000-000000000001.md\n"
            f"    build_result: PASS\n"
            f"    package_result: PASS\n"
            f"    board_verify: PASS\n"
            f"    evidence:\n"
            f"      ki_gate: pass\n",
            encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "修复(test): board 收据与 candidate 入库")
        self._git("push", "origin", "dev")
        board_head = self._git("rev-parse", "--short=12", "HEAD").stdout.strip()
        self._write_receipt(board_head, batch_id="000000000002",
                            cases="", verify_mode="skip")
        self._git("add", "-A")
        self._git("commit", "-m", "修复(test): skip 收据入库")
        self._git("push", "origin", "dev")
        r = self._promote()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("promote 完成", r.stdout)
        self.assertIn("verified/BL-TEST-01",
                      self._git("tag", "-l", "verified/BL-TEST-01").stdout)

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
        # 方向 2/3 树不等拒：meta 提交夹带 data/evil.txt（非 code/ 非排除项，
        # 不被 code 收紧误拦）→ verify-tree 失败，rollback 一并删除本地与
        # 远端 verified tag（方向 4），退 1
        self._setup_remote()
        self._receipt_commit_c3()
        self._candidate_yaml()
        (self.root / "data" / "evil.txt").write_text("x\n", encoding="utf-8")
        self._git("add", "-A")
        self._git("commit", "-m", "构建(baseline): 伪造元提交夹带")
        self._git("push", "origin", "dev")
        r = self._promote()
        self.assertEqual(r.returncode, 1)
        self.assertIn("树等价", r.stderr)
        self.assertIn("data/evil.txt", r.stderr)
        self.assertEqual(
            self._git("tag", "-l", "verified/BL-TEST-01").stdout.strip(), "")
        self.assertEqual(
            self._git("ls-remote", "origin",
                      "refs/tags/verified/BL-TEST-01").stdout.strip(), "")

    def test_promote_passes_known_issues_excluded(self):
        # 方向 3/4 排除生效：meta 提交夹带 data/known-issues/ 文件（promote
        # 清算删除目录）→ verify-tree 排除后仍等价，promote 放行；
        # 且清算目录随晋升提交入库（git add -A data/known-issues）
        self._setup_remote()
        self._receipt_commit_c3(verify_mode="skip")
        self._candidate_yaml()
        # 合法登记（活项，门禁不拒）：随收据提交后的 meta 提交夹带入 dev
        cdp_issue.write_issue(cdp_issue.Issue(
            issue_id="KI-EXCL", title="排除目录条目", discovered_in="abc",
            origin="pre-existing", blocking=False, status="open",
            task="t1", batch_id="0000000000ab"), "现场")
        self._git("add", "-A")
        self._git("commit", "-m", "构建(baseline): 夹带 known-issues 清算文件")
        self._git("push", "origin", "dev")
        r = self._promote()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("promote 完成", r.stdout)
        # 方向 4：data/known-issues 随晋升提交入库（main 树含清算文件）
        self.assertIn("data/known-issues",
                      self._git("ls-tree", "-r", "--name-only", "main",
                                "data/known-issues").stdout)

    def test_prepare_without_task_infers_ki_gate(self):
        # 方向 7：prepare 全不传（--task/--evidence-scope）走通——门禁无条件执行，
        # 缺省从唯一活跃 task 推断（KIGATE=inferred），scope 从收据 cases 推导
        self._setup_remote()
        # 唯一活跃 task（非阻塞非 introduced，门禁推断后可放行），随收据入库为 c3
        cdp_issue.write_issue(self._mk_issue(task="t1", origin="pre-existing",
                                             blocking=False), "现场")
        self._receipt_commit_c3()
        r = self._run("--prepare")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("known-issues 门禁通过（task=t1", r.stderr)
        data = yaml.safe_load(
            (self.root / "harness" / "config" / "baseline-status.yaml").read_text(
                encoding="utf-8"))
        self.assertEqual(data["baselines"][0]["evidence"]["ki_gate"], "inferred")
        # scope 缺省推导自收据 cases，而非要求人工申报
        self.assertEqual(data["baselines"][0]["evidence_scope"], "lcview-liveness")
        # 推断任务（t1）的 open 条目同样自动携带（方向 2：read_index 取 open/scheduled）
        self.assertEqual(data["baselines"][0]["evidence"]["known_issues_carried"],
                         "KI-X")

    def test_prepare_with_task_records_ki_gate_pass(self):
        # 方向 3/4：--task 门禁通过（空登记合法）→ KIGATE=pass 写入 evidence
        self._setup_remote()
        # 该任务存在 open（非阻塞非 introduced）遗留 → prepare 自动携带进 candidate
        cdp_issue.write_issue(self._mk_issue(task="t1", origin="pre-existing",
                                             blocking=False), "现场")
        self._receipt_commit_c3()
        r = self._run("--prepare", "--task", "t1", "--evidence-scope", "lcview-liveness")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("KIGATE=not-run", r.stderr)
        data = yaml.safe_load(
            (self.root / "harness" / "config" / "baseline-status.yaml").read_text(
                encoding="utf-8"))
        self.assertEqual(data["baselines"][0]["evidence"]["ki_gate"], "pass")
        # 带病项自动携带：open 条目 id 写入 known_issues_carried（只记录不阻断）
        self.assertEqual(data["baselines"][0]["evidence"]["known_issues_carried"],
                         "KI-X")

    def test_promote_without_task_infers_ki_gate(self):
        # 方向 7：promote 全不传（--task/--evidence-scope）走通——强制 --task 已删，
        # 门禁在共用段推断唯一活跃 task 后放行，完整 e2e 晋升完成
        self._setup_remote()
        cdp_issue.write_issue(self._mk_issue(task="t1", origin="pre-existing",
                                             blocking=False), "现场")
        self._candidate_yaml()
        self._receipt_commit_c3(verify_mode="skip")
        msg = Path(self._remote_tmp.name) / "promote-msg.txt"
        msg.write_text("构建(baseline): BL-TEST-01 基线晋升\n", encoding="utf-8")
        r = self._run("--promote", "--baseline-id", "BL-TEST-01",
                      "--message-file", str(msg), "--approved-by", "t")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("known-issues 门禁通过（task=t1", r.stderr)
        self.assertIn("promote 完成", r.stdout)
        self.assertIn("refs/tags/verified/BL-TEST-01",
                      self._git("ls-remote", "origin",
                                "refs/tags/verified/BL-TEST-01").stdout)


if __name__ == "__main__":
    unittest.main()
