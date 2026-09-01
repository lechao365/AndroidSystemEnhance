"""方向 2 单测：验证阶段脚本自动打点（_mark_stage）不阻断主流程。

覆盖：
- 无打点文件且无 CDP_BATCH_ID：静默跳过（无异常，不写文件）
- CDP_BATCH_ID + start 打点：mark 成功写入对应段名
- 多打点文件无 env：静默跳过（不误标其他批次）
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ws_upload_tests  # noqa: E402（import ws_adb_connect，安全无副作用）


class TestStageMark(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_root = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self._old_batch = os.environ.get("CDP_BATCH_ID")
        os.environ.pop("CDP_BATCH_ID", None)
        self.batch = "aabbccddeeff"

    def tearDown(self):
        if self._old_root is None:
            os.environ.pop("CDP_PROJECT_ROOT", None)
        else:
            os.environ["CDP_PROJECT_ROOT"] = self._old_root
        if self._old_batch is None:
            os.environ.pop("CDP_BATCH_ID", None)
        else:
            os.environ["CDP_BATCH_ID"] = self._old_batch
        self._tmp.cleanup()

    def _timing_dir(self):
        return Path(self._tmp.name) / "harness" / "log" / "cross-device"

    def test_stage_mark_silent_without_source(self):
        # 无打点文件且无 CDP_BATCH_ID：静默跳过（不阻断主流程）
        ws_upload_tests._mark_stage("verify_unit_test")

    def test_stage_mark_writes_via_env_batch(self):
        # CDP_BATCH_ID + start 打点：mark 写入指定段名
        timing = Path(__file__).resolve().parents[2] / "cross-device" / "lib" / "python" / "cdp_timing.py"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(timing.parent) + os.pathsep + env.get("PYTHONPATH", "")
        import subprocess
        subprocess.run([sys.executable, str(timing), "start", "--batch", self.batch],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env, check=True)
        os.environ["CDP_BATCH_ID"] = self.batch
        ws_upload_tests._mark_stage("verify_unit_test")
        data = json.loads(
            (self._timing_dir() / f"timings-{self.batch}.json").read_text(encoding="utf-8"))
        self.assertEqual(data["marks"][0]["name"], "verify_unit_test")

    def test_stage_mark_silent_with_multiple_files(self):
        # 多打点文件且无 env：静默跳过（不误标其他批次）
        timing = Path(__file__).resolve().parents[2] / "cross-device" / "lib" / "python" / "cdp_timing.py"
        env = dict(os.environ)
        env["PYTHONPATH"] = str(timing.parent) + os.pathsep + env.get("PYTHONPATH", "")
        import subprocess
        for bid in (self.batch, "112233445566"):
            subprocess.run([sys.executable, str(timing), "start", "--batch", bid],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", env=env, check=True)
        ws_upload_tests._mark_stage("verify_unit_test")
        for p in self._timing_dir().glob("timings-*.json"):
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["marks"], [], f"{p.name} 不应被误标")


if __name__ == "__main__":
    unittest.main()