import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_paths
import cdp_receipt


def _mk_receipt(batch_id="abc123def456", result="pass"):
    return cdp_receipt.Receipt(
        schema_version=1,
        batch_id=batch_id,
        batch_base="111111111111",
        verified_commit="222222222222",
        verify_mode="board",
        result=result,
        build="pass",
        push_board="pass",
        acceptance="svc:lechao_lcview pass",
        elapsed_s=120,
        summary="验证通过",
    )


class TestReceipt(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self._dir = cdp_paths.data_verify_dir()

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    def test_write_and_read_roundtrip(self):
        r = _mk_receipt()
        p = cdp_receipt.write_receipt(r, "正文: CDP 原文 + 失败现场")
        self.assertTrue(p.name.endswith("-abc123def456.md"))
        got = cdp_receipt.read_receipt(p)
        self.assertEqual(got.batch_id, "abc123def456")
        self.assertEqual(got.result, "pass")
        self.assertEqual(got.verified_commit, "222222222222")

    def test_body_fields_do_not_bleed(self):
        # 正文含 "- result: fail" 行不得覆盖头部字段（只解析 ## body 之前）
        r = _mk_receipt(result="pass")
        p = cdp_receipt.write_receipt(r, "## 现场\n- result: fail\n- batch_id: fake000000000")
        got = cdp_receipt.read_receipt(p)
        self.assertEqual(got.result, "pass")
        self.assertEqual(got.batch_id, "abc123def456")

    def test_latest_returns_most_recent(self):
        cdp_receipt.write_receipt(_mk_receipt("aaa111111111", "fail"), "x")
        cdp_receipt.write_receipt(_mk_receipt("bbb222222222", "pass"), "y")
        latest = cdp_receipt.read_latest_receipt(self._dir)
        self.assertEqual(latest.batch_id, "bbb222222222")

    def test_latest_ignores_trend_md(self):
        # trend.md 按文件名排序恒在时间戳文件之后，必须被排除
        cdp_receipt.write_receipt(_mk_receipt("ccc333333333", "pass"), "z")
        (self._dir / "trend.md").write_text(
            "2026-08-23 10:00:00 ccc333333333 pass build=pass x\n", encoding="utf-8")
        latest = cdp_receipt.read_latest_receipt(self._dir)
        self.assertEqual(latest.batch_id, "ccc333333333")
        self.assertEqual(latest.result, "pass")

    def test_latest_receipt_with_path(self):
        cdp_receipt.write_receipt(_mk_receipt("aaa111111111", "fail"), "x")
        cdp_receipt.write_receipt(_mk_receipt("bbb222222222", "pass"), "y")
        (self._dir / "trend.md").write_text(
            "2026-08-23 10:00:00 bbb222222222 pass build=pass x\n", encoding="utf-8")
        path, r = cdp_receipt.latest_receipt_with_path(self._dir)
        self.assertTrue(path.name.endswith("-bbb222222222.md"))
        self.assertEqual(r.result, "pass")
        self.assertEqual(r.verified_commit, "222222222222")

    def test_latest_receipt_with_path_empty(self):
        path, r = cdp_receipt.latest_receipt_with_path(self._dir)
        self.assertIsNone(path)
        self.assertIsNone(r)

    def test_read_latest_delegates_to_latest_with_path(self):
        # read_latest_receipt 委托 latest_receipt_with_path（去重），结果必须一致
        cdp_receipt.write_receipt(_mk_receipt("aaa111111111", "fail"), "x")
        cdp_receipt.write_receipt(_mk_receipt("bbb222222222", "pass"), "y")
        latest = cdp_receipt.read_latest_receipt(self._dir)
        _, with_path = cdp_receipt.latest_receipt_with_path(self._dir)
        self.assertEqual(latest.batch_id, with_path.batch_id)
        self.assertEqual(latest.result, with_path.result)

    def test_read_latest_empty_returns_none(self):
        # 无收据时 read_latest_receipt 返回 None（委托路径不崩）
        self.assertIsNone(cdp_receipt.read_latest_receipt(self._dir))

    def test_trend_append_and_read(self):
        cdp_receipt.append_trend("2026-08-23 10:00:00", "abc123def456", "pass",
                                 "build=pass board=pass acc=pass", "验证通过")
        line = cdp_receipt.read_trend_last(self._dir)
        self.assertIn("abc123def456", line)
        self.assertIn("pass", line)

    def test_prune_keeps_50_details_and_keeps_trend(self):
        for i in range(55):
            cdp_receipt.write_receipt(_mk_receipt(f"batch{i:012d}"), f"body{i}")
        cdp_receipt.append_trend("2026-08-23 10:00:00", "batch54000000000",
                                 "pass", "build=pass x", "y")
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 50)
        self.assertTrue((self._dir / "trend.md").exists())

    def test_invalid_int_field_falls_back(self):
        # 头部数值字段非法 → 回落默认值，不崩
        r = cdp_receipt.Receipt.from_text(
            "- schema_version: abc\n- elapsed_s: 12x\n## body\n\nx\n")
        self.assertEqual(r.schema_version, 1)
        self.assertEqual(r.elapsed_s, 0)


if __name__ == "__main__":
    unittest.main()