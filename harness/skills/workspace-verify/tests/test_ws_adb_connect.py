import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ws_adb_connect as ac
import ws_serial


class TestCmdBuild(unittest.TestCase):
    def test_connect_static(self):
        self.assertEqual(ac.build_connect_cmd("10.0.0.5:5555"),
                         ["adb", "connect", "10.0.0.5:5555"])

    def test_exec_returns_exit_code_tagged(self):
        joined = " ".join(ac.build_exec_cmd("getprop ro.build.version"))
        self.assertIn("__LE_EXIT_CODE__", joined)

    def test_logcat_tail(self):
        joined = " ".join(ac.build_logcat_cmd("lechao", tail=200))
        self.assertIn("-d", joined)
        self.assertIn("200", joined)

    def test_logcat_since_overrides_tail(self):
        # 给出 since 时以 -t <since> 收窄时间窗（代 -t <tail>，避免命中旧日志）
        cmd = ac.build_logcat_cmd(None, 5000, since="08-26 10:00:00.000")
        self.assertIn("-t", cmd)
        self.assertEqual(cmd[cmd.index("-t") + 1], "08-26 10:00:00.000")
        self.assertNotIn("5000", cmd)

    def test_logcat_pid_narrows_by_process(self):
        # pid 非空 → 追加 --pid=<pid> 按进程归属收窄（logfield 5 段写法，
        # 防旧进程心跳残留行被当新进程心跳）；缺省/空 pid 不追加
        cmd = ac.build_logcat_cmd(None, 5000, pid="4242")
        self.assertIn("--pid=4242", cmd)
        self.assertNotIn("--pid=", " ".join(ac.build_logcat_cmd(None, 5000)))
        self.assertNotIn("--pid=", " ".join(ac.build_logcat_cmd(None, 5000, pid=None)))

    def test_parse_devices_states(self):
        out = ("List of devices attached\n"
               "192.168.1.5:5555\tdevice\n"
               "10.0.0.9:5555\toffline\n"
               "rp5.local:5555\tunauthorized\n")
        d = ac.parse_devices(out)
        self.assertEqual(d["192.168.1.5:5555"], "device")
        self.assertEqual(d["10.0.0.9:5555"], "offline")
        self.assertNotIn("List of", d)

    def test_is_online_by_full_serial_state(self):
        # 只有 serial 全匹配且 state==device 才在线（offline/unauthorized/子串不算）
        self.assertTrue(ac._state_online("192.168.1.5:5555",
                                         "List of devices attached\n192.168.1.5:5555\tdevice\n"))
        self.assertFalse(ac._state_online("192.168.1.5:5555",
                                          "List of devices attached\n192.168.1.5:5555\toffline\n"))
        # 子串不得误配（10.0.0.5 不能命中 10.0.0.50）
        self.assertFalse(ac._state_online("10.0.0.5:5555",
                                          "List of devices attached\n10.0.0.50:5555\tdevice\n"))

    def test_mdns_excludes_tls_pairing(self):
        # _adb-tls-pairing._tcp 服务不入选（仅普通 adb 端点）
        fake = mock.Mock()
        fake.returncode = 0
        fake.stdout = ("rp5.local._adb._tcp local 192.168.1.5:5555\n"
                       "rp5.local._adb-tls-pairing._tcp local 192.168.1.5:5555\n")
        with mock.patch.object(ac.subprocess, "run", return_value=fake):
            eps = ac.mdns_discover()
        self.assertEqual(eps, ["192.168.1.5:5555"])


class TestEnsureFailureDetail(unittest.TestCase):
    def test_local_domain_single_out(self):
        # 方向 6：.local 域名解析失败须单列（PIT-1 复发可辨），mDNS 与静态两因分开
        with mock.patch.object(ac, "mdns_discover", return_value=[]):
            with mock.patch.dict("os.environ", {}, clear=False):
                parts = ac._ensure_failure_detail()
        self.assertEqual(len(parts), 2)
        self.assertIn("mDNS 未发现端点", parts[0])
        self.assertIn(".local 域名解析失败", parts[1])
        self.assertIn("PIT-1", parts[1])

    def test_static_ip_no_local_hint(self):
        # 静态地址非 .local：不单列域名解析提示
        with mock.patch.object(ac, "mdns_discover", return_value=["10.0.0.9:5555"]):
            with mock.patch.dict("os.environ",
                                 {"LC_VERIFY_ADB_HOST": "10.0.0.9"}):
                parts = ac._ensure_failure_detail()
        self.assertEqual(len(parts), 2)
        self.assertIn("10.0.0.9", parts[1])
        self.assertNotIn(".local", parts[1])

    def test_static_endpoint_from_host_port(self):
        # 静态端点推导走 host_port()（单一事实源，不重复推导默认值）
        with mock.patch.object(ac, "mdns_discover", return_value=[]):
            with mock.patch.object(ac, "host_port", return_value="10.1.2.3:4444"):
                parts = ac._ensure_failure_detail()
        self.assertIn("10.1.2.3:4444", parts[1])


class TestRescue(unittest.TestCase):
    def _fake_ws_serial(self, exec_results, read_text=None):
        fake = mock.Mock()
        fake.SerialError = ws_serial.SerialError  # except 需要真异常类
        fake._IPV4_RE = ws_serial._IPV4_RE
        fake._BAD_IPS = ws_serial._BAD_IPS
        fake.serial_endpoint = lambda: ("127.0.0.1", 9700)
        fake.SerialConn.return_value.connect.side_effect = None
        fake._execute.side_effect = exec_results
        fake._read_some.return_value = read_text if read_text is not None else ""
        return fake

    def _patch_sleep(self):
        # adbd 重启后有 2s settle（方向 4），测试中跳过真等待——等待统一经
        # ac._sleep 注入点（方向 5：rescue/clock_sync 窗口口径一致），
        # patch ac.time.sleep 不再能拦截 _sleep 名字绑定
        return mock.patch.object(ac, "_sleep")

    def _run_rescue(self, fake, **kw):
        with mock.patch.dict(sys.modules, {"ws_serial": fake}):
            with self._patch_sleep():
                return ac.rescue(**kw)

    def test_success_returns_endpoint(self):
        # 设 tcp.port + 重启 adbd + 取 wlan0 IPv4 → 返回端点与 ok 态
        fake = self._fake_ws_serial(
            [("", 0), ("2: wlan0 inet 192.168.1.28/24\n", 0)])
        ep, state, detail = self._run_rescue(fake)
        self.assertEqual(ep, "192.168.1.28:5555")
        self.assertEqual(state, "ok")
        self.assertIn("救援成功", detail)
        # 命令含 setprop + adbd 重启（副作用须在 detail 明示）
        cmd = fake._execute.call_args_list[0].args[1]
        self.assertIn("service.adb.tcp.port 5555", cmd)
        self.assertIn("stop adbd", cmd)
        self.assertIn("start adbd", cmd)
        self.assertIn("adbd 已重启", detail)
        # 方向 1：成功路径也关连接（finally close，防 socket 泄漏）
        fake.SerialConn.return_value.close.assert_called_once()

    def test_all_paths_close_conn(self):
        # 方向 1：各返回路径（含异常/失败）均 finally close
        err = ws_serial.SerialError("DEVICE_UNRESPONSIVE", "timeout")
        fake = self._fake_ws_serial([err])
        self._run_rescue(fake)
        fake.SerialConn.return_value.close.assert_called_once()

    def test_serial_port_from_endpoint_source(self):
        # 方向 3：rescue 转发器端口走 ws_serial.serial_endpoint 单一事实源
        # （消 LC_SERIAL_HOST/PORT 默认值在 rescue 与 ws_serial 重复推导）
        fake = self._fake_ws_serial([("", 0), ("", 0)])
        fake.serial_endpoint = lambda: ("10.0.0.1", 9600)
        self._run_rescue(fake)
        args = fake.SerialConn.call_args.args
        self.assertEqual(args[0], "10.0.0.1")
        self.assertEqual(args[1], 9600)

    def test_serial_silent_full_brick(self):
        # SERIAL_SILENT（串口静默无输出）→ 全砖
        err = ws_serial.SerialError("SERIAL_SILENT", "recv 返空")
        fake = self._fake_ws_serial([err])
        ep, state, detail = self._run_rescue(fake)
        self.assertIsNone(ep)
        self.assertEqual(state, "full_brick")
        self.assertIn("断电全砖", detail)

    def test_no_ipv4_half_brick(self):
        # 链路通但无有效 IPv4 → 半砖（设备无网）
        fake = self._fake_ws_serial(
            [("", 0), ("2: wlan0 <BROADCAST,MULTICAST,UP>\n", 0)])
        ep, state, detail = self._run_rescue(fake)
        self.assertIsNone(ep)
        self.assertEqual(state, "half_brick")
        self.assertIn("无有效 IPv4", detail)

    def test_adbd_failed_half_brick(self):
        # adbd 重启命令 exit != 0 → 半砖（adbd 未起）
        fake = self._fake_ws_serial([("cannot start", 1)])
        ep, state, detail = self._run_rescue(fake)
        self.assertIsNone(ep)
        self.assertEqual(state, "half_brick")
        self.assertIn("adbd 未起", detail)

    def test_boot_loop_detected(self):
        # 无 IP 且串口反复相同启动日志 → boot loop
        fake = self._fake_ws_serial(
            [("", 0), ("2: wlan0 <BROADCAST,MULTICAST,UP>\n", 0)],
            read_text="Booting...\nBooting...\nBooting...\n")
        ep, state, detail = self._run_rescue(fake)
        self.assertIsNone(ep)
        self.assertEqual(state, "boot_loop")
        self.assertIn("boot loop", detail)

    def test_connect_failure_rescue_unavailable(self):
        # 方向 2：TCP 连不上是转发器未起（救援通道不可用），非设备断电全砖，
        # 不得错判 full_brick（此前测试固化错分，本次同步修正）
        err = ws_serial.SerialError("ENDPOINT_UNREACHABLE", "refused")
        fake = mock.Mock()
        fake.SerialError = ws_serial.SerialError
        fake.serial_endpoint = lambda: ("127.0.0.1", 9700)
        fake.SerialConn.return_value.connect.side_effect = err
        ep, state, detail = self._run_rescue(fake)
        self.assertIsNone(ep)
        self.assertEqual(state, "rescue_unavailable")
        self.assertIn("救援通道不可用", detail)
        self.assertIn("转发器未起", detail)
        fake.SerialConn.return_value.close.assert_called_once()


class TestEnsureConnectedRescueLevel(unittest.TestCase):
    """ensure 编排（方向 5：verify_push 打点已移至 ws_push.py 推送循环
    完成后，ensure CLI 连接成功不再自动打点）：

    本类用例不触发真实 adb；历史口径曾在此隔离 CDP_PROJECT_ROOT 防
    自动打点写入真实批次打点文件，隔离保留防回归（打点行为已删除，
    隔离无害）。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_root = os.environ.get("CDP_PROJECT_ROOT")
        os.environ["CDP_PROJECT_ROOT"] = self._tmp.name

    def tearDown(self):
        if self._old_root is None:
            os.environ.pop("CDP_PROJECT_ROOT", None)
        else:
            os.environ["CDP_PROJECT_ROOT"] = self._old_root
        self._tmp.cleanup()

    def test_ensure_cli_does_not_mark_verify_push(self):
        # 方向 5：verify_push 打点移至 ws_push.py 实际推送循环完成后，
        # ensure CLI 连接成功不得再打点——打点函数已从本模块删除
        # （无函数可 patch），并以"无子进程调用"兜底断言打点未复活
        self.assertFalse(hasattr(ac, "_mark_stage"))
        with mock.patch.object(ac, "ensure_connected", return_value="ep"):
            with mock.patch.object(ac, "_ensure_failure_detail", return_value=[]):
                with mock.patch.object(ac.subprocess, "run") as run:
                    ac.main(["ensure"])
        run.assert_not_called()

    def test_third_level_rescue_connects(self):
        # mDNS 与静态皆败 → rescue 取端点 → connect 复核在线（第三级通道，
        # rescue_enabled 由调用方显式开）；配置未设（env_path 空）时
        # 身份校验跳过，rescue 端点直接可用
        with mock.patch.object(ac, "env_path", return_value=""), \
                mock.patch.object(ac, "mdns_discover", return_value=[]):
            with mock.patch.object(ac, "host_port", return_value="rp5.local:5555"):
                with mock.patch.object(ac, "rescue",
                                       return_value=("10.9.9.9:5555", "ok",
                                                     "串口救援成功 10.9.9.9:5555")):
                    # 静态端点不在线，rescue 端点在线（按参数区分）
                    with mock.patch.object(
                            ac, "_is_online",
                            side_effect=lambda ep: ep == "10.9.9.9:5555") as online:
                        with mock.patch.object(ac.subprocess, "run"):
                            ep = ac.ensure_connected(rescue_enabled=True)
        self.assertEqual(ep, "10.9.9.9:5555")
        self.assertEqual(online.call_count, 2)

    def test_third_level_offline_after_connect(self):
        # rescue 取到端点但 connect 复核不在线 → None（adbd 未就绪）
        with mock.patch.object(ac, "mdns_discover", return_value=[]):
            with mock.patch.object(ac, "host_port", return_value="rp5.local:5555"):
                with mock.patch.object(ac, "rescue",
                                       return_value=("10.9.9.9:5555", "ok",
                                                     "串口救援成功")):
                    with mock.patch.object(ac, "_is_online",
                                           return_value=False):
                        with mock.patch.object(ac.subprocess, "run"):
                            ep = ac.ensure_connected()
        self.assertIsNone(ep)

    def test_rescue_disabled_returns_none(self):
        # rescue_enabled=False（默认，方向 6）→ 不触发串口救援（无重启 adbd 副作用）
        with mock.patch.object(ac, "mdns_discover", return_value=[]):
            with mock.patch.object(ac, "host_port", return_value="rp5.local:5555"):
                with mock.patch.object(ac, "_is_online", return_value=False):
                    with mock.patch.object(ac.subprocess, "run"):
                        with mock.patch.object(ac, "rescue") as r:
                            ep = ac.ensure_connected()
        self.assertIsNone(ep)
        r.assert_not_called()

    def test_ensure_cli_default_no_rescue(self):
        # 方向 6：ensure CLI 不带 --rescue → rescue_enabled=False（默认关）
        with mock.patch.object(ac, "ensure_connected") as ec:
            with mock.patch.object(ac, "_ensure_failure_detail", return_value=[]):
                ac.main(["ensure"])
        self.assertEqual(ec.call_args.kwargs.get("rescue_enabled"), False)

    def test_ensure_cli_flag_enables_rescue(self):
        # ensure --rescue 显式开（副作用动作须调用方显式要求）
        with mock.patch.object(ac, "ensure_connected") as ec:
            with mock.patch.object(ac, "_ensure_failure_detail", return_value=[]):
                ac.main(["ensure", "--rescue"])
        self.assertEqual(ec.call_args.kwargs.get("rescue_enabled"), True)

    def test_fast_path_online_device_returns(self):
        # 方向 2：adb devices 在线预检快路径命中（已连接在线设备）→ 过身份
        # 校验直接返回，不触发 mDNS 逐候选 connect 重连（提速点：
        # verify_acceptance_connect 六次累计 961s 占全批 31.4%）
        with mock.patch.object(ac, "_adb_devices_online",
                               return_value=["10.0.0.5:5555"]), \
                mock.patch.object(ac, "_is_online", return_value=True), \
                mock.patch.object(ac, "_verify_identity",
                                  return_value=(True, "")) as vid, \
                mock.patch.object(ac, "mdns_discover") as mdns:
            ep = ac.ensure_connected()
        self.assertEqual(ep, "10.0.0.5:5555")
        vid.assert_called_once()
        mdns.assert_not_called()

    def test_fast_path_identity_mismatch_falls_through(self):
        # 快路径设备身份不符逐拒（防连错设备），回落 mDNS 继续尝试
        with mock.patch.object(ac, "_adb_devices_online",
                               return_value=["10.0.0.5:5555"]), \
                mock.patch.object(ac, "_is_online", return_value=True), \
                mock.patch.object(ac, "_verify_identity",
                                  side_effect=[(False, "身份不符"),
                                               (True, "")]), \
                mock.patch.object(ac, "mdns_discover",
                                  return_value=["10.0.0.6:5555"]), \
                mock.patch.object(ac, "host_port",
                                  return_value="rp5.local:5555"), \
                mock.patch.object(ac.subprocess, "run"):
            ep = ac.ensure_connected()
        self.assertEqual(ep, "10.0.0.6:5555")

    def test_fast_path_empty_falls_through(self):
        # 快路径无在线设备 → 走原 mDNS 路径（行为不变）
        with mock.patch.object(ac, "_adb_devices_online", return_value=[]), \
                mock.patch.object(ac, "mdns_discover",
                                  return_value=["10.0.0.6:5555"]), \
                mock.patch.object(ac, "_is_online", return_value=True), \
                mock.patch.object(ac, "_verify_identity",
                                  return_value=(True, "")), \
                mock.patch.object(ac.subprocess, "run"):
            ep = ac.ensure_connected()
        self.assertEqual(ep, "10.0.0.6:5555")


class TestClockSync(unittest.TestCase):
    def _fake(self, rc, stdout):
        f = mock.Mock()
        f.returncode = rc
        f.stdout = stdout
        return f

    def test_skew_within_threshold_noop(self):
        # 偏差 ≤ max_skew：只读取不修正（不调 root）
        now = int(__import__("time").time())
        fake = self._fake(0, f"{now}\n__LE_EXIT_CODE__=0\n")
        with mock.patch.object(ac.subprocess, "run", return_value=fake) as m:
            ok, detail = ac.clock_sync(max_skew=120)
        self.assertTrue(ok)
        self.assertIn("无需修正", detail)
        self.assertNotIn("root", [c.args[0][0] if c.args else "" for c in m.call_args_list])

    def test_skew_over_threshold_fixes(self):
        # 偏差 > max_skew：root → 重连 → date -u 修正 → 复核偏差落回阈值
        now = int(__import__("time").time())
        results = [
            self._fake(0, f"{now - 3600}\n__LE_EXIT_CODE__=0\n"),  # date +%s
            self._fake(0, ""),                                    # root
            self._fake(0, ""),                                    # connect
            self._fake(0, "ok\n__LE_EXIT_CODE__=0\n"),            # date -u 修正
            self._fake(0, f"{now}\n__LE_EXIT_CODE__=0\n"),        # 复核 date +%s
        ]
        with mock.patch.object(ac.subprocess, "run",
                               side_effect=results) as m, \
                mock.patch.object(ac, "_sleep") as sl:
            ok, detail = ac.clock_sync(max_skew=120)
        # 方向 5：root 重启 adbd settle 2s 经注入点（无真实等待）
        self.assertIn(mock.call(2), sl.call_args_list)
        self.assertTrue(ok)
        self.assertIn("已修正", detail)
        self.assertIn("复核偏差", detail)
        cmds = [c.args[0] for c in m.call_args_list]
        self.assertIn(["adb", "root"], cmds)
        date_cmds = [c for c in cmds if c[0] == "adb" and c[1] == "shell"
                     and "date" in c[2]]
        self.assertEqual(len(date_cmds), 3)
        # 修正命令须以 -u 下发（UTC 组串，避免设备时区解释引入新偏差）
        self.assertRegex(date_cmds[1][2], r"date -u \d{12}\.\d{2}")
        # 复核为纯读取（无 -u）
        self.assertIn("date +%s", date_cmds[2][2])
        self.assertNotIn("-u", date_cmds[2][2])

    def test_device_date_unreadable_fails(self):
        # 设备 date 返回非数字（exit 非 0）→ False，不进入修正
        fake = self._fake(1, "unknown\n__LE_EXIT_CODE__=1\n")
        with mock.patch.object(ac.subprocess, "run", return_value=fake) as m:
            ok, detail = ac.clock_sync()
        self.assertFalse(ok)
        self.assertIn("返回异常", detail)
        self.assertEqual(len(m.call_args_list), 1)

    def test_fix_failure_returns_false(self):
        # date 修正 exit 非 0 → False 且 detail 带退出码
        now = int(__import__("time").time())
        results = [
            self._fake(0, f"{now - 3600}\n__LE_EXIT_CODE__=0\n"),
            self._fake(0, ""),
            self._fake(0, ""),
            self._fake(1, "date: bad\n__LE_EXIT_CODE__=1\n"),
        ]
        with mock.patch.object(ac.subprocess, "run", side_effect=results), \
                mock.patch.object(ac, "_sleep"):
            ok, detail = ac.clock_sync()
        self.assertFalse(ok)
        self.assertIn("exit=1", detail)

    def test_fix_verify_still_skewed_fails(self):
        # 修正命令退出 0 但复核偏差仍超阈值（date 静默失败）→ False 拒宣布成功
        now = int(__import__("time").time())
        results = [
            self._fake(0, f"{now - 3600}\n__LE_EXIT_CODE__=0\n"),  # date +%s
            self._fake(0, ""),                                    # root
            self._fake(0, ""),                                    # connect
            self._fake(0, "ok\n__LE_EXIT_CODE__=0\n"),            # date -u 修正
            self._fake(0, f"{now - 3600}\n__LE_EXIT_CODE__=0\n"),  # 复核仍偏 1h
        ]
        with mock.patch.object(ac.subprocess, "run", side_effect=results), \
                mock.patch.object(ac, "_sleep"):
            ok, detail = ac.clock_sync(max_skew=120)
        self.assertFalse(ok)
        self.assertIn("复核未通过", detail)


class TestVerifyIdentity(unittest.TestCase):
    """方向 5：设备身份校验——LC_VERIFY_EXPECT_SERIAL 设置时核对序列号，不符即拒。"""

    def test_unset_expect_skips(self):
        # 期望来源含 paths.conf 配置位（env_path，支持环境变量覆盖）：
        # 配置未设（env 与 env_path 皆空）才跳过校验
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(ac, "env_path", return_value=""):
            ok, detail = ac._verify_identity("10.0.0.5:5555")
        self.assertTrue(ok)

    def test_match_passes(self):
        with mock.patch.dict("os.environ",
                             {"LC_VERIFY_EXPECT_SERIAL": "ABC123"}):
            with mock.patch.object(ac, "run_adb",
                                   return_value=("ABC123", 0)) as m:
                ok, detail = ac._verify_identity("10.0.0.5:5555")
        self.assertTrue(ok)
        # 校验经 -s 指定端点取 ro.serialno
        self.assertEqual(m.call_args.args[0][0:3], ["-s", "10.0.0.5:5555",
                                                    "shell"])

    def test_mismatch_rejected(self):
        with mock.patch.dict("os.environ",
                             {"LC_VERIFY_EXPECT_SERIAL": "ABC123"}):
            with mock.patch.object(ac, "run_adb",
                                   return_value=("OTHER99", 0)):
                ok, detail = ac._verify_identity("10.0.0.5:5555")
        self.assertFalse(ok)
        self.assertIn("身份不符", detail)

    def test_unreadable_rejected(self):
        with mock.patch.dict("os.environ",
                             {"LC_VERIFY_EXPECT_SERIAL": "ABC123"}):
            with mock.patch.object(ac, "run_adb", return_value=("", 1)):
                ok, detail = ac._verify_identity("10.0.0.5:5555")
        self.assertFalse(ok)
        self.assertIn("无法读取设备序列号", detail)


class TestEnsureIdentityCheck(unittest.TestCase):
    """方向 5：ensure_connected 内嵌期望身份校验——连上后序列号不符即拒，
    救援通道返回路径同样过校验。"""

    def test_static_identity_mismatch_rejected(self):
        # 静态端点在线但身份不符 → 拒，返回 None（不误连）
        with mock.patch.object(ac, "mdns_discover", return_value=[]), \
                mock.patch.object(ac, "host_port",
                                  return_value="10.0.0.5:5555"), \
                mock.patch.object(ac, "_is_online", return_value=True), \
                mock.patch.object(ac.subprocess, "run"), \
                mock.patch.object(ac, "_verify_identity",
                                  return_value=(False, "设备身份不符")):
            ep = ac.ensure_connected()
        self.assertIsNone(ep)

    def test_mdns_identity_skip_to_match(self):
        # mDNS 首端点身份不符 → 拒绝继续尝试，直至匹配端点返回
        with mock.patch.object(ac, "mdns_discover",
                               return_value=["10.0.0.1:5555",
                                             "10.0.0.2:5555"]), \
                mock.patch.object(ac, "_is_online", return_value=True), \
                mock.patch.object(ac.subprocess, "run"), \
                mock.patch.object(ac, "_verify_identity",
                                  side_effect=[(False, "身份不符"),
                                               (True, "")]):
            ep = ac.ensure_connected()
        self.assertEqual(ep, "10.0.0.2:5555")

    def test_rescue_identity_mismatch_rejected(self):
        # 救援通道端点身份不符 → 拒，返回 None（救援路径同过校验）
        with mock.patch.object(ac, "mdns_discover", return_value=[]), \
                mock.patch.object(ac, "host_port",
                                  return_value="rp5.local:5555"), \
                mock.patch.object(ac, "rescue",
                                  return_value=("10.9.9.9:5555", "ok",
                                                "串口救援成功")), \
                mock.patch.object(ac, "_is_online", return_value=True), \
                mock.patch.object(ac.subprocess, "run"), \
                mock.patch.object(ac, "_verify_identity",
                                  return_value=(False, "身份不符")):
            ep = ac.ensure_connected(rescue_enabled=True)
        self.assertIsNone(ep)


class TestEnsureConnectedBudget(unittest.TestCase):
    """A2：ensure_connected 连接预算——失败轮编排层先做带预算的廉价探测，
    预算耗尽快速失败（env_fail 归因），不进三级发现链长等待。默认
    budget_s=None 行为完全不变。"""

    def test_budget_exhausted_fails_fast(self):
        # 预算已耗尽（budget_s=0 即 deadline 已过）→ 不进入 mDNS 等任何
        # 后续发现级，返回 None 快速失败
        with mock.patch.object(ac, "_adb_devices_online", return_value=[]), \
                mock.patch.object(ac, "mdns_discover",
                                  side_effect=AssertionError("不应进入 mDNS")):
            self.assertIsNone(ac.ensure_connected(budget_s=0))

    def test_budget_none_walks_discovery(self):
        # 不传预算 → 现行为不变：快路径未命中后仍进 mDNS 发现；
        # mDNS 空 + 静态 host_port=None + rescue 默认关 → 返回 None
        with mock.patch.object(ac, "_adb_devices_online", return_value=[]), \
                mock.patch.object(ac, "mdns_discover", return_value=[]) as md, \
                mock.patch.object(ac, "host_port", return_value=None), \
                mock.patch.object(ac.subprocess, "run"):
            self.assertIsNone(ac.ensure_connected())
        md.assert_called_once()

    def test_ensure_cli_budget_passthrough(self):
        # ensure --budget N 透传 ensure_connected（budget_s=N）；
        # 缺省无 --budget → budget_s=None（默认行为零变化）
        with mock.patch.object(ac, "ensure_connected") as ec, \
                mock.patch.object(ac, "_ensure_failure_detail",
                                  return_value=[]):
            ac.main(["ensure", "--budget", "60"])
        self.assertEqual(ec.call_args.kwargs.get("budget_s"), 60)
        with mock.patch.object(ac, "ensure_connected") as ec_default, \
                mock.patch.object(ac, "_ensure_failure_detail",
                                  return_value=[]):
            ac.main(["ensure"])
        self.assertIsNone(ec_default.call_args.kwargs.get("budget_s"))


if __name__ == "__main__":
    unittest.main()
