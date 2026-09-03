import argparse
import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ws_acceptance as wa


class TestParseAcceptance(unittest.TestCase):
    def test_parse_tags(self):
        tags = wa.parse_acceptance("svc:lechao_lcview log:ERROR prop:a=b file:/data/x")
        self.assertEqual(tags, ["svc:lechao_lcview", "log:ERROR",
                                "prop:a=b", "file:/data/x"])

    def test_cmd_with_spaces_quoted(self):
        # cmd 含空格必须用引号包裹且整体保留
        tags = wa.parse_acceptance('cmd:"/system/bin/usb-verify --version"')
        self.assertEqual(tags, ['cmd:"/system/bin/usb-verify --version"'])

    def test_boot_bare_word(self):
        tags = wa.parse_acceptance("boot svc:lechao_lcview")
        self.assertEqual(tags, ["boot", "svc:lechao_lcview"])

    def test_free_text_single(self):
        tags = wa.parse_acceptance("设备能正常播放音频")
        self.assertEqual(tags, ["设备能正常播放音频"])

    def test_split_kind(self):
        self.assertEqual(wa.split_tag("svc:lechao_lcview"), ("svc", "lechao_lcview"))
        self.assertEqual(wa.split_tag("boot"), ("boot", ""))
        self.assertEqual(wa.split_tag('cmd:"a b"'), ("cmd", "a b"))
        self.assertEqual(wa.split_tag('hostcmd:"a b"'), ("hostcmd", "a b"))
        self.assertEqual(wa.split_tag('logfield:"a|x|=|0"'),
                         ("logfield", "a|x|=|0"))
        self.assertEqual(wa.split_tag('logfresh:"a|x"'),
                         ("logfresh", "a|x"))
        self.assertEqual(wa.split_tag("自由文本"), ("text", "自由文本"))

    def test_logfield_takes_anchor_last_line_value(self):
        # logfield：取 logcat 含锚点的最后一行按字段取值比较（非子串匹配）
        def adb_logcat():
            return ("LcView: alive beat=30 buffered=0B overrun=0 dropped=0 "
                    "readErr=1 flush=2\n"
                    "LcView: alive beat=60 buffered=0B overrun=0 dropped=0 "
                    "readErr=1 flush=2\n")

        status, detail = wa.execute_tag(
            'logfield:"LcView: alive beat=|readErr|=|1"',
            adb_exec=None, adb_logcat=adb_logcat)
        self.assertEqual(status, "pass")
        status, detail = wa.execute_tag(
            'logfield:"LcView: alive beat=|readErr|=|0"',
            adb_exec=None, adb_logcat=adb_logcat)
        self.assertEqual(status, "fail")
        # 锚点未命中 → fail
        status, detail = wa.execute_tag('logfield:"无此锚点|overrun|=|0"',
                                        adb_exec=None,
                                        adb_logcat=lambda: "x\n")
        self.assertEqual(status, "fail")
        self.assertIn("未命中锚点", detail)
        # 语法错误（非 4 段）→ fail
        status, detail = wa.execute_tag('logfield:"a|b"',
                                        adb_exec=None, adb_logcat=adb_logcat)
        self.assertEqual(status, "fail")
        self.assertIn("语法错误", detail)

    def test_logfield_pid_narrows_and_passes(self):
        # 5 段写法：pidof 取 pid → adb_logcat(pid) 按进程收窄；旧进程行
        #（pid 不匹配，已被 logcat --pid 过滤出返回）不被采纳为末行
        seen = {}

        def adb_exec(cmd):
            if cmd == "pidof lechao_lcview":
                return "4242", 0
            return "", 1

        def adb_logcat(pid=None, force=False):
            seen["pid"] = pid
            seen["force"] = force
            return ("4242: heartbeat, loop=0 overrun=0 dropped=0 readErr=1\n"
                    "4242: heartbeat, loop=0 overrun=0 dropped=0 readErr=0\n")

        status, detail = wa.execute_tag(
            'logfield:"heartbeat, loop=|readErr|=|0|lechao_lcview"',
            adb_exec=adb_exec, adb_logcat=adb_logcat)
        self.assertEqual(status, "pass")
        self.assertEqual(seen["pid"], "4242")  # pid 透传进 adb_logcat
        self.assertFalse(seen["force"])  # 首次调用走缓存（同批同 key 复用）
        self.assertIn("readErr=0", detail)

    def test_logfield_pid_process_missing_red(self):
        # 进程不存在（pidof 空）→ 判红
        def adb_exec(cmd):
            return "", 1

        status, detail = wa.execute_tag(
            'logfield:"heartbeat, loop=|readErr|=|0|lechao_lcview"',
            adb_exec=adb_exec, adb_logcat=lambda pid=None: "x\n")
        self.assertEqual(status, "fail")
        self.assertIn("pidof 为空或非数字", detail)

    def test_logfield_pid_non_numeric_red(self):
        # pidof 输出非数字（如多实例空格分隔）→ 判红
        def adb_exec(cmd):
            return "4242 4243", 0

        status, detail = wa.execute_tag(
            'logfield:"heartbeat, loop=|readErr|=|0|lechao_lcview"',
            adb_exec=adb_exec, adb_logcat=lambda pid=None: "x\n")
        self.assertEqual(status, "fail")
        self.assertIn("非数字", detail)

    def test_logfield_pid_anchor_miss_polls_until_timeout(self):
        # 5 段锚点未命中：每 5s 重取（重新 pidof + logcat），90s 超时判红；
        # 不得回落全量筛——全程仅带 pid 调用 adb_logcat（无全量回落）；
        # 首次调用走缓存（同 key 复用），轮询重试须 force=True 绕缓存
        # （走缓存永远读首拉旧内容死等 90s 超时判红）
        calls = []
        forces = []

        def adb_exec(cmd):
            return "4242", 0

        def adb_logcat(pid=None, force=False):
            calls.append(pid)
            forces.append(force)
            return "其他进程日志行\n"  # 锚点永不命中

        mono = mock.patch("ws_acceptance.time.monotonic",
                          side_effect=[0] + [5 * i for i in range(1, 21)])
        sleep = mock.patch("ws_acceptance.time.sleep")
        with mono, sleep:
            status, detail = wa.execute_tag(
                'logfield:"heartbeat, loop=|readErr|=|0|lechao_lcview"',
                adb_exec=adb_exec, adb_logcat=adb_logcat)
        self.assertEqual(status, "fail")
        self.assertIn("90s 内未命中锚点", detail)
        self.assertIn("未回落全量筛", detail)
        self.assertTrue(calls)
        self.assertTrue(all(p == "4242" for p in calls))
        # 首次走缓存（force=False），轮询重试全部 force=True（绕缓存重取）
        self.assertFalse(forces[0])
        self.assertTrue(all(forces[1:]), "轮询重试必须绕缓存（force=True）")

    def test_logfresh_hit_in_window_passes(self):
        # logfresh 窗内命中锚点 → pass；时间窗 = 设备时钟回退 90s
        # （设备 1788226000 = 2026-09-01 01:26:40 GMT，窗起点 01:25:10）
        fake = mock.Mock()
        fake.stdout = "09-01 01:26:40 heartbeat, loop=5\n"

        def adb_exec(cmd):
            if cmd == "date +%s":
                return "1788226000", 0
            if cmd == "date +%z":
                return "+0000", 0
            return "", 1

        with mock.patch.object(wa.subprocess, "run", return_value=fake) as m:
            status, detail = wa.execute_tag(
                'logfresh:"heartbeat, loop=|90"',
                adb_exec=adb_exec, adb_logcat=None)
        self.assertEqual(status, "pass")
        self.assertIn("命中", detail)
        # 复用 build_logcat_cmd：-t <since> 时间窗 = 设备时钟回退 90s
        args = m.call_args.args[0]
        self.assertEqual(args[args.index("-t") + 1],
                         "2026-09-01 01:25:10.000")

    def test_logfresh_miss_in_window_fails(self):
        # 窗内未命中 → fail（daemon 卡死而进程存活时，90s 窗内无新心跳；
        # logcat -t 时间窗已把旧心跳过滤出窗，窗内输出不含锚点）
        fake = mock.Mock()
        fake.stdout = "09-01 01:26:00 其他进程日志行\n"

        def adb_exec(cmd):
            if cmd == "date +%s":
                return "1788226000", 0
            if cmd == "date +%z":
                return "+0000", 0
            return "", 1

        with mock.patch.object(wa.subprocess, "run", return_value=fake):
            status, detail = wa.execute_tag(
                'logfresh:"heartbeat, loop=|90"',
                adb_exec=adb_exec, adb_logcat=None)
        self.assertEqual(status, "fail")
        self.assertIn("未命中", detail)

    def test_logfresh_syntax_error_fails(self):
        # 语法错误（非 锚点|秒数 两段）→ fail
        def adb_exec(cmd):
            return "1788226000", 0

        status, detail = wa.execute_tag(
            'logfresh:"heartbeat, loop="',
            adb_exec=adb_exec, adb_logcat=None)
        self.assertEqual(status, "fail")
        self.assertIn("语法错误", detail)

    def test_logfresh_device_clock_unavailable_fails(self):
        # 设备时钟读不到（date +%s 失败）→ fail（判据不可信）
        def adb_exec(cmd):
            return "", 1

        status, detail = wa.execute_tag(
            'logfresh:"heartbeat, loop=|90"',
            adb_exec=adb_exec, adb_logcat=None)
        self.assertEqual(status, "fail")
        self.assertIn("无法读取设备时钟", detail)

    def test_hostcmd_quoted_payload(self):
        # hostcmd 照 cmd 支持引号包裹（含空格命令整体保留）
        tags = wa.parse_acceptance(
            'hostcmd:"cases/lcview_check.sh --mode files"')
        self.assertEqual(tags, ['hostcmd:"cases/lcview_check.sh --mode files"'])

    def test_hostcmd_runs_on_host(self):
        # hostcmd 走 host 执行（不经 adb）：cwd 落在 workspace-verify，相对路径可解析
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "ok"
        fake.stderr = ""
        with mock.patch.object(wa.subprocess, "run", return_value=fake) as m:
            status, detail = wa.execute_tag(
                'hostcmd:"cases/lcview_check.sh --mode files"',
                adb_exec=None, adb_logcat=None)
        self.assertEqual(status, "pass")
        self.assertEqual(m.call_args.args[0],
                         "cases/lcview_check.sh --mode files")
        self.assertTrue(m.call_args.kwargs["shell"])
        self.assertTrue(m.call_args.kwargs["cwd"].endswith("workspace-verify"))

    def test_hostcmd_failure_fails(self):
        # hostcmd 退出非 0 → fail，detail 带 host 侧 stderr/stdout 摘录
        fake = mock.Mock()
        fake.returncode = 1
        fake.stdout = "ERROR: 无任何非空 jsonl"
        fake.stderr = ""
        with mock.patch.object(wa.subprocess, "run", return_value=fake):
            status, detail = wa.execute_tag(
                'hostcmd:"cases/lcview_check.sh --mode files"',
                adb_exec=None, adb_logcat=None)
        self.assertEqual(status, "fail")
        self.assertIn("ERROR: 无任何非空 jsonl", detail)

    def test_hostcmd_timeout_fails(self):
        # hostcmd 超时 → fail（detail 标注超时，与命令失败区分）
        with mock.patch.object(wa.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired("c", 180)):
            status, detail = wa.execute_tag('hostcmd:"sleep 999"',
                                            adb_exec=None, adb_logcat=None)
        self.assertEqual(status, "fail")
        self.assertIn("超时", detail)

    def test_free_text_returns_ai(self):
        # 自由文本返回 "ai"（交 verify AI 判定），不是 unknown
        status, _ = wa.execute_tag("设备正常", adb_exec=None, adb_logcat=None)
        self.assertEqual(status, "ai")

    def test_prop_code_nonzero_fails(self):
        # adb 执行失败（如超时 code=-1）时 prop 判 fail，即使输出恰好等于期望值
        status, detail = wa.execute_tag("prop:key=", adb_exec=lambda c: ("", -1),
                                        adb_logcat=None)
        self.assertEqual(status, "fail")
        self.assertIn("adb 执行超时", detail)

    def test_prop_requires_code_zero(self):
        # 命令失败（code 非 0）即使输出等于期望值也判 fail
        status, _ = wa.execute_tag("prop:key=x", adb_exec=lambda c: ("x", 1),
                                   adb_logcat=None)
        self.assertEqual(status, "fail")

    def test_log_fail_detail_distinguishes(self):
        # log 未命中：detail 带关键字与「未命中」标记（可辨，非恒写命中 N 字符）
        status, detail = wa.execute_tag("log:KEYWORD", adb_exec=None,
                                        adb_logcat=lambda: "nothing here")
        self.assertEqual(status, "fail")
        self.assertIn("未命中", detail)
        self.assertIn("KEYWORD", detail)
        # 命中：detail 带「命中」与关键字
        status, detail = wa.execute_tag("log:KEYWORD", adb_exec=None,
                                        adb_logcat=lambda: "line KEYWORD found")
        self.assertEqual(status, "pass")
        self.assertIn("命中", detail)
        self.assertIn("KEYWORD", detail)

    def test_wait_ready_log_without_since_rejected(self):
        # 假绿精确条件：--wait-ready 且含 log: 标签却无 --log-since → 返 2
        rc = wa.main(["run", "--acceptance", "log:KEYWORD", "--wait-ready"])
        self.assertEqual(rc, 2)

    def test_wait_ready_logfield_without_since_rejected(self):
        # 假绿精确条件：--wait-ready 且含 logfield: 标签却无 --log-since → 返 2
        # （logfield 取锚点末行累计值，reboot 后同样可能命中旧日志，须一并拦截）
        rc = wa.main(["run", "--acceptance",
                      'logfield:"heartbeat, loop=|overrun|=|0"', "--wait-ready"])
        self.assertEqual(rc, 2)

    def test_log_since_invalid_format_rejected(self):
        # --log-since 格式非法（非 MM-DD/YYYY-MM-DD 的 HH:MM:SS.mmm）→ 返 2
        rc = wa.main(["run", "--acceptance", "boot", "--log-since", "10:00:00"])
        self.assertEqual(rc, 2)

    def test_overall_ai_only(self):
        overall, items = wa.run_acceptance(
            "设备正常", adb_exec=lambda c: ("", 0), adb_logcat=lambda: "")
        self.assertEqual(overall, "ai")
        self.assertEqual(items[0]["status"], "ai")

    def test_overall_mixed(self):
        acc = 'boot cmd:"true"'
        overall, items = wa.run_acceptance(
            acc,
            adb_exec=lambda c: ("1", 0) if "boot_completed" in c else ("", 0),
            adb_logcat=lambda: "")
        self.assertEqual(overall, "pass")
        self.assertEqual(len(items), 2)

    def test_overall_fail_wins_over_ai(self):
        # 任一自动项 fail 即 fail，即使存在未判定 ai 项
        with mock.patch.object(wa, "parse_acceptance",
                               return_value=["svc:nope", "设备正常"]):
            overall, items = wa.run_acceptance(
                "ignored", adb_exec=lambda c: ("", 0), adb_logcat=lambda: "")
        self.assertEqual(overall, "fail")
        self.assertEqual([i["status"] for i in items], ["fail", "ai"])

    def test_overall_ai_when_pass_with_ai(self):
        # 全 pass 但含未判定 ai 项 → ai（未判定不算成功，不报 pass）
        with mock.patch.object(wa, "parse_acceptance",
                               return_value=["boot", "设备正常"]):
            overall, items = wa.run_acceptance(
                "ignored", adb_exec=lambda c: ("1", 0), adb_logcat=lambda: "")
        self.assertEqual(overall, "ai")
        self.assertEqual([i["status"] for i in items], ["pass", "ai"])

    def test_shlex_quote_svc_prop_file(self):
        # svc/prop/file 分支 payload 经 shlex.quote 包裹（注入防护），cmd 分支不包裹
        calls = []

        def adb_exec(cmd):
            calls.append(cmd)
            return ("", 0)

        wa.execute_tag("svc:svc a", adb_exec=adb_exec, adb_logcat=None)
        wa.execute_tag("prop:key a=v", adb_exec=adb_exec, adb_logcat=None)
        wa.execute_tag("file:/data/a b", adb_exec=adb_exec, adb_logcat=None)
        wa.execute_tag("cmd:echo 'a;b'", adb_exec=adb_exec, adb_logcat=None)
        self.assertEqual(calls[0], "getprop init.svc.'svc a'")
        self.assertEqual(calls[1], "getprop 'key a'")
        self.assertEqual(calls[2], "ls -la '/data/a b'")
        self.assertEqual(calls[3], "echo 'a;b'")

    def test_ensure_boot_appends_when_missing(self):
        # ensure_boot=True 且标签无 boot 时自动追加
        seen = []

        def adb_exec(cmd):
            seen.append(cmd)
            if "boot_completed" in cmd:
                return ("1", 0)
            if "init.svc" in cmd:
                return ("running", 0)
            return ("", 0)

        overall, items = wa.run_acceptance(
            "svc:lechao_lcview", adb_exec=adb_exec, adb_logcat=lambda: "",
            ensure_boot=True)
        self.assertEqual(overall, "pass")
        self.assertEqual([i["tag"] for i in items],
                         ["svc:lechao_lcview", "boot"])

    def test_ensure_boot_no_dup_when_present(self):
        # 标签已含 boot 时不重复追加
        seen = []

        def adb_exec(cmd):
            seen.append(cmd)
            if "boot_completed" in cmd:
                return ("1", 0)
            if "init.svc" in cmd:
                return ("running", 0)
            return ("", 0)

        overall, items = wa.run_acceptance(
            "boot svc:lechao_lcview", adb_exec=adb_exec, adb_logcat=lambda: "",
            ensure_boot=True)
        self.assertEqual(overall, "pass")
        self.assertEqual([i["tag"] for i in items],
                         ["boot", "svc:lechao_lcview"])


class TestResolveAcceptance(unittest.TestCase):
    def _args(self, **kw):
        a = argparse.Namespace()
        a.acceptance = kw.get("acceptance")
        a.case = kw.get("case")
        a.batch_file = kw.get("batch_file")
        return a

    def test_ensure_retries_with_rescue_once(self):
        # 方向 1（编排层接线）：ensure 失败后以 rescue_enabled=True 重试一次，
        # 重试成功继续验收（rescue 不再成死代码）
        with mock.patch.object(wa, "ac") as m_ac:
            m_ac.ensure_connected.side_effect = [None, "10.9.9.9:5555"]
            m_ac.ensure_ready.return_value = True
            m_ac.clock_sync.return_value = (True, "ok")
            m_ac.build_exec_cmd.side_effect = lambda c: ["adb", "shell", c]
            m_ac.parse_exec_output.return_value = ("1", 0)
            m_ac.build_logcat_cmd.return_value = ["adb", "logcat", "-d"]
            m_sub = mock.Mock()
            m_sub.run.return_value.stdout = "out\n__LE_EXIT_CODE__=0\n"
            m_sub.TimeoutExpired = subprocess.TimeoutExpired
            buf = io.StringIO()
            with mock.patch.object(wa.subprocess, "run", m_sub):
                with contextlib.redirect_stdout(buf):
                    rc = wa.main(["run", "--acceptance", "boot"])
        self.assertEqual(rc, 0)
        calls = m_ac.ensure_connected.call_args_list
        self.assertEqual(len(calls), 2)
        # 第一次无参（默认 rescue_enabled=False），重试显式 True
        self.assertEqual(calls[0].kwargs.get("rescue_enabled"), None)
        self.assertEqual(calls[1].kwargs.get("rescue_enabled"), True)

    def test_ensure_retry_fails_still_unreachable(self):
        # rescue 重试仍失败 → 设备不可达返 1（重试只一次，不无限循环）
        with mock.patch.object(wa, "ac") as m_ac:
            m_ac.ensure_connected.side_effect = [None, None]
            buf = io.StringIO()
            err = io.StringIO()
            with mock.patch.object(wa.subprocess, "run") as m_sub:
                m_sub.TimeoutExpired = subprocess.TimeoutExpired
                with contextlib.redirect_stdout(buf):
                    with contextlib.redirect_stderr(err):
                        rc = wa.main(["run", "--acceptance", "boot"])
        self.assertEqual(rc, 1)
        self.assertIn("设备不可达", buf.getvalue())
        self.assertEqual(m_ac.ensure_connected.call_count, 2)

    def test_clock_sync_without_wait_ready_when_fresh(self):
        # 方向 3：验收含 --mode fresh/ts 判据但无 --wait-ready 也须 clock_sync
        # （时间敏感判据须设备时钟可信，此前只挂 wait_ready 门禁致恒红）
        with mock.patch.object(wa, "ac") as m_ac:
            m_ac.ensure_connected.side_effect = ["ep", "ep"]
            m_ac.ensure_ready.return_value = True
            m_ac.clock_sync.return_value = (True, "ok")
            m_ac.build_exec_cmd.side_effect = lambda c: ["adb", "shell", c]
            m_ac.parse_exec_output.return_value = ("1", 0)
            m_ac.build_logcat_cmd.return_value = ["adb", "logcat", "-d"]
            m_ac.parse_exec_output.return_value = ("1", 0)
            m_ac.build_logcat_cmd.return_value = ["adb", "logcat", "-d"]
            m_run = mock.Mock(return_value=mock.Mock(stdout="ok\n", stderr="",
                                                     returncode=0))
            m_run.side_effect = None
            buf = io.StringIO()
            with mock.patch.object(wa.subprocess, "run", m_run):
                with contextlib.redirect_stdout(buf):
                    rc = wa.main(["run", "--acceptance",
                                  'hostcmd:"cases/lcview_check.sh --mode fresh --window 600"'])
        self.assertEqual(rc, 0)
        m_ac.clock_sync.assert_called_once()

    def test_no_clock_sync_without_ts_fresh(self):
        # 无 ts/fresh 判据且无 --wait-ready → 不触发 clock_sync
        with mock.patch.object(wa, "ac") as m_ac:
            m_ac.ensure_connected.side_effect = ["ep", "ep"]
            m_ac.build_exec_cmd.side_effect = lambda c: ["adb", "shell", c]
            m_ac.parse_exec_output.return_value = ("1", 0)
            m_ac.build_logcat_cmd.return_value = ["adb", "logcat", "-d"]
            m_sub = mock.Mock()
            m_sub.run.return_value.stdout = "out\n__LE_EXIT_CODE__=0\n"
            m_sub.TimeoutExpired = subprocess.TimeoutExpired
            buf = io.StringIO()
            with mock.patch.object(wa.subprocess, "run", m_sub):
                with contextlib.redirect_stdout(buf):
                    rc = wa.main(["run", "--acceptance", "boot"])
        self.assertEqual(rc, 0)
        m_ac.clock_sync.assert_not_called()

    def test_all_missing_returns_error(self):
        # 验收来源三选一，全缺返 2
        acc, err = wa.resolve_acceptance(self._args())
        self.assertIsNone(acc)
        self.assertIn("必传其一", err)

    def test_mutually_exclusive(self):
        # --acceptance 与 --case 互斥
        acc, err = wa.resolve_acceptance(self._args(acceptance="boot",
                                                    case="lcview-liveness"))
        self.assertIsNone(acc)
        self.assertIn("互斥", err)

    def test_batch_file_takes_acceptance(self):
        # --batch-file 经 cdp_parse 解析取批次验收文本
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.cdp"
            p.write_text("-sv base:8c583f57f4e4\n意图: 测试\n"
                         "验收: svc:lechao_lcview boot\n方向: 测试\n",
                         encoding="utf-8")
            acc, err = wa.resolve_acceptance(self._args(batch_file=str(p)))
        self.assertIsNone(err)
        self.assertEqual(acc, "svc:lechao_lcview boot")

    def test_batch_file_s_mode_rejected(self):
        # -s 批次验收为「无」，无验收文本，拒绝
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.cdp"
            p.write_text("-s base:8c583f57f4e4\n意图: 测试\n"
                         "验收: 无\n方向: 测试\n", encoding="utf-8")
            acc, err = wa.resolve_acceptance(self._args(batch_file=str(p)))
        self.assertIsNone(acc)
        self.assertIn("无", err)

    def test_batch_file_hostcmd_rejected(self):
        # 方向 1：batch-file 分支取到验收后，含 hostcmd:/cmd: 标签即拒
        # （禁批次自带宿主命令执行意图），令 main 退 2
        for tag in ("hostcmd:whoami", "cmd:echo hi"):
            with tempfile.TemporaryDirectory() as d:
                p = Path(d) / "b.cdp"
                p.write_text("-sv base:8c583f57f4e4\n意图: 测试\n"
                             f"验收: {tag}\n方向: 测试\n", encoding="utf-8")
                acc, err = wa.resolve_acceptance(self._args(batch_file=str(p)))
            self.assertIsNone(acc)
            self.assertIn("hostcmd:/cmd:", err)

    def test_case_branch_unchanged_for_hostcmd(self):
        # 方向 1：case 与 acceptance 两分支不变——yaml 用例值含 hostcmd: 不因
        # batch-file 禁令被拒（禁宿主命令仅限批次自带通道）
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "verify-cases.yaml"
            y.write_text("cases:\n  my-case: \"hostcmd:'whoami' boot\"\n",
                         encoding="utf-8")
            acc, err = wa.resolve_acceptance(self._args(case="my-case"),
                                             cases_path=y)
        self.assertIsNone(err)
        self.assertIn("hostcmd", acc)

    def test_batch_file_case_unknown_id_rejected(self):
        # 方向 3：batch-file 验收 case: id 未知 → err（main 退 2，任一未知判死）
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.cdp"
            p.write_text("-sv base:8c583f57f4e4\n意图: 测试\n"
                         "验收: case:no-such-case\n方向: 测试\n", encoding="utf-8")
            acc, err = wa.resolve_acceptance(self._args(batch_file=str(p)))
        self.assertIsNone(acc)
        self.assertIn("不存在于", err)

    def test_batch_file_case_known_id_ok(self):
        # 方向 3：case id 存在于 verify-cases.yaml 则通过（真实 cases 表）
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.cdp"
            p.write_text("-sv base:8c583f57f4e4\n意图: 测试\n"
                         "验收: case:lcview-liveness\n方向: 测试\n", encoding="utf-8")
            acc, err = wa.resolve_acceptance(self._args(batch_file=str(p)))
        self.assertIsNone(err)
        self.assertEqual(acc, "case:lcview-liveness")

    def test_batch_file_manual_free_text_ok(self):
        # 方向 3：manual 模式自由文本不查表，直接返回
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "b.cdp"
            p.write_text("-sv base:8c583f57f4e4\n意图: 测试\n"
                         "验收: manual:lcview 服务运行正常\n方向: 测试\n",
                         encoding="utf-8")
            acc, err = wa.resolve_acceptance(self._args(batch_file=str(p)))
        self.assertIsNone(err)
        self.assertEqual(acc, "manual:lcview 服务运行正常")

    def test_case_from_yaml(self):
        # --case 从 verify-cases.yaml cases 段取标签，值内可用引号
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "verify-cases.yaml"
            y.write_text('cases:\n  my-case: "svc:a file:/data/x boot"\n',
                         encoding="utf-8")
            acc, err = wa.resolve_acceptance(self._args(case="my-case"), cases_path=y)
        self.assertIsNone(err)
        self.assertEqual(acc, "svc:a file:/data/x boot")

    def test_case_missing_label(self):
        # 标签不存在于 cases 段 → 拒绝并列出可选标签
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "verify-cases.yaml"
            y.write_text("cases:\n  my-case: boot\n", encoding="utf-8")
            acc, err = wa.resolve_acceptance(self._args(case="nope"), cases_path=y)
        self.assertIsNone(acc)
        self.assertIn("不存在", err)
        self.assertIn("my-case", err)

    def test_case_comma_multi_concat(self):
        # 逗号分隔多用例：逐个查表按序拼接（空格连接）
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "verify-cases.yaml"
            y.write_text('cases:\n  a: "svc:x"\n  b: "log:heartbeat"\n',
                         encoding="utf-8")
            acc, err = wa.resolve_acceptance(self._args(case="a,b"), cases_path=y)
        self.assertIsNone(err)
        self.assertEqual(acc, "svc:x log:heartbeat")

    def test_case_comma_multi_missing_rejects(self):
        # 多用例中任一标签缺失 → 整批拒绝（不部分拼接）
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "verify-cases.yaml"
            y.write_text("cases:\n  a: boot\n", encoding="utf-8")
            acc, err = wa.resolve_acceptance(self._args(case="a,nope"), cases_path=y)
        self.assertIsNone(acc)
        self.assertIn("nope", err)
        self.assertIn("不存在", err)

    def test_inbuilt_lcview_liveness_present(self):
        # 资产层内建 lcview-liveness 可解析（daemon 直读内核 + 持续心跳 +
        # logfield 字段断言 0（overrun/dropped/drop_invalidwrite/invalid_records/
        # readErr，防子串命中历史零值心跳假绿）+ logfresh 90s 时效判据 +
        # boot 判据；HAL 已退役，svc 只留 lechao_lcview，
        # conserve 已迁至 lcview-transfer 不在本用例）
        # 五条 logfield 均带第 5 段进程名 lechao_lcview（按进程归属收窄）
        acc, err = wa.resolve_acceptance(self._args(case="lcview-liveness"))
        self.assertIsNone(err)
        self.assertEqual(
            acc,
            'svc:lechao_lcview log:"heartbeat, loop=" '
            'logfield:"heartbeat, loop=|overrun|=|0|lechao_lcview" '
            'logfield:"heartbeat, loop=|dropped|=|0|lechao_lcview" '
            'logfield:"heartbeat, loop=|drop_invalidwrite|=|0|lechao_lcview" '
            'logfield:"heartbeat, loop=|invalid_records|=|0|lechao_lcview" '
            'logfield:"heartbeat, loop=|readErr|=|0|lechao_lcview" '
            'logfresh:"heartbeat, loop=|90" boot')
        # log: 子串断言 0 已弃用（5000 行缓冲命中开机初期零值心跳假绿）
        self.assertNotIn('log:"overrun=0"', acc)

    def test_inbuilt_lcview_pipeline_present(self):
        # L1 主用例（critical 3 项）已内聚到 verify-cases.yaml，hostcmd 相对路径可解析；
        # fresh 已移入 trigger（静止态无新事件永不能过）
        acc, err = wa.resolve_acceptance(self._args(case="lcview-pipeline"))
        self.assertIsNone(err)
        self.assertIn('hostcmd:"cases/lcview_check.sh --mode files"', acc)
        self.assertIn('hostcmd:"cases/lcview_check.sh --mode schema"', acc)
        self.assertNotIn("--mode fresh", acc)

    def test_inbuilt_lcview_pipeline_warn_present(self):
        # warn 项：no_invalid + ts 全历史卫生检查（无 baseline 限定——
        # 校准回拨前未来记录在此暴露，trigger 的 ts 已 baseline 限定只判新记录；
        # ts 前插 dd 产载使静止态无新事件的组内自洽，不再依赖验收顺序）
        acc, err = wa.resolve_acceptance(self._args(case="lcview-pipeline-warn"))
        self.assertIsNone(err)
        self.assertIn('hostcmd:"cases/lcview_check.sh --mode invalid"', acc)
        self.assertIn('--mode ts --skew 600', acc)
        self.assertNotIn("--baseline", acc)

    def test_inbuilt_lcview_trigger_present(self):
        # L2 触发型全链路：显式 adb root → baseline → authorize → delta 保序，
        # probe 后追加 fresh/ts（产生新事件后时间判据可过）
        acc, err = wa.resolve_acceptance(self._args(case="lcview-trigger"))
        self.assertIsNone(err)
        tags = wa.parse_acceptance(acc)
        kinds = [wa.split_tag(t)[0] for t in tags]
        self.assertEqual(kinds, ["hostcmd"] * 8)
        self.assertIn("adb root", acc)
        self.assertIn("${LCVIEW_USB_DEV:-1-2}", acc)
        self.assertIn("--vid ${LCVIEW_USB_VID:-1256}", acc)
        self.assertIn("${LCVIEW_USB_PID:-25344}", acc)
        self.assertIn("--mode fresh --window 600", acc)
        # ts 以 --baseline 显式限定（只判基线后新记录，全历史卫生检查在 warn）；
        # 基线路径经 LCVIEW_BASELINE_FILE 环境变量透传（按轮次隔离，防跨轮串扰）
        self.assertIn("--mode ts --skew 600 --baseline "
                      "${LCVIEW_BASELINE_FILE:-/tmp/lcview_baseline.json}", acc)
        # 不再有裸设备路径硬编码（env 兜底表达式）
        self.assertNotIn("/sys/bus/usb/devices/1-2/", acc)

    def test_trigger_authorized_tags_not_truncated(self):
        # 方向 1 回归：转义引号命令（adb shell \"...\"）不得在转义处截断，
        # USB 开关整体保留（曾实测 6 标签中 2 条坏）
        acc, err = wa.resolve_acceptance(self._args(case="lcview-trigger"))
        self.assertIsNone(err)
        tags = wa.parse_acceptance(acc)
        shell_tags = [wa.split_tag(t)[1] for t in tags
                      if wa.split_tag(t)[1].startswith("adb shell")]
        self.assertEqual(len(shell_tags), 2)
        for payload in shell_tags:
            self.assertIn("/authorized", payload)
            self.assertIn("${LCVIEW_USB_DEV:-1-2}", payload)
            self.assertIn("&& cat", payload)

    def test_all_asset_cases_parse_clean(self):
        # 方向 4：遍历 verify-cases.yaml 全部 cases——无残余（parse 不抛）
        # 且无未剥转义引号残留（dict 生命周期形态经 _case_text 取 acceptance）
        data = yaml.safe_load(
            Path(wa._CASES_PATH).read_text(encoding="utf-8")) or {}
        cases = data.get("cases") or {}
        self.assertTrue(cases)
        for name, val in cases.items():
            acc = wa._case_text(val)
            tags = wa.parse_acceptance(acc)  # 残余非空会 raise ValueError
            self.assertTrue(tags, f"case {name} 解析为空")
            for t in tags:
                _, payload = wa.split_tag(t)
                self.assertNotIn('\\"', payload,
                                 f"case {name} 标签 {t[:50]} 转义引号未反转义")

    def test_residual_text_rejected(self):
        # 方向 3：标签外残文本不得静默丢弃——含残余直接报错
        with self.assertRaises(ValueError):
            wa.parse_acceptance('hostcmd:"adb shell \\"echo x\\"" 残余尾巴')
        with self.assertRaises(ValueError):
            wa.parse_acceptance("boot 设备能正常播放音频")


class TestMultiCase(unittest.TestCase):
    def test_case_comma_separated_concatenates(self):
        # --case 支持逗号分隔多用例：逐个查表，验收文本按序拼接（空格连接）
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "verify-cases.yaml"
            y.write_text("cases:\n  c1: svc:a\n  c2: log:x\n  c3: boot\n",
                         encoding="utf-8")
            acc, err = wa.resolve_acceptance(
                argparse.Namespace(acceptance=None, case="c1,c2,c3",
                                   batch_file=None),
                cases_path=y)
        self.assertIsNone(err)
        self.assertEqual(acc, "svc:a log:x boot")

    def test_case_comma_any_missing_rejected(self):
        # 逗号分隔多用例中任一缺失 → 整批拒绝（不部分拼接，防静默丢用例）
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "verify-cases.yaml"
            y.write_text("cases:\n  c1: boot\n", encoding="utf-8")
            acc, err = wa.resolve_acceptance(
                argparse.Namespace(acceptance=None, case="c1,missing",
                                   batch_file=None),
                cases_path=y)
        self.assertIsNone(acc)
        self.assertIn("missing", err)
        self.assertIn("不存在", err)

    def test_case_comma_all_empty_rejected(self):
        # 逗号分隔后无有效标签（全空白/空串）→ 拒绝
        with tempfile.TemporaryDirectory() as d:
            y = Path(d) / "verify-cases.yaml"
            y.write_text("cases:\n  c1: boot\n", encoding="utf-8")
            acc, err = wa.resolve_acceptance(
                argparse.Namespace(acceptance=None, case=" ,, ",
                                   batch_file=None),
                cases_path=y)
        self.assertIsNone(acc)
        self.assertIn("为空", err)


class TestEmptyAcceptance(unittest.TestCase):
    def test_run_acceptance_empty_fails(self):
        # 空验收（无任何标签）→ 判红并附说明项：防空验收静默返 pass 的假绿
        overall, items = wa.run_acceptance("", lambda c: ("", 0), lambda: "")
        self.assertEqual(overall, "fail")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["status"], "fail")
        self.assertIn("验收为空", items[0]["detail"])

    def test_run_acceptance_blank_fails(self):
        # 纯空白验收同样判红（parse 无标签且 strip 后为空）
        overall, items = wa.run_acceptance("   \n\t ", lambda c: ("", 0), lambda: "")
        self.assertEqual(overall, "fail")


class TestLogcatCacheAndTiming(unittest.TestCase):
    """批次方向 1/2/3：logcat 同 key 缓存复用、轮询绕缓存、每项计时字段。"""

    LOGCAT = ["adb", "logcat", "-d"]

    def _main_env(self, logcat_stdout):
        """构造 main 全流程 mock 环境，返回 (m_ac, m_run, logcat_calls)。"""
        logcat_calls = []

        def fake_run(cmd, **kw):
            if cmd == self.LOGCAT:
                logcat_calls.append(1)
                return mock.Mock(stdout=logcat_stdout, stderr="", returncode=0)
            return mock.Mock(stdout="out\n__LE_EXIT_CODE__=0\n", stderr="",
                             returncode=0)

        m_ac = mock.Mock()
        m_ac.ensure_connected.return_value = "ep"
        m_ac.ensure_ready.return_value = True
        m_ac.clock_sync.return_value = (True, "ok")
        m_ac.build_exec_cmd.side_effect = lambda c: ["adb", "shell", c]
        m_ac.parse_exec_output.return_value = ("4242", 0)
        m_ac.build_logcat_cmd.return_value = self.LOGCAT
        m_run = mock.patch.object(wa.subprocess, "run",
                                  side_effect=fake_run)
        return m_ac, m_run, logcat_calls

    def test_main_writes_result_file(self):
        # 方向 1/3：--result-file 原子写自描述验收产物（run_id/输入摘要/
        # 设备序列号/设备指纹/起止单调时间/逐项结果/总判定），无 .tmp 残留
        m_ac, m_run, _ = self._main_env(
            "LcView: heartbeat, loop=0 overrun=0 dropped=0 readErr=0 KEY\n")
        out_json = Path(tempfile.mkdtemp()) / "acceptance.json"
        buf = io.StringIO()
        with mock.patch.object(wa, "ac", m_ac), m_run:
            with contextlib.redirect_stdout(buf):
                rc = wa.main(["run", "--acceptance", "log:KEY",
                              "--result-file", str(out_json)])
        self.assertEqual(rc, 0)
        self.assertTrue(out_json.is_file())
        self.assertFalse(out_json.with_name(out_json.name + ".tmp").exists())
        data = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertTrue(data["run_id"])
        self.assertIn("log:KEY", data["input_summary"])
        self.assertIn("device_serial", data)
        self.assertIn("device_fingerprint", data)
        self.assertLess(data["start_monotonic"], data["end_monotonic"])
        self.assertIn("overall", data)
        self.assertIn("items", data)

    def test_main_logcat_cache_same_key_pulled_once(self):
        # 方向 2：同批多标签只拉一次——log:KEY x2 同 key (None, since) 拉 1 次，
        # 同 pid logfield x2 同 key (4242, since) 拉 1 次，合计 2 次（非 4 次）
        m_ac, m_run, calls = self._main_env(
            "LcView: heartbeat, loop=0 overrun=0 dropped=0 readErr=0 KEY\n")
        buf = io.StringIO()
        with mock.patch.object(wa, "ac", m_ac), m_run:
            with contextlib.redirect_stdout(buf):
                rc = wa.main(["run", "--acceptance",
                              'log:KEY log:KEY '
                              'logfield:"heartbeat, loop=|readErr|=|0|lechao_lcview" '
                              'logfield:"heartbeat, loop=|dropped|=|0|lechao_lcview"'])
        self.assertEqual(rc, 0)
        self.assertEqual(len(calls), 2)
        self.assertIn('"elapsed_s"', buf.getvalue())

    def test_main_logfield_poll_bypasses_cache(self):
        # 方向 3：轮询绕缓存——锚点永不命中时每 5s force 重拉，
        # 90s 超时判红（deadline=90，check 值 5..90 共 18 轮判红）；
        # 若走缓存只会有 1 次拉取 → 死等 90s 恒红
        m_ac, m_run, calls = self._main_env("其他进程日志行\n")
        mono = mock.patch("ws_acceptance.time.monotonic",
                          side_effect=[0.0, 0.0] + [5 * i for i in range(1, 21)]
                          + [95.0])
        sleep = mock.patch("ws_acceptance.time.sleep")
        buf = io.StringIO()
        with mock.patch.object(wa, "ac", m_ac), m_run, mono, sleep:
            with contextlib.redirect_stdout(buf):
                rc = wa.main(["run", "--acceptance",
                              'logfield:"heartbeat, loop=|readErr|=|0|lechao_lcview"'])
        self.assertEqual(rc, 1)
        self.assertEqual(len(calls), 18)
        self.assertIn("90s 内未命中锚点", buf.getvalue())

    def test_run_acceptance_items_have_elapsed_s(self):
        # 方向 1：每项 wall-clock 计时写 items elapsed_s（收据 acceptance 可见）
        mono = mock.patch("ws_acceptance.time.monotonic",
                          side_effect=[1.0, 1.25, 2.0, 2.5])

        def adb_exec(cmd):
            if "boot_completed" in cmd:
                return ("1", 0)
            return ("", 0)

        with mono:
            overall, items = wa.run_acceptance(
                "boot cmd:true", adb_exec=adb_exec, adb_logcat=lambda: "")
        self.assertEqual(overall, "pass")
        self.assertEqual(items[0]["elapsed_s"], 0.25)
        self.assertEqual(items[1]["elapsed_s"], 0.5)


class TestAcceptanceInternalSegments(unittest.TestCase):
    """方向 1：acceptance 段内部分段打点——connect/wait_ready/clock_sync/
    since 换算/每个 case 各记一段（收据 timings 可归因 180s 去向）。"""

    def _batch(self, d):
        p = Path(d) / "b.cdp"
        p.write_text("-sv base:111111111111\n意图: 分段\n"
                     "验收: boot logfresh:\"heartbeat, loop=|90\"\n"
                     "方向: 测试\n", encoding="utf-8")
        return p

    def test_internal_segments_and_case_marks(self):
        # batch-file 模式：connect/wait_ready/clock_sync/since_convert/
        # acc_1..n/verify_acceptance 全序列落本批打点文件（batch_id 显式
        # 传参，多打点文件也不静默跳过）
        marks = []
        LOGCAT = ["adb", "logcat", "-d"]

        def fake_run(cmd, **kw):
            if cmd == LOGCAT:
                return mock.Mock(stdout="09-02 01:00:00 heartbeat, loop=5\n",
                                 stderr="", returncode=0)
            if cmd == ["adb", "shell", "date +%s"]:
                out = "1788226000\n"
            elif cmd == ["adb", "shell", "date +%z"]:
                out = "+0000\n"
            elif cmd == ["adb", "shell", "getprop sys.boot_completed"]:
                out = "1\n"
            else:
                out = ""
            return mock.Mock(stdout=out + "__LE_EXIT_CODE__=0\n", stderr="",
                             returncode=0)

        def fake_parse(stdout):
            body = stdout.split("__LE_EXIT_CODE__=")[0].rstrip()
            return body, 0

        def fake_mark(name, batch_id=None, zero=False):
            marks.append((name, batch_id, zero))

        with tempfile.TemporaryDirectory() as d:
            batch = self._batch(d)
            with mock.patch.object(wa, "ac") as m_ac, \
                    mock.patch.object(wa, "_mark_stage",
                                      side_effect=fake_mark), \
                    mock.patch.object(wa, "_backfill_zero_marks"), \
                    mock.patch.object(wa.subprocess, "run",
                                      side_effect=fake_run):
                m_ac.ensure_connected.return_value = "ep"
                m_ac.ensure_ready.return_value = True
                m_ac.clock_sync.return_value = (True, "ok")
                m_ac.build_exec_cmd.side_effect = lambda c: ["adb", "shell", c]
                m_ac.parse_exec_output.side_effect = fake_parse
                m_ac.build_logcat_cmd.return_value = LOGCAT
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = wa.main(["run", "--batch-file", str(batch),
                                  "--wait-ready",
                                  "--log-since", "09-02 01:00:00.000"])
        self.assertEqual(rc, 0)
        names = [m[0] for m in marks]
        self.assertEqual(names, ["verify_acceptance_connect",
                                 "verify_acceptance_wait_ready",
                                 "verify_acceptance_clock_sync",
                                 "verify_acceptance_since_convert",
                                 "verify_acceptance_acc_1",
                                 "verify_acceptance_acc_2",
                                 "verify_acceptance"])
        # batch_id 显式传参（来自批次内容），非 None
        self.assertTrue(all(m[1] for m in marks), "batch-file 模式须显式传 batch_id")
        self.assertIn("overall", buf.getvalue())

    def test_no_wait_ready_skips_segment(self):
        # 无 --wait-ready/--log-since/无时间判据：条件段不 mark（未执行无
        # 耗时可记），connect 与 case 级照记
        marks = []
        LOGCAT = ["adb", "logcat", "-d"]

        def fake_run(cmd, **kw):
            if cmd == LOGCAT:
                return mock.Mock(stdout="x\n", stderr="", returncode=0)
            if cmd == ["adb", "shell", "getprop sys.boot_completed"]:
                out = "1\n"
            else:
                out = ""
            return mock.Mock(stdout=out + "__LE_EXIT_CODE__=0\n", stderr="",
                             returncode=0)

        def fake_parse(stdout):
            return stdout.split("__LE_EXIT_CODE__=")[0].rstrip(), 0

        def fake_mark(name, batch_id=None, zero=False):
            marks.append((name, batch_id, zero))

        with tempfile.TemporaryDirectory() as d:
            batch = self._batch(d)
            p = Path(d) / "b2.cdp"
            p.write_text("-sv base:111111111111\n意图: 分段\n"
                         "验收: boot\n方向: 测试\n", encoding="utf-8")
            with mock.patch.object(wa, "ac") as m_ac, \
                    mock.patch.object(wa, "_mark_stage",
                                      side_effect=fake_mark), \
                    mock.patch.object(wa, "_backfill_zero_marks"), \
                    mock.patch.object(wa.subprocess, "run",
                                      side_effect=fake_run):
                m_ac.ensure_connected.return_value = "ep"
                m_ac.build_exec_cmd.side_effect = lambda c: ["adb", "shell", c]
                m_ac.parse_exec_output.side_effect = fake_parse
                m_ac.build_logcat_cmd.return_value = LOGCAT
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    rc = wa.main(["run", "--batch-file", str(p)])
        self.assertEqual(rc, 0)
        names = [m[0] for m in marks]
        self.assertEqual(names, ["verify_acceptance_connect",
                                 "verify_acceptance_acc_1",
                                 "verify_acceptance"])


class TestBackfillZeroMarks(unittest.TestCase):
    """方向 3：sync/build/push/unit_test 四段跳过时补零 mark（段完整可归因）。"""

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

    def _timing(self):
        return wa.cdp_paths.log_apply_dir() / f"timings-{self.batch}.json"

    def test_fills_missing_four_segments_zero(self):
        # 四段均缺失：以最近 mark 同刻补零，收据 timings 五段齐全
        wa.cdp_timing.main(["start", "--batch", self.batch])
        wa.cdp_timing.main(["mark", "--batch", self.batch, "--name",
                            "verify_acceptance"])
        wa._backfill_zero_marks(self.batch)
        data = json.loads(self._timing().read_text(encoding="utf-8"))
        names = [m["name"] for m in data["marks"]]
        self.assertEqual(names, ["verify_acceptance", "verify_sync",
                                 "verify_build", "verify_push",
                                 "verify_unit_test"])
        last_wall = data["marks"][0]["wall"]
        for m in data["marks"][1:]:
            self.assertEqual(m["wall"], last_wall, "补零段须与最近 mark 同刻")
        # 段耗时可归因：补零段 0
        wa.cdp_timing.main(["finish", "--batch", self.batch])
        data = json.loads(self._timing().read_text(encoding="utf-8"))
        segs = {s["name"]: s["elapsed_s"] for s in data["segments"]}
        for seg in ("verify_sync", "verify_build", "verify_push",
                    "verify_unit_test"):
            self.assertEqual(segs[seg], 0)

    def test_existing_segments_not_overwritten(self):
        # 已有真实 mark 的段不重复补零（真实耗时保留）
        wa.cdp_timing.main(["start", "--batch", self.batch])
        wa.cdp_timing.main(["mark", "--batch", self.batch, "--name",
                            "verify_sync"])
        wa._backfill_zero_marks(self.batch)
        data = json.loads(self._timing().read_text(encoding="utf-8"))
        names = [m["name"] for m in data["marks"]]
        self.assertEqual(names, ["verify_sync", "verify_build",
                                 "verify_push", "verify_unit_test"])
        self.assertEqual(data["marks"][0]["wall"],
                         data["marks"][1]["wall"])

    def test_no_batch_id_skips(self):
        # 无 batch_id（非 batch-file 模式）不写（自动识别不可靠时不补，
        # 防误标其他批次打点文件）
        wa.cdp_timing.main(["start", "--batch", self.batch])
        wa._backfill_zero_marks(None)
        data = json.loads(self._timing().read_text(encoding="utf-8"))
        self.assertEqual(data["marks"], [])


class TestWriteCases(unittest.TestCase):
    """方向 1：本次实跑 case 标签落盘 cases-<batch_id>.json（三级回落识别 batch）。"""

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

    def _cases_file(self, bid=None):
        return wa.cdp_paths.log_apply_dir() / f"cases-{bid or self.batch}.json"

    def test_explicit_batch_id_writes(self):
        # 显式 batch_id 优先：cases-<batch_id>.json 落盘且内容与标签一致
        wa._write_cases(self.batch, "lcview-liveness,lcview-sepolicy-label")
        data = json.loads(self._cases_file().read_text(encoding="utf-8"))
        self.assertEqual(data["batch_id"], self.batch)
        self.assertEqual(data["cases"],
                         "lcview-liveness,lcview-sepolicy-label")

    def test_env_batch_id_fallback(self):
        # 无显式 batch_id → 环境变量 CDP_BATCH_ID 回落
        os.environ["CDP_BATCH_ID"] = "envbatch123456"
        try:
            wa._write_cases(None, "lcview-liveness")
        finally:
            os.environ.pop("CDP_BATCH_ID", None)
        data = json.loads(self._cases_file("envbatch123456")
                          .read_text(encoding="utf-8"))
        self.assertEqual(data["batch_id"], "envbatch123456")

    def test_unique_timing_file_fallback(self):
        # 无显式/环境变量 → log 目录唯一 timings 文件回落（复用 _mark_stage
        # 同款识别口径；多打点文件时静默跳过防误标）
        wa.cdp_timing.main(["start", "--batch", self.batch])
        wa._write_cases(None, "lcview-perf")
        data = json.loads(self._cases_file().read_text(encoding="utf-8"))
        self.assertEqual(data["batch_id"], self.batch)

    def test_no_batch_skips(self):
        # 无显式/环境变量/唯一打点文件 → 静默跳过不落盘（独立 CLI 无 batch
        # 上下文属正常降级，不阻断）
        wa._write_cases(None, "lcview-liveness")
        self.assertFalse(self._cases_file().exists())

    def test_empty_cases_skips(self):
        # 无实跑标签（--case 空）→ 不落盘（-s 批次无标签属正常）
        wa._write_cases(self.batch, "")
        self.assertFalse(self._cases_file().exists())


class TestConvertSince(unittest.TestCase):
    def _dev(self, epoch, tz="+0000"):
        def dev(cmd):
            if cmd == "date +%z":
                return (tz, 0)
            return (f"{int(epoch)}", 0)
        return dev

    def test_cst_to_utc_device_converted(self):
        # 设备 UTC（+0000）、epoch 与本地一致：本地 CST 时刻须换算为设备 UTC
        # 表示（时区差 8h，PIT-5 复发场景——直接传本地时刻会落在设备未来）
        local_since = "08-28 11:16:00.000"
        local_epoch = wa._parse_since_epoch(local_since)
        with mock.patch.object(wa.time, "time", return_value=local_epoch):
            since, err = wa.convert_since_to_device(local_since,
                                                    self._dev(local_epoch))
        self.assertIsNone(err)
        expect = (datetime.fromtimestamp(local_epoch, tz=timezone.utc)
                  .strftime("%m-%d %H:%M:%S.%f")[:-3])
        self.assertEqual(since, expect)
        self.assertNotEqual(since, local_since)

    def test_device_clock_behind_local(self):
        # PIT-5：设备时钟落后本地 1h → 换算后时间窗相应提前 1h
        local_since = "2026-08-28 11:16:00.000"
        local_epoch = wa._parse_since_epoch(local_since)
        device_epoch = local_epoch - 3600
        with mock.patch.object(wa.time, "time", return_value=local_epoch):
            since, err = wa.convert_since_to_device(local_since,
                                                    self._dev(device_epoch))
        self.assertIsNone(err)
        expect = (datetime.fromtimestamp(device_epoch, tz=timezone.utc)
                  .strftime("%Y-%m-%d %H:%M:%S.%f")[:-3])
        self.assertEqual(since, expect)

    def test_device_clock_unreadable(self):
        def dev(cmd):
            return ("", 1)
        with mock.patch.object(wa.time, "time", return_value=0.0):
            since, err = wa.convert_since_to_device("08-28 11:16:00.000", dev)
        self.assertIsNone(since)
        self.assertIn("设备时钟", err)

    def test_device_tz_illegal(self):
        def dev(cmd):
            if cmd == "date +%z":
                return ("CST", 0)
            return ("100", 0)
        with mock.patch.object(wa.time, "time", return_value=0.0):
            since, err = wa.convert_since_to_device("08-28 11:16:00.000", dev)
        self.assertIsNone(since)
        self.assertIn("时区", err)

    def test_year_prefixed_kept_in_output(self):
        # 有年格式输出保留年份前缀（reboot 跨年场景窗起点明确）
        local_since = "2026-08-28 11:16:00.000"
        local_epoch = wa._parse_since_epoch(local_since)
        with mock.patch.object(wa.time, "time", return_value=local_epoch):
            since, err = wa.convert_since_to_device(local_since,
                                                    self._dev(local_epoch))
        self.assertIsNone(err)
        self.assertTrue(since.startswith("2026-08-28 "))


class TestDeviceSerial(unittest.TestCase):
    """方向 3：设备身份标识回落——ro.serialno → ro.boot.serialno → eth0 MAC，
    三者皆空判红。"""

    def _exec(self, serialno="", boot_serialno="", mac="", mac_rc=0):
        def adb_exec(cmd):
            if cmd == "getprop ro.serialno":
                return (serialno, 0)
            if cmd == "getprop ro.boot.serialno":
                return (boot_serialno, 0)
            if cmd == "cat /sys/class/net/eth0/address":
                return (mac, mac_rc)
            return ("", 0)
        return adb_exec

    def test_ro_serialno_primary(self):
        serial, src = wa._device_serial(self._exec(serialno="SN123"))
        self.assertEqual(serial, "SN123")
        self.assertEqual(src, "getprop ro.serialno")

    def test_fallback_to_boot_serialno(self):
        # ro.serialno 空 → 回落 ro.boot.serialno
        serial, src = wa._device_serial(self._exec(boot_serialno="BSN456"))
        self.assertEqual(serial, "BSN456")
        self.assertEqual(src, "getprop ro.boot.serialno")

    def test_fallback_to_eth0_mac(self):
        # ro.serialno 与 ro.boot.serialno 皆空 → 回落 eth0 MAC（命令成功且非空）
        serial, src = wa._device_serial(self._exec(mac="02:00:00:aa:bb:cc"))
        self.assertEqual(serial, "02:00:00:aa:bb:cc")
        self.assertEqual(src, "eth0 MAC")

    def test_mac_requires_success_and_nonempty(self):
        # eth0 MAC 命令失败或为空 → 不算（仍判空）
        serial, _ = wa._device_serial(self._exec(mac="", mac_rc=1))
        self.assertIsNone(serial)
        serial, _ = wa._device_serial(self._exec(mac="", mac_rc=0))
        self.assertIsNone(serial)

    def test_all_empty_returns_none(self):
        # 三者皆空 → None（调用方判红）
        serial, src = wa._device_serial(self._exec())
        self.assertIsNone(serial)
        self.assertEqual(src, "")


class TestHostcmdEnv(unittest.TestCase):
    """方向 2：hostcmd 子进程按 run_id 导出轮次隔离基线文件环境变量。"""

    def test_env_exports_baseline_paths_by_run_id(self):
        env = wa._hostcmd_env("run12345678")
        self.assertEqual(env["LCVIEW_BASELINE_FILE"],
                         "/tmp/lcview_baseline_run12345678.json")
        self.assertEqual(env["LCIOD_BASELINE_FILE"],
                         "/tmp/lciod_baseline_run12345678.json")

    def test_execute_tag_passes_host_env_to_subprocess(self):
        # execute_tag 带 host_env 时，hostcmd 子进程 env 含轮次隔离基线路径
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "ok"
        fake.stderr = ""
        host_env = wa._hostcmd_env("run999")
        with mock.patch.object(wa.subprocess, "run", return_value=fake) as m:
            status, _ = wa.execute_tag(
                'hostcmd:"cases/lcview_check.sh --mode files"',
                adb_exec=None, adb_logcat=None, host_env=host_env)
        self.assertEqual(status, "pass")
        self.assertEqual(m.call_args.kwargs["env"],
                         host_env)

    def test_execute_tag_host_env_none_keeps_default(self):
        # host_env 为 None（独立 CLI/测试）→ 不传 env，行为不变（继承环境）
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = "ok"
        fake.stderr = ""
        with mock.patch.object(wa.subprocess, "run", return_value=fake) as m:
            status, _ = wa.execute_tag('hostcmd:"echo ok"',
                                       adb_exec=None, adb_logcat=None)
        self.assertEqual(status, "pass")
        self.assertNotIn("env", m.call_args.kwargs)


class TestRunIdLifecycle(unittest.TestCase):
    """方向 1：run_id 提前到执行前生成，沿用到 hostcmd 环境与产物。"""

    def test_product_run_id_matches_hostcmd_env_run_id(self):
        # 产物 run_id 与 hostcmd 基线环境变量同源（同一次执行生成一次）
        out_json = Path(tempfile.mkdtemp()) / "acc.json"
        captured = {}

        def fake_run_acceptance(acc, adb_exec, adb_logcat, ensure_boot=False,
                                on_item=None, host_env=None):
            captured["host_env"] = host_env
            return "pass", [{"tag": "boot", "status": "pass", "detail": "ok"}]

        with mock.patch.object(wa, "ac") as m_ac:
            m_ac.ensure_connected.side_effect = ["ep", "ep"]
            m_ac.build_exec_cmd.side_effect = lambda c: ["adb", "shell", c]
            m_ac.parse_exec_output.return_value = ("1", 0)
            m_ac.build_logcat_cmd.return_value = ["adb", "logcat", "-d"]
            m_sub = mock.Mock()
            m_sub.run.return_value.stdout = "out\n__LE_EXIT_CODE__=0\n"
            m_sub.TimeoutExpired = subprocess.TimeoutExpired
            with mock.patch.object(wa.subprocess, "run", m_sub):
                with mock.patch.object(wa, "run_acceptance",
                                       side_effect=fake_run_acceptance):
                    with mock.patch.object(wa, "_device_serial",
                                           return_value=("SN1",
                                                         "getprop ro.serialno")):
                        rc = wa.main(["run", "--acceptance", "boot",
                                      "--result-file", str(out_json)])
        self.assertEqual(rc, 0)
        data = json.loads(out_json.read_text(encoding="utf-8"))
        env = captured["host_env"]
        env_run_id = env["LCVIEW_BASELINE_FILE"].rsplit("_", 1)[-1].split(".")[0]
        self.assertEqual(data["run_id"], env_run_id)
        self.assertTrue(data["run_id"])
        self.assertEqual(data["device_serial"], "SN1")
        # 方向 4：产物注明身份标识只认基镜像
        self.assertIn("只认基镜像", data["identity_note"])
        self.assertIn("增量推送不改变", data["identity_note"])

    def test_device_serial_all_empty_red(self):
        # 方向 3：产物写入路径上序列号三者皆空 → 判红返 1（不写产物）
        out_json = Path(tempfile.mkdtemp()) / "acc.json"
        with mock.patch.object(wa, "ac") as m_ac:
            m_ac.ensure_connected.side_effect = ["ep", "ep"]
            m_ac.build_exec_cmd.side_effect = lambda c: ["adb", "shell", c]
            m_ac.parse_exec_output.return_value = ("1", 0)
            m_ac.build_logcat_cmd.return_value = ["adb", "logcat", "-d"]
            m_sub = mock.Mock()
            m_sub.run.return_value.stdout = "out\n__LE_EXIT_CODE__=0\n"
            m_sub.TimeoutExpired = subprocess.TimeoutExpired
            buf = io.StringIO()
            with mock.patch.object(wa.subprocess, "run", m_sub):
                with mock.patch.object(wa, "run_acceptance",
                                       return_value=("pass", [])):
                    with mock.patch.object(wa, "_device_serial",
                                           return_value=(None, "")):
                        with contextlib.redirect_stdout(buf):
                            rc = wa.main(["run", "--acceptance", "boot",
                                          "--result-file", str(out_json)])
        self.assertEqual(rc, 1)
        self.assertIn("判红", buf.getvalue())
        self.assertFalse(out_json.exists())


class TestRunCaseLifecycle(unittest.TestCase):
    """方向 2/3/4：副作用用例生命周期编排（固定顺序/差分恢复/dirty/超时）。"""

    @staticmethod
    def _lc(setup=None, teardown=None, timeout=None):
        d = {}
        if setup is not None:
            d["setup_snapshot"] = setup
        if teardown is not None:
            d["teardown"] = teardown
        if timeout is not None:
            d["timeout_s"] = timeout
        return d

    def test_fixed_order_fail_then_forensics_then_teardown(self):
        # 方向 2 固定顺序：first_error → ws_forensics 取证 → teardown → 返回
        events = []
        fake_exec = lambda cmd: ("stopped", 1)
        with mock.patch.object(wa, "_run_host_cmd", return_value=("1", 0)), \
                mock.patch.object(wa, "_run_forensics",
                               side_effect=lambda *a, **k:
                                   events.append("forensics") or "/f"), \
                mock.patch.object(wa, "_restore_state",
                                  side_effect=lambda *a, **k:
                                      events.append("teardown") or (False, "ok")):
            overall, items, meta = wa.run_case_lifecycle(
                "svc:no_such_svc", self._lc(
                    setup=['adb shell "cat x"'],
                    teardown=['adb shell "echo ${SNAPSHOT_0} > x"']),
                fake_exec, lambda **k: "", ep="ep", host_env={})
        self.assertEqual(overall, "fail")
        self.assertEqual(events, ["forensics", "teardown"])
        self.assertEqual(meta["forensics_dir"], "/f")
        self.assertFalse(meta["device_dirty"])

    def test_teardown_skipped_when_state_unchanged(self):
        # 方向 3：状态未变（重读快照与初值同）→ teardown 命令不执行（不写设备）
        reads = iter([("1\n", 0), ("1\n", 0), ("1\n", 0)])
        executed = []
        def fake_host(cmd, host_env=None):
            if "echo" in cmd:
                executed.append(cmd)
                return "0", 0
            return next(reads)
        with mock.patch.object(wa, "_run_host_cmd", side_effect=fake_host):
            dirty, detail = wa._restore_state(
                ['adb shell "cat authorized"'],
                ['adb shell "echo ${SNAPSHOT_0} > authorized"'], ["1"])
        self.assertFalse(dirty)
        self.assertEqual(executed, [])
        self.assertIn("跳过", detail)

    def test_teardown_restores_changed_state(self):
        # 方向 3：状态变了 → teardown 执行且 ${SNAPSHOT_0} 展开为快照值 →
        # 复核恢复 → 不脏（读序：重拍=0 已变 → teardown → 复核=1 已恢复）
        reads = iter(["0\n", "1\n"])
        executed = []
        def fake_host(cmd, host_env=None):
            if "echo" in cmd:
                executed.append(cmd)
                return "0", 0
            return next(reads), 0
        with mock.patch.object(wa, "_run_host_cmd", side_effect=fake_host):
            dirty, detail = wa._restore_state(
                ['adb shell "cat authorized"'],
                ['adb shell "echo ${SNAPSHOT_0} > authorized"'], ["1"])
        self.assertFalse(dirty)
        self.assertEqual(len(executed), 1)
        self.assertIn("echo 1 >", executed[0])

    def test_teardown_command_failure_marks_dirty(self):
        # 方向 3：teardown 命令 rc!=0 → device_dirty（读序：重拍=0 已变）
        reads = iter(["0\n"])
        def fake_host(cmd, host_env=None):
            if "echo" in cmd:
                return "boom", 1
            return next(reads), 0
        with mock.patch.object(wa, "_run_host_cmd", side_effect=fake_host):
            dirty, detail = wa._restore_state(
                ['adb shell "cat authorized"'],
                ['adb shell "echo ${SNAPSHOT_0} > authorized"'], ["1"])
        self.assertTrue(dirty)
        self.assertIn("失败 rc=1", detail)

    def test_teardown_still_dirty_after_restore(self):
        # 方向 3：恢复后重读快照仍与初值不符 → device_dirty
        # （读序列：重拍=0 已变更 → teardown → 复核拍=0 仍未恢复）
        reads = iter(["0\n", "0\n"])
        def fake_host(cmd, host_env=None):
            if "echo" in cmd:
                return "", 0
            return next(reads), 0
        with mock.patch.object(wa, "_run_host_cmd", side_effect=fake_host):
            dirty, detail = wa._restore_state(
                ['adb shell "cat authorized"'],
                ['adb shell "echo ${SNAPSHOT_0} > authorized"'], ["1"])
        self.assertTrue(dirty)
        self.assertIn("仍与初值不符", detail)

    def test_snapshot_failure_is_fail_and_dirty(self):
        # 快照失败 = 状态不可知 → 判红 + dirty + 跳过 teardown（无初值可恢复）
        with mock.patch.object(wa, "_run_host_cmd", return_value=("", 1)), \
                mock.patch.object(wa, "_run_forensics", return_value=None):
            overall, items, meta = wa.run_case_lifecycle(
                "svc:a", self._lc(setup=['adb shell "cat x"'],
                                  teardown=['adb shell "echo 0 > x"']),
                lambda c: ("running", 0), lambda **k: "", ep="ep")
        self.assertEqual(overall, "fail")
        self.assertTrue(meta["device_dirty"])
        self.assertIn("跳过 teardown", meta["teardown_detail"])

    def test_timeout_interrupts_remaining_tags(self):
        # 方向 4：deadline 中断剩余判据项并落 __timeout__ 标记项
        def slow_tag(tag, *a, **k):
            time.sleep(0.06)
            return ("pass", "ok")
        with mock.patch.object(wa, "execute_tag", side_effect=slow_tag):
            overall, items = wa.run_acceptance(
                "svc:a svc:b svc:c svc:d svc:e", lambda c: ("", 0),
                lambda **k: "", deadline=time.monotonic() + 0.12)
        # 超时即失败（判据未跑完），items 落 __timeout__ 标记项
        self.assertEqual(overall, "fail")
        self.assertLess(len(items), 5)
        self.assertEqual(items[-1]["tag"], "__timeout__")

    def test_timeout_walks_same_lifecycle_order(self):
        # 方向 4：超时按同一顺序收尾（取证 + teardown 均执行）
        lc = self._lc(setup=['adb shell "cat x"'],
                      teardown=['adb shell "echo ${SNAPSHOT_0} > x"'],
                      timeout=1)
        events = []
        def slow_tag(tag, *a, **k):
            time.sleep(0.3)
            return ("pass", "ok")
        with mock.patch.object(wa, "_run_host_cmd", return_value=("1", 0)), \
                mock.patch.object(wa, "execute_tag", side_effect=slow_tag), \
                mock.patch.object(wa, "_run_forensics",
                                  side_effect=lambda *a, **k:
                                      events.append("forensics") or "/f"), \
                mock.patch.object(wa, "_restore_state",
                                  side_effect=lambda *a, **k:
                                      events.append("teardown") or (False, "ok")):
            overall, items, meta = wa.run_case_lifecycle(
                "svc:a svc:b svc:c svc:d svc:e", lc, lambda c: ("", 0),
                lambda **k: "", ep="ep")
        self.assertTrue(meta["timed_out"])
        self.assertEqual(events, ["forensics", "teardown"])

    def test_pass_without_error_no_forensics(self):
        # 全过 → 不取证（取证只对 first_error），teardown 仍执行（差分守门）
        events = []
        with mock.patch.object(wa, "_run_host_cmd", return_value=("1", 0)), \
                mock.patch.object(wa, "_run_forensics",
                               side_effect=lambda *a, **k:
                                   events.append("forensics") or "/f"), \
                mock.patch.object(wa, "_restore_state",
                                  side_effect=lambda *a, **k:
                                      events.append("teardown") or (False, "ok")):
            overall, items, meta = wa.run_case_lifecycle(
                "svc:a", self._lc(setup=['adb shell "cat x"'],
                                  teardown=['adb shell "echo ${SNAPSHOT_0} > x"']),
                lambda c: ("running", 0), lambda **k: "", ep="ep")
        self.assertEqual(overall, "pass")
        self.assertEqual(events, ["teardown"])


class TestLoadLifecycle(unittest.TestCase):
    """方向 1：cases 段 dict 生命周期形态（str 旧形态兼容）。"""

    def test_dict_form_loads(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text(
                "cases:\n"
                "  x:\n"
                "    acceptance: 'svc:a'\n"
                "    setup_snapshot: ['c1']\n"
                "    teardown: ['c2']\n"
                "    timeout_s: 60\n",
                encoding="utf-8")
            lc = wa._load_lifecycle(str(cfg), "x")
        self.assertEqual(lc["timeout_s"], 60)
        self.assertEqual(lc["setup_snapshot"], ["c1"])
        self.assertEqual(lc["teardown"], ["c2"])

    def test_str_form_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text("cases:\n  x: 'svc:a'\n", encoding="utf-8")
            self.assertIsNone(wa._load_lifecycle(str(cfg), "x"))

    def test_dict_missing_acceptance_raises(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text("cases:\n  x:\n    timeout_s: 60\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                wa._load_lifecycle(str(cfg), "x")

    def test_unknown_label_raises(self):
        with tempfile.TemporaryDirectory() as d:
            cfg = Path(d) / "verify-cases.yaml"
            cfg.write_text("cases: {}\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                wa._load_lifecycle(str(cfg), "no-such")

    def test_case_text_dict_extracts_acceptance(self):
        self.assertEqual(wa._case_text({"acceptance": "svc:a"}), "svc:a")
        self.assertEqual(wa._case_text("svc:a"), "svc:a")


if __name__ == "__main__":
    unittest.main()