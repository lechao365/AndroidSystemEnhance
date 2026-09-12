#!/usr/bin/env python3
"""check_ci_head 单测：push 前置 CI 门禁（curl 免认证查 actions runs）。

接口（方向 2）：弃 /commits/<sha>/check-runs（total_count 恒 0），改查
/actions/runs?head_sha=；核对待推送 HEAD 与上一个已推送提交（prev-head，
新 HEAD 未推送无 run 时以已推送提交为主判据）。

判红：actions run conclusion 含 failure/cancelled/timed_out → 阻断；
无 run 记录（新 commit 未推送 / 422）→ 放行；fail-open 于 API 抖动
（网络失败/限流/5xx/未知响应/非 JSON）与仓库非公开不可达（404 登记放弃）。"""

import re
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_ci_head as cci  # noqa: E402


def _resp(code, body):
    return mock.patch.object(cci, "_curl", return_value=(code, body))


class TestCheckCi(unittest.TestCase):
    def test_all_success_passes(self):
        with _resp("200", '{"workflow_runs":[{"id":1,"conclusion":"success"},'
                           '{"id":2,"conclusion":"neutral"}]}'):
            self.assertEqual(cci.check_ci("a" * 40, "b" * 40, "o/r"), 0)

    def test_failure_conclusion_blocks(self):
        # 判红：conclusion=failure 阻断（fail-closed）
        with _resp("200", '{"workflow_runs":[{"id":1,"conclusion":"success"},'
                           '{"id":2,"conclusion":"failure"}]}'):
            self.assertEqual(cci.check_ci("a" * 40, "b" * 40, "o/r"), 1)
        # cancelled / timed_out 同为失败结论
        for c in ("cancelled", "timed_out"):
            with _resp("200", '{"workflow_runs":[{"conclusion":"%s"}]}' % c):
                self.assertEqual(cci.check_ci("a" * 40, "b" * 40, "o/r"), 1)

    def test_prev_head_failure_blocks(self):
        # 方向 2：上一个已推送提交（prev-head）CI 失败 → 阻断（主判据——
        # 待推送新 HEAD 无 run 记录时须以已推送提交 CI 状态为准）
        # 第一次调用 prev-head 返回 failure，第二次（HEAD）未触达
        def _curl(url):
            if "head_sha=b" in url:
                return "200", '{"workflow_runs":[{"conclusion":"failure"}]}'
            return "200", '{"workflow_runs":[]}'
        with mock.patch.object(cci, "_curl", side_effect=_curl):
            self.assertEqual(cci.check_ci("a" * 40, "b" * 40, "o/r"), 1)

    def test_new_head_no_runs_passes(self):
        # 方向 2：待推送新 HEAD 无 run 记录（未推送）→ 放行，prev-head 无
        # 失败 → 整体放行（422 场景同此，不再当 API 抖动阻断/误报）
        def _curl(url):
            return "200", '{"workflow_runs":[]}'
        with mock.patch.object(cci, "_curl", side_effect=_curl):
            self.assertEqual(cci.check_ci("a" * 40, "b" * 40, "o/r"), 0)

    def test_422_unknown_commit_passes(self):
        # 方向 2：推送前 HEAD 在 GitHub 尚不存在（422）→ 放行（无 run 记录
        # 是正常新提交场景，非 API 抖动）
        with _resp("422", '{"message":"No commit found"}'):
            self.assertEqual(cci.check_ci("a" * 40, None, "o/r"), 0)

    def test_no_prev_head_skips_prev_check(self):
        # prev-head 为空（origin/dev 不可解析）→ 只核对待推送 HEAD
        with _resp("200", '{"workflow_runs":[{"conclusion":"success"}]}'):
            self.assertEqual(cci.check_ci("a" * 40, None, "o/r"), 0)

    def test_network_failure_warns_not_blocks(self):
        # API 抖动：网络失败 → 降级告警不阻断
        with _resp(None, "curl: could not resolve host"):
            self.assertEqual(cci.check_ci("a" * 40, "b" * 40, "o/r"), 0)

    def test_rate_limit_warns_not_blocks(self):
        for code in ("403", "429"):
            with _resp(code, "rate limited"):
                self.assertEqual(cci.check_ci("a" * 40, "b" * 40, "o/r"), 0)

    def test_server_error_warns_not_blocks(self):
        with _resp("500", "boom"):
            self.assertEqual(cci.check_ci("a" * 40, "b" * 40, "o/r"), 0)

    def test_404_repo_not_public_warns_not_blocks(self):
        # 仓库非公开或不可达：登记放弃并写明（非静默），不阻断
        with _resp("404", '{"message":"Not Found"}'):
            self.assertEqual(cci.check_ci("a" * 40, "b" * 40, "o/r"), 0)

    def test_unknown_code_warns_not_blocks(self):
        with _resp("418", "teapot"):
            self.assertEqual(cci.check_ci("a" * 40, "b" * 40, "o/r"), 0)

    def test_non_json_body_warns_not_blocks(self):
        with _resp("200", "<html>not json</html>"):
            self.assertEqual(cci.check_ci("a" * 40, "b" * 40, "o/r"), 0)

    def test_slug_inferred_from_ssh_remote(self):
        # 从 remote.origin.url 推断 owner/repo：临时仓显式设 origin（ssh 格式），
        # 不依赖真实仓（净克隆/CI 中 origin 各异的假失败已剔除）
        import subprocess
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "-C", d, "init", "-q"], check=True)
            subprocess.run(["git", "-C", d, "remote", "add", "origin",
                            "git@github.com:lechao365/AndroidSystemEnhance.git"],
                           check=True)
            self.assertEqual(
                cci._repo_slug_from_remote(d), "lechao365/AndroidSystemEnhance")

    def test_slug_regex_variants(self):
        pat = r"(?:github\.com[:/])([^/\s]+/[^/\s]+?)(?:\.git)?$"
        self.assertEqual(
            re.search(pat, "git@github.com:lechao365/AndroidSystemEnhance.git")
            .group(1), "lechao365/AndroidSystemEnhance")
        self.assertEqual(
            re.search(pat, "https://github.com/o/r.git").group(1), "o/r")
        self.assertIsNone(re.search(pat, "git@gitlab.com:o/r.git"))

    def test_bad_head_param_returns_2(self):
        with mock.patch.object(cci, "_curl") as c:
            rc = cci.main(["--head", "xyz", "--repo-slug", "o/r"])
        self.assertEqual(rc, 2)
        c.assert_not_called()

    def test_bad_prev_head_param_returns_2(self):
        rc = cci.main(["--head", "a" * 40, "--prev-head", "zz",
                       "--repo-slug", "o/r"])
        self.assertEqual(rc, 2)

    def test_missing_slug_blocks(self):
        with mock.patch.object(cci, "_repo_slug_from_remote", return_value=None):
            rc = cci.main(["--head", "a" * 40])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
