import collections
import contextlib
import io
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import selfcheck

# 治理工具默认 fake 结果（run_parallel_tools 返回形态，B2 并行采集后
# 治理段与 pytest 段解耦 mock：用例专注 pytest 序列，治理 rc/结论行由
# 此桩注入；真跑级由 TestParallelTools 单独覆盖解析逻辑）
_FAKE_TOOLS = {
    "refs": (0, "OK: harness/skills + docs 引用完整，无悬空。\n",
             "OK: harness/skills + docs 引用完整，无悬空。"),
    "cfg": (0, "OK: config 检查通过，无违规。\n",
            "OK: config 检查通过，无违规。"),
     "ctr": (0, "OK: contract 检查通过，无违规。\n",
            "OK: contract 检查通过，无违规。"),
    "discipline": (0, "OK: 测试改动无新增 xfail/skip/sleep 重试\n", ""),
    "scan": (0, "OK: 热路径检查器无全树 rglob/os.walk\n", ""),
}


class _FakeProc:
    def __init__(self, returncode, out="", err=""):
        self.returncode = returncode
        self.stdout = out
        self.stderr = err


def _fake_run(seq):
    """桩 subprocess.run：按调用顺序返回假进程；seq 耗尽时兜底返回 ioctl
    检查成功桩（方向 2 接入后 main 二次 run_tool 调 check_ioctl_headers，
    既有用例无需逐个补 ioctl 桩）。"""
    def _run(cmd, **kw):
        if seq:
            return seq.pop(0)
        return _FakeProc(0, "[OK] 一致: vendor_lechao_usbd_config\n")
    return _run


def _fake_tools(**overrides):
    """治理工具桩工厂（E1 样板收敛）：按 key 覆盖返回元组。"""
    tools = {k: tuple(v) for k, v in _FAKE_TOOLS.items()}
    for key, val in overrides.items():
        tools[key] = tuple(val)
    return tools


@contextlib.contextmanager
def _patched_parallel():
    """屏蔽 main 的治理并行段（方向 1 拆分后）：_spawn_tools/_spawn_cmd
    只 Popen 不阻塞、_collect_tools/_collect_cmd 桩注入结果，避免真跑
    refs/cfg ~27s 与 ioctl/manifest 进程拖慢用例（收据不依赖真跑值）。
    """
    with mock.patch.object(selfcheck, "_spawn_tools",
                           return_value={"refs": "p", "cfg": "p"}), \
            mock.patch.object(selfcheck, "_collect_tools",
                              return_value=(_fake_tools(), 1.0, 2.0, 0.1, 0.1)), \
            mock.patch.object(selfcheck, "_spawn_cmd",
                              return_value="p"), \
            mock.patch.object(selfcheck, "_collect_cmd",
                              return_value=(0, "[OK] 一致\n", "", 0.1)):
        yield


@contextlib.contextmanager
def _patched_cli_argv():
    """main() 无参直调走 CLI 语义（lib-01：argv=None → sys.argv[1:]）；
    pytest 进程内 sys.argv 是 pytest 参数会被 argparse 误解析，固定为
    名义 argv（等价 CLI 直跑无参形态）。"""
    with mock.patch.object(sys, "argv", ["selfcheck.py"]):
        yield


class TestSelfcheck(unittest.TestCase):
    def setUp(self):
        # 屏蔽自发 apply_selfcheck 打点（打点不影响 selfcheck 结果断言，
        # 其行为由 TestMarkSelfcheck 单独覆盖）与治理工具真跑（方向 1 后
        # main 经 _spawn_tools/_spawn_cmd 并行拉起 refs/cfg/ioctl/manifest
        # 进程，桩注入结果避免真跑 ~27s/轮）；argv patch 适配 lib-01 CLI
        # 语义（进程内直调 main() 不吃 pytest 的 sys.argv）
        self._mark = mock.patch.object(selfcheck, "_mark_selfcheck")
        self._ctx = _patched_parallel()
        self._argv = mock.patch.object(sys, "argv", ["selfcheck.py"])
        self._mark.start()
        self._ctx.__enter__()
        self._argv.start()
        self.addCleanup(self._argv.stop)

    def tearDown(self):
        self._mark.stop()
        self._ctx.__exit__(None, None, None)

    def test_rc_nonzero_passed_through(self):
        # 方向 6：桩令 pytest 与 refs 均非零，rc 必须如实透出（不经管道）
        fake = _fake_run([
            _FakeProc(1, "1 failed, 119 passed in 5.0s\n"),
        ])
        tools = _fake_tools(refs=(2, "==== 共 3 处悬空引用（exit 1）====\n",
                                  ""))
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake), \
                mock.patch.object(selfcheck, "_collect_tools",
                                  return_value=(tools, 1.0, 2.0, 0.1, 0.1)):
            with redirect_stdout(buf):
                self.assertEqual(selfcheck.main(), 0)
        out = buf.getvalue()
        self.assertIn("pytest_rc=1", out)
        self.assertIn("refs_rc=2", out)
        self.assertIn("1 failed, 119 passed", out)
        self.assertIn("悬空引用", out)

    def test_rc_zero_with_skip_in_summary_no_fake_skipped(self):
        # pytest 通过且摘要含 skipped → 不补 skipped=0（已有计数）
        fake = _fake_run([
            _FakeProc(0, "121 passed, 3 skipped in 6.0s\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("pytest_rc=0", out)
        self.assertIn("3 skipped", out)
        self.assertNotIn("skipped=0", out)

    def test_rc_zero_no_skip_appends_zero(self):
        # 全绿无跳过 → 补 skipped=0（平台跳过数显式可见）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        self.assertIn("skipped=0", buf.getvalue())

    def test_pytest_crash_no_fake_skipped(self):
        # 方向 3：pytest 崩溃（rc 非零且末行无 skipped）→ 不补 skipped=0
        # （兜底会为崩溃的运行伪造计数，使 skipped 门禁永不生效）
        fake = _fake_run([
            _FakeProc(2, "INTERNALERROR> Killed\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("pytest_rc=2", out)
        self.assertNotIn("skipped=0", out)

    def test_stderr_warning_does_not_replace_count_line(self):
        # 方向 1/5：stderr 有告警而 stdout 有计数行 → 计数行仍正确提取
        # （只认 stdout；拼接 stderr 会顶掉计数行，使兜底补 skipped=0 谎报）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n",
                      "warn: 某插件加载失败\nwarn: 忽略\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("531 passed in 27.9s", out)
        self.assertIn("skipped=0", out)
        self.assertNotIn("某插件加载失败", out)

    def test_stderr_only_no_count_no_fake_skipped(self):
        # 方向 1/2/5：stdout 无计数行（计数被 stderr 顶掉/异常）→ 不补 skipped=0，
        # 交 ws_report 缺 skipped 拒写（不伪造也不静默通过）
        fake = _fake_run([
            _FakeProc(0, "\n", "warn: 某插件加载失败\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("pytest_rc=0", out)
        self.assertNotIn("skipped=0", out)
        self.assertNotIn("某插件加载失败", out)

    def test_refs_conclusion_only_from_stdout(self):
        # 方向 3：refs 结论行只取 stdout 末行，stderr 仅附注不参与判定
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        tools = _fake_tools(refs=(0, "OK: 引用完整\n", "warn: 非判定信息\n"))
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake), \
                mock.patch.object(selfcheck, "_collect_tools",
                                  return_value=(tools, 1.0, 2.0, 0.1, 0.1)):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("OK: 引用完整", out)
        self.assertNotIn("非判定信息", out)

    def test_config_contract_rcs_passed_through(self):
        # 方向 4 + B2：--all 双模式 rc 分判红透出（config/contract 结论行
        # 各自拼接，任一非零由 ws_report 判红拒写）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        tools = _fake_tools(
            cfg=(1, "[VIOLATION] x\n==== config: 共 1 处违规（判红）====\n",
                 "==== config: 共 1 处违规（判红）===="),
            ctr=(0, "OK: contract 检查通过，无违规。\n",
                 "OK: contract 检查通过，无违规。"))
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake), \
                mock.patch.object(selfcheck, "_collect_tools",
                                  return_value=(tools, 1.0, 2.0, 0.1, 0.1)):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("config_rc=1", out)
        self.assertIn("共 1 处违规", out)
        self.assertIn("contract_rc=0", out)

    # ── 方向 1：xdist 可导入时 -n auto，导入不到回落串行 ────────────────
    def _run_capture_cmd(self, fake):
        """桩 subprocess.run 并捕获 pytest 命令；返回捕获列表。"""
        seen = []

        def _run(cmd, **kw):
            seen.append(cmd)
            return fake(cmd, **kw)

        with mock.patch.object(selfcheck.subprocess, "run", side_effect=_run):
            with redirect_stdout(io.StringIO()):
                selfcheck.main()
        return seen

    def test_xdist_importable_uses_parallel(self):
        # xdist 可导入 → pytest 命令加 -n auto（并行提速，计数行正则不动）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        with mock.patch.dict(sys.modules, {"xdist": mock.Mock()}):
            seen = self._run_capture_cmd(fake)
        pytest_cmd = seen[0]
        self.assertIn("-n", pytest_cmd)
        self.assertIn("auto", pytest_cmd)

    def test_xdist_missing_falls_back_serial(self):
        # xdist 不可导入（sys.modules 置 None → import 抛 ImportError）
        # → pytest 命令照旧串行（无 -n auto）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        with mock.patch.dict(sys.modules, {"xdist": None}):
            seen = self._run_capture_cmd(fake)
        pytest_cmd = seen[0]
        self.assertNotIn("-n", pytest_cmd)

    def test_pytest_timeout_returns_124(self):
        # B3：pytest 挂起超时 → rc=124（约定超时标记），不无限阻塞
        def _timeout(cmd, **kw):
            raise selfcheck.subprocess.TimeoutExpired(cmd, 900)
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run",
                               side_effect=_timeout):
            with redirect_stdout(buf):
                selfcheck.main()
        self.assertIn("pytest_rc=124", buf.getvalue())

    def test_host_collect_timeout_above_module_timeout(self):
        # 方向 2：host 收口超时 = _HOST_TIMEOUT_S（650）透传 _collect_cmd，
        # 仍须大于 check_host_tests 单模块 make 超时（300s）——否则外层收口
        # rc=124 抢先截断内层 TimeoutExpired，丢失超时归因（判红同效但诊断
        # 退化）；650 也兜住两模块顺序跑最坏 ~600s
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        seen = {}

        def _collect(proc, name, **kw):
            seen[name] = kw.get("timeout")
            return (0, "[OK] 一致\n", "", 0.1)

        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake), \
                mock.patch.object(selfcheck, "_collect_cmd",
                                  side_effect=_collect):
            with redirect_stdout(buf):
                selfcheck.main()
        self.assertEqual(selfcheck._HOST_TIMEOUT_S, 650)
        self.assertEqual(seen.get("host"), 650)
        self.assertGreater(seen.get("host"), 300)


class TestParallelTools(unittest.TestCase):
    """B2：run_parallel_tools 的 --all 输出解析（mock Popen，不真跑治理
    进程——真跑级由 selfcheck main 实际运行覆盖）。"""

    def test_all_mode_rc_split(self):
        # --all 末尾机器 rc 行分解析：config/contract 各自 rc 与结论行
        out = ("[VIOLATION] x\n==== config: 共 1 处违规（判红）====\n"
               "OK: contract 检查通过，无违规。\n"
               "config_rc=1\ncontract_rc=0\n")
        proc = mock.Mock()
        proc.communicate.return_value = (out, "")
        proc.returncode = 1
        with mock.patch.object(selfcheck.subprocess, "Popen",
                               return_value=proc):
            tools = selfcheck.run_parallel_tools()
        self.assertEqual(tools["cfg"][0], 1)
        self.assertEqual(tools["ctr"][0], 0)
        self.assertEqual(tools["cfg"][2], "==== config: 共 1 处违规（判红）====")
        self.assertEqual(tools["ctr"][2], "OK: contract 检查通过，无违规。")
        # contract 段切分不含 config 段与机器行
        self.assertNotIn("[VIOLATION]", tools["ctr"][1])
        self.assertNotIn("config_rc=", tools["ctr"][1])

    def test_all_mode_tool_timeout_returns_124(self):
        # 治理工具挂起超时 → kill + rc=124（不无限阻塞自检）
        proc = mock.Mock()
        # refs 超时路径：communicate×2（首超时 + kill 后回收）；其余三进程正常
        proc.communicate.side_effect = [
            selfcheck.subprocess.TimeoutExpired("cmd", 120), ("", ""),
            ("OK: config 检查通过，无违规。\nconfig_rc=0\ncontract_rc=0\n", ""),
            ("OK: 测试改动无新增 xfail/skip/sleep 重试\n", ""),
            ("OK: 热路径检查器无全树 rglob/os.walk\n", ""),
        ]
        with mock.patch.object(selfcheck.subprocess, "Popen",
                               return_value=proc):
            with contextlib.redirect_stderr(io.StringIO()):
                tools = selfcheck.run_parallel_tools()
        rc = tools["refs"][0] if tools["refs"][0] == 124 else tools["cfg"][0]
        self.assertEqual(rc, 124)
        proc.kill.assert_called_once()

    def test_collect_tools_returns_split_durs(self):
        # 方向 2：_collect_tools 返回 (tools, refs_dur, cfg_dur, dis_dur,
        # scan_dur)，durs 拆开 refs/cfg/discipline/scan 各自自报（合并 tools
        # 无法定位慢点归因）；收口顺序 refs 先、cfg 后，用 side_effect 逐个注入
        out = ("[VIOLATION] x\n==== config: 共 1 处违规（判红）====\n"
               "OK: contract 检查通过，无违规。\nconfig_rc=1\ncontract_rc=0\n")
        refs_res = (1, "==== 共 3 处悬空引用 ====\n", "", 0.5)
        cfg_res = (1, out, "", 2.5)
        dis_res = (0, "OK: 测试改动无新增禁戒\n", "", 0.2)
        scan_res = (0, "OK: 热路径无全树 rglob\n", "", 0.3)
        with mock.patch.object(selfcheck, "_collect_cmd",
                               side_effect=[refs_res, cfg_res,
                                            dis_res, scan_res]):
            (tools, refs_dur, cfg_dur, dis_dur, scan_dur) = \
                selfcheck._collect_tools({"refs": "p", "cfg": "p",
                                          "discipline": "p", "scan": "p"})
        self.assertEqual(refs_dur, 0.5)
        self.assertEqual(cfg_dur, 2.5)
        self.assertEqual(dis_dur, 0.2)
        self.assertEqual(scan_dur, 0.3)
        self.assertEqual(tools["refs"][0], 1)
        self.assertEqual(tools["cfg"][0], 1)
        self.assertEqual(tools["ctr"][0], 0)
        self.assertEqual(tools["discipline"][0], 0)
        self.assertEqual(tools["scan"][0], 0)

    def test_collect_cmd_timeout_kills_124(self):
        # 方向 1：_collect_cmd 超时 → kill + rc=124（约定超时标记，不无限
        # 阻塞自检收口）；dur 自 Popen 时刻起（方向 4，_spawn_t0 缺省回落
        # 收口时刻）
        proc = mock.Mock()
        proc._spawn_t0 = 0.0
        proc.communicate.side_effect = [
            selfcheck.subprocess.TimeoutExpired("cmd", 120), ("", "")]
        with contextlib.redirect_stderr(io.StringIO()):
            rc, out, err, dur = selfcheck._collect_cmd(proc, "ioctl")
        self.assertEqual(rc, 124)
        self.assertIn("timeout after", err)
        proc.kill.assert_called_once()

    def test_collect_cmd_dur_uses_exit_not_collect(self):
        # 方向 3：dur 取进程退出时刻（wait 线程 _exit_t0）减 spawn，而非收口
        # 时刻——收口在 pytest 之后致六项 durs 恒等 pytest 总时长
        now = time.time()
        proc = mock.Mock()
        proc._spawn_t0 = now - 7.0   # 7s 前启动
        proc._exit_t0 = now - 0.5    # 5.5s 后退出（早于收口）
        proc.communicate.return_value = ("out", "")
        proc.returncode = 0
        rc, out, err, dur = selfcheck._collect_cmd(proc, "manifest")
        self.assertAlmostEqual(dur, 6.5, places=1)


class TestMarkSelfcheck(unittest.TestCase):
    """方向 1：自检跑完自发 mark apply_selfcheck（emit_mark 进程内直调，
    batch 回落识别，失败不阻断）。"""

    def test_main_marks_after_selfcheck(self):
        # main() 完成自检后调用 _mark_selfcheck（打点入 cdp_timing mark 链）。
        # 本类无 setUp 桩，须显式屏蔽方向 1 并行段（_spawn_tools/_spawn_cmd
        # 真拉起治理进程会拖慢用例触发 slow_guard 判红）与 CLI argv（lib-01）
        with mock.patch.object(selfcheck, "_mark_selfcheck") as m, \
                _patched_parallel(), _patched_cli_argv():
            fake = _fake_run([
                _FakeProc(0, "531 passed in 27.9s\n"),
            ])
            with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
                with redirect_stdout(io.StringIO()):
                    selfcheck.main()
        m.assert_called_once()

    def test_mark_calls_emit_mark_with_name(self):
        # _mark_selfcheck 进程内直调 cdp_timing.emit_mark（B8 胶水收敛），
        # 不显式 batch_id（交 emit_mark 回落：CDP_BATCH_ID > current-batch.json）
        fake_ct = mock.Mock()
        with mock.patch.object(selfcheck, "cdp_timing", fake_ct):
            selfcheck._mark_selfcheck(dur_s=1.5)
        fake_ct.emit_mark.assert_called_once_with("apply_selfcheck", dur_s=1.5)

    def test_mark_failure_not_blocking(self):
        # 发点失败（emit_mark 返 False）不抛异常不阻断（自检结果与打点解耦）
        fake_ct = mock.Mock()
        fake_ct.emit_mark.return_value = False
        with mock.patch.object(selfcheck, "cdp_timing", fake_ct):
            selfcheck._mark_selfcheck()  # 不抛异常即通过
        fake_ct.emit_mark.assert_called_once()


class TestMarkSelfcheckDegraded(unittest.TestCase):
    """方向 5：CI 无打点指针降级——CDP_PROJECT_ROOT 指空临时根（无 timings
    文件、无 CDP_BATCH_ID）时 emit_mark 静默返 False，不抛异常、不阻断
    自检（进程内直调，无真子进程）。"""

    def test_mark_selfcheck_no_pointer_no_raise(self):
        root = tempfile.TemporaryDirectory()
        try:
            env = dict(os.environ)
            env["CDP_PROJECT_ROOT"] = root.name
            env.pop("CDP_BATCH_ID", None)
            with mock.patch.dict(os.environ, env):
                with contextlib.redirect_stderr(io.StringIO()):
                    selfcheck._mark_selfcheck()
        finally:
            root.cleanup()


class TestCheckPythonEnv(unittest.TestCase):
    """check_python_env：Python 版本（>=3.8）与 requirements.txt 依赖探测。"""

    def setUp(self):
        # 与 TestSelfcheck 同款桩：屏蔽打点与治理工具真跑（方向 1 后 main
        # 并行拉起 refs/cfg/ioctl/manifest 进程，会拖慢用例；本组用例只
        # 关注 pyenv 段，治理结果以桩注入）；argv patch 适配 lib-01
        self._mark = mock.patch.object(selfcheck, "_mark_selfcheck")
        self._ctx = _patched_parallel()
        self._argv = mock.patch.object(sys, "argv", ["selfcheck.py"])
        self._mark.start()
        self._ctx.__enter__()
        self._argv.start()
        self.addCleanup(self._argv.stop)

    def tearDown(self):
        self._mark.stop()
        self._ctx.__exit__(None, None, None)

    def test_healthy_env_reports_ok(self):
        # 真实环境（python>=3.8 且 requirements.txt 各依赖可导入）→ ok=True，
        # 汇总行含版本号与依赖名
        ok, summary = selfcheck.check_python_env()
        self.assertTrue(ok)
        self.assertIn("python=", summary)
        self.assertIn("PyYAML", summary)
        self.assertNotIn("MISSING", summary)

    def test_missing_dep_reported_not_ok(self):
        # 依赖导入失败（不存在的包）→ ok=False，汇总行点名 MISSING 依赖
        # （失败不抛异常，以汇总行形式透出）
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                         encoding="utf-8") as f:
            f.write("nonexistent_pkg_zz>=1.0\n")
            path = f.name
        try:
            ok, summary = selfcheck.check_python_env(req_path=path)
        finally:
            os.unlink(path)
        self.assertFalse(ok)
        self.assertIn("nonexistent_pkg_zz>=1.0", summary)
        self.assertIn("MISSING", summary)

    def test_old_python_version_not_ok(self):
        # 版本低于 3.8 → ok=False 且汇总行标注 TOO_OLD（版本结论可见）
        _FakeVI = collections.namedtuple("_FakeVI", "major minor micro")
        with mock.patch.object(selfcheck.sys, "version_info",
                               _FakeVI(3, 7, 15)):
            ok, summary = selfcheck.check_python_env()
        self.assertFalse(ok)
        self.assertIn("python=3.7.15", summary)
        self.assertIn("TOO_OLD", summary)

    def test_main_includes_pyenv_segment(self):
        # main 汇总输出纳入 pyenv_rc 与环境摘要（环境破损由 rc 非零透出，
        # 交 ws_report 全 *_rc 扫描判红）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("pyenv_rc=0", out)
        self.assertIn("python=", out)

    def test_main_includes_ioctl_segment(self):
        # 方向 2：selfcheck 接入 check_ioctl_headers → 输出含 ioctl_rc=0
        # 与一致性结论行（ioctl 走 _spawn_cmd/_collect_cmd 并行段，由
        # _patched_parallel 桩返成功）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("ioctl_rc=0", out)
        self.assertIn("一致", out)

    def test_ioctl_nonzero_passed_through(self):
        # 方向 2：ioctl 头漂移/双空 → ioctl_rc=1 非零透出（交 ws_report
        # 全 *_rc 扫描判红拒写，不得静默假绿）；main 收口顺序 ioctl 先、
        # manifest 后，用 side_effect 逐个注入
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        ioctl_res = (1, "签名漂移: vendor_lechao_usbd_config\n", "", 0.1)
        manifest_res = (0, "[OK] 一致\n", "", 0.1)
        ruff_res = (0, "All checks passed!\n", "", 0.1)
        host_res = (0, "OK: 内核 host 单测全部通过\n", "", 0.1)
        opencode_res = (0, "[INFO] 所有校验通过\n", "", 0.1)
        metrics_res = (0, "metrics_rc=0\n", "", 0.1)
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake), \
                mock.patch.object(selfcheck, "_collect_cmd",
                                  side_effect=[ioctl_res, ruff_res,
                                               host_res, opencode_res,
                                               manifest_res, metrics_res]):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("ioctl_rc=1", out)
        self.assertIn("签名漂移", out)

    def test_main_includes_manifest_segment(self):
        # 方向 2：selfcheck 接入 gen_manifest --check-only → 输出含 manifest_rc=0
        # 与结论行（manifest 走并行段，由 _patched_parallel 桩返成功）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("manifest_rc=0", out)

    def test_manifest_nonzero_passed_through(self):
        # 方向 2：code/rpi5 manifest 未登记/有变化 → gen_manifest --check-only
        # 判红，manifest_rc=1 非零透出（交 ws_report 判红拒写）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        ioctl_res = (0, "[OK] 一致\n", "", 0.1)
        manifest_res = (1, "[ERROR] manifest 未登记 1 个 code/rpi5 文件\n", "", 0.1)
        ruff_res = (0, "All checks passed!\n", "", 0.1)
        host_res = (0, "OK: 内核 host 单测全部通过\n", "", 0.1)
        opencode_res = (0, "[INFO] 所有校验通过\n", "", 0.1)
        metrics_res = (0, "metrics_rc=0\n", "", 0.1)
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake), \
                mock.patch.object(selfcheck, "_collect_cmd",
                                  side_effect=[ioctl_res, ruff_res,
                                               host_res, opencode_res,
                                               manifest_res, metrics_res]):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("manifest_rc=1", out)

    def test_main_includes_ruff_segment(self):
        # P0-B：selfcheck 接入 check_ruff → 输出含 ruff_rc=0 与结论行
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        ioctl_res = (0, "[OK] 一致\n", "", 0.1)
        manifest_res = (0, "[OK] 一致\n", "", 0.1)
        ruff_res = (0, "All checks passed!\n", "", 0.1)
        host_res = (0, "OK: 内核 host 单测全部通过\n", "", 0.1)
        opencode_res = (0, "[INFO] 所有校验通过\n", "", 0.1)
        metrics_res = (0, "metrics_rc=0\n", "", 0.1)
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake), \
                mock.patch.object(selfcheck, "_collect_cmd",
                                  side_effect=[ioctl_res, ruff_res,
                                               host_res, opencode_res,
                                               manifest_res, metrics_res]):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("ruff_rc=0", out)
        self.assertIn("All checks passed!", out)

    def test_main_includes_host_segment(self):
        # P0-A：selfcheck 接入 check_host_tests → 输出含 host_rc=0 与结论行
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        ioctl_res = (0, "[OK] 一致\n", "", 0.1)
        manifest_res = (0, "[OK] 一致\n", "", 0.1)
        ruff_res = (0, "All checks passed!\n", "", 0.1)
        host_res = (0, "OK: 内核 host 单测全部通过\n", "", 0.1)
        opencode_res = (0, "[INFO] 所有校验通过\n", "", 0.1)
        metrics_res = (0, "metrics_rc=0\n", "", 0.1)
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake), \
                mock.patch.object(selfcheck, "_collect_cmd",
                                  side_effect=[ioctl_res, ruff_res,
                                               host_res, opencode_res,
                                               manifest_res, metrics_res]):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("host_rc=0", out)
        self.assertIn("内核 host 单测全部通过", out)

    def test_main_includes_opencode_segment(self):
        # 方向 6：selfcheck 接入 validate_opencode_server → 输出含 opencode_rc=0
        # 与结论行（opencode 走并行段，由 _patched_parallel 桩返成功）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("opencode_rc=0", out)
        self.assertIn("一致", out)

    def test_opencode_nonzero_passed_through(self):
        # 方向 6：opencode-server 脚本破坏（|| true 吞错/EnvironmentFile 硬编码）
        # → opencode_rc=1 非零透出（交 ws_report 全 *_rc 判红拒写）
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        ioctl_res = (0, "[OK] 一致\n", "", 0.1)
        manifest_res = (0, "[OK] 一致\n", "", 0.1)
        ruff_res = (0, "All checks passed!\n", "", 0.1)
        host_res = (0, "OK: 内核 host 单测全部通过\n", "", 0.1)
        opencode_res = (1, "[ERROR] 校验失败: EnvironmentFile 硬编码 %h\n", "", 0.1)
        metrics_res = (0, "metrics_rc=0\n", "", 0.1)
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake), \
                mock.patch.object(selfcheck, "_collect_cmd",
                                  side_effect=[ioctl_res, ruff_res,
                                               host_res, opencode_res,
                                               manifest_res, metrics_res]):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("opencode_rc=1", out)
        self.assertIn("EnvironmentFile 硬编码", out)

    def test_main_includes_checker_durations(self):
        # 方向 2 + 6：逐检查器耗时入输出行（durs: py/refs/cfg/pyenv/ioctl/
        # manifest，refs 与 cfg 拆开各自自报）；*_dur 前缀不匹配 ws_report
        # 的 *_rc 判红正则不干扰
        fake = _fake_run([
            _FakeProc(0, "531 passed in 27.9s\n"),
        ])
        buf = io.StringIO()
        with mock.patch.object(selfcheck.subprocess, "run", side_effect=fake):
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        m = re.search(r"durs: py=[0-9.]+ refs=[0-9.]+ cfg=[0-9.]+ "
                      r"pyenv=[0-9.]+ ioctl=[0-9.]+ manifest=[0-9.]+", out)
        self.assertIsNotNone(m, "自检输出须含逐检查器耗时 durs 段（refs/cfg 拆开）")
        # 耗时不会误成 *_rc 判红键（ws_report 正则 \w+_rc= 不匹配 *_dur=）
        self.assertNotRegex(out, r"\w+_dur=(\d+)")

    def test_main_spawns_all_tools_before_pytest(self):
        # 方向 1：main 先 Popen 全部治理（_spawn_tools + 两次 _spawn_cmd）
        # 再跑 pytest，保证治理与 pytest 全重叠（顺序错则单轮多耗 ~26s）
        calls = []

        def _spawn_tools():
            calls.append("tools")
            return {"refs": "p", "cfg": "p"}

        def _spawn_cmd(cmd):
            calls.append("spawn")
            return "p"

        def _run(cmd, **kw):
            calls.append("pytest")
            return _FakeProc(0, "531 passed in 27.9s\n")

        buf = io.StringIO()
        with mock.patch.object(selfcheck, "_spawn_tools",
                               side_effect=_spawn_tools), \
                mock.patch.object(selfcheck, "_spawn_cmd",
                                  side_effect=_spawn_cmd), \
                mock.patch.object(selfcheck.subprocess, "run",
                                  side_effect=_run):
            with redirect_stdout(buf):
                selfcheck.main()
        # 顺序固定：refs/cfg 并行段启动 → ioctl Popen → ruff Popen →
        # host Popen → metrics Popen → opencode Popen → pytest（重叠开始；
        # gen_manifest 延后至 host 收口后单独跑——host 编译产物与 manifest
        # 扫描并发竞态 KIR-002 修复，见 selfcheck.py）
        self.assertEqual(calls[:7], ["tools", "spawn", "spawn", "spawn",
                                     "spawn", "spawn", "pytest"])


class TestFlakeRerun(unittest.TestCase):
    """方向 1：偶现失败机械判定（全量红 → 全新进程单跑 → KIR-002 抖动登记
    放行 / KIR-001 不顺延 / 单跑仍红阻塞）。"""

    def test_failed_nodeids_extracts(self):
        # 从 pytest -q 输出提取失败用例 nodeid（FAILED <nodeid> - reason 行）
        out = ("...\nFAILED harness/lib/tests/test_x.py::TestX::test_y - AssertionError\n"
               "FAILED harness/lib/tests/test_z.py::TestZ::test_w - boom\n")
        self.assertEqual(selfcheck._failed_nodeids(out),
                         ["harness/lib/tests/test_x.py::TestX::test_y",
                          "harness/lib/tests/test_z.py::TestZ::test_w"])

    def test_rerun_failures_flaky_registers_and_notes(self):
        # 全量红用例单跑绿（KIR-002 抖动，nodeid 不在本批 git diff 内）→
        # 自动登记 flake known-issue 并返回放行标注（不阻塞本轮）
        py_out = "FAILED harness/lib/tests/test_x.py::TestX::test_y - AssertionError\n"
        with mock.patch.object(selfcheck, "_git_changed_files", return_value=set()), \
                mock.patch.object(selfcheck, "_register_flake_issue",
                                  return_value=("harness/lib/tests/test_x.py::TestX::test_y",
                                                1, "abc123")) as reg, \
                mock.patch.object(selfcheck.subprocess, "run",
                                  return_value=mock.Mock(returncode=0)):
            notes, ki001 = selfcheck._rerun_failures(py_out)
        self.assertEqual(len(notes), 1)
        self.assertEqual(notes[0][0], "harness/lib/tests/test_x.py::TestX::test_y")
        self.assertEqual(ki001, [])
        reg.assert_called_once_with("harness/lib/tests/test_x.py::TestX::test_y")

    def test_rerun_failures_ki001_not_deferred(self):
        # KIR-001：失败用例命中本批 git diff（引入嫌疑）→ 不得顺延，即便
        # 单跑绿也只记 ki001_hits（pytest_rc 保持非零阻塞）
        py_out = "FAILED harness/lib/tests/test_x.py::TestX::test_y - AssertionError\n"
        with mock.patch.object(selfcheck, "_git_changed_files",
                               return_value={"harness/lib/tests/test_x.py"}), \
                mock.patch.object(selfcheck.subprocess, "run",
                                  return_value=mock.Mock(returncode=0)):
            notes, ki001 = selfcheck._rerun_failures(py_out)
        self.assertEqual(notes, [])
        self.assertEqual(ki001, ["harness/lib/tests/test_x.py::TestX::test_y"])

    def test_rerun_failures_still_red_blocking(self):
        # 单跑仍红 → 真回归阻塞（不登记不顺延，pytest_rc 保持非零）
        py_out = "FAILED harness/lib/tests/test_x.py::TestX::test_y - AssertionError\n"
        with mock.patch.object(selfcheck, "_git_changed_files", return_value=set()), \
                mock.patch.object(selfcheck.subprocess, "run",
                                  return_value=mock.Mock(returncode=1)):
            notes, ki001 = selfcheck._rerun_failures(py_out)
        self.assertEqual((notes, ki001), ([], []))

    def test_main_flaky_passes_round_writes_flake(self):
        # main 集成：pytest 全量红但单跑全绿（KIR-002 抖动）→ 输出
        # pytest_rc=0（放行本轮）+ flake 标注（用例/轮次/首现批次/复现命令）
        # + skipped 可见（summary 定位到计数行才拼，lib-06）+ 无 failed
        # 非零计数（ws_report 文本防线不误拒）
        fake = _fake_run([
            _FakeProc(1, "1 failed, 1153 passed, 3 skipped in 27.0s\n"),
        ])
        flake = [("harness/lib/tests/test_x.py::TestX::test_y", 2, "abc123")]
        buf = io.StringIO()
        with mock.patch.object(selfcheck, "_rerun_failures",
                               return_value=(flake, [])), \
                mock.patch.object(selfcheck.subprocess, "run", side_effect=fake), \
                _patched_parallel(), _patched_cli_argv():
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("pytest_rc=0", out)
        self.assertIn("flake:", out)
        self.assertIn("round=2", out)
        self.assertIn("first=abc123", out)
        self.assertIn('cmd="python3 -m pytest harness/lib/tests/test_x.py::TestX::test_y -q"', out)
        self.assertIn("skipped=3", out)
        # ws_report 文本防线：不得残留 "1 failed" 等 failed 非零计数
        self.assertNotRegex(out, r"\b[1-9]\d*\s*failed\b")

    def test_main_flaky_no_skipped_line_fact_zero(self):
        # wsv2-01：flake 放行分支 summary 存在但缺 skipped 计数行 → 补
        # skipped=0（事实值）。lib-06 曾整体不补，致缺键被 ws_report
        # 「缺 skipped 计数」拒写、KIR-002 放行收据自锁；崩溃/截断（摘要
        # 缺失）才维持不补交 ws_report 拒写
        fake = _fake_run([
            _FakeProc(1, "1 failed, 1153 passed in 27.0s\n"),  # 无 skipped
        ])
        flake = [("harness/lib/tests/test_x.py::TestX::test_y", 2, "abc123")]
        buf = io.StringIO()
        with mock.patch.object(selfcheck, "_rerun_failures",
                               return_value=(flake, [])), \
                mock.patch.object(selfcheck.subprocess, "run", side_effect=fake), \
                _patched_parallel(), _patched_cli_argv():
            with redirect_stdout(buf):
                selfcheck.main()
        out = buf.getvalue()
        self.assertIn("pytest_rc=0", out)
        self.assertIn("flake:", out)
        self.assertIn("skipped=0", out)


class TestCliArgv(unittest.TestCase):
    """lib-01：CLI 直跑（sys.exit(main())，argv=None）时 argparse 仍须执行。"""

    def test_main_no_arg_uses_sys_argv(self):
        # argv=None → 回落 sys.argv[1:]：--mode quick 被真实解析，pytest
        # 按 quick 推导目标跑（修复前 `if argv:` 恒 False，quick 被静默忽略
        # 恒跑全量，SKILL.md 文档化用法失效）
        seen = []

        def _run(cmd, **kw):
            seen.append(cmd)
            return _FakeProc(0, "12 passed in 1.0s\n")

        fake_argv = ["selfcheck.py", "--mode", "quick"]
        with mock.patch.object(sys, "argv", fake_argv), \
                mock.patch.object(selfcheck, "_quick_test_targets",
                                  return_value=["harness/lib/tests/test_selfcheck.py"]), \
                mock.patch.object(selfcheck.subprocess, "run", side_effect=_run), \
                _patched_parallel(), \
                mock.patch.object(selfcheck, "_mark_selfcheck"), \
                mock.patch.object(selfcheck, "_ensure_edit_close_mark"):
            with redirect_stdout(io.StringIO()):
                selfcheck.main()
        self.assertIn("harness/lib/tests/test_selfcheck.py", seen[0])

    def test_cli_help_exits_zero_without_full_run(self):
        # CLI 直跑形态轻量验证：python3 selfcheck.py --help 须经 argparse
        # 输出 usage 并 exit 0（修复前 argv=None 不解析 → --help 被忽略、
        # 直接真跑全量 pytest）；--help 在 main 早期 SystemExit，不触发全量
        script = Path(selfcheck.__file__).resolve()
        r = subprocess.run([sys.executable, str(script), "--help"],
                           capture_output=True, text=True,
                           encoding="utf-8", timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("--mode", r.stdout)
        self.assertIn("quick", r.stdout)


class TestFlakeIssueId(unittest.TestCase):
    """lib-02：flake issue_id 稳定摘要（sha256，跨进程一致）。"""

    def test_issue_id_format_and_prefix(self):
        # 格式 KI-FLAKE-{first_batch}-{6位hex}，摘要为 sha256 稳定产物
        aid = selfcheck._flake_issue_id(
            "harness/lib/tests/test_x.py::TestX::test_y", "abc123")
        self.assertRegex(aid, r"^KI-FLAKE-abc123-[0-9a-f]{6}$")

    def test_issue_id_stable_same_process(self):
        a = selfcheck._flake_issue_id("harness/lib/tests/test_x.py::T::t", "b1")
        b = selfcheck._flake_issue_id("harness/lib/tests/test_x.py::T::t", "b1")
        self.assertEqual(a, b)

    def test_issue_id_stable_across_processes(self):
        # 两个独立子进程（PYTHONHASHSEED 随机化互不相同）取同一 nodeid
        # 摘要须一致（此前 abs(hash(nodeid)) 跨进程不稳定致 KIR-002 归链失效）
        code = (
            "import sys\n"
            "sys.path.insert(0, sys.argv[1])\n"
            "import selfcheck\n"
            "print(selfcheck._flake_issue_id("
            "'harness/lib/tests/test_x.py::T::t', 'b1'))\n"
        )
        lib_dir = str(Path(selfcheck.__file__).resolve().parent)
        outs = []
        for _ in range(2):
            r = subprocess.run([sys.executable, "-c", code, lib_dir],
                               capture_output=True, text=True,
                               encoding="utf-8", timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            outs.append(r.stdout.strip())
        self.assertTrue(outs[0], "摘要输出为空")
        self.assertEqual(outs[0], outs[1])


class TestFlakeHistory(unittest.TestCase):
    """lib-03：_flake_history 行级精确匹配（参数化用例前缀不误命中）。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        issues = self.root / "data" / "known-issues"
        issues.mkdir(parents=True)
        self.nodeid = "harness/lib/tests/test_x.py::TestX::test_bar[param0]"
        (issues / "ki-a.md").write_text(
            f"- nodeid: {self.nodeid}\n"
            "- kind: flake\n"
            "- round: 3\n"
            "- first_seen_batch: b9\n",
            encoding="utf-8")

    def test_parametrized_prefix_not_matched(self):
        # 查询 test_bar 不得命中条目 test_bar[param0]（此前子串匹配误命中
        # round=3 致轮次虚高）
        with mock.patch.object(selfcheck, "ROOT", self.root):
            self.assertEqual(selfcheck._flake_history(
                "harness/lib/tests/test_x.py::TestX::test_bar"), (0, ""))

    def test_exact_nodeid_matches_round_and_batch(self):
        with mock.patch.object(selfcheck, "ROOT", self.root):
            self.assertEqual(selfcheck._flake_history(self.nodeid),
                             (3, "b9"))


class TestRerunTwoPass(unittest.TestCase):
    """lib-04：_rerun_failures 两遍扫描——存在真回归时不登记任何条目。"""

    def test_second_regression_blocks_registration(self):
        # 两个 flake 候选：第一个单跑绿、第二个真回归（单跑红）→ 整体阻塞，
        # 第一个不得落盘（此前循环内先登记先放行，后续红时已登记条目成幽灵）
        py_out = ("FAILED harness/lib/tests/test_x.py::TestX::test_a - boom\n"
                  "FAILED harness/lib/tests/test_y.py::TestY::test_b - boom\n")
        procs = [_FakeProc(0, "1 passed in 0.1s\n"),
                 _FakeProc(1, "1 failed in 0.1s\n")]
        with mock.patch.object(selfcheck, "_git_changed_files",
                               return_value=set()), \
                mock.patch.object(selfcheck, "_register_flake_issue") as reg, \
                mock.patch.object(selfcheck.subprocess, "run",
                                  side_effect=procs):
            notes, ki001 = selfcheck._rerun_failures(py_out)
        self.assertEqual((notes, ki001), ([], []))
        reg.assert_not_called()

    def test_all_green_registers_both(self):
        # 全部单跑绿 → 两遍扫描后统一登记（顺序与原语义一致）
        py_out = ("FAILED harness/lib/tests/test_x.py::TestX::test_a - boom\n"
                  "FAILED harness/lib/tests/test_y.py::TestY::test_b - boom\n")
        procs = [_FakeProc(0, "1 passed in 0.1s\n"),
                 _FakeProc(0, "1 passed in 0.1s\n")]
        notes_fake = [("nid_a", 1, "b"), ("nid_b", 1, "b")]
        with mock.patch.object(selfcheck, "_git_changed_files",
                               return_value=set()), \
                mock.patch.object(selfcheck, "_register_flake_issue",
                                  side_effect=notes_fake) as reg, \
                mock.patch.object(selfcheck.subprocess, "run",
                                  side_effect=procs):
            notes, ki001 = selfcheck._rerun_failures(py_out)
        self.assertEqual(len(notes), 2)
        self.assertEqual(ki001, [])
        self.assertEqual(reg.call_count, 2)


class TestGitChangedFilesTimeout(unittest.TestCase):
    """lib-11：_git_changed_files subprocess 带 timeout，超时空集回落。"""

    def test_timeout_returns_empty_set(self):
        # git diff 挂死超时 → warn + 空集（不抛异常拖垮自检链）
        def _timeout(cmd, **kw):
            raise selfcheck.subprocess.TimeoutExpired(cmd, 30)

        with mock.patch.object(selfcheck.subprocess, "run",
                               side_effect=_timeout):
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(selfcheck._git_changed_files(), set())


class TestQuickMode(unittest.TestCase):
    """方向 2：quick 档（git diff 推导受影响测试；推导不出回落全量）。"""

    def test_quick_targets_maps_module_to_test(self):
        # harness/lib/x.py → harness/lib/tests/test_x.py
        with mock.patch.object(selfcheck, "_git_changed_files",
                               return_value={"harness/lib/selfcheck.py"}):
            self.assertEqual(selfcheck._quick_test_targets(),
                             ["harness/lib/tests/test_selfcheck.py"])

    def test_quick_targets_direct_test_file(self):
        # 改动本身是 tests/test_*.py → 直接跑
        with mock.patch.object(selfcheck, "_git_changed_files",
                               return_value={"harness/lib/tests/test_x.py"}):
            self.assertEqual(selfcheck._quick_test_targets(),
                             ["harness/lib/tests/test_x.py"])

    def test_quick_targets_undeducible_falls_back_full(self):
        # 推导不出（非 .py 改动）→ None 回落全量（保守保证覆盖）
        with mock.patch.object(selfcheck, "_git_changed_files",
                               return_value={"docs/a.md"}):
            self.assertIsNone(selfcheck._quick_test_targets())

    def test_quick_targets_missing_test_falls_back_full(self):
        # 模块对应测试不存在 → None 回落全量
        with mock.patch.object(selfcheck, "_git_changed_files",
                               return_value={"harness/lib/nonexist_mod.py"}):
            self.assertIsNone(selfcheck._quick_test_targets())

    def test_main_quick_uses_derived_scope(self):
        # --mode quick：pytest 只跑 git diff 推导的测试（非全量 harness）
        seen = []

        def _run(cmd, **kw):
            seen.append(cmd)
            return _FakeProc(0, "12 passed in 1.0s\n")

        with mock.patch.object(selfcheck, "_quick_test_targets",
                               return_value=["harness/lib/tests/test_selfcheck.py"]), \
                mock.patch.object(selfcheck.subprocess, "run", side_effect=_run), \
                _patched_parallel():
            with redirect_stdout(io.StringIO()):
                selfcheck.main(["--mode", "quick"])
        pytest_cmd = seen[0]
        self.assertIn("harness/lib/tests/test_selfcheck.py", pytest_cmd)

    def test_main_quick_falls_back_full(self):
        # 推导不出（_quick_test_targets 返 None）→ 回落全量 harness
        seen = []

        def _run(cmd, **kw):
            seen.append(cmd)
            return _FakeProc(0, "531 passed in 27.0s\n")

        with mock.patch.object(selfcheck, "_quick_test_targets", return_value=None), \
                mock.patch.object(selfcheck.subprocess, "run", side_effect=_run), \
                _patched_parallel():
            with redirect_stdout(io.StringIO()):
                selfcheck.main(["--mode", "quick"])
        self.assertIn("harness", seen[0])


if __name__ == "__main__":
    unittest.main()
