import argparse
import contextlib
import io
import subprocess
import sys
import tempfile
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

    def test_inbuilt_lcview_liveness_present(self):
        # 资产层内建 lcview-liveness 可解析（daemon 直读内核 + 持续心跳 +
        # logfield 字段断言 0（overrun/dropped/readErr，防子串命中历史零值
        # 心跳假绿）+ boot 判据；HAL 已退役，svc 只留 lechao_lcview，
        # conserve 已迁至 lcview-transfer 不在本用例）
        acc, err = wa.resolve_acceptance(self._args(case="lcview-liveness"))
        self.assertIsNone(err)
        self.assertEqual(
            acc,
            'svc:lechao_lcview log:"heartbeat, loop=" '
            'logfield:"heartbeat, loop=|overrun|=|0" '
            'logfield:"heartbeat, loop=|dropped|=|0" '
            'logfield:"heartbeat, loop=|readErr|=|0" boot')
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
        # 校准回拨前未来记录在此暴露，trigger 的 ts 已 baseline 限定只判新记录）
        acc, err = wa.resolve_acceptance(self._args(case="lcview-pipeline-warn"))
        self.assertIsNone(err)
        self.assertIn('hostcmd:"cases/lcview_check.sh --mode invalid"', acc)
        self.assertIn('hostcmd:"cases/lcview_check.sh --mode ts --skew 600"', acc)
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
        # ts 以 --baseline 显式限定（只判基线后新记录，全历史卫生检查在 warn）
        self.assertIn("--mode ts --skew 600 --baseline /tmp/lcview_baseline.json", acc)
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
        # 且无未剥转义引号残留
        data = yaml.safe_load(
            Path(wa._CASES_PATH).read_text(encoding="utf-8")) or {}
        cases = data.get("cases") or {}
        self.assertTrue(cases)
        for name, acc in cases.items():
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


if __name__ == "__main__":
    unittest.main()