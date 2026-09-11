#!/usr/bin/env python3
"""check_known_issues 单测：baseline-status.yaml 引用的 KI 编号须有对应
记录文件（悬空引用判红，防 promote 删文件后引用断裂无从追溯）。"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_known_issues as cki  # noqa: E402


def _mk(repo: Path, *, baselines: str, issues: dict[str, str]):
    """构造临时仓：baseline-status.yaml + data/known-issues/ 记录文件。"""
    cfg = repo / "harness" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "baseline-status.yaml").write_text(baselines, encoding="utf-8")
    d = repo / "data" / "known-issues"
    d.mkdir(parents=True, exist_ok=True)
    for name, content in issues.items():
        (d / name).write_text(content, encoding="utf-8")


_HEADER = (
    "- schema_version: 1\n- issue_id: KI-20260901-001\n- title: t\n"
    "- discovered_in: aaaa1111bbbb\n- origin: pre-existing\n- severity: P2\n"
    "- blocking: False\n- blocking_reason: \n- status: fixed\n- task: t\n"
    "- resolved_in: aaaa1111bbbb\n- archived_in: BL-20260901-01\n"
    "\n## body\n现场\n"
)


class TestRefIds(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_carried_single_and_closed_list(self):
        # carried 单 id 字符串 + closed 列表 dict 均解析
        _mk(self.repo, baselines=(
            "baselines:\n"
            "- baseline_id: BL-A\n  evidence:\n"
            "    known_issues_carried: KI-20260901-001\n"
            "    known_issues_closed:\n"
            "    - issue_id: KI-20260831-001\n"
            "      resolved_in: aaaa1111bbbb\n"),
            issues={})
        self.assertEqual(cki.ref_ids(self.repo),
                         ["KI-20260829-001" if False else "KI-20260831-001",
                          "KI-20260901-001"])

    def test_carried_multi_separators(self):
        # carried 多 id 逗号/空格分隔均解析
        _mk(self.repo, baselines=(
            "baselines:\n"
            "- baseline_id: BL-A\n  evidence:\n"
            "    known_issues_carried: KI-20260901-001, KI-20260831-001\n"),
            issues={})
        self.assertEqual(cki.ref_ids(self.repo),
                         ["KI-20260831-001", "KI-20260901-001"])

    def test_empty_carried_ignored(self):
        _mk(self.repo, baselines=(
            "baselines:\n"
            "- baseline_id: BL-A\n  evidence:\n"
            "    known_issues_carried: ''\n    known_issues_closed: []\n"),
            issues={})
        self.assertEqual(cki.ref_ids(self.repo), [])


class TestScan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_missing_issue_reported(self):
        # 红灯：baseline 引用 KI 但 data/known-issues/ 无记录文件 → 判红
        _mk(self.repo, baselines=(
            "baselines:\n"
            "- baseline_id: BL-A\n  evidence:\n"
            "    known_issues_carried: KI-20260901-001\n"),
            issues={})
        out = cki.scan(self.repo)
        self.assertTrue(any("KI-20260901-001" in o and "悬空" in o for o in out))

    def test_existing_issue_ok(self):
        # 记录文件含 issue_id 头 → 放行
        _mk(self.repo, baselines=(
            "baselines:\n"
            "- baseline_id: BL-A\n  evidence:\n"
            "    known_issues_carried: KI-20260901-001\n"),
            issues={"20260901-123109-912659304ee2-t.md": _HEADER})
        self.assertEqual(cki.scan(self.repo), [])

    def test_closed_reference_reported(self):
        # closed 列表引用缺文件 → 判红
        _mk(self.repo, baselines=(
            "baselines:\n"
            "- baseline_id: BL-A\n  evidence:\n"
            "    known_issues_closed:\n"
            "    - issue_id: KI-20260831-001\n      resolved_in: aaaa1111bbbb\n"),
            issues={})
        out = cki.scan(self.repo)
        self.assertTrue(any("KI-20260831-001" in o for o in out))

    def test_no_references_ok(self):
        # 无任何引用（carried 空/closed 空）→ 放行
        _mk(self.repo, baselines=(
            "baselines:\n"
            "- baseline_id: BL-A\n  evidence:\n"
            "    known_issues_carried: ''\n    known_issues_closed: []\n"),
            issues={})
        self.assertEqual(cki.scan(self.repo), [])


class TestScanFailClosed(unittest.TestCase):
    def test_yaml_missing_reported_red(self):
        # 依赖缺失 fail-closed：PyYAML 不可用 → 判红不静默放行
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d)
            with mock.patch.object(cki, "yaml", None):
                out = cki.scan(repo)
        self.assertTrue(out)


if __name__ == "__main__":
    unittest.main()
