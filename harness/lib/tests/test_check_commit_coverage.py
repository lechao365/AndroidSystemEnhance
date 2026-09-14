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
    f = d / "20260912-000000-manual-test.md"
    f.write_text(f"- schema_version: 1\n- batch_id: manual-test\n"
                 f"- result: skip\n- commit_scope: {scope}\n\n## body\nx\n",
                 encoding="utf-8")
    # 证据只认 HEAD 中已提交的收据（批次 b410b688d206 方向 2）：git add 不
    # commit 即授予覆盖是漏洞——收据须 commit 到 HEAD 才算覆盖证据
    subprocess.run(["git", "-C", str(repo), "add", "--", f.as_posix()],
                   check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-m",
                    "构建(baseline): 收据登记"], check=True)


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
        # meta 提交（构建/文档）豁免：构建(baseline) 提交改动面限于
        # baseline-status.yaml（发布/晋升本职）、文档(x) 提交改文档文件
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("文档(docs): 纯文档更新", "doc.md"),
        ])
        _mk_baseline(self.repo, shas[0])
        cfg = self.repo / "harness" / "config" / "baseline-status.yaml"
        cfg.write_text(
            "baselines:\n- baseline_id: BL-B\n  status: promoted\n"
            f"  source_commit: {shas[1]}\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "--",
                        cfg.as_posix()], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m",
                        "构建(baseline): 再次发布"], check=True)
        self.assertEqual(ccc.uncovered_commits(self.repo), [])

    def test_build_meta_smuggles_code_red(self):
        # 方向 1（批次 b410b688d206）：构建( 无条件豁免收窄——构建(baseline)
        # 标题夹带代码文件（非 baseline-status.yaml/收据目录改动面）→ 不豁免判红
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("构建(baseline): 夹带 harness 代码", "harness/lib/checker.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        out = ccc.uncovered_commits(self.repo)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0][:12], shas[1])

    def test_build_meta_evidence_dirs_exempt(self):
        # 方向 1（批次 e3f5f80d7b22）：晋升提交本职 add baseline-status.yaml 与
        # data/baselines/、data/known-issues/ 证据目录（publish_main_base.sh
        # 541）——改动面限于这三处仍豁免；仅 baseline-status.yaml 曾致 files
        # 子集判定恒假、promote 每次必红
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
        ])
        _mk_baseline(self.repo, shas[0])
        # 晋升提交：baseline-status.yaml（保留基线内容）+ 两个证据目录
        for rel in ("data/baselines/BL-X.md", "data/known-issues/ki-001.md"):
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("x\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m",
                        "构建(baseline): 晋升 promoted"], check=True)
        self.assertEqual(ccc.uncovered_commits(self.repo), [])

    def test_docs_prefix_code_not_doc_red(self):
        # 方向 1：docs/ 前缀不再不看扩展名——docs/ 下代码文件按非文档判红
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("文档(docs): 顺手改代码", "docs/tool.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        out = ccc.uncovered_commits(self.repo)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0][:12], shas[1])

    def test_harness_rules_md_not_doc_red(self):
        # 方向 1：harness/rules/ 下被程序读取的判据 md 不算文档——文档(x)
        # 标题夹带改判据判红
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("文档(rules): 顺手改判据", "harness/rules/known-issues.md"),
        ])
        _mk_baseline(self.repo, shas[0])
        out = ccc.uncovered_commits(self.repo)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0][:12], shas[1])

    def test_known_issues_md_not_doc_red(self):
        # 方向 1：data/known-issues/ 下被程序读取的 known-issue md 不算文档
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("文档(ki): 顺手关 known-issue", "data/known-issues/ki-001.md"),
        ])
        _mk_baseline(self.repo, shas[0])
        out = ccc.uncovered_commits(self.repo)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0][:12], shas[1])

    def test_untracked_handwritten_receipt_red(self):
        # fail-open 修复：收据 glob 扫未跟踪 md——手写一份未跟踪收据即免检
        # 是漏洞。未跟踪收据不作覆盖证据，判红
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 修复", "fix.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        d = self.repo / "data" / "verify-results"
        d.mkdir(parents=True, exist_ok=True)
        (d / "20260912-000000-handwritten.md").write_text(
            "- schema_version: 1\n- batch_id: hw\n- result: skip\n"
            "- commit_scope: add=1 mod=0 del=0 | fix.py\n", encoding="utf-8")
        out = ccc.uncovered_commits(self.repo)
        self.assertEqual(len(out), 2)  # 未跟踪收据错误 + fix.py 未被有效覆盖
        self.assertIn("<receipt-invalid>", out[0][0])
        self.assertIn("未跟踪", out[0][1])

    def test_fail_receipt_not_evidence_red(self):
        # fail-open 修复：不滤 result=fail 收据——失败收据不能证明覆盖，
        # 跳过并判红（此前静默当作证据=免检）
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 修复", "fix.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        d = self.repo / "data" / "verify-results"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "20260912-000000-fail.md"
        f.write_text("- schema_version: 1\n- batch_id: fail\n- result: fail\n"
                     "- commit_scope: add=1 mod=0 del=0 | fix.py\n",
                     encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "--", f.as_posix()],
                       check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m",
                        "构建(baseline): 收据登记"], check=True)
        out = ccc.uncovered_commits(self.repo)
        # fail 收据跳过不作覆盖证据（不判红收据本身），fix.py 因此无覆盖判红
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0][:12], shas[1])

    def test_uppercase_fail_result_receipt_red(self):
        # 方向 2（批次 b410b688d206）：result 白名单仅 pass/skip——result=FAIL
        # （大写）不作覆盖证据，判红（此前只精确匹配小写 fail 才跳过）
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 修复", "fix.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        d = self.repo / "data" / "verify-results"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "20260912-000000-uppercase.md"
        f.write_text("- schema_version: 1\n- batch_id: up\n- result: FAIL\n"
                     "- commit_scope: add=1 mod=0 del=0 | fix.py\n",
                     encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "--", f.as_posix()],
                       check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m",
                        "构建(baseline): 收据登记"], check=True)
        out = ccc.uncovered_commits(self.repo)
        # FAIL 收据判红（result 非法）+ fix.py 无有效覆盖
        self.assertEqual(len(out), 2)
        self.assertIn("<receipt-invalid>", out[0][0])
        self.assertIn("result 非法", out[0][1])

    def test_workspace_modified_receipt_not_evidence(self):
        # 方向 2：收据内容从 git show HEAD 读——工作区未提交修改不改判定
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 修复", "fix.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        d = self.repo / "data" / "verify-results"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "20260912-000000-scope.md"
        f.write_text("- schema_version: 1\n- batch_id: ok\n- result: skip\n"
                     "- commit_scope: add=1 mod=0 del=0 | fix.py\n",
                     encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "--", f.as_posix()],
                       check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m",
                        "构建(baseline): 收据登记"], check=True)
        self.assertEqual(ccc.uncovered_commits(self.repo), [])
        # 工作区把 scope 改成空/伪造 → HEAD 不变仍按 HEAD 判定，覆盖不失效
        f.write_text("- schema_version: 1\n- batch_id: ok\n- result: skip\n"
                     "- commit_scope: add=0 mod=0 del=0 |\n", encoding="utf-8")
        self.assertEqual(ccc.uncovered_commits(self.repo), [])

    def test_broken_receipt_parse_red(self):
        # fail-open 修复：收据解析失败丢错误——坏收据静默丢弃等于免检。
        # 解析失败判红
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 修复", "fix.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        d = self.repo / "data" / "verify-results"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "20260912-000000-broken.md"
        f.write_text("- schema_version: 999\n- batch_id: broken\n"
                     "- result: skip\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.repo), "add", "--", f.as_posix()],
                       check=True)
        subprocess.run(["git", "-C", str(self.repo), "commit", "-q", "-m",
                        "构建(baseline): 收据登记"], check=True)
        out = ccc.uncovered_commits(self.repo)
        self.assertEqual(len(out), 2)  # 坏收据错误 + fix.py 未被有效覆盖
        self.assertIn("<receipt-invalid>", out[0][0])
        self.assertIn("解析失败", out[0][1])

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

    def test_meta_title_smuggles_code_red(self):
        # fail-open 修复（批次 e503284f97b9 方向 2）：meta 豁免只认标题——
        # 写 文档(x): 标题即可挟带代码改动逃过收据覆盖。标题 meta 但改动
        # 含非文档文件（.py）→ 不豁免，判红
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("文档(docs): 纯文档更新", "doc.md"),
            ("文档(docs): 顺手改代码", "code.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        out = ccc.uncovered_commits(self.repo)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0][:12], shas[2])

    def test_meta_doc_commit_exempt(self):
        # 标题 meta 且改动全为文档类 → 豁免
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("文档(docs): 纯文档更新", "doc.md"),
        ])
        _mk_baseline(self.repo, shas[0])
        self.assertEqual(ccc.uncovered_commits(self.repo), [])

    def test_no_baseline_skips(self):
        # fail-open 修复（批次 e503284f97b9 方向 2）：无 promoted baseline
        # 此前全放行且打印 OK——无法界定覆盖起点即无法证实覆盖，改为判红
        # （fail-closed）；此前该用例把 fail-open 写成期望须改
        _mk_git(self.repo, commits=[
            ("修复(harness): 无基线改动", "fix.py"),
        ])
        out = ccc.uncovered_commits(self.repo)
        self.assertEqual(len(out), 1)
        self.assertIn("<no-baseline>", out[0][0])

    def test_bad_baseline_yaml_red(self):
        # 坏 baseline-status.yaml（非法 yaml）判红：无法界定覆盖起点
        cfg = self.repo / "harness" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "baseline-status.yaml").write_text("baselines: [unclosed\n",
                                                  encoding="utf-8")
        _mk_git(self.repo, commits=[
            ("修复(harness): 修复", "fix.py"),
        ])
        out = ccc.uncovered_commits(self.repo)
        self.assertEqual(len(out), 1)
        self.assertIn("<no-baseline>", out[0][0])

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

    def test_range_uncovered_fully_covered(self):
        # 方向 3（批次 b410b688d206）：区间内每个未覆盖 sha 均被 scope 覆盖
        # → 豁免可证（True）
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 修复a", "fixa.py"),
            ("修复(harness): 修复b", "fixb.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        self.assertTrue(ccc.range_uncovered_fully_covered(
            shas[0], shas[2], "add=2 mod=0 del=0 | fixa.py, fixb.py",
            self.repo))

    def test_range_partial_scope_red(self):
        # 方向 3：只补一半（scope 只覆盖区间内部分未覆盖提交）→ 不豁免。
        # 存在性检查在此场景仍返回"有缺口"放行，逐 sha 判定须拦下
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 修复a", "fixa.py"),
            ("修复(harness): 修复b", "fixb.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        self.assertFalse(ccc.range_uncovered_fully_covered(
            shas[0], shas[2], "add=1 mod=0 del=0 | fixa.py", self.repo))

    def test_range_no_gap_returns_false(self):
        # 方向 3：区间内无未覆盖提交（全 meta）→ 不豁免（rc=1 属静音/伪造）
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
        ])
        _mk_baseline(self.repo, shas[0])
        self.assertFalse(ccc.range_uncovered_fully_covered(
            shas[0], shas[0], "add=0 mod=0 del=0 |", self.repo))

    def test_range_bad_scope_returns_false(self):
        # 方向 3：scope 非法 → 不豁免（fail-closed）
        shas = _mk_git(self.repo, commits=[
            ("构建(baseline): 基线发布", "base.txt"),
            ("修复(harness): 修复a", "fixa.py"),
        ])
        _mk_baseline(self.repo, shas[0])
        self.assertFalse(ccc.range_uncovered_fully_covered(
            shas[0], shas[1], "非法scope", self.repo))


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
