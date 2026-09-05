# ws_verify_chain 单测：sync→push→unit_test 三步串联编排。关键场景：
# 全过顺序执行、中途失败即停（后续步骤不执行）、JSON 产物含逐段 rc 与耗时。
# 注：_CHAIN_STEPS patch 为步骤名序列（真实 argv 由 _build_argv 按步骤名
# 构造——与计划测试 [(name, cmd)] 形态的适配差异，argv 断言按脚本名判定）。

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ws_verify_chain as wc


class TestChain(unittest.TestCase):
    def _steps(self):
        return ("sync", "push", "unit_test")

    def test_all_pass_runs_in_order(self):
        calls = []

        def fake_run(cmd, **kw):
            calls.append(os.path.basename(cmd[1]))
            return mock.Mock(returncode=0, stdout="ok", stderr="")

        with mock.patch.object(wc.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(wc, "_CHAIN_STEPS", self._steps()), \
                mock.patch.object(wc.time, "monotonic",
                                  side_effect=[0, 1, 1, 2, 2, 3]):
            rc, result = wc.run_chain()
        self.assertEqual(rc, 0)
        self.assertEqual(result["overall"], "pass")
        self.assertEqual(calls, ["sync_code_to_workspace.py", "ws_push.py",
                                 "ws_upload_tests.py"])
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "push", "unit_test"])
        self.assertEqual([s["rc"] for s in result["steps"]], [0, 0, 0])
        self.assertEqual([s["dur_s"] for s in result["steps"]],
                         [1.0, 1.0, 1.0])
        self.assertEqual(result["skipped"], [])

    def test_fail_stops_chain(self):
        def fake_run(cmd, **kw):
            hit = os.path.basename(cmd[1]) == "ws_push.py"
            return mock.Mock(returncode=(1 if hit else 0),
                             stdout="", stderr="boom")

        with mock.patch.object(wc.subprocess, "run", side_effect=fake_run), \
                mock.patch.object(wc, "_CHAIN_STEPS", self._steps()), \
                mock.patch.object(wc.time, "monotonic",
                                  side_effect=[0, 1, 1, 2]):
            rc, result = wc.run_chain()
        self.assertEqual(rc, 1)
        self.assertEqual(result["overall"], "fail")
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "push"])  # unit_test 未执行
        self.assertEqual([s["rc"] for s in result["steps"]], [0, 1])
        self.assertIn("unit_test", result["skipped"])

    def test_result_file_written_with_run_id(self):
        with mock.patch.object(wc.subprocess, "run",
                               return_value=mock.Mock(returncode=0,
                                                      stdout="ok", stderr="")), \
                mock.patch.object(wc, "_CHAIN_STEPS", self._steps()), \
                mock.patch.dict("os.environ", {"CDP_RUN_ID": "run-xyz"}):
            with tempfile.TemporaryDirectory() as d:
                out = Path(d) / "chain.json"
                rc, _ = wc.run_chain(result_file=str(out))
                data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(rc, 0)
        self.assertEqual(data["run_id"], "run-xyz")
        self.assertEqual(data["overall"], "pass")
        self.assertEqual(data["skipped"], [])


if __name__ == "__main__":
    unittest.main()
