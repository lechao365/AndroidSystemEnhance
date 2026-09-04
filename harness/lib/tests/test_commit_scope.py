"""批次 261f10265269 方向 1/2：commit_scope 提交面清单与比对。"""

import io
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "skills"
                       / "cross-device" / "lib" / "python"))
import commit_scope  # noqa: E402


class TestFormatParse(unittest.TestCase):
    def test_format_scope_counts_and_paths(self):
        lines = ["A\tf1.txt", "M\tf2.txt", "D\tf3.txt", "T\tf4.txt"]
        s = commit_scope.format_scope(lines)
        self.assertEqual(s, "add=1 mod=2 del=1 | f1.txt, f2.txt, f4.txt, f3.txt")
        counts, paths = commit_scope.parse_scope(s)
        self.assertEqual(counts, {"add": 1, "mod": 2, "del": 1})
        self.assertEqual(paths, {"f1.txt", "f2.txt", "f4.txt", "f3.txt"})

    def test_format_scope_excludes_receipt_dir(self):
        # 自引用豁免：data/verify-results/（收据+trend）两侧同排除
        lines = ["A\tdata/verify-results/20260101-000000-x.md",
                 "A\tdata/verify-results/trend.md",
                 "M\tf.txt"]
        s = commit_scope.format_scope(lines)
        self.assertEqual(s, "add=0 mod=1 del=0 | f.txt")

    def test_parse_scope_invalid_returns_none(self):
        counts, paths = commit_scope.parse_scope("add=1 mod=0 del=0")
        self.assertIsNone(counts)
        self.assertIsNone(paths)


class TestCompare(unittest.TestCase):
    def test_identical_returns_empty(self):
        scope = commit_scope.format_scope(["A\tf1.txt", "M\tf2.txt"])
        self.assertEqual(commit_scope.compare(scope, ["A\tf1.txt", "M\tf2.txt"]), [])

    def test_mismatch_lists_both_directions(self):
        scope = commit_scope.format_scope(["A\tf1.txt", "M\tf2.txt"])
        diffs = commit_scope.compare(scope, ["A\tf1.txt", "M\tf9.txt"])
        self.assertIn("收据声明但不在提交面: f2.txt", diffs)
        self.assertIn("提交面存在但收据未声明: f9.txt", diffs)

    def test_invalid_scope_treated_as_mismatch(self):
        # scope 格式非法 → 无法证明绑定，按不一致拒
        diffs = commit_scope.compare("不是scope", ["A\tf1.txt"])
        self.assertTrue(diffs)
        self.assertIn("格式非法", diffs[0])

    def test_porcelain_to_name_status(self):
        conv = commit_scope.porcelain_to_name_status
        self.assertEqual(conv("?? new.txt"), "A\tnew.txt")
        self.assertEqual(conv(" M mod.txt"), "M\tmod.txt")
        self.assertEqual(conv("D  del.txt"), "D\tdel.txt")
        # 重命名取终态路径归 mod
        self.assertEqual(conv('R  old.txt -> new.txt'), "M\tnew.txt")


class TestLatestScope(unittest.TestCase):
    def test_latest_scope_reads_newest_receipt(self):
        # 最新 = 文件名升序最后一；trend.md 不参与；旧收据缺字段不遮蔽
        import cdp_receipt
        with mock.patch.object(
                cdp_receipt, "data_verify_results_dir",
                return_value=Path(self.enterContext(
                    __import__("tempfile").TemporaryDirectory()))):
            d = cdp_receipt.data_verify_results_dir()
            (d / "20260101-000000-111111111111.md").write_text(
                "- schema_version: 1\n- batch_id: 111111111111\n"
                "- commit_scope: add=1 mod=0 del=0 | old.txt\n\n## body\n",
                encoding="utf-8")
            (d / "20260102-000000-222222222222.md").write_text(
                "- schema_version: 1\n- batch_id: 222222222222\n"
                "- commit_scope: add=0 mod=2 del=0 | a.txt, b.txt\n\n## body\n",
                encoding="utf-8")
            (d / "trend.md").write_text("trend\n", encoding="utf-8")
            self.assertEqual(commit_scope.latest_scope(d),
                             "add=0 mod=2 del=0 | a.txt, b.txt")

    def test_latest_scope_empty_when_no_receipts(self):
        import cdp_receipt
        import tempfile
        with mock.patch.object(
                cdp_receipt, "data_verify_results_dir",
                return_value=Path(tempfile.mkdtemp())):
            self.assertEqual(commit_scope.latest_scope(), "")


class TestCli(unittest.TestCase):
    def test_check_cli_flow(self):
        # 实际面与 scope 一致 → 0；不一致 → 1 且 stdout 列差异
        scope = "add=1 mod=1 del=0 | f1.txt, f2.txt"
        with mock.patch.object(commit_scope.sys, "stdin",
                               io.StringIO("A\tf1.txt\nM\tf2.txt\n")):
            self.assertEqual(commit_scope.main(["--check", scope]), 0)
        with mock.patch.object(commit_scope.sys, "stdin",
                               io.StringIO("A\tf1.txt\nM\tf9.txt\n")) as _:
            out = io.StringIO()
            with redirect_stdout(out):
                rc = commit_scope.main(["--check", scope])
            self.assertEqual(rc, 1)
            self.assertIn("f9.txt", out.getvalue())

    def test_usage_on_no_args(self):
        err = io.StringIO()
        with redirect_stderr(err):
            rc = commit_scope.main([])
        self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()
