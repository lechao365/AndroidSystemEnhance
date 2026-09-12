# ws_verify_chain 单测：全链六步串联编排（sync→connect→push→unit_test→
# acceptance→report）。关键场景：全过顺序执行与 report 参数派生、中途失败
# 即停（后续步骤不执行）、单步超时 killpg 有界 teardown（canceled 记账）、
# 运行态 JSON 落盘（runs/ 目录，仅编排器写）、编排锁占用 exit 3、
# 无验收/收据源时确定性 skipped。
# 注：_CHAIN_STEPS patch 为步骤名序列（真实 argv 由 _build_argv/_build_report_argv
# 按步骤名构造）；Popen 打桩隔离真实子进程；_RUNS_DIR patch 到临时目录。

import contextlib
import io
import json
import os
import re
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

_SELFCHECK_OK = ("pytest_rc=0 | 900 passed | skipped=0 | refs_rc=0 | OK | "
                 "config_rc=0 | OK | contract_rc=0 | OK")


def _fake_popen(rc=0):
    """Popen 打桩：wait 返 rc（不捕获 argv stdout）。"""
    proc = mock.Mock()
    proc.wait = mock.Mock(return_value=rc)
    return mock.Mock(side_effect=lambda argv, **kw: proc), proc


def _script_names(calls):
    return [os.path.basename(c.args[0][1]) for c in calls]


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
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            kw.setdefault("use_locks", False)
            rc, result = wc.run_chain(**kw)
        return rc, result, proc.wait.call_args_list

    def test_all_pass_runs_in_order(self):
        rc, result, _ = self._run()
        self.assertEqual(rc, 0)
        self.assertEqual(result["overall"], "pass")
        self.assertEqual(result["exit_rc"], 0)
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "build", "connect", "push", "unit_test",
                          "acceptance", "package", "report"])
        self.assertEqual([s["rc"] for s in result["steps"]], [0] * 8)
        self.assertEqual(result["skipped"], [])

    def test_package_step_uses_systemd_run_with_batch_id_evidence(self):
        # 方向 3：package 步用 systemd-run --user --wait 拉起 ws_package.py，
        # --evidence-file 指向 package-<batch_id>.json（与 ws_report 探测同源）；
        # 打包失败不置 overall=fail、不阻断链（证据补充非门禁）
        calls = []
        ctor = mock.Mock(side_effect=lambda argv, **kw: (
            calls.append(argv),
            mock.Mock(wait=mock.Mock(return_value=0)))[1])
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      use_locks=False)
        self.assertEqual(rc, 0)
        self.assertEqual(result["overall"], "pass")
        pkg = next(c for c in calls
                   if any(os.path.basename(x) == "ws_package.py" for x in c))
        # argv: [systemd-run, --user, --wait, python, ws_package.py, ...]
        self.assertEqual(pkg[0], "systemd-run")
        self.assertEqual(pkg[1], "--user")
        self.assertEqual(pkg[2], "--wait")
        self.assertIn("--evidence-file", pkg)
        bid = wc.batch_id_from_text(self.batch.read_text(encoding="utf-8"))
        self.assertEqual(pkg[pkg.index("--evidence-file") + 1],
                         str(wc._SCRIPT_DIR.parents[1] / "log"
                             / "workspace-verify"
                             / f"package-{bid}.json"))

    def test_package_failure_does_not_fail_chain(self):
        # 方向 3：package 失败（如 sudo 不可用）只记步不改 overall，
        # report 仍落盘（evidence 已含真实 script_rc 供内嵌）
        def run(argv, **kw):
            if any(os.path.basename(x) == "ws_package.py" for x in argv):
                return mock.Mock(wait=mock.Mock(return_value=1))
            return mock.Mock(wait=mock.Mock(return_value=0))
        ctor = mock.Mock(side_effect=run)
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      use_locks=False)
        self.assertEqual(rc, 0)
        self.assertEqual(result["overall"], "pass")
        by_name = {s["name"]: s for s in result["steps"]}
        self.assertEqual(by_name["package"]["rc"], 1)
        # report 步仍执行（steps 含 report）
        self.assertIn("report", by_name)

    def test_run_id_shared_with_children(self):
        # 编排器注入 CDP_RUN_ID：链内子步骤经 env 读取同批 run_id（产物
        # 同批核验依赖）；链结束复原现场（CDP_RUN_ID 不残留，防同进程
        # 多轮 run_chain 串扰）
        seen = []
        ctor, _ = _fake_popen(0)
        ctor.side_effect = lambda argv, **kw: (
            seen.append(os.environ.get("CDP_RUN_ID")),
            mock.Mock(wait=mock.Mock(return_value=0)))[1]
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      use_locks=False)
        self.assertEqual(rc, 0)
        self.assertTrue(seen)
        self.assertTrue(all(v == result["run_id"] for v in seen),
                        "链内子进程须能从 env 读到本轮 run_id")
        self.assertIsNone(os.environ.get("CDP_RUN_ID"),
                          "chain 结束须复原 CDP_RUN_ID 现场")

    def test_cdp_run_id_not_leaked_between_chains(self):
        # wsv-02：同进程连续两轮 run_chain——第二轮 run_id 不同于首轮
        #（泄漏会使两轮产物共享 run_id，ws_report 同批核验串扰）
        rc1, r1, _ = self._run()
        rc2, r2, _ = self._run()
        self.assertEqual(rc1, 0)
        self.assertEqual(rc2, 0)
        self.assertNotEqual(r1["run_id"], r2["run_id"])
        self.assertIsNone(os.environ.get("CDP_RUN_ID"))

    def test_cdp_run_id_preexisting_restored(self):
        # wsv-02：外部注入的 CDP_RUN_ID 优先复用，chain 结束复原为原值
        os.environ["CDP_RUN_ID"] = "pre-run-001"
        try:
            rc, result, _ = self._run()
            self.assertEqual(result["run_id"], "pre-run-001")
            self.assertEqual(os.environ.get("CDP_RUN_ID"), "pre-run-001")
        finally:
            os.environ.pop("CDP_RUN_ID", None)

    def test_step_argv_shape(self):
        # 各步 argv 形态：脚本与关键参数（connect=ensure、acceptance 带
        # --case/--batch-file/--result-file、report 带批次源与三产物）
        ctor, _ = _fake_popen(0)
        calls = []
        ctor.side_effect = lambda argv, **kw: (calls.append(argv),
                                               mock.Mock(wait=mock.Mock(return_value=0)))[1]
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      case="lcview-liveness", use_locks=False)
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
        self.assertIn("--selfcheck", rep)
        for flag in ("--push-file", "--unit-test-file", "--acceptance-file"):
            self.assertIn(flag, rep)

    def test_fail_stops_chain(self):
        # push 失败（rc=1）：余下验证步不执行；report 仍跑落 fail 收据
        # （A1 修订：失败轮次也有收据，loop done --receipt 记账不卡死）
        ctor, _ = _fake_popen(0)

        def run(argv, **kw):
            if os.path.basename(argv[1]) == "ws_push.py":
                return mock.Mock(wait=mock.Mock(return_value=1))
            return mock.Mock(wait=mock.Mock(return_value=0))

        ctor.side_effect = run
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      use_locks=False)
        self.assertEqual(rc, 1)
        self.assertEqual(result["overall"], "fail")
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "build", "connect", "push", "report"])
        self.assertEqual([s["rc"] for s in result["steps"]], [0, 0, 0, 1, 0])
        self.assertEqual(result["skipped"], ["unit_test", "acceptance", "package"])
        self.assertIn("unit_test", result["skip_reasons"])
        self.assertIn("链已停", result["skip_reasons"]["unit_test"])

    def test_fail_report_derives_fail_receipt_args(self):
        # A1：fail 收据参数由真实 rc 机械派生（result=fail/board=fail，
        # _derive_report_args fail 分支不再死代码）
        ctor, _ = _fake_popen(0)

        def run(argv, **kw):
            if os.path.basename(argv[1]) == "ws_push.py":
                return mock.Mock(wait=mock.Mock(return_value=1))
            return mock.Mock(wait=mock.Mock(return_value=0))

        ctor.side_effect = run
        calls = []
        ctor.side_effect = lambda argv, **kw: (
            calls.append(argv),
            mock.Mock(wait=mock.Mock(
                return_value=1 if os.path.basename(argv[1]) == "ws_push.py" else 0)))[1]
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      use_locks=False)
        rep = " ".join(calls[-1])
        self.assertIn("--result fail", rep)
        self.assertIn("--board fail", rep)
        self.assertIn("链停于 push", rep)

    def test_timeout_kills_process_group_and_marks_canceled(self):
        # 单步超时：killpg TERM→KILL 有界 teardown，被杀步 rc=None + canceled；
        # A1 后 report 步仍执行（fail 收据落盘）→ wait 序列多一段 report
        proc = mock.Mock()
        te = subprocess.TimeoutExpired(cmd="x", timeout=0.1)
        proc.wait = mock.Mock(side_effect=[te, te, te, 0, 0])  # 超时/宽限/KILL 段/report
        ctor = mock.Mock(return_value=proc)
        kills = []
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc.os, "killpg",
                                  side_effect=lambda pid, sig: kills.append(sig)), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      use_locks=False)
        self.assertEqual(rc, 1)
        self.assertEqual(kills, [signal.SIGTERM, signal.SIGKILL])
        self.assertTrue(result["canceled"])
        killed = result["steps"][0]
        self.assertEqual(killed["name"], "sync")
        self.assertIsNone(killed["rc"])
        self.assertTrue(killed["canceled"])
        # A1 后 report 步执行落 fail 收据（不在 skipped 内）
        self.assertEqual(result["skipped"],
                         ["build", "connect", "push", "unit_test",
                          "acceptance", "package"])

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

    def test_chain_ensures_timings_wired_to_report(self):
        # 链式耗时接线（修 elapsed_s=0/timings 空心）：独立拉起（未经 apply
        # 会话 cdp_timing start）时链须补建打点文件、verify_start/verify_end
        # mark 落盘、--timings-file 显式下传 report，且子脚本自发 mark 定位
        # 本批（CDP_BATCH_ID 注入）
        from cdp_paths import log_apply_dir
        bid = wc.batch_id_from_text(self.batch.read_text(encoding="utf-8"))
        tpath = log_apply_dir() / f"timings-{bid}.json"
        self.assertFalse(tpath.exists(), "前置：独立拉起时无打点文件")
        ctor, _ = _fake_popen(0)
        calls = []
        ctor.side_effect = lambda argv, **kw: (
            calls.append(argv),
            mock.Mock(wait=mock.Mock(return_value=0)))[1]
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      use_locks=False)
        self.assertEqual(rc, 0)
        # 1) 打点文件已补建，marks 落盘（verify_start/verify_end 可读）
        data = json.loads(tpath.read_text(encoding="utf-8"))
        names = [m["name"] for m in data.get("marks") or []]
        self.assertIn("verify_start", names)
        self.assertIn("verify_end", names)
        self.assertIn("start_wall", data)
        # 2) report argv 显式接线 --timings-file（指向补建文件）
        rep = next(" ".join(c) for c in calls if "ws_report.py" in c[1])
        self.assertIn("--timings-file", rep)
        self.assertIn(str(tpath), rep)
        # 3) 子脚本自发 mark 定位本批：CDP_BATCH_ID 注入生效（链内子进程
        #    读 env 定位），chain 结束已复原——方向 5：残留会污染同进程
        #    后续用例（单测进程内多次 run_chain 错绑批次）
        self.assertIsNone(os.environ.get("CDP_BATCH_ID"),
                          "CDP_BATCH_ID 用完须复原，不得残留污染后续用例")
        # 4) 模拟链路真实耗时（起跑时刻回拨 5s）→ ws_report 解析
        #    elapsed_s > 0 且 timings 非空（segments 含链内段）
        data["start_wall"] -= 5.0
        tpath.write_text(json.dumps(data), encoding="utf-8")
        import ws_report
        tj, el = ws_report._resolve_timings(str(tpath), bid, "board")
        self.assertTrue(tj, "timings 须非空")
        self.assertIsNotNone(el)
        self.assertGreaterEqual(el, 5, "elapsed_s 须反映链路真实总耗时")
        self.assertIn('"verify_start"', tj)

    def test_chain_marks_each_standard_step(self):
        # 方向 1 判红：链编排器每步完成自发 verify_<step>（真实耗时入账）——
        # mock Popen 下子脚本不真跑不自发 mark，修复前 verify_sync/verify_push/
        # verify_unit_test/verify_acceptance 缺失（被 ws_acceptance 盲目补零）。
        from cdp_paths import log_apply_dir
        old = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self.addCleanup(
            lambda: os.environ.__setitem__("CDP_PROJECT_ROOT", old)
            if old is not None else os.environ.pop("CDP_PROJECT_ROOT", None))
        bid = wc.batch_id_from_text(self.batch.read_text(encoding="utf-8"))
        rc, _, _ = self._run()
        self.assertEqual(rc, 0)
        data = json.loads((log_apply_dir()
                           / f"timings-{bid}.json").read_text(encoding="utf-8"))
        marks = {m["name"]: m for m in data.get("marks") or []}
        for seg in ("verify_build", "verify_sync", "verify_push",
                    "verify_unit_test", "verify_acceptance"):
            self.assertIn(seg, marks,
                          f"链步完成须自发 mark {seg}（真实耗时入账）")
            self.assertIsNotNone(marks[seg].get("dur_s"),
                                 f"{seg} 为执行步须带实测 dur_s 入账")

    def test_chain_marks_skipped_standard_step_zero(self):
        # 方向 1 判红：真跳过的标准步发 zero mark（补零只兜真跳过的步）——
        # 链前序失败后 unit_test 等余步 skipped，其 verify_<step> 须为 zero
        from cdp_paths import log_apply_dir
        old = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name
        self.addCleanup(
            lambda: os.environ.__setitem__("CDP_PROJECT_ROOT", old)
            if old is not None else os.environ.pop("CDP_PROJECT_ROOT", None))
        bid = wc.batch_id_from_text(self.batch.read_text(encoding="utf-8"))
        rc, result, _ = self._run(popen_rc=1)
        self.assertNotEqual(rc, 0)
        self.assertIn("unit_test", result["skipped"])
        data = json.loads((log_apply_dir()
                           / f"timings-{bid}.json").read_text(encoding="utf-8"))
        marks = {m["name"]: m for m in data.get("marks") or []}
        self.assertIn("verify_unit_test", marks,
                      "跳过的步须发 verify_<step> mark")
        self.assertIsNone(marks["verify_unit_test"].get("dur_s"),
                          "真跳过的步段零 mark 不带 dur_s（非实测耗时）")

    def test_build_step_compiles_in_aosp_cwd(self):
        # 方向 1 判红：build 链步在锁内直跑 AOSP 编译（勿用包装器）——
        # argv 为 bash -c 拼 envsetup+lunch+CCACHE+m targets（verify-cases
        # modules 段并集），cwd=AOSP 工作区根；verify_build 由链步自发实测
        #（见 test_chain_marks_each_standard_step 的 verify_build 断言）
        calls = []
        ctor = mock.Mock(side_effect=lambda argv, **kw: (
            calls.append((argv, kw.get("cwd"))),
            mock.Mock(wait=mock.Mock(return_value=0)))[1])
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK), \
                mock.patch.object(wc, "_aosp_root",
                                  return_value="/tmp/fake-aosp"):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      use_locks=False)
        self.assertEqual(rc, 0)
        build = next((argv, cwd) for argv, cwd in calls
                     if argv[:2] == ["bash", "-c"])
        argv, cwd = build
        self.assertEqual(cwd, "/tmp/fake-aosp",
                         "build 步须在 AOSP 工作区根编译")
        cmd = argv[2]
        self.assertIn("source build/envsetup.sh", cmd)
        self.assertIn("lunch aosp_rpi5-bp1a-userdebug", cmd)
        self.assertIn("CCACHE_DIR=out/ccache", cmd)
        self.assertNotIn("clean", cmd, "INC-001 禁 make clean/clobber")
        import yaml
        cases = yaml.safe_load(Path(
            wc._SCRIPT_DIR.parents[1] / "config" / "verify-cases.yaml"
        ).read_text(encoding="utf-8"))
        for mod in cases["modules"].values():
            for t in (mod.get("targets") or []) + (mod.get("test_targets") or []):
                self.assertIn(t, cmd, f"编译目标 {t} 须入 m 命令")

    def test_build_failure_stops_chain(self):
        # 方向 1 判红：build 步失败即停链（编译不可信时推送/上板无意义），
        # 余验证步记 skipped，report 仍执行落 fail 收据（A1 失败收据契约）
        def run(argv, **kw):
            if argv[:2] == ["bash", "-c"]:
                return mock.Mock(wait=mock.Mock(return_value=1))
            return mock.Mock(wait=mock.Mock(return_value=0))
        ctor = mock.Mock(side_effect=run)
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      use_locks=False)
        self.assertEqual(rc, 1)
        self.assertEqual(result["overall"], "fail")
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "build", "report"])
        for n in ("connect", "push", "unit_test", "acceptance", "package"):
            self.assertIn(n, result["skipped"], f"build 失败后 {n} 须记 skipped")

    def test_lock_held_returns_3_no_run_json(self):
        # 编排锁被占用：exit 3，不执行任何步骤，运行态不落盘
        # （预跑线程仍会启动但被 mock——LockHeld 提前返回不等它收割）
        with mock.patch.object(wc.ws_lock, "verify_locks",
                               side_effect=wc.ws_lock.LockHeld("占用")), \
                mock.patch.object(wc.subprocess, "Popen") as ctor, \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch))
        self.assertEqual(rc, 3)
        self.assertEqual(result["exit_rc"], 3)
        self.assertEqual(result["overall"], "fail")
        ctor.assert_not_called()
        self.assertEqual(list(self.runs.glob("*.json")), [])

    def test_lock_held_requests_yield(self):
        # 方向 1（闲时加固让路协议）：正式任务取锁失败即置让路标志，持锁的
        # idle-hardening 会话在原子步骤边界检查到后收敛让路（不抢占验证中的
        # 正式任务）
        with mock.patch.object(wc.ws_lock, "verify_locks",
                               side_effect=wc.ws_lock.LockHeld("占用")), \
                mock.patch.object(wc.ws_lock, "request_yield") as req_yield, \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch))
        self.assertEqual(rc, 3)
        req_yield.assert_called_once()

    def test_no_batch_skips_acceptance_and_report(self):
        # 无验收源/无收据源（裸三步用法兼容）：acceptance/report 记 skipped
        rc, result, _ = self._run(batch_file=None)
        self.assertEqual(rc, 0)
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "build", "connect", "push", "unit_test"])
        self.assertEqual(result["skipped"], ["acceptance", "package", "report"])
        self.assertIn("acceptance", result["skip_reasons"])
        self.assertIn("report", result["skip_reasons"])

    def test_case_without_batch_runs_acceptance_only(self):
        # 仅 --case：acceptance 执行（无 --batch-file），report 记 skipped
        ctor, _ = _fake_popen(0)
        calls = []
        ctor.side_effect = lambda argv, **kw: (calls.append(argv),
                                               mock.Mock(wait=mock.Mock(return_value=0)))[1]
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(case="lcview-liveness",
                                      use_locks=False)
        self.assertEqual(rc, 0)
        self.assertEqual([s["name"] for s in result["steps"]],
                         ["sync", "build", "connect", "push", "unit_test",
                          "acceptance"])
        self.assertEqual(result["skipped"], ["package", "report"])
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
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, _ = wc.run_chain(batch_file=str(self.batch), build="fail",
                             use_locks=False)
        self.assertEqual(rc, 0)
        rep = next(" ".join(c) for c in calls if "ws_report.py" in c[1])
        self.assertIn("--build fail", rep)

    def test_push_executed_fail_build_fail(self):
        # wsv-13：push 已执行且 rc!=0（含编译产物缺失）→ build=fail
        #（不得机械降级 skip 掩盖编译段失败）
        d = wc._derive_report_args(
            self._steps(("sync", 0), ("connect", 0), ("push", 1)), "fail")
        self.assertEqual((d["result"], d["build"], d["board"]),
                         ("fail", "fail", "fail"))

    def test_coverage_fail_then_acceptance_fail_board_fail(self):
        # 方向 4：coverage 步（只记录不门禁）失败不得抢先成为 failed，使
        # board 判 skip 掩盖其后真实上板失败（acceptance 在板上跑失败 → fail）
        d = wc._derive_report_args(
            self._steps(("sync", 0), ("push", 0), ("unit_test", 0),
                        ("coverage", 1), ("acceptance", 1)), "fail")
        self.assertEqual((d["result"], d["build"], d["board"]),
                         ("fail", "pass", "fail"))
        self.assertIn("acceptance", d["summary"], "链停归因须落到真实失败步")

    def test_coverage_fail_alone_does_not_change_overall(self):
        # 方向 4：coverage 失败不改 overall（只记录不门禁），board 仍全过
        #（无真实上板失败，coverage 失败仅是记录）
        d = wc._derive_report_args(
            self._steps(("sync", 0), ("push", 0), ("unit_test", 0),
                        ("coverage", 1), ("acceptance", 0)), "pass")
        self.assertEqual((d["result"], d["build"], d["board"]),
                         ("pass", "pass", "pass"))
        self.assertIn("全链通过", d["summary"])

    def test_push_not_executed_build_skip(self):
        # wsv-13：push 未执行（sync 失败停链，步骤不在 steps）→ build=skip
        d = wc._derive_report_args(self._steps(("sync", 1)), "fail")
        self.assertEqual((d["result"], d["build"], d["board"]),
                         ("fail", "skip", "skip"))

    def test_push_canceled_build_fail(self):
        # wsv-13：push 超时取消（步骤已执行、rc=None）→ build=fail
        d = wc._derive_report_args(
            [{"name": "push", "rc": None, "canceled": True}], "fail")
        self.assertEqual(d["build"], "fail")


class TestSelfcheckFallbackRcKeys(unittest.TestCase):
    """wsv-01：selfcheck 超时/启动失败兜底文本须覆盖 REQUIRED_RC_KEYS 全集
    （跨模块一致：ws_report 缺任一 *_rc 键即拒写，兜底文本缺键会让故障场景
    以错误的「缺键」门禁判 2，诊断失真）。"""

    def _fallback_text(self, exc):
        with mock.patch.object(wc.subprocess, "run", side_effect=exc):
            return wc._run_selfcheck(timeout=1)

    def _report_reject_stderr(self, selfcheck_text):
        """把兜底文本喂给 ws_report.main（-s 批，result=skip），返回 stderr。
        期望路径：rc 键校验通过 → 按「非零退出码」判 2（而非「缺 *_rc」）。"""
        import ws_report
        with tempfile.TemporaryDirectory() as d:
            with mock.patch.dict("os.environ", {"CDP_PROJECT_ROOT": d}):
                batch = Path(d) / "b.cdp"
                batch.write_text(
                    "-s base:1a2b3c4d5e6f\n"
                    "意图: selfcheck 兜底文本 rc 键覆盖端到端验证用批次（占位"
                    "说明文字拉长长度以满足批次长度预算要求，无实际编辑意图）。\n"
                    "验收: 无\n"
                    "方向: 1) 端到端验证兜底文本能通过 ws_report 缺键门禁判红。\n",
                    encoding="utf-8")
                body = Path(d) / "body.txt"
                body.write_text("## 现场\n", encoding="utf-8")
                err = io.StringIO()
                with contextlib.redirect_stderr(err), \
                        contextlib.redirect_stdout(io.StringIO()):
                    ws_report.main([
                        "--batch-file", str(batch), "--body", str(body),
                        "--result", "skip", "--build", "skip",
                        "--board", "skip", "--summary", "s",
                        "--selfcheck", selfcheck_text])
                return err.getvalue()

    def test_timeout_text_covers_required_rc_keys(self):
        # 超时兜底文本：键集合 == REQUIRED_RC_KEYS 全集（动态同源）
        from selfcheck import REQUIRED_RC_KEYS
        text = self._fallback_text(subprocess.TimeoutExpired("x", 1))
        found = set(re.findall(r"\b(\w+_rc)=\d+\b", text))
        self.assertEqual(found, set(REQUIRED_RC_KEYS))
        self.assertIn("pytest_rc=124", text, "超时 rc 语义值保留")
        # 能通过 ws_report 的 rc 键校验：报错为「非零退出码」（真实语义），
        # 不得再出现「缺 *_rc」的缺键误报
        stderr = self._report_reject_stderr(text)
        self.assertIn("非零退出码", stderr)
        self.assertNotIn("缺 ", stderr)

    def test_oserror_text_covers_required_rc_keys(self):
        # 启动失败兜底文本：同样覆盖全集且通过 rc 键校验
        from selfcheck import REQUIRED_RC_KEYS
        text = self._fallback_text(OSError("adb missing"))
        found = set(re.findall(r"\b(\w+_rc)=\d+\b", text))
        self.assertEqual(found, set(REQUIRED_RC_KEYS))
        self.assertIn("启动失败", text)
        stderr = self._report_reject_stderr(text)
        self.assertIn("非零退出码", stderr)
        self.assertNotIn("缺 ", stderr)


class TestQuickMode(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.runs = Path(self._tmp.name) / "runs"
        self.batch = Path(self._tmp.name) / "b.cdp"
        self.batch.write_text(_BATCH % ("a" * 12), encoding="utf-8")
        envpatcher = mock.patch.dict("os.environ", {}, clear=False)
        envpatcher.start()
        self.addCleanup(envpatcher.stop)
        self.addCleanup(self._tmp.cleanup)

    def test_quick_runs_sync_host_and_selfcheck_no_receipt(self):
        # --quick：只跑 sync + host-tests + selfcheck，不触碰设备、不落收据
        ctor, proc = _fake_popen(0)
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR",
                                  Path(self._tmp.name) / "runs") as runs, \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK), \
                mock.patch.object(wc, "_build_argv",
                                  side_effect=wc._build_argv):
            rc, result = wc.run_quick(use_locks=False)
        names = _script_names(ctor.call_args_list)
        self.assertEqual(names, ["sync_code_to_workspace.py",
                                 "check_host_tests.py"])
        self.assertEqual(rc, 0)
        # 不落运行态/收据
        self.assertFalse(list(runs.glob("*.json")) if runs.exists() else False)

    def test_quick_host_fail_returns_1(self):
        def _popen(argv, **kw):
            proc = mock.Mock()
            proc.wait = mock.Mock(return_value=1
                                  if "check_host_tests" in str(argv) else 0)
            return proc
        with mock.patch.object(wc.subprocess, "Popen", _popen), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, _ = wc.run_quick(use_locks=False)
        self.assertEqual(rc, 1)

    def test_coverage_step_runs_after_unit_test(self):
        ctor, proc = _fake_popen(0)
        with mock.patch.object(wc.subprocess, "Popen", ctor), \
                mock.patch.object(wc, "_RUNS_DIR", self.runs), \
                mock.patch.object(wc, "_run_selfcheck",
                                  return_value=_SELFCHECK_OK):
            rc, result = wc.run_chain(batch_file=str(self.batch),
                                      coverage=True, use_locks=False)
        names = _script_names(ctor.call_args_list)
        self.assertIn("ws_coverage.py", names)
        self.assertGreater(
            [i for i, n in enumerate(names) if n == "ws_coverage.py"][0],
            [i for i, n in enumerate(names) if n == "ws_upload_tests.py"][0])


class TestBuildDryRun(unittest.TestCase):
    """CDP 2026-09-11 方向 1：build 步秒级干跑（source envsetup+lunch 验证
    环境，不跑真 m 编译——build 步 8107948 新增以来一次未执行，首跑一小时
    赌不起）。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.aosp = Path(self._tmp.name) / "aosp"
        self.aosp.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def test_dry_run_success(self):
        # 环境可用：targets 非空 + aosp 存在 + source/lunch 执行成功 → rc 0
        run_mock = mock.Mock(returncode=0, stdout="", stderr="")
        with mock.patch.object(wc, "_aosp_root", return_value=str(self.aosp)), \
                mock.patch.object(wc, "_load_build_targets",
                                  return_value=["a", "b"]), \
                mock.patch.object(wc.subprocess, "run", return_value=run_mock):
            rc = wc.run_build_dry_run("rpi5")
            call = wc.subprocess.run.call_args
        self.assertEqual(rc, 0)
        self.assertEqual(call.args[0][0], "bash")
        self.assertIn("source build/envsetup.sh", call.args[0][2])
        self.assertIn("lunch aosp_rpi5-bp1a-userdebug", call.args[0][2])
        self.assertIn("ANDROID_PRODUCT_OUT", call.args[0][2])
        self.assertNotIn("m ", call.args[0][2], "dry-run 不跑真编译")

    def test_dry_run_missing_targets_returns_2(self):
        # 前置校验红灯：verify-cases 无编译目标 → 返 2（拒干跑）
        with mock.patch.object(wc, "_aosp_root", return_value=str(self.aosp)), \
                mock.patch.object(wc, "_load_build_targets", return_value=[]):
            rc = wc.run_build_dry_run("rpi5")
        self.assertEqual(rc, 2)

    def test_dry_run_missing_aosp_returns_2(self):
        # 前置校验红灯：_aosp_root 目录不存在 → 返 2
        with mock.patch.object(wc, "_aosp_root",
                               return_value=str(self.aosp / "nope")):
            rc = wc.run_build_dry_run("rpi5")
        self.assertEqual(rc, 2)

    def test_dry_run_envsetup_failure_returns_1(self):
        # 执行红灯：source envsetup/lunch 失败 rc=1 → 返 1（环境不可用）
        with mock.patch.object(wc, "_aosp_root", return_value=str(self.aosp)), \
                mock.patch.object(wc, "_load_build_targets",
                                  return_value=["a"]), \
                mock.patch.object(wc.subprocess, "run",
                                  return_value=mock.Mock(returncode=1,
                                                         stdout="",
                                                         stderr="boom")):
            rc = wc.run_build_dry_run("rpi5")
        self.assertEqual(rc, 1)

    def test_build_validate_rules(self):
        # BLD-004/005 静态校验：空 targets 拒；bootimage 在 systemimage 后拒
        self.assertTrue(wc._build_validate([], "x"))
        self.assertTrue(wc._build_validate(["a", "b"], ""))
        self.assertIsNone(wc._build_validate(
            ["bootimage", "systemimage", "vendorimage"], "l"),
            "bootimage 先于 systemimage 合规")
        bad = wc._build_validate(["systemimage", "bootimage"], "l")
        self.assertTrue(bad and "BLD-005" in bad, "bootimage 后于 systemimage 违规")

    def test_main_build_dry_run_flag(self):
        # CLI --build-dry-run 走干跑并返回其 rc（不经整链）
        with mock.patch.object(wc, "run_build_dry_run", return_value=0) as m, \
                mock.patch.object(wc, "_aosp_root", return_value=str(self.aosp)):
            rc = wc.main(["--build-dry-run"])
        self.assertEqual(rc, 0)
        m.assert_called_once_with("rpi5")


if __name__ == "__main__":
    unittest.main()
