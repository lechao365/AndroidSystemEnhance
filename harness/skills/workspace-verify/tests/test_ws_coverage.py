"""ws_coverage.py 覆盖率采集测试：产物判定 + 降级语义 + 自描述 JSON。"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ws_coverage as wc  # noqa: E402


class TestWsCoverage(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.out = Path(self._tmp.name) / "out"
        (self.out / "target" / "product" / "rpi5" / "data" / "nativetest64"
         / "lechao_lcview_unit_test").mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def test_no_coverage_artifacts_degrades(self):
        # 无 gcda/gcno/profraw 产物 → 如实标注 unavailable（不门禁不假绿）
        data = wc.collect(self.out, product="rpi5")
        self.assertEqual(data["status"], "unavailable")

    def test_gcda_present_sets_targets(self):
        gcda = (self.out / "target" / "product" / "rpi5" / "data"
                / "nativetest64" / "lechao_lcview_unit_test" / "c.gcda")
        gcda.write_bytes(b"gcda")
        with mock.patch.object(wc, "_run_lcov") as m:
            m.return_value = (0, "lines: 50.0% of 100")
            data = wc.collect(self.out, product="rpi5")
        self.assertEqual(data["status"], "ok")
        self.assertIn("lechao_lcview_unit_test", data["targets"])

    def test_lcov_failure_degrades(self):
        gcda = (self.out / "target" / "product" / "rpi5" / "data"
                / "nativetest64" / "lechao_lcview_unit_test" / "c.gcda")
        gcda.write_bytes(b"gcda")
        with mock.patch.object(wc, "_run_lcov") as m:
            m.return_value = (1, "lcov error")
            data = wc.collect(self.out, product="rpi5")
        self.assertEqual(data["status"], "partial")


if __name__ == "__main__":
    unittest.main()
