"""批次 ca64a314af81 方向 4：最小 CI workflow 硬约束固化。

约束：GitHub 托管 runner（禁 self-hosted）、最小权限、不用 secrets、
action 按 SHA 固定、只跑自检（无打点指针降级路径）。
"""

import re
import unittest
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "selfcheck.yml"


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
        # 唯一 job 只跑自检；显式注入 CDP_PROJECT_ROOT（无打点指针降级路径）
        job = self.doc["jobs"]["selfcheck"]
        steps = job["steps"]
        run_steps = [s.get("run", "") for s in steps]
        joined = "\n".join(run_steps)
        self.assertIn("selfcheck.py", joined)
        self.assertEqual(job.get("env", {}).get("CDP_PROJECT_ROOT"),
                         "${{ runner.temp }}/cdp-root")


if __name__ == "__main__":
    unittest.main()
