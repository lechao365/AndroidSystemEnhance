import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ws_report

VALID_S = """-s base:1a2b3c4d5e6f
意图: 更新 README 映射表说明
验收: 无
方向: 补充新增文件条目描述
"""


class TestWsReport(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self._dir = Path(self._tmp.name) / "data" / "verify-results"

    def tearDown(self):
        self._tmp.cleanup()
        os.environ.pop("CDP_PROJECT_ROOT")

    def _write(self, content, suffix=".txt"):
        f = tempfile.NamedTemporaryFile("w", suffix=suffix, delete=False,
                                        encoding="utf-8")
        f.write(content)
        f.close()
        self.addCleanup(Path(f.name).unlink)
        return f.name

    def test_mode_a_normal(self):
        # 模式 A：--batch-file + --body → exit 0 且收据落盘、body 内容写入
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\nadb 失败\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s 说明",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        self.assertIn("receipt:", buf.getvalue())
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("batch_id: ", content)
        self.assertIn("## body", content)
        self.assertIn("adb 失败", content)

    def test_skip_without_selfcheck_rejected(self):
        # 方向 4：result=skip 而 --selfcheck 为空 → 返 2 拒写（堵零验证通道，
        # 对照 -sv 缺 --acceptance 返 2 的既有约束）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s 无自检"])
        self.assertEqual(rc, 2)
        self.assertIn("必须传 --selfcheck", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_selfcheck_failed_nonzero_rejected(self):
        # 方向 5：--selfcheck 含 failed 后跟非零数字（带红）→ 返 2 拒写，防带红落地
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 1 failed, 119 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 2)
        self.assertIn("failed 非零", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_selfcheck_missing_skipped_rejected(self):
        # 方向 6：--selfcheck 缺 skipped 计数（平台跳过数不可见）→ 返 2 拒写
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed in 5.0s"])
        self.assertEqual(rc, 2)
        self.assertIn("缺 skipped 计数", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_selfcheck_missing_rc_rejected(self):
        # 方向 4：--selfcheck 缺 pytest_rc/refs_rc（rc 为主判据，不可见则不可信）→ 返 2
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "531 passed in 27.9s | skipped=0"])
        self.assertEqual(rc, 2)
        self.assertIn("缺 pytest_rc", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_selfcheck_pytest_rc_nonzero_rejected(self):
        # 方向 5：pytest_rc 非零（崩溃/带红）→ 返 2 拒写
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=1 refs_rc=0 | "
                                 "1 failed, 119 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 2)
        self.assertIn("非零退出码", err.getvalue())
        self.assertIn("pytest_rc=1", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_selfcheck_refs_rc_nonzero_rejected(self):
        # 方向 2/5：悬空引用场景（refs_rc=1，末行 "共 N 处悬空引用"，
        # 不含 failed 也不含 skipped，文本门禁读不到）→ 靠 refs_rc 拒写
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=1 | "
                                 "531 passed in 27.9s | skipped=0 | "
                                 "==== 共 3 处悬空引用（exit 1）===="])
        self.assertEqual(rc, 2)
        self.assertIn("非零退出码", err.getvalue())
        self.assertIn("refs_rc=1", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_selfcheck_contradictory_refs_text_rejected(self):
        # 方向 4/5：rc 为 0 而文本仍含悬空引用字样（矛盾：工具已败却报 rc=0）
        # → 冗余文本防线拒写
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | "
                                 "531 passed in 27.9s | skipped=0 | "
                                 "==== 共 3 处悬空引用（exit 1）===="])
        self.assertEqual(rc, 2)
        self.assertIn("悬空引用", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_selfcheck_normal_roundtrip(self):
        # 方向 2/3/7：正常自检文本（含 skipped 计数、failed 零）写读往返
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        selfcheck = ("pytest_rc=0 refs_rc=0\n"
                     "121 passed, 3 skipped in 6.0s\n"
                     "OK: harness/skills + docs 引用完整，无悬空。")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", selfcheck])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        # selfcheck 单行化落盘（header 逐行 key-value）：多行并入一行，skipped 计数可见
        expected = " | ".join(l for l in selfcheck.splitlines() if l.strip())
        self.assertIn("- selfcheck: " + expected, content)
        from cdp_receipt import read_receipt
        got = read_receipt(details[0])
        self.assertEqual(got.selfcheck, expected)

    def test_mode_mutex_both_missing(self):
        # --batch-file 与 --target 皆缺 → exit 2，不落盘
        rc = ws_report.main(["--result", "skip"])
        self.assertEqual(rc, 2)
        self.assertFalse(self._dir.exists())

    def test_mode_a_body_missing_file(self):
        # 模式 A 下 --body 文件不存在 → exit 2，不落盘
        batch = self._write(VALID_S, ".cdp")
        rc = ws_report.main(["--batch-file", batch, "--body",
                             "/nonexistent/body.txt", "--result", "skip"])
        self.assertEqual(rc, 2)
        self.assertFalse(self._dir.exists())

    def test_mode_a_flattened_batch_returns_2(self):
        # 模式 A 下批次原文被压平（echo 类写法致多行并成一行、base 丢失）
        # → validate_batch 校验失败 exit 2，报错到 stderr，不落盘
        flat = self._write(
            "-s base:1a2b3c4d5e6f 意图: 更新 README 映射表说明 验收: 无 方向: 补充条目\n",
            ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with contextlib.redirect_stderr(err):
                rc = ws_report.main(["--batch-file", flat, "--body", body,
                                     "--result", "skip", "--build", "skip",
                                     "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 2)
        self.assertIn("error: 批次校验失败", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_mode_a_soft_error_degrades_to_warn(self):
        # apply 角色下 SOFT_ERRORS（-sv 批次验收为「无」→ 17 验收规则违规）
        # 仅 warn 不 return 2，收据仍落盘（与 cdp_parse 降级语义一致）
        soft = self._write(
            "-sv base:1a2b3c4d5e6f\n"
            "意图: 触发验收规则违规降级路径\n"
            "验收: 无\n"
            "方向: 验证 apply 角色下 17 降级为 warn 不阻断\n",
            ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with contextlib.redirect_stderr(err):
                rc = ws_report.main(["--batch-file", soft, "--body", body,
                                     "--result", "skip", "--build", "skip",
                                     "--board", "skip", "--summary", "sv 降级",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                     "--acceptance", "svc:x running",
                                     "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        self.assertIn("warn: 批次校验失败", err.getvalue())
        self.assertNotIn("error: 批次校验失败", err.getvalue())
        self.assertIn("receipt:", buf.getvalue())
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)

    def test_mode_a_sv_requires_acceptance(self):
        # 模式 A -sv 批次必须传 --acceptance，否则返 2 拒写收据（baseline 证据链防洞）
        sv = self._write(
            "-sv base:1a2b3c4d5e6f\n"
            "意图: 上板验证 lcview\n"
            "验收: svc:lechao_lcview\n"
            "方向: 检查 service 运行\n",
            ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", sv, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "sv 无证据",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 2)
        self.assertIn("必须传 --acceptance", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_mode_a_metrics_structured_saved(self):
        # 方向 2：--metrics JSON 结构化写入收据头 metrics 字段 + trend 行尾
        # （跨批可 diff，不散在正文）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 性能采集\n")
        metrics = ('{"load_mb": 64, "throughput_evs": 328.0, '
                   '"drain_ms_per_event": 6.4, "daemon_rss_kb": 5516}')
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "pass", "--build", "pass",
                                 "--board", "pass", "--summary", "性能基线",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--acceptance",
                                 '{"overall": "pass", "items": []}',
                                 "--metrics", metrics])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- metrics: {", content)
        self.assertIn('"throughput_evs": 328.0', content)
        trend = (self._dir / "trend.md").read_text(encoding="utf-8")
        self.assertIn('| {"daemon_rss_kb": 5516', trend)
        self.assertIn('"throughput_evs": 328.0', trend)

    def test_mode_a_metrics_normalized_sorted_keys(self):
        # metrics 规范化：排序键输出（同批不同序的 diff 稳定）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--metrics", '{"b": 2, "a": 1}'])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn('- metrics: {"a": 1, "b": 2}', content)

    def test_mode_a_metrics_invalid_json_rejected(self):
        # --metrics 非合法 JSON 对象 → exit 2，不落盘
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with contextlib.redirect_stderr(err):
                rc = ws_report.main(["--batch-file", batch, "--body", body,
                                     "--result", "skip", "--build", "skip",
                                     "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                     "--metrics", "{broken"])
        self.assertEqual(rc, 2)
        self.assertIn("--metrics 须为合法 JSON 对象", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_mode_a_timings_file_written(self):
        # --timings-file（cdp_timing start/mark 原始结构）→ 收据 timings 字段含
        # 段耗时（ws_report 内部经 compute_segments 计算，不等同原 marks）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        timings = json.dumps({
            "batch_id": "abc123def456",
            "start_wall": 1000.0,
            "marks": [{"name": "precheck", "wall": 1001.5},
                      {"name": "edit", "wall": 1005.0}],
        })
        tfile = self._write(timings, ".json")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--timings-file", tfile])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- timings: {", content)
        self.assertIn('"name": "precheck"', content)
        self.assertIn('"elapsed_s": 1.5', content)

    def test_mode_a_timings_file_finished_struct(self):
        # --timings-file 传 finish 归档结构（含 segments，无 marks）→ 直接用
        # segments 写入收据（AI 先 finish 再落收据也 OK）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        timings = json.dumps({
            "batch_id": "abc123def456",
            "start_wall": 1000.0,
            "wall_end": 1005.0,
            "marks": [{"name": "precheck", "wall": 1001.0}],
            "segments": [{"name": "precheck", "elapsed_s": 1.0},
                         {"name": "finish", "elapsed_s": 4.0}],
        })
        tfile = self._write(timings, ".json")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--timings-file", tfile])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn('"elapsed_s": 1.0', content)

    def test_mode_a_timings_file_missing_warns_not_block(self):
        # --timings-file 缺失/非法：warn 降级（timings 置空），收据仍落盘不阻断
        # （诊断数据非验收证据，区别于 --acceptance 的返 2）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        buf = io.StringIO()
        with redirect_stdout(buf):
            with contextlib.redirect_stderr(err):
                rc = ws_report.main(["--batch-file", batch, "--body", body,
                                     "--result", "skip", "--build", "skip",
                                     "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                     "--timings-file", "/nonexistent/timings.json"])
        self.assertEqual(rc, 0)
        self.assertIn("warn: --timings-file 读取失败", err.getvalue())
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- timings: ", content)

    # ── 方向 2/3：未传 --timings-file 自动探测 + elapsed 推导 ────────────
    def _write_probe_timings(self, batch_path, payload):
        """在 log_apply_dir()（cdp_paths 绝对路径，认 CDP_PROJECT_ROOT）下
        按 batch_id 写打点探测文件（与 cdp_timing.py 写入同源）。"""
        from cdp_parse import batch_id_from_text
        from cdp_paths import log_apply_dir
        bid = batch_id_from_text(Path(batch_path).read_text(encoding="utf-8"))
        probe_dir = log_apply_dir()
        probe_dir.mkdir(parents=True, exist_ok=True)
        tfile = probe_dir / f"timings-{bid}.json"
        tfile.write_text(json.dumps(payload), encoding="utf-8")
        self.addCleanup(lambda: tfile.unlink(missing_ok=True))
        return bid

    def test_timings_auto_probe_hit_and_elapsed_derived(self):
        # 未传 --timings-file → 自动探测 timings-<batch_id>.json 命中即用，
        # elapsed_s 缺省从 wall_end-wall_start 取整推导（1005-1000=5）；
        # 探测路径与 cdp_timing 写入同源（log_apply_dir，认 CDP_PROJECT_ROOT）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        bid = self._write_probe_timings(batch, {
            "batch_id": "probe-hit",
            "start_wall": 1000.0,
            "wall_end": 1005.0,
            "marks": [{"name": "precheck", "wall": 1001.5},
                      {"name": "edit", "wall": 1005.0}],
        })
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        from cdp_paths import log_apply_dir
        self.assertIn(f"自动探测到打点文件: {log_apply_dir()}/timings-{bid}.json",
                      err.getvalue())
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- timings: {", content)
        self.assertIn('"name": "precheck"', content)
        self.assertIn("- elapsed_s: 5", content)  # 推导值

    def test_timings_probe_respects_cdp_project_root(self):
        # 同源验证：探测路径由 CDP_PROJECT_ROOT 驱动（不依赖 cwd）——
        # 真实 cwd 下不存在该文件，仅 tmp 根（CDP_PROJECT_ROOT）下存在即命中
        from cdp_paths import log_apply_dir
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        bid = self._write_probe_timings(batch, {
            "batch_id": "probe-root",
            "start_wall": 2000.0,
            "wall_end": 2003.0,
            "marks": [{"name": "precheck", "wall": 2001.0}],
        })
        self.assertFalse((Path("harness") / "log" / "cross-device"
                          / f"timings-{bid}.json").exists())
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        self.assertIn(f"自动探测到打点文件: {log_apply_dir()}/timings-{bid}.json",
                      err.getvalue())

    def test_timings_auto_probe_miss_warns_not_block(self):
        # 未传 --timings-file 且探测不到 → warn 降级（timings 置空，
        # elapsed 推导不出记 0），收据仍落盘不阻断
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        self.assertIn("未探测到", err.getvalue())
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- timings: ", content)
        self.assertIn("- elapsed_s: 0", content)

    def test_elapsed_explicit_overrides_derived(self):
        # --elapsed 显式传参优先于 timings 推导（推导出 5 也以显式 42 为准）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        self._write_probe_timings(batch, {
            "batch_id": "probe-hit",
            "start_wall": 1000.0,
            "wall_end": 1005.0,
            "marks": [{"name": "precheck", "wall": 1001.5}],
        })
        with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--elapsed", "42",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- elapsed_s: 42", content)

    def test_mode_b_board_skip_verify_mode_none(self):
        # 模式 B：--board skip（revert 恢复验证未上板）→ verify_mode=none
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--target", "1a2b3c4d5e6f",
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "revert 恢复",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- verify_mode: none", content)

    def test_mode_b_board_pass_verify_mode_board(self):
        # 模式 B：--board pass（真上板验证）→ verify_mode=board；
        # board+pass 必须带 cases（新门禁，防 prepare evidence-scope 死锁）
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--target", "1a2b3c4d5e6f",
                                 "--result", "pass", "--build", "pass",
                                 "--board", "pass", "--summary", "上板通过",
                                 "--case", "lcview-liveness",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--acceptance",
                                 '{"overall": "pass", "items": []}'])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- verify_mode: board", content)
        self.assertIn("- cases: lcview-liveness", content)

    def test_pass_without_acceptance_rejected(self):
        # result=pass 而无 --acceptance → 返 2 拒写（堵零验收证据假绿）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "pass", "--build", "pass",
                                 "--board", "pass", "--summary", "无验收",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 2)
        self.assertIn("必须传 --acceptance", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_pass_acceptance_invalid_json_rejected(self):
        # result=pass 而 acceptance 非合法 JSON（如手填 "ok"）→ 返 2 拒写
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "pass", "--build", "pass",
                                 "--board", "pass", "--summary", "手填假绿",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--acceptance", "手填 ok"])
        self.assertEqual(rc, 2)
        self.assertIn("须为合法 JSON", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_pass_acceptance_overall_fail_rejected(self):
        # result=pass 而 acceptance overall=fail → 返 2 拒写（失败验收不得过 promote）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "pass", "--build", "pass",
                                 "--board", "pass", "--summary", "假绿",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--acceptance",
                                 '{"overall": "fail", "items": []}'])
        self.assertEqual(rc, 2)
        self.assertIn("overall 非 pass", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_pass_acceptance_fail_item_rejected(self):
        # overall=pass 但 items 含 fail 项（自相矛盾）→ 返 2 拒写
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "pass", "--build", "pass",
                                 "--board", "pass", "--summary", "假绿",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--acceptance",
                                 '{"overall": "pass", "items": [{"status": "fail"}]}'])
        self.assertEqual(rc, 2)
        self.assertIn("含 fail 项", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_pass_acceptance_multiline_valid_singlelined(self):
        # 合法多行 JSON（ws_acceptance.run 输出）→ exit 0，且单行化落盘
        # （header 逐行 key-value，多行 JSON 会被 from_text 截断）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        multiline = ('{\n  "overall": "pass",\n  "items": [\n'
                     '    {"tag": "svc:x", "status": "pass", "detail": "running"}\n'
                     '  ]\n}')
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "pass", "--build", "pass",
                                 "--board", "pass", "--summary", "多行验收",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--acceptance", multiline])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        acc_line = [l for l in content.splitlines()
                    if l.startswith("- acceptance: ")][0]
        self.assertNotIn("\n", acc_line)
        self.assertIn('"overall":"pass"', acc_line)
        from cdp_receipt import read_receipt
        got = read_receipt(details[0])
        self.assertIn('"overall":"pass"', got.acceptance)
        self.assertNotIn("\n", got.acceptance)

    def test_pass_acceptance_array_format_valid(self):
        # 历史数组格式（无 overall）全 pass → 放行（有逐项证据即真绿）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "pass", "--build", "pass",
                                 "--board", "pass", "--summary", "数组验收",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--acceptance",
                                 '[{"tag": "svc:x", "status": "pass"}]'])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)

    def test_pass_acceptance_array_with_fail_rejected(self):
        # 数组格式含 fail 项 → 返 2 拒写
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "pass", "--build", "pass",
                                 "--board", "pass", "--summary", "假绿",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--acceptance",
                                 '[{"tag": "svc:x", "status": "fail"}]'])
        self.assertEqual(rc, 2)
        self.assertIn("含 fail 项", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_sanitize_workspace_placeholder(self):
        # KERNEL_WS/AOSP_WS 绝对路径 → <KEY> 占位符，且优先于家目录正则
        with mock.patch.object(ws_report, "env_path",
                               side_effect=lambda k, d=None: {
                                   "KERNEL_WS": "/home/u/ws/kernel",
                                   "AOSP_WS": "/home/u/ws/aosp"}.get(k, "")):
            out = ws_report._sanitize(
                "编译 /home/u/ws/kernel/out 失败，镜像 /home/u/ws/aosp/out/aosp.img")
        self.assertIn("<KERNEL_WS>/out", out)
        self.assertIn("<AOSP_WS>/out/aosp.img", out)
        self.assertNotIn("/home/u/ws", out)

    def test_sanitize_home_only(self):
        # 无 workspace 路径时家目录绝对路径 → ~
        with mock.patch.object(ws_report, "env_path", return_value=""):
            out = ws_report._sanitize("/home/lechao/foo 与 /home/other/bar")
        self.assertEqual(out, "~/foo 与 ~/bar")

    def test_resolve_target_12hex_passthrough(self):
        # 12hex 原样返回且无错误，不触发 git 调用
        val, err = ws_report._resolve_target("1a2b3c4d5e6f")
        self.assertEqual(val, "1a2b3c4d5e6f")
        self.assertIsNone(err)

    def test_resolve_target_dev_via_git(self):
        # dev 等描述经 git rev-parse --short=12 换算为 12hex（promote 门禁比对 HEAD^）
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "aabbccddeeff\n"
        with mock.patch.object(ws_report.subprocess, "run", return_value=fake) as m:
            val, err = ws_report._resolve_target("dev")
        self.assertEqual(val, "aabbccddeeff")
        self.assertIsNone(err)
        self.assertEqual(m.call_args.args[0],
                         ["git", "rev-parse", "--short=12", "dev"])

    def test_resolve_target_git_failure_rejects(self):
        # git 不可用（OSError）或引用不存在（rc!=0）→ 返回 err 供调用方拒写，
        # 不再写空串蒙混（空 verified_commit 致 promote 门禁比对不等空串）
        with mock.patch.object(ws_report.subprocess, "run",
                               side_effect=OSError("no git")):
            val, err = ws_report._resolve_target("dev")
        self.assertEqual(val, "")
        self.assertIn("无法解析", err)
        fake = mock.Mock()
        fake.returncode = 128
        fake.stdout = ""
        with mock.patch.object(ws_report.subprocess, "run", return_value=fake):
            val, err = ws_report._resolve_target("nope")
        self.assertEqual(val, "")
        self.assertIn("退出 128", err)

    def test_mode_b_target_dev_resolved_to_commit(self):
        # 模式 B --target dev：收据 verified_commit 为 rev-parse 结果（非字面量 dev）
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "aabbccddeeff\n"
        buf = io.StringIO()
        with mock.patch.object(ws_report.subprocess, "run", return_value=fake):
            with redirect_stdout(buf):
                rc = ws_report.main(["--target", "dev",
                                     "--result", "skip", "--build", "skip",
                                     "--board", "skip", "--summary", "dev 描述",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("aabbccddeeff", content)
        self.assertNotIn("- verified_commit: dev", content)

    def test_mode_b_target_unresolvable_rejects(self):
        # 模式 B --target 解析失败（git 引用不存在）→ exit 2 拒写收据
        fake = mock.Mock()
        fake.returncode = 128
        fake.stdout = ""
        err = io.StringIO()
        buf = io.StringIO()
        with mock.patch.object(ws_report.subprocess, "run", return_value=fake):
            with redirect_stdout(buf):
                with contextlib.redirect_stderr(err):
                    rc = ws_report.main(["--target", "nope",
                                         "--result", "skip", "--build", "skip",
                                         "--board", "skip", "--summary", "坏 target",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 2)
        self.assertIn("无法解析 --target", err.getvalue())
        self.assertFalse(self._dir.exists())

    def test_mode_a_target_dev_resolved_to_commit(self):
        # 模式 A 显式 --target dev 同走 _resolve_target（模式 B 之外的遗漏覆盖）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "aabbccddeeff\n"
        buf = io.StringIO()
        with mock.patch.object(ws_report.subprocess, "run", return_value=fake):
            with redirect_stdout(buf):
                rc = ws_report.main(["--batch-file", batch, "--body", body,
                                     "--target", "dev",
                                     "--result", "skip", "--build", "skip",
                                     "--board", "skip", "--summary", "A dev target",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        self.assertEqual(len(details), 1)
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("aabbccddeeff", content)
        self.assertNotIn("- verified_commit: dev", content)

    # ── 方向 2/3：cases 自动探测补全 + board+pass 空 cases 拒写 ──────────
    def _write_probe_cases(self, batch_path, cases_text):
        """在 log_apply_dir()（cdp_paths 绝对路径，认 CDP_PROJECT_ROOT）下
        按 batch_id 写 cases 探测文件（与 ws_acceptance 写入同源）。"""
        from cdp_parse import batch_id_from_text
        from cdp_paths import log_apply_dir
        bid = batch_id_from_text(Path(batch_path).read_text(encoding="utf-8"))
        probe_dir = log_apply_dir()
        probe_dir.mkdir(parents=True, exist_ok=True)
        cfile = probe_dir / f"cases-{bid}.json"
        cfile.write_text(json.dumps({"batch_id": bid, "cases": cases_text}),
                         encoding="utf-8")
        self.addCleanup(lambda: cfile.unlink(missing_ok=True))
        return bid

    def test_cases_auto_probe_hit_boardsv(self):
        # 方向 1/2：-sv 批次未传 --case → 自动探测 cases-<batch_id>.json
        # 命中即补全（与 timings 探测同源），board pass 收据 cases 字段落盘
        batch = self._write("""-sv base:1a2b3c4d5e6f
意图: liveness 验证
验收: svc:lechao_lcview
方向: x
""", ".cdp")
        body = self._write("## 现场\n")
        bid = self._write_probe_cases(batch, "lcview-liveness,lcview-sepolicy-label")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "pass", "--build", "skip",
                                 "--board", "pass", "--summary", "liveness",
                                 "--acceptance",
                                 json.dumps({"overall": "pass",
                                             "items": [{"tag": "svc:lechao_lcview",
                                                        "status": "pass"}]})])
        self.assertEqual(rc, 0)
        self.assertIn(f"自动探测到 cases 文件: {ws_report.log_apply_dir()}/cases-{bid}.json",
                      err.getvalue())
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- cases: lcview-liveness,lcview-sepolicy-label", content)

    def test_cases_explicit_overrides_probe(self):
        # 方向 2：显式 --case 优先于探测文件（探测文件存在也不覆盖显式传参）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        self._write_probe_cases(batch, "lcview-perf")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--case", "lcview-liveness",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        self.assertNotIn("自动探测到 cases 文件", err.getvalue())
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- cases: lcview-liveness", content)

    def test_cases_auto_probe_miss_warns_not_block(self):
        # 方向 2：未传 --case 且探测不到 → warn 降级（cases 置空），
        # skip 收据仍落盘不阻断（空 cases 阻断仅限 board+pass）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        self.assertIn("未探测到", err.getvalue())
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- cases: ", content)

    def test_cases_board_pass_empty_rejected(self):
        # 方向 3：verify_mode=board 且 result=pass 时 cases 为空 → 返 2 拒写
        # （空 cases 会让 prepare evidence-scope 推导死锁，堵源头优于事后改收据）
        batch = self._write("""-sv base:1a2b3c4d5e6f
意图: liveness 验证
验收: svc:lechao_lcview
方向: x
""", ".cdp")
        body = self._write("## 现场\n")
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "pass", "--build", "skip",
                                 "--board", "pass", "--summary", "liveness",
                                 "--acceptance",
                                 json.dumps({"overall": "pass",
                                             "items": [{"tag": "svc:lechao_lcview",
                                                        "status": "pass"}]})])
        self.assertEqual(rc, 2)
        self.assertIn("必须带 cases", err.getvalue())
        self.assertFalse(any(f.name != "trend.md"
                             for f in self._dir.glob("*.md") if f.exists()))

    def test_cases_skip_empty_not_rejected(self):
        # 方向 3 边界：skip 收据（verify_mode=none）空 cases 不拒写
        # （-s 批次无实跑用例属正常，非 board 证据锚点不受 prepare 死锁影响）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        with redirect_stdout(io.StringIO()):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- cases: ", content)

    # ── 方向 2：解析打点前自发 report mark（兜底段收窄为纯写收据）─────────
    def test_report_mark_appended_to_timing_file(self):
        # 显式 --timings-file 时：解析前自发 mark report 追加到打点文件，
        # 兜底段（finish）收窄为 report → 算段时刻（纯写收据耗时）
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        timings = json.dumps({
            "batch_id": "abc123def456",
            "start_wall": 1000.0,
            "marks": [{"name": "precheck", "wall": 1001.5},
                      {"name": "edit", "wall": 1005.0}],
        })
        tfile = self._write(timings, ".json")
        with redirect_stdout(io.StringIO()):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s",
                                 "--timings-file", tfile])
        self.assertEqual(rc, 0)
        data = json.loads(Path(tfile).read_text(encoding="utf-8"))
        names = [m["name"] for m in data["marks"]]
        self.assertIn("report", names)
        self.assertEqual(names[-1], "report", "report 须为末个 mark")
        # 收据 timings 段含 report 与兜底段（finish = report 到算段时刻）
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn('"name": "report"', content)
        self.assertIn('"name": "finish"', content)

    def test_report_mark_auto_probe_batch(self):
        # 未传 --timings-file：自动探测 timings-<batch_id>.json 并自发
        # report mark（batch 识别三级回落同源）；探测不到不阻断
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        from cdp_parse import batch_id_from_text
        from cdp_paths import log_apply_dir
        bid = batch_id_from_text(Path(batch).read_text(encoding="utf-8"))
        probe_dir = log_apply_dir()
        probe_dir.mkdir(parents=True, exist_ok=True)
        tfile = probe_dir / f"timings-{bid}.json"
        tfile.write_text(json.dumps({
            "batch_id": bid, "start_wall": 2000.0,
            "marks": [{"name": "precheck", "wall": 2001.0}],
        }), encoding="utf-8")
        self.addCleanup(lambda: tfile.unlink(missing_ok=True))
        err = io.StringIO()
        with redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        data = json.loads(tfile.read_text(encoding="utf-8"))
        names = [m["name"] for m in data["marks"]]
        self.assertEqual(names, ["precheck", "report"])

    def test_report_mark_no_timing_skips(self):
        # 无打点文件（未 start）时自发 report mark 静默跳过不阻断
        batch = self._write(VALID_S, ".cdp")
        body = self._write("## 现场\n")
        with redirect_stdout(io.StringIO()):
            rc = ws_report.main(["--batch-file", batch, "--body", body,
                                 "--result", "skip", "--build", "skip",
                                 "--board", "skip", "--summary", "s",
                                 "--selfcheck", "pytest_rc=0 refs_rc=0 | 120 passed, 2 skipped in 5.0s"])
        self.assertEqual(rc, 0)
        details = [f for f in self._dir.glob("*.md") if f.name != "trend.md"]
        content = details[0].read_text(encoding="utf-8")
        self.assertIn("- timings: ", content)


if __name__ == "__main__":
    unittest.main()
