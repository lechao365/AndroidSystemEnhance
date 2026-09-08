"""metrics.py 自度量工具测试：聚合收据/趋势/known-issues，缺数据容错。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1].parent / "skills"
                         / "cross-device" / "lib" / "python"))

import metrics as mt  # noqa: E402


def _receipt(verify_dir, batch_id, result, elapsed):
    from cdp_receipt import Receipt
    r = Receipt(batch_id=batch_id, result=result, elapsed_s=elapsed,
                verify_mode="board")
    content = r.header_lines() + "\n\n## body\n\nbody\n"
    (verify_dir / f"20260908-{batch_id}.md").write_text(content,
                                                        encoding="utf-8")


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.verify = self.root / "verify-results"
        self.verify.mkdir()
        self.issues = self.root / "known-issues"
        self.issues.mkdir()
        self.addCleanup(self._tmp.cleanup)

    def test_empty_dir_returns_zero_stats(self):
        stats = mt.compute([], [], [])
        self.assertEqual(stats["total"], 0)
        self.assertEqual(stats["pass_rate"], 0.0)

    def test_counts_and_rates(self):
        for i, res in enumerate(["pass", "pass", "fail", "skip"]):
            _receipt(self.verify, f"b{i}", res, 30 + i)
        files = sorted(self.verify.glob("*.md"))
        stats = mt.compute(mt.load_receipts(self.verify),
                           mt.load_trend(self.verify), [])
        self.assertEqual(stats["total"], 4)
        self.assertEqual(stats["pass"], 2)
        self.assertEqual(stats["fail"], 1)
        self.assertEqual(stats["skip"], 1)
        self.assertAlmostEqual(stats["pass_rate"], 0.5)

    def test_elapsed_stats(self):
        for i, res in enumerate(["pass", "pass"]):
            _receipt(self.verify, f"b{i}", res, 40 + i * 40)
        stats = mt.compute(mt.load_receipts(self.verify), [], [])
        self.assertEqual(stats["avg_elapsed_s"], 60)
        self.assertEqual(stats["p90_elapsed_s"], 80)

    def test_known_issues_board(self):
        (self.issues / "KI-1.md").write_text(
            "- kind: flake\n- status: open\n", encoding="utf-8")
        (self.issues / "KI-2.md").write_text(
            "- kind: idle-eligible\n- status: fixed\n", encoding="utf-8")
        board = mt.ki_board(self.issues)
        self.assertEqual(board["flake"]["open"], 1)
        self.assertEqual(board["idle-eligible"]["fixed"], 1)

    def test_render_and_json(self):
        stats = mt.compute([], [], [])
        self.assertIsInstance(mt.render_stats(stats), str)
        parsed = json.loads(mt.render_stats(stats, as_json=True))
        self.assertIn("total", parsed)


if __name__ == "__main__":
    unittest.main()
