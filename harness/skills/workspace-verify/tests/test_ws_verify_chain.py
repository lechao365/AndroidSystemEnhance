# ws_verify_chain 单测：全链六步串联编排（sync→connect→push→unit_test→
# acceptance→report）。关键场景：全过顺序执行与 report 参数派生、中途失败
# 即停（后续步骤不执行）、单步超时 killpg 有界 teardown（canceled 记账）、
# 运行态 JSON 落盘（runs/ 目录，仅编排器写）、编排锁占用 exit 3、
# 无验收/收据源时确定性 skipped。
# 注：_CHAIN_STEPS patch 为步骤名序列（真实 argv 由 _build_argv/_build_report_argv
# 按步骤名构造）；Popen 打桩隔离真实子进程；_RUNS_DIR patch 到临时目录。

import json
import os
import signal
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ws_verify_chain as wc

_BATCH = ("-sv base:%s\n"
          "意图: 单测批次\n"
          "验收: case:lcview-liveness\n"
          "方向: 1) 测试。\n")


def _fake_popen(rc=0):
    """Popen 打桩：wait 返 rc（不捕获 argv stdout）。"""
    proc = mock.Mock()
    proc.wait = mock.Mock(return_value=rc)
    return mock.Mock(side_effect=lambda argv, **kw: proc), proc


def _script_names(calls):
    return [os.path.basename(c[1]) for c in calls]


class TestChain(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self._tmp.name) / "runs"
        self.batch = Path(self._tmp.name) / "b.cdp"
        self.batch.write_text(_BATCH % ("a" * 12), encoding="utf-8")
        envpatcher = mock.patch.dict("os.environ", {}, clear=False)
        envpatcher.start()
        self.addCleanup(envpatcher.stop)

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, popen_rc=0, **kw):
        ctor, proc = _fake_popen(popen_rc)
        kw.setdefault("batch_file", str(self.batch))
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs):
            rc, result = wc.run_chain(**kw)
        return rc, result, proc.wait.call_args_list

    def test_all_pass_runs_in_order(self):
        rc, result, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(result["overall"], "pass")
        self.assertEqual(result["exit_rc"], 0)
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "connect", "push", "unit_test",
                          "acceptance", "report"])
        self.assertEqual([s["rc"] for s in result["steps"]], [0] * 6)
        self.assertEqual(result["skipped"], [])

    def test_run_id_shared_with_children(self):
        # 编排器注入 CDP_RUN_ID：push/unit_test/acceptance 产物同批同 run_id
        rc, result, _ = self._run()
        self.assertEqual(os.environ.get("CDP_RUN_ID"), result["run_id"])

    def test_step_argv_shape(self):
        # 各步 argv 形态：脚本与关键参数（connect=ensure、acceptance 带
        # --case/--batch-file/--result-file、report 带批次源与三产物）
        ctor, _ = _fake_popen(0)
        calls = []
        ctor.side_effect = lambda argv, **kw: (calls.append(argv),
                                               mock.Mock(wait=mock.Mock(return_value=0)))[1]
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      case="lcview-liveness")
        self.assertEqual(rc, 0)
        by_name = {s["name"]: c for s, c in zip(result["steps"], calls)}
        self.assertIn("ensure", by_name["connect"])
        self.assertEqual(by_name["acceptance"][2], "run")
        # 验收源互斥：批次文件在场时只传 --batch-file（case: 前缀自动查表），
        # 不与 --case 同传（ws_acceptance 硬约束）
        acc = by_name["acceptance"]
        self.assertIn("--batch-file", acc)
        self.assertNotIn("--case", acc)
        self.assertIn("--result-file", acc)
        rep = " ".join(by_name["report"])
        self.assertIn("--result pass", rep)
        self.assertIn("--build pass", rep)
        self.assertIn("--board pass", rep)
        self.assertIn("--summary 全链通过", rep)
        self.assertIn("--batch-file", rep)
        self.assertIn("--body", rep)
        for flag in ("--push-file", "--unit-test-file", "--acceptance-file"):
            self.assertIn(flag, rep)

    def test_fail_stops_chain(self):
        # push 失败（rc=1）：后续 unit_test/acceptance/report 不执行
        ctor, _ = _fake_popen(0)

        def run(argv, **kw):
            if os.path.basename(argv[1]) == "ws_push.py":
                return mock.Mock(wait=mock.Mock(return_value=1))
            return mock.Mock(wait=mock.Mock(return_value=0))

        ctor.side_effect = run
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs):
            rc, result = wc.run_chain(batch_file=str(self.batch))
        self.assertEqual(rc, 1)
        self.assertEqual(result["overall"], "fail")
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "connect", "push"])
        self.assertEqual([s["rc"] for s in result["steps"]], [0, 0, 1])
        self.assertEqual(result["skipped"],
                         ["unit_test", "acceptance", "report"])

    def test_timeout_kills_process_group_and_marks_canceled(self):
        # 单步超时：killpg TERM→KILL 有界 teardown，被杀步 rc=None + canceled
        proc = mock.Mock()
        te = subprocess.TimeoutExpired(cmd="x", timeout=0.1)
        proc.wait = mock.Mock(side_effect=[te, te, te, 0])  # 超时/宽限/KILL 段/兜底
        ctor = mock.Mock(return_value=proc)
        kills = []
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc.os, "killpg",
                                  side_effect=lambda pid, sig: kills.append(sig)), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs):
            rc, result = wc.run_chain(batch_file=str(self.batch))
        self.assertEqual(rc, 1)
        self.assertEqual(kills, [signal.SIGTERM, signal.SIGKILL])
        self.assertTrue(result["canceled"])
        killed = result["steps"][0]
        self.assertEqual(killed["name"], "sync")
        self.assertIsNone(killed["rc"])
        self.assertTrue(killed["canceled"])
        self.assertEqual(result["skipped"],
                         ["connect", "push", "unit_test", "acceptance",
                          "report"])

    def test_run_state_json_written(self):
        # 运行态落盘（仅编排器写）：runs/<run_id>.json 记真实 rc/起止/canceled
        out = Path(self._tmp.name) / "chain.json"
        rc, result, _ = self._run(result_file=str(out))
        self.assertEqual(rc, 0)
        run_json = self.runs / f"{result['run_id']}.json"
        data = json.loads(run_json.read_text(encoding="utf-8"))
        self.assertEqual(data["run_id"], result["run_id"])
        self.assertEqual(data["overall"], "pass")
        step = data["steps"][0]
        for key in ("name", "rc", "start", "end", "dur_s", "canceled"):
            self.assertIn(key, step)
        self.assertLessEqual(step["start"], step["end"])
        # result-file 为同构副本
        self.assertEqual(json.loads(out.read_text(encoding="utf-8")),
                         data)

    def test_lock_held_returns_3_no_run_json(self):
        # 编排锁被占用：exit 3，不执行任何步骤，运行态不落盘
        with mock.patch.object(wc.ws_lock, "verify_locks",
                               side_effect=wc.ws_lock.LockHeld("占用")), \
                mock.patch.object(wc.subprocess, "Popen") as ctor, \
                mock.patch.object(wc, "_RUNS_DIR", self.runs):
            rc, result = wc.run_chain(batch_file=str(self.batch))
        self.assertEqual(rc, 3)
        self.assertEqual(result["exit_rc"], 3)
        self.assertEqual(result["overall"], "fail")
        ctor.assert_not_called()
        self.assertEqual(list(self.runs.glob("*.json")), [])

    def test_no_batch_skips_acceptance_and_report(self):
        # 无验收源/无收据源（裸三步用法兼容）：acceptance/report 记 skipped
        rc, result, _ = self._run(batch_file=None)
        self.assertEqual(rc, 0)
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "connect", "push", "unit_test"])
        self.assertEqual(result["skipped"], ["acceptance", "report"])
        self.assertIn("acceptance", result["skip_reasons"])
        self.assertIn("report", result["skip_reasons"])

    def test_case_without_batch_runs_acceptance_only(self):
        # 仅 --case：acceptance 执行（无 --batch-file），report 记 skipped
        ctor, _ = _fake_popen(0)
        calls = []
        ctor.side_effect = lambda argv, **kw: (calls.append(argv),
                                               mock.Mock(wait=mock.Mock(return_value=0)))[1]
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs):
            rc, result = wc.run_chain(case="lcview-liveness")
        self.assertEqual(rc, 0)
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "connect", "push", "unit_test",
                          "acceptance"])
        self.assertEqual(result["skipped"], ["report"])
        acc = next(c for c in calls if "ws_acceptance.py" in c[1])
        self.assertIn("--case", acc)
        self.assertNotIn("--batch-file", acc)


class TestDeriveReportArgs(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self._tmp.name) / "runs"
        self.batch = Path(self._tmp.name) / "b.cdp"
        self.batch.write_text(_BATCH % ("a" * 12), encoding="utf-8")

    def tearDown(self):
        self._tmp.cleanup()

    def _steps(self, *pairs):
        return [{"name": n, "rc": rc, "canceled": False} for n, rc in pairs]

    def test_all_pass(self):
        d = wc._derive_report_args(
            self._steps(("sync", 0), ("push", 0), ("acceptance", 0),
                        ("report", 0)), "pass")
        self.assertEqual(d["result"], "pass")
        self.assertEqual(d["build"], "pass")
        self.assertEqual(d["board"], "pass")
        self.assertIn("全链通过", d["summary"])

    def test_unit_test_fail_board_fail(self):
        d = wc._derive_report_args(
            self._steps(("sync", 0), ("push", 0), ("unit_test", 1)), "fail")
        self.assertEqual((d["result"], d["build"], d["board"]),
                         ("fail", "pass", "fail"))
        self.assertIn("链停于 unit_test", d["summary"])

    def test_sync_fail_board_skip(self):
        d = wc._derive_report_args(self._steps(("sync", 1)), "fail")
        self.assertEqual((d["result"], d["build"], d["board"]),
                         ("fail", "skip", "skip"))

    def test_canceled_summary(self):
        d = wc._derive_report_args(
            [{"name": "push", "rc": None, "canceled": True}], "fail")
        self.assertIn("超时取消", d["summary"])

    def test_build_override_applied_in_chain(self):
        # --build 显式传参覆盖派生值（AI 判定优先）：全链过但 build=fail
        ctor, _ = _fake_popen(0)
        calls = []
        ctor.side_effect = lambda argv, **kw: (calls.append(argv),
                                               mock.Mock(wait=mock.Mock(return_value=0)))[1]
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs):
            rc, _ = wc.run_chain(batch_file=str(self.batch), build="fail")
        self.assertEqual(rc, 0)
        rep = next(" ".join(c) for c in calls if "ws_report.py" in c[1])
        self.assertIn("--build fail", rep)


if __name__ == "__main__":
    unittest.main()
