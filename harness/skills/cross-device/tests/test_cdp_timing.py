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

    def test_mark_silent_skip_no_source(self):
        # 无 env 且目录多文件（无法唯一识别）→ 静默跳过返 0，不写任何文件
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        self.assertEqual(cdp_timing.main(["start", "--batch", "fedcba654321"]), 0)
        before = sorted(p.name for p in cdp_paths.log_apply_dir().glob("timings-*.json"))
        self.assertEqual(cdp_timing.main(["mark", "--name", "verify_sync"]), 0)
        after = sorted(p.name for p in cdp_paths.log_apply_dir().glob("timings-*.json"))
        self.assertEqual(before, after, "多文件且无 env 应静默跳过，不得误标")

    def test_mark_silent_skip_empty_dir(self):
        # 无 env 且目录无打点文件 → 静默跳过返 0（脚本自动 mark 未 start 不阻断）
        self.assertEqual(cdp_timing.main(["mark", "--name", "verify_sync"]), 0)

    def test_mark_explicit_batch_overrides_env(self):
        # 显式 --batch 优先于环境变量（env 指向另一文件时写显式目标）
        self.assertEqual(cdp_timing.main(["start", "--batch", self.batch]), 0)
        self.assertEqual(cdp_timing.main(["start", "--batch", "fedcba654321"]), 0)
        os.environ["CDP_BATCH_ID"] = "fedcba654321"
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


if __name__ == "__main__":
    unittest.main()
