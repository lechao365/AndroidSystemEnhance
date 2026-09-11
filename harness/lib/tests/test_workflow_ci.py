"""批次 ca64a314af81 方向 4：最小 CI workflow 硬约束固化。

约束：GitHub 托管 runner（禁 self-hosted）、最小权限、不用 secrets、
action 按 SHA 固定、只跑自检（无打点指针降级路径）。
"""

import re
import sys
import unittest
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "selfcheck.yml"

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from selfcheck import REQUIRED_RC_KEYS  # noqa: E402 方向 3：必查键单点定义


@unittest.skipUnless(WORKFLOW.exists(), "workflow 文件不存在（CI 未随仓检出）")
class TestSelfcheckWorkflow(unittest.TestCase):
    # 需真实仓：读仓库真实 .github/workflows/selfcheck.yml（放行隔离）
    pytestmark = pytest.mark.real_repo("校验仓库真实 CI workflow")

    def setUp(self):
        self.raw = WORKFLOW.read_text(encoding="utf-8")
        self.doc = yaml.safe_load(self.raw)

    def test_github_hosted_runner_only(self):
        # 仅 GitHub 托管 Linux；runs-on 固定值即天然排除 self-hosted 标签
        job = self.doc["jobs"]["selfcheck"]
        self.assertEqual(job["runs-on"], "ubuntu-latest")

    def test_minimal_permissions_no_secrets(self):
        # 最小权限 contents: read；全文不出现 secrets 上下文
        self.assertEqual(self.doc["permissions"], {"contents": "read"})
        self.assertNotIn("secrets.", self.raw)

    def test_actions_pinned_by_sha(self):
        # 所有 uses 的 action 一律按 40 位 commit SHA 固定（禁浮动 tag）
        uses = re.findall(r"uses:\s*(\S+)@(\S+)", self.raw)
        self.assertGreaterEqual(len(uses), 1)
        for action, ref in uses:
            self.assertRegex(ref, r"^[0-9a-f]{40}$",
                             f"{action} 未按 SHA 固定: {ref}")

    def test_runs_selfcheck_only_with_degraded_pointer(self):
        # 唯一 job 只跑自检；CDP_PROJECT_ROOT 在自检 step 级 env 注入
        # （无打点指针降级路径）。方向 1 修正：原 job 级 env 用
        # ${{ runner.temp }}——runner 上下文仅 step 级可用，job 级 env
        # 求值失败致工作流启动失败（89 次 run 全 failure 且零 job），
        # 改 step 内 $RUNNER_TEMP 环境变量
        job = self.doc["jobs"]["selfcheck"]
        steps = job["steps"]
        run_steps = [s.get("run", "") for s in steps]
        joined = "\n".join(run_steps)
        self.assertIn("selfcheck.py", joined)
        # job 级 env 不得再用表达式上下文（runner.temp 在此不可用）
        self.assertNotIn("runner.temp", str(job.get("env", {})))
        # 自检 step 级 env 用 $RUNNER_TEMP（step 内环境变量可用）
        selfcheck = next(s for s in steps if "selfcheck.py" in s.get("run", ""))
        self.assertEqual(selfcheck.get("env", {}).get("CDP_PROJECT_ROOT"),
                         "$RUNNER_TEMP/cdp-root")

    def test_ci_parses_required_rcs(self):
        # 方向 1 + 方向 3：selfcheck main 恒返 0（只采集不判定），CI 步骤必须
        # 解析全部 REQUIRED_RC_KEYS（含 pyenv_rc/ioctl_rc/manifest_rc）任一
        # 非零即失败——否则测试失败也绿（CI 只跑不判）。键集合与 ws_report
        # 必查键同源（selfcheck.REQUIRED_RC_KEYS 单点定义）。
        # 方向 3：不再用 assertIn 扫全文（rc 名出现在注释里即可被 assertIn
        # 满足，假阳性）；解析 CI 的 `for rc_name in <键集合>` 行与
        # REQUIRED_RC_KEYS 双向比对（集合相等）。且须**先剥注释**——判定段
        # 整体被注释掉时正则仍能匹配注释原文致判绿，剥掉整行 # 注释后无键
        # 集合即 assertIsNotNone 判红。
        job = self.doc["jobs"]["selfcheck"]
        run_steps = [s.get("run", "") for s in job["steps"]]
        joined = "\n".join(run_steps)
        stripped = "\n".join(
            ln for ln in joined.splitlines() if not ln.lstrip().startswith("#"))
        m = re.search(r"for\s+rc_name\s+in\s+([^;\n]+);", stripped)
        self.assertIsNotNone(m, "CI 须有未注释的 `for rc_name in <键集合>` 循环"
                                "（判定段被注释掉不得判绿）")
        ci_keys = set(m.group(1).split())
        self.assertEqual(ci_keys, set(REQUIRED_RC_KEYS),
                         "CI for 键集合须与 REQUIRED_RC_KEYS 双向一致（漏键/"
                         "多余键/注释伪满足均判红）")
        # 逐项判定非零即失败
        self.assertIn('"${rc_name}=0"', stripped, "CI 须判定 *_rc 非零即失败")
        self.assertIn("exit 1", stripped, "CI 判定失败须显式 exit 1")

    def test_host_tests_job_runs_make_test(self):
        # P0-A：host-tests 作业须跑两个内核 host 单测 make test（业务快检）
        job = self.doc["jobs"]["host-tests"]
        run_steps = "\n".join(s.get("run", "") for s in job["steps"])
        for d in ("LcView", "LcIod"):
            self.assertIn(f"make -C code/rpi5/kernel/new/vendor/lechao/{d}/tests test",
                          run_steps)

    def test_host_tests_job_static_check_diff_driven(self):
        # P0-B：C/C++ 静态检查须 diff 驱动（相对 origin/main，无改动跳过）——
        # 存量 code/ 文件不强制归一（避免 RECEIPT_MISSING 推送门禁）
        job = self.doc["jobs"]["host-tests"]
        run_steps = "\n".join(s.get("run", "") for s in job["steps"])
        self.assertIn("git fetch --quiet origin main", run_steps)
        self.assertIn("git diff --name-only", run_steps)
        self.assertIn("clang-format --dry-run --Werror", run_steps)
        self.assertIn("clang-tidy", run_steps)

    def test_host_tests_job_installs_clang_tools(self):
        job = self.doc["jobs"]["host-tests"]
        run_steps = "\n".join(s.get("run", "") for s in job["steps"])
        self.assertIn("apt-get", run_steps)

    def test_strip_comments_before_gate_check(self):
        # 方向 3 内部逻辑：先剥整行 # 注释再找 for 键集合——注释掉的判定段
        # 不再被匹配（防注释伪满足判绿）
        raw = "# for rc_name in pytest_rc refs_rc; do\nfor rc_name in a b; do\n"
        stripped = "\n".join(ln for ln in raw.splitlines()
                             if not ln.lstrip().startswith("#"))
        m = re.search(r"for\s+rc_name\s+in\s+([^;\n]+);", stripped)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), "a b")
        # 判定段整体被注释掉 → 剥注释后无键集合（assertIsNone 即判红依据）
        only_comment = "# for rc_name in a b; do\n"
        stripped2 = "\n".join(ln for ln in only_comment.splitlines()
                              if not ln.lstrip().startswith("#"))
        self.assertIsNone(
            re.search(r"for\s+rc_name\s+in\s+([^;\n]+);", stripped2),
            "整体注释掉的判定段剥注释后须无键集合（不得判绿）")

    def test_every_required_rc_has_ws_report_red_case(self):
        # CDP-DOD-001 门禁化（方向 3）：REQUIRED_RC_KEYS 每个必查 rc 都须有
        # ws_report 侧判红用例——制造破坏→该 rc=1→ws_report 返 2 拒写收据。
        # 防新增检查器接入必查键却无判红用例（接了不判=假绿）。
        # 覆盖来源两类（取并集）：
        #   1) 字面量判红：源码存在 "{rc}=1" 文本（手写用例，如 config_rc=1）
        #   2) 模板注册：_assert_rc_nonzero_rejected("<rc>") 调用（模板化构造，
        #      运行时拼 {rc}=1，源码无字面量）
        src = (REPO_ROOT / "harness" / "skills" / "workspace-verify"
               / "tests" / "test_ws_report.py").read_text(encoding="utf-8")
        # 剥整行 # 注释再扫描——注释掉的模板注册/判红构造不得判绿（与
        # test_strip_comments_before_gate_check 同款防注释伪满足教训）
        clean = "\n".join(ln for ln in src.splitlines()
                          if not ln.lstrip().startswith("#"))
        literal = {m.group(1) for m in re.finditer(r"(\w+_rc)=1\b", clean)}
        registered = set(re.findall(
            r'_assert_rc_nonzero_rejected\("(\w+_rc)"\)', clean))
        covered = literal | registered
        missing = [rc for rc in REQUIRED_RC_KEYS if rc not in covered]
        self.assertEqual(
            missing, [],
            f"以下必查 rc 缺 ws_report 判红用例（须制造破坏→该 rc=1→拒写收据，"
            f"可并入 _assert_rc_nonzero_rejected 模板）: {sorted(missing)}")


if __name__ == "__main__":
    unittest.main()
