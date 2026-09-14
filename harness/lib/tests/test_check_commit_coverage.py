#!/usr/bin/env python3
"""check_commit_coverage 单测：自最近 promoted baseline 起非 meta 提交须被
某份收据 commit_scope 覆盖（manual 与 CDP 同等；判红打印补齐命令）。

CDP-DOD-001 三要件（批次 133b55812a81 方向 3）：
  1. 调用方 = selfcheck _spawn_tools 以 commit_coverage_rc 透出、CI/ws_report 判红
  2. 破坏即判红用例（本文件 test_uncovered_commit_red / test_uncovered_reports_fix_cmd）
  3. rc 接入 ws_report REQUIRED_RC_KEYS 判红链（test_workflow_ci 覆盖断言）
"""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import check_commit_coverage as ccc  # noqa: E402


def _mk_git(repo: Path, *, commits: list[tuple[str, str]]):
    """构造真 git 仓：commit = (标题, 改动文件)。按序提交，返回 sha 列表。

    首提交作为"最近 promoted baseline" source_commit（测试起点），其后提交
    模拟 baseline 后的非 meta/meta 提交。
    """
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "t"],
                   check=True)
    shas = []
    for title, fname in commits:
        p = repo / fname
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "--", fname],
                       check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m", title],
                       check=True)
        shas.append(subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--short=12", "HEAD"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", check=True).stdout.strip())
    return shas


def _mk_baseline(repo: Path, base_sha: str):
    cfg = repo / "harness" / "config"
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / "baseline-status.yaml").write_text(
        f"baselines:\n- baseline_id: BL-A\n  status: promoted\n"
        f"  source_commit: {base_sha}\n", encoding="utf-8")


def _mk_receipt(repo: Path, scope: str):
    d = repo / "data" / "verify-results"
    d.mkdir(parents=True, exist_ok=True)
    (d / "20260912-000000-manual-test.md").write_text(
        f"- schema_version: 1\n- batch_id: manual-test\n"
        f"- result: skip\n- commit_scope: {scope}\n\n## body\nx\n",
        encoding="utf-8")


class TestUncoveredScan(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_uncovered_commit_red(self):
        # 红灯：baseline 后的非 meta 提交无收据覆盖 → 判红并打印补齐命令
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 直连开发修复", "fix.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        out = ccc.uncovered_commits(self.repo)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0][:12], shas[1])
        self.assertIn("修复(harness)", out[0][1])

    def test_covered_by_receipt_ok(self):
        # 绿灯：非 meta 提交被某份收据 commit_scope 覆盖 → 不判红
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 直连开发修复", "fix.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        _mk_receipt(self.repo, "add=1 mod=0 del=0 | fix.py")
        self.assertEqual(ccc.uncovered_commits(self.repo), [])

    def test_meta_commit_exempt(self):
        # meta 提交（构建/文档）豁免，不改动文件也豁免
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("文档(docs): 纯文档更新", "doc.md"),
            ("构建(baseline): 再次发布", "base2.txt"),
        ])
        _mk_baseline(self.repo, shas[0])
        self.assertEqual(ccc.uncovered_commits(self.repo), [])

    def test_verify_dir_self_exempt(self):
        # 收据目录自引用豁免：提交只改 data/verify-results/ 不算改动面
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("杂项(known-issues): 登记", "data/verify-results/x.md"),
        ])
        _mk_baseline(self.repo, shas[0])
        self.assertEqual(ccc.uncovered_commits(self.repo), [])

    def test_directory_scope_prefix_match(self):
        # 目录项（结尾 /）按前缀匹配其下文件
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 目录面改动", "harness/lib/a.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        _mk_receipt(self.repo, "add=1 mod=0 del=0 | harness/lib/")
        self.assertEqual(ccc.uncovered_commits(self.repo), [])

    def test_no_baseline_skips(self):
        # 无 promoted baseline → 无扫描对象，不判红
        _mk_git(self.repo, commits=[
            ("修复(harness): 无基线改动", "fix.py"),
        ])
        self.assertEqual(ccc.uncovered_commits(self.repo), [])

    def test_rev_list_failure_red(self):
        # git rev-list 失败 fail-closed：无法证实覆盖即判红，不静默放行
        _mk_baseline(self.repo, "aabbccddeeff")
        with mock.patch.object(ccc, "_git", return_value=None):
            out = ccc.uncovered_commits(self.repo)
        self.assertEqual(len(out), 1)
        self.assertIn("rev-list", out[0][0])

    def test_uncovered_reports_fix_cmd(self):
        # 判红时打印补齐命令（ws_report --manual）
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 直连开发修复", "fix.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        cmd = ccc.fix_cmd(self.repo)
        self.assertIn("ws_report.py --manual", cmd)
        self.assertIn(shas[0], cmd)
        self.assertIn("--result skip", cmd)


class TestMain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_main_rc_1_on_uncovered(self):
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 直连开发修复", "fix.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        self.assertEqual(ccc.main(["--repo", str(self.repo)]), 1)

    def test_main_rc_0_when_covered(self):
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 直连开发修复", "fix.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        _mk_receipt(self.repo, "add=1 mod=0 del=0 | fix.py")
        self.assertEqual(ccc.main(["--repo", str(self.repo)]), 0)

    def test_main_rc_0_non_git_repo(self):
        self.assertEqual(ccc.main(["--repo", str(self.repo)]), 0)


if __name__ == "__main__":
    unittest.main()
