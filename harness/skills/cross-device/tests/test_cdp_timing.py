import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib" / "python"))
import cdp_paths
import cdp_timing


class TestCdpTiming(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self.batch = "abc123def456"

    def tearDown(self):
        if self._old is None:
            os.environ.pop("CDP_PROJECT_ROOT", None)
        else:
            os.environ["CDP_PROJECT_ROOT"] = self._old
        self._tmp.cleanup()

    def _path(self):
        return cdp_paths.log_apply_dir() / f"timings-{self.batch}.json"

    def test_start_creates_file_with_batch_id(self):
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        p = self._path()
        self.assertTrue(p.is_file())
        data = json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(data["batch_id"], self.batch)
        self.assertIn("start_wall", data)
        self.assertEqual(data["marks"], [])

    def test_start_with_batch_file(self):
        cdp = Path(self._tmp.name) / "batch.cdp"
        cdp.write_text(
            f"-sv base:111111111111\n意图: 测试\n验收: svc:x boot\n方向: 改 1 处\n",
            encoding="utf-8")
        self.assertEqual(cdp_timing.main(["start", "--batch-file", str(cdp)]), 0)
        # batch_id 来自批次内容哈希（12 hex），打点文件落在工作态目录
        files = list(cdp_paths.log_apply_dir().glob("timings-*.json"))
        self.assertEqual(len(files), 1)
        data = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual(len(data["batch_id"]), 12)
        self.assertTrue(files[0].name.endswith(f"-{data['batch_id']}.json"))

    def test_start_requires_exactly_one_source(self):
        self.assertNotEqual(cdp_timing.main(["start"]), 0)
        self.assertNotEqual(
            cdp_timing.main(["start", "--batch", self.batch, "--batch-file", "x"]), 0)

    def test_mark_appends(self):
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        self.assertEqual(cdp_timing.main(["mark", "--batch", self.batch, "--name", "precheck"]), 0)
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][0]["name"], "precheck")
        self.assertIn("wall", data["marks"][0])

    def test_mark_without_start_returns_3(self):
        self.assertEqual(
            cdp_timing.main(["mark", "--batch", self.batch, "--name", "x"]), 3)

    def test_finish_without_start_returns_3(self):
        self.assertEqual(cdp_timing.main(["finish", "--batch", self.batch]), 3)

    def test_finish_outputs_segments_in_order(self):
        cdp_timing.main(["start", "--batch", self.batch])
        cdp_timing.main(["mark", "--batch", self.batch, "--name", "precheck"])
        cdp_timing.main(["mark", "--batch", self.batch, "--name", "edit"])
        self.assertEqual(cdp_timing.main(["finish", "--batch", self.batch]), 0)
        data = json.loads(self._path().read_text(encoding="utf-8"))
        names = [s["name"] for s in data["segments"]]
        self.assertEqual(names, ["precheck", "edit", "finish"])
        for s in data["segments"]:
            self.assertGreaterEqual(s["elapsed_s"], 0)
            self.assertLess(s["elapsed_s"], 3600)

    def test_finish_no_marks_empty_segments(self):
        # start 后直接 finish：空 segments 不崩（调用方按缺打点处理）
        cdp_timing.main(["start", "--batch", self.batch])
        self.assertEqual(cdp_timing.main(["finish", "--batch", self.batch]), 0)
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(data["segments"], [])

    def test_start_overwrites_previous(self):
        cdp_timing.main(["start", "--batch", self.batch])
        cdp_timing.main(["mark", "--batch", self.batch, "--name", "precheck"])
        cdp_timing.main(["start", "--batch", self.batch])
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(data["marks"], [], "start 应覆盖重建，清空历史 mark")

    def test_finish_preserves_marks_and_start(self):
        # finish 保留原始 start_wall/marks（ws_report 两种结构皆可读，
        # 后续 mark 仍可追加），仅新增 wall_end + segments
        cdp_timing.main(["start", "--batch", self.batch])
        cdp_timing.main(["mark", "--batch", self.batch, "--name", "precheck"])
        cdp_timing.main(["mark", "--batch", self.batch, "--name", "edit"])
        cdp_timing.main(["finish", "--batch", self.batch])
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertIn("start_wall", data)
        self.assertEqual(len(data["marks"]), 2)
        self.assertIn("wall_end", data)
        self.assertIn("segments", data)

    def test_compute_segments_math(self):
        # 纯函数：固定 start/marks，验证相邻段耗时精确（首段含 start 到首 mark）
        data = {
            "batch_id": self.batch,
            "start_wall": 1000.0,
            "marks": [
                {"name": "a", "wall": 1005.5},
                {"name": "b", "wall": 1030.0},
            ],
        }
        segs = cdp_timing.compute_segments(data)
        self.assertEqual([s["name"] for s in segs], ["a", "b", "finish"])
        self.assertAlmostEqual(segs[0]["elapsed_s"], 5.5)
        self.assertAlmostEqual(segs[1]["elapsed_s"], 24.5)

    def test_compute_segments_no_marks_empty(self):
        self.assertEqual(cdp_timing.compute_segments({"start_wall": 1.0, "marks": []}), [])
        self.assertEqual(cdp_timing.compute_segments({}), [])

    # ── 方向 3：batch 自动识别（脚本自动 mark 依赖）──────────────────
    # 优先级：显式 --batch/--file > 环境变量 CDP_BATCH_ID > log 目录唯一
    # timings 文件；均缺静默跳过（返 0 不阻断，且不写文件）。

    def test_mark_uses_env_cdp_batch_id(self):
        # CDP_BATCH_ID 环境变量识别：不传 --batch 也能 mark 到对应文件
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        os.environ["CDP_BATCH_ID"] = self.batch
        try:
            self.assertEqual(
                cdp_timing.main(["mark", "--name", "verify_sync"]), 0)
        finally:
            os.environ.pop("CDP_BATCH_ID", None)
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][0]["name"], "verify_sync")

    def test_mark_uses_unique_timing_file(self):
        # 目录仅一个 timings 文件且无 env → 自动识别该文件
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        self.assertEqual(cdp_timing.main(["mark", "--name", "verify_acceptance"]), 0)
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][0]["name"], "verify_acceptance")

    def test_mark_multi_start_uses_latest_current_batch(self):
        # 多次 start 后 current-batch.json 指向最近批次，自动 mark 落到本批；
        # 归档机制把旧批移入 archive/，工作态顶层仅当前批
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        self.assertEqual(cdp_timing.main(["start", "--batch", "fedcba654321"]), 0)
        self.assertEqual(cdp_timing.main(["mark", "--name", "verify_sync"]), 0)
        d = cdp_paths.log_apply_dir()
        data = json.loads((d / "timings-fedcba654321.json").read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][0]["name"], "verify_sync",
                         "current-batch 应定位最近批")
        top = sorted(p.name for p in d.glob("timings-*.json"))
        self.assertEqual(top, ["timings-fedcba654321.json"], "旧批应已归档")

    def test_mark_skip_empty_dir_warns(self):
        # 无打点且无 current-batch.json（未 start）：stderr warn 后 rc 0
        # （取消静默跳过：缺打点不再无提示，仍不阻断）
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cdp_timing.main(["mark", "--name", "verify_sync"])
        self.assertEqual(rc, 0)
        self.assertIn("warn", err.getvalue())
        self.assertIn("current-batch.json", err.getvalue())

    def test_mark_explicit_batch_overrides_env(self):
        # 显式 --batch 优先于环境变量（env 指向未 start 文件时仍写显式目标）
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        os.environ["CDP_BATCH_ID"] = "envbatch123456"
        try:
            self.assertEqual(
                cdp_timing.main(["mark", "--batch", self.batch, "--name", "precheck"]), 0)
        finally:
            os.environ.pop("CDP_BATCH_ID", None)
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][0]["name"], "precheck")

    # ── 方向 3：--zero 记零 mark（跳过段占位，段耗时 0）──────────────

    def test_mark_zero_uses_last_mark_wall(self):
        # --zero 的 wall 取最近 mark 同刻：段耗时 0（跳过段占位可归因）
        cdp_timing.main(["start", "--batch", self.batch])
        cdp_timing.main(["mark", "--batch", self.batch, "--name", "verify_acceptance"])
        self.assertEqual(
            cdp_timing.main(["mark", "--batch", self.batch, "--name",
                             "verify_build", "--zero"]), 0)
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][0]["name"], "verify_acceptance")
        self.assertEqual(data["marks"][1]["name"], "verify_build")
        self.assertEqual(data["marks"][1]["wall"], data["marks"][0]["wall"],
                         "零 mark 须与最近 mark 同刻")
        cdp_timing.main(["finish", "--batch", self.batch])
        data = json.loads(self._path().read_text(encoding="utf-8"))
        segs = {s["name"]: s["elapsed_s"] for s in data["segments"]}
        self.assertEqual(segs["verify_build"], 0, "零 mark 段耗时须为 0")

    def test_mark_zero_no_marks_uses_start_wall(self):
        # 无任何 mark 时 --zero 落 start_wall 同刻（start 后直接补零）
        cdp_timing.main(["start", "--batch", self.batch])
        self.assertEqual(
            cdp_timing.main(["mark", "--batch", self.batch, "--name",
                             "verify_sync", "--zero"]), 0)
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][0]["wall"], data["start_wall"])

    # ── 方向 2：start 落 current-batch.json + 归档历史 timings ─────────
    def test_start_writes_current_batch(self):
        # start 落 current-batch.json 记 batch_id（自动 mark/finish 定位本批）
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        cb = json.loads((cdp_paths.log_apply_dir() / "current-batch.json")
                        .read_text(encoding="utf-8"))
        self.assertEqual(cb["batch_id"], self.batch)

    def test_start_archives_previous_timings(self):
        # 再次 start 把已有 timings 移入 archive/ 子目录，工作态顶层仅当前批
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        self.assertEqual(cdp_timing.main(["mark", "--batch", self.batch,
                                          "--name", "precheck"]), 0)
        batch2 = "fedcba654321"
        self.assertEqual(cdp_timing.main(["start", "--batch", batch2]), 0)
        d = cdp_paths.log_apply_dir()
        top = sorted(p.name for p in d.glob("timings-*.json"))
        self.assertEqual(top, [f"timings-{batch2}.json"], "旧批应归档，顶层仅当前批")
        archived = sorted(p.name for p in (d / "archive").glob("timings-*.json"))
        self.assertEqual(archived, [f"timings-{self.batch}.json"])
        cb = json.loads((d / "current-batch.json").read_text(encoding="utf-8"))
        self.assertEqual(cb["batch_id"], batch2, "current-batch 应指向最近批")

    # ── 方向 3：第三级回落读 current-batch.json，多文件共存仍定位本批 ──
    def test_mark_uses_current_batch_multiple_files(self):
        # current-batch.json 为自动识别指针：即使顶层残留多个 timings 文件
        # （历史遗留/手工放置，未走 start 归档）仍定位 current-batch 本批
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        leftover = cdp_paths.log_apply_dir() / "timings-zzzzzzzzzzzz.json"
        leftover.write_text(json.dumps(
            {"batch_id": "zzzzzzzzzzzz", "start_wall": 1.0, "marks": []}),
            encoding="utf-8")
        self.assertEqual(cdp_timing.main(["mark", "--name", "verify_sync"]), 0)
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][0]["name"], "verify_sync",
                         "current-batch 应定位本批打点文件")
        ldata = json.loads(leftover.read_text(encoding="utf-8"))
        self.assertEqual(ldata["marks"], [], "残留文件不得被误标")

    # ── 方向 5：段名常量表，表外名仅 warn 不阻断 ───────────────────────
    def test_known_segments_include_edit_sub_stages(self):
        # 方向 2：KNOWN_SEGMENTS 增 edit_validate/gen_manifest/edit_plan/edit_retry
        # （edit 段细分 + 编辑打点约定），自发/约定 mark 不告警
        for seg in ("edit_validate", "gen_manifest", "edit_plan", "edit_retry"):
            self.assertIn(seg, cdp_timing.KNOWN_SEGMENTS)

    def test_conditional_segments_defined(self):
        # 方向 1：CONDITIONAL_SEGMENTS 含 4 个条件段（未产出不判缺），
        # 且为 KNOWN_SEGMENTS 子集（表内 mark 不告警）
        self.assertEqual(cdp_timing.CONDITIONAL_SEGMENTS,
                         frozenset({"edit_validate", "gen_manifest",
                                    "edit_plan", "edit_retry"}))
        self.assertLessEqual(cdp_timing.CONDITIONAL_SEGMENTS,
                             cdp_timing.KNOWN_SEGMENTS)

    def test_mark_known_segment_no_warn(self):
        # 表内段名（如 verify_acceptance）不告警
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cdp_timing.main(["mark", "--batch", self.batch,
                                  "--name", "verify_acceptance"])
        self.assertEqual(rc, 0)
        self.assertNotIn("warn", err.getvalue())

    def test_mark_unknown_segment_warns(self):
        # 表外段名 stderr warn 但不阻断（rc 0，仍记录）
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cdp_timing.main(["mark", "--batch", self.batch,
                                  "--name", "unknown_stage"])
        self.assertEqual(rc, 0)
        self.assertIn("warn", err.getvalue())
        self.assertIn("unknown_stage", err.getvalue())
        self.assertIn("verify_acceptance", err.getvalue(),
                      "warn 应附常量表名便于对照")
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][0]["name"], "unknown_stage",
                         "表外名仍记录，仅告警")

    # ── 方向 1：mark --dur-s 自报自测真实耗时 ─────────────────────────
    def test_mark_dur_s_written(self):
        # mark 带 --dur-s 时写入 mark 记录（compute_segments 归因读取）
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        self.assertEqual(
            cdp_timing.main(["mark", "--batch", self.batch, "--name",
                             "apply_selfcheck", "--dur-s", "18.25"]), 0)
        data = json.loads(self._path().read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][0]["name"], "apply_selfcheck")
        self.assertAlmostEqual(data["marks"][0]["dur_s"], 18.25)

    def test_mark_dur_s_with_zero_mutex(self):
        # --zero 与 --dur-s 互斥（零 mark 段耗时恒 0，无自测耗时可报）
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cdp_timing.main(["mark", "--batch", self.batch, "--name",
                                  "verify_build", "--zero", "--dur-s", "1.0"])
        self.assertEqual(rc, 2)
        self.assertIn("互斥", err.getvalue())

    # ── 方向 2：compute_segments 归因（dur_s 覆盖 + gap_before 余量）──
    def test_compute_segments_dur_s_attribution(self):
        # mark 带 dur_s：该段耗时取 dur_s，相邻差额余量落 gap_before_<name>
        # （余量 >= 阈值 1.0）；gap 段在 name 段前（时间序）
        data = {
            "batch_id": self.batch,
            "start_wall": 1000.0,
            "marks": [
                {"name": "a", "wall": 1005.5},
                {"name": "apply_selfcheck", "wall": 1030.0, "dur_s": 18.0},
            ],
        }
        segs = cdp_timing.compute_segments(data)
        self.assertEqual([s["name"] for s in segs],
                         ["a", "gap_before_apply_selfcheck", "apply_selfcheck",
                          "finish"])
        self.assertAlmostEqual(segs[0]["elapsed_s"], 5.5)
        self.assertAlmostEqual(segs[1]["elapsed_s"], 6.5)
        self.assertAlmostEqual(segs[2]["elapsed_s"], 18.0)

    def test_compute_segments_dur_s_small_gap_skipped(self):
        # 余量小于阈值不落 gap 段（gap=0.5 < 1.0，防计时噪声污染归因）
        data = {
            "batch_id": self.batch,
            "start_wall": 1000.0,
            "marks": [
                {"name": "a", "wall": 1001.0},
                {"name": "b", "wall": 1007.5, "dur_s": 6.0},
            ],
        }
        segs = cdp_timing.compute_segments(data)
        self.assertEqual([s["name"] for s in segs], ["a", "b", "finish"])
        self.assertAlmostEqual(segs[1]["elapsed_s"], 6.0)

    def test_compute_segments_dur_s_invalid_falls_back(self):
        # dur_s 越界（> interval）或非数值：回退旧算法（整段差额归该段）
        data = {
            "batch_id": self.batch,
            "start_wall": 1000.0,
            "marks": [
                {"name": "a", "wall": 1001.0},
                {"name": "b", "wall": 1007.0, "dur_s": 99.0},
                {"name": "c", "wall": 1012.0, "dur_s": "oops"},
            ],
        }
        segs = cdp_timing.compute_segments(data)
        self.assertEqual([s["name"] for s in segs], ["a", "b", "c", "finish"])
        self.assertAlmostEqual(segs[1]["elapsed_s"], 6.0)
        self.assertAlmostEqual(segs[2]["elapsed_s"], 5.0)

    # ── 方向 4：同名段名 #n + 剥序号校验 + gap 段忽略 ─────────────────
    def test_compute_segments_duplicate_name_numbered(self):
        # 同名 mark 第 n 次段名 name#n（首次不加序号），返工轮次可数
        data = {
            "batch_id": self.batch,
            "start_wall": 1000.0,
            "marks": [
                {"name": "edit", "wall": 1002.0},
                {"name": "edit", "wall": 1004.0},
                {"name": "edit", "wall": 1007.0},
            ],
        }
        segs = cdp_timing.compute_segments(data)
        self.assertEqual([s["name"] for s in segs],
                         ["edit", "edit#2", "edit#3", "finish"])

    def test_base_seg_name(self):
        # 剥序号 + gap 段忽略（段名表校验/ws_report missing 判定共用）
        self.assertEqual(cdp_timing._base_seg_name("apply_selfcheck"),
                         "apply_selfcheck")
        self.assertEqual(cdp_timing._base_seg_name("edit#2"), "edit")
        self.assertEqual(cdp_timing._base_seg_name("gap_before_edit"), "")
        self.assertEqual(cdp_timing._base_seg_name("gap_before_edit#2"), "")

    def test_mark_suffixed_name_no_warn(self):
        # 段名表校验剥序号：显式传 name#n 按基础名比对（表内不告警）
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = cdp_timing.main(["mark", "--batch", self.batch, "--name",
                                  "apply_selfcheck#2"])
        self.assertEqual(rc, 0)
        self.assertNotIn("warn", err.getvalue())


if __name__ == "__main__":
    unittest.main()
