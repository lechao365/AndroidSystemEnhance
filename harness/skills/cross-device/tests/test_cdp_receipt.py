import contextlib
import io
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
        self._dir = cdp_paths.data_verify_results_dir()

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

    def test_prune_keeps_quota_details_and_keeps_trend(self):
        keep = cdp_receipt._DETAIL_KEEP
        for i in range(keep + 5):
            cdp_receipt.write_receipt(_mk_receipt(f"batch{i:012d}"), f"body{i}")
        cdp_receipt.append_trend("2026-08-23 10:00:00", "batch24000000000",
                                 "pass", "build=pass x", "y")
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), keep)
        self.assertTrue((self._dir / "trend.md").exists())

    def _write_baseline_status(self, refs):
        """在 CDP_PROJECT_ROOT 下写 baseline-status.yaml（引用指定收据名列表）。"""
        cfg = Path(self._tmp.name) / "harness" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        lines = ["baselines:"]
        for i, name in enumerate(refs):
            lines.append(f"- baseline_id: BL-TEST-{i:02d}")
            lines.append("  status: promoted")
            lines.append(f"  sync_manifest: data/verify-results/{name}")
        (cfg / "baseline-status.yaml").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")

    def test_prune_keeps_referred_receipt(self):
        # 被 baseline-status.yaml 引用的收据不得被老化删除（证据链保护）：
        # 先写满配额，再写 4 份触发老化，被引用最旧份须保留
        keep = cdp_receipt._DETAIL_KEEP
        for i in range(keep):
            cdp_receipt.write_receipt(_mk_receipt(f"batch{i:012d}"), f"body{i}")
        files = sorted(f.name for f in self._dir.glob("*.md")
                       if f.name != "trend.md")
        self.assertEqual(len(files), keep)
        referred = files[0]  # 最旧一份将被新写入挤出配额
        self._write_baseline_status([referred])
        for i in range(keep, keep + 4):
            cdp_receipt.write_receipt(_mk_receipt(f"batch{i:012d}"), f"body{i}")
        details = [f.name for f in self._dir.glob("*.md")
                   if f.name != "trend.md"]
        self.assertIn(referred, details,
                      "被引用收据必须保留（证据链保护）")
        self.assertEqual(len(details), keep + 1)  # 配额 + 1 受保护

    def test_prune_without_yaml_ages_normally(self):
        # 无 baseline-status.yaml：无引用，正常老化到配额
        keep = cdp_receipt._DETAIL_KEEP
        for i in range(keep + 5):
            cdp_receipt.write_receipt(_mk_receipt(f"batch{i:012d}"), f"body{i}")
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), keep)

    def test_prune_yaml_unreadable_conservative(self):
        # baseline-status.yaml 非法（无法解析引用）→ 保守不删任何文件
        keep = cdp_receipt._DETAIL_KEEP
        cfg = Path(self._tmp.name) / "harness" / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "baseline-status.yaml").write_text(
            "baselines: [unclosed\n", encoding="utf-8")
        for i in range(keep + 1):
            cdp_receipt.write_receipt(_mk_receipt(f"batch{i:012d}"), f"body{i}")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            cdp_receipt.prune_details(self._dir)
        self.assertIn("跳过老化保护判定", err.getvalue())
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), keep + 1, "yaml 不可读时须保守保留全部")

    def test_prune_broken_reference_ignored(self):
        # 断链引用（sync_manifest 指向已丢失收据）不影响老化：目标文件已
        # 不存在，仅保护仍存在的引用收据
        keep = cdp_receipt._DETAIL_KEEP
        for i in range(keep + 5):
            cdp_receipt.write_receipt(_mk_receipt(f"batch{i:012d}"), f"body{i}")
        self._write_baseline_status(["lost-20260828-lost00000000.md"])
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), keep, "断链引用不产生保护对象，正常老化")

    def test_same_second_same_batch_id_no_overwrite(self):
        """同秒同 batch_id 写入两份：不覆盖，latest 取最新写入（失败现场不丢）。"""
        r1 = _mk_receipt(result="fail")
        p1 = cdp_receipt.write_receipt(r1, "第一次失败现场")
        r2 = _mk_receipt(result="pass")
        p2 = cdp_receipt.write_receipt(r2, "第二次通过")
        self.assertNotEqual(p1, p2, "同秒同批应防覆盖，文件名须唯一")
        self.assertTrue(p1.exists(), "第一次收据不应被覆盖")
        self.assertTrue(p2.exists())
        self.assertEqual(cdp_receipt.read_receipt(p1).result, "fail")
        latest = cdp_receipt.read_latest_receipt(self._dir)
        self.assertEqual(latest.batch_id, "abc123def456")
        self.assertEqual(latest.result, "pass", "latest 应取最新写入的收据")

    def test_append_trend_truncates_to_keep(self):
        """trend 超过 _TREND_KEEP 行时截断保留最新（原子写语义不变）。"""
        keep = cdp_receipt._TREND_KEEP
        for i in range(keep + 5):
            cdp_receipt.append_trend(
                f"2026-08-23 10:0{i % 60}:00", f"batch{i:012d}", "pass",
                "build=pass x", f"summary{i}")
        lines = (self._dir / "trend.md").read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), keep)
        self.assertIn(f"batch{keep + 4:012d}", lines[-1], "应保留最新一行")

    def test_invalid_int_field_falls_back(self):
        # 头部数值字段非法 → 回落默认值，不崩
        r = cdp_receipt.Receipt.from_text(
            "- schema_version: abc\n- elapsed_s: 12x\n## body\n\nx\n")
        self.assertEqual(r.schema_version, 1)
        self.assertEqual(r.elapsed_s, 0)

    def test_timings_roundtrip(self):
        # timings 字段（链路耗时打点 JSON 字符串）写读往返
        r = _mk_receipt()
        r.timings = '{"batch_id": "abc123def456", "segments": [{"name": "edit", "elapsed_s": 12.5}]}'
        p = cdp_receipt.write_receipt(r, "正文")
        got = cdp_receipt.read_receipt(p)
        self.assertIn('"name": "edit"', got.timings)
        self.assertIn('"elapsed_s": 12.5', got.timings)

    def test_old_receipt_without_timings_falls_back(self):
        # 旧收据无 timings 字段 → from_text 回落空串，不崩
        r = cdp_receipt.Receipt.from_text(
            "- schema_version: 1\n- batch_id: abc123def456\n## body\n\nx\n")
        self.assertEqual(r.timings, "")

    def test_cases_roundtrip(self):
        # cases 字段（本次实际验收用例标签，逗号分隔）写读往返
        r = _mk_receipt()
        r.cases = "lcview-liveness,lcview-transfer,lcview-pipeline,lcview-perf"
        p = cdp_receipt.write_receipt(r, "正文")
        got = cdp_receipt.read_receipt(p)
        self.assertEqual(got.cases, r.cases)

    def test_selfcheck_roundtrip(self):
        # selfcheck 字段（-s 批次自检摘要）写读往返
        r = _mk_receipt()
        r.selfcheck = "121 passed, 3 skipped in 6.0s\nOK: 引用完整"
        p = cdp_receipt.write_receipt(r, "正文")
        got = cdp_receipt.read_receipt(p)
        # 多行字段 header 单行解析（与 acceptance 同语义）：回落首行
        self.assertEqual(got.selfcheck, r.selfcheck.splitlines()[0])

    def test_old_receipt_without_selfcheck_falls_back(self):
        # 老收据无 selfcheck 字段 → 回落空串（不崩）
        r = _mk_receipt()
        p = cdp_receipt.write_receipt(r, "正文")
        text = p.read_text(encoding="utf-8").replace("- selfcheck: ", "- xselfcheck: ")
        p.write_text(text, encoding="utf-8")
        got = cdp_receipt.read_receipt(p)
        self.assertEqual(got.selfcheck, "")

    def test_old_receipt_without_cases_falls_back(self):
        # 旧收据无 cases 字段 → from_text 回落空串（证据推导无源时须显式报错）
        r = cdp_receipt.Receipt.from_text(
            "- schema_version: 1\n- batch_id: abc123def456\n## body\n\nx\n")
        self.assertEqual(r.cases, "")

    def test_latest_board_receipt_picks_board_not_latest(self):
        # 最新收据是 skip（-s 文档批）→ latest_board_receipt 须跳过，
        # 取最新 verify_mode=board 的收据（evidence-scope 推导锚点）
        r_skip = _mk_receipt("aaa111111111", "skip")
        r_skip.verify_mode = "skip"
        cdp_receipt.write_receipt(r_skip, "skip 批")
        r_board = _mk_receipt("bbb222222222", "pass")
        cdp_receipt.write_receipt(r_board, "board 批")
        r_last = _mk_receipt("ccc333333333", "pass")
        r_last.verify_mode = "manual"
        cdp_receipt.write_receipt(r_last, "manual 批")
        path, got = cdp_receipt.latest_board_receipt(self._dir)
        self.assertTrue(path.name.endswith("-bbb222222222.md"))
        self.assertEqual(got.batch_id, "bbb222222222")
        self.assertEqual(got.verify_mode, "board")

    def test_latest_board_receipt_none_when_no_board(self):
        # 全无 board 收据 → (None, None)
        r = _mk_receipt("aaa111111111", "skip")
        r.verify_mode = "skip"
        cdp_receipt.write_receipt(r, "skip 批")
        path, got = cdp_receipt.latest_board_receipt(self._dir)
        self.assertIsNone(path)
        self.assertIsNone(got)


if __name__ == "__main__":
    unittest.main()