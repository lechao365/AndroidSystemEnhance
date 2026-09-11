#!/usr/bin/env python3
"""check_ci_head 单测：push 前置 CI 门禁（curl 免认证查 HEAD check-runs）。

判红：CI 存在失败结论（failure/cancelled/timed_out）阻断；fail-open 于
API 抖动（网络失败/限流/5xx/未知响应/非 JSON）与仓库非公开不可达（404
登记放弃），均降级告警不阻断推送（不得静默丢弃也不拖垮主流程）。"""

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
        with _resp("200", '{"check_runs":[{"conclusion":"success"},'
                           '{"conclusion":"neutral"}]}'):
            self.assertEqual(cci.check_ci("a" * 40, "o/r"), 0)

    def test_failure_conclusion_blocks(self):
        # 判红：conclusion=failure 阻断（fail-closed）
        with _resp("200", '{"check_runs":[{"conclusion":"success"},'
                           '{"conclusion":"failure"}]}'):
            self.assertEqual(cci.check_ci("a" * 40, "o/r"), 1)
        # cancelled / timed_out 同为失败结论
        for c in ("cancelled", "timed_out"):
            with _resp("200", '{"check_runs":[{"conclusion":"%s"}]}' % c):
                self.assertEqual(cci.check_ci("a" * 40, "o/r"), 1)

    def test_network_failure_warns_not_blocks(self):
        # API 抖动：网络失败 → 降级告警不阻断
        with _resp(None, "curl: could not resolve host"):
            self.assertEqual(cci.check_ci("a" * 40, "o/r"), 0)

    def test_rate_limit_warns_not_blocks(self):
        for code in ("403", "429"):
            with _resp(code, "rate limited"):
                self.assertEqual(cci.check_ci("a" * 40, "o/r"), 0)

    def test_server_error_warns_not_blocks(self):
        with _resp("500", "boom"):
            self.assertEqual(cci.check_ci("a" * 40, "o/r"), 0)

    def test_404_repo_not_public_warns_not_blocks(self):
        # 仓库非公开或不可达：登记放弃并写明（非静默），不阻断
        with _resp("404", '{"message":"Not Found"}'):
            self.assertEqual(cci.check_ci("a" * 40, "o/r"), 0)

    def test_unknown_code_warns_not_blocks(self):
        with _resp("418", "teapot"):
            self.assertEqual(cci.check_ci("a" * 40, "o/r"), 0)

    def test_non_json_body_warns_not_blocks(self):
        with _resp("200", "<html>not json</html>"):
            self.assertEqual(cci.check_ci("a" * 40, "o/r"), 0)

    def test_empty_runs_passes(self):
        with _resp("200", '{"check_runs":[]}'):
            self.assertEqual(cci.check_ci("a" * 40, "o/r"), 0)

    def test_slug_inferred_from_ssh_remote(self):
        r = cci._repo_slug_from_remote(".")  # 真实 remote 推断
        self.assertIsInstance(r, str)
        self.assertIn("/", r)

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

    def test_missing_slug_blocks(self):
        with mock.patch.object(cci, "_repo_slug_from_remote", return_value=None):
            rc = cci.main(["--head", "a" * 40])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
