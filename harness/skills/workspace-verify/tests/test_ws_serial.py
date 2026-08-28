import io
import socket
import sys
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ws_serial as ws


class FakeSock:
    """伪 socket：recv 按脚本序列返回（b"" 返空 / 超时对象抛超时）。

    settimeout(0)（drain 弃残留）时 recv 恒抛 BlockingIOError——
    drain 阶段不得消耗数据序列，否则后续 read 无数据可返回。
    """

    def __init__(self, recv_seq=None, drain_seq=None):
        self._seq = list(recv_seq or [])
        self._drain_seq = list(drain_seq or [])
        self.sent = b""
        self.timeout = None

    def sendall(self, data):
        self.sent += data

    def recv(self, n):
        if self.timeout == 0:
            if self._drain_seq:
                item = self._drain_seq.pop(0)
                if isinstance(item, BaseException):
                    raise item
                return item
            raise BlockingIOError("drain: empty")
        if self._seq:
            item = self._seq.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        raise socket.timeout("no more data")

    def settimeout(self, t):
        self.timeout = t

    def close(self):
        pass


def _conn(recv_seq=None, drain_seq=None):
    c = ws.SerialConn("127.0.0.1", 9700, timeout=1.0)
    c.sock = FakeSock(recv_seq, drain_seq=drain_seq)
    c.connect = lambda: None  # 已注入伪 sock，跳过真实 TCP 连接
    return c


class TestErrorClassification(unittest.TestCase):
    def test_connect_failure_endpoint_unreachable(self):
        # TCP 连不上 → ENDPOINT_UNREACHABLE
        c = ws.SerialConn("127.0.0.1", 9700)
        with mock.patch.object(ws.socket, "create_connection",
                               side_effect=OSError("refused")):
            with self.assertRaises(ws.SerialError) as cm:
                c.connect()
        self.assertEqual(cm.exception.category, "ENDPOINT_UNREACHABLE")

    def test_read_timeout_device_unresponsive(self):
        # 超时未捕获 marker → DEVICE_UNRESPONSIVE
        c = _conn([socket.timeout("t")])
        with self.assertRaises(ws.SerialError) as cm:
            c.read_until_marker(ws._EXEC_TAG_RE, timeout=0.5)
        self.assertEqual(cm.exception.category, "DEVICE_UNRESPONSIVE")

    def test_recv_empty_serial_silent(self):
        # recv 返空（连接被关闭）→ SERIAL_SILENT
        c = _conn([b""])
        with self.assertRaises(ws.SerialError) as cm:
            c.read_until_marker(ws._EXEC_TAG_RE, timeout=0.5)
        self.assertEqual(cm.exception.category, "SERIAL_SILENT")

    def test_drain_empty_serial_silent(self):
        # drain 遇 recv 返空 → SERIAL_SILENT（转发器已退出）
        c = _conn(drain_seq=[b""])
        with self.assertRaises(ws.SerialError) as cm:
            c.drain()
        self.assertEqual(cm.exception.category, "SERIAL_SILENT")

    def test_output_bloat_device_unresponsive(self):
        # 输出膨胀未捕获 marker → DEVICE_UNRESPONSIVE
        c = _conn([b"x" * 9 * 1024 * 1024])
        with self.assertRaises(ws.SerialError) as cm:
            c.read_until_marker(ws._EXEC_TAG_RE, timeout=0.5)
        self.assertEqual(cm.exception.category, "DEVICE_UNRESPONSIVE")


class TestExecute(unittest.TestCase):
    def test_single_line_merged(self):
        # 方向 4：cmd 与 echo marker 合成一行发送（防第二行被首命令当 stdin 吞）
        c = _conn([b"out\n__LE_EXIT_CODE__=0\n"])
        body, code = ws._execute(c, "echo hi", 2.0)
        self.assertEqual(body, "out")
        self.assertEqual(code, 0)
        self.assertEqual(c.sock.sent,
                         b"echo hi; echo __LE_EXIT_CODE__=$?\r")
        self.assertEqual(c.sock.sent.count(b"\r"), 1)

    def test_exit_code_nonzero(self):
        c = _conn([b"err\n__LE_EXIT_CODE__=1\n"])
        _, code = ws._execute(c, "false", 2.0)
        self.assertEqual(code, 1)

    def test_marker_missing_unresponsive(self):
        # 未捕获 exit code → DEVICE_UNRESPONSIVE
        c = _conn([b"no marker at all", socket.timeout("t")])
        with self.assertRaises(ws.SerialError) as cm:
            ws._execute(c, "cat", 0.5)
        self.assertEqual(cm.exception.category, "DEVICE_UNRESPONSIVE")


class TestCmdExec(unittest.TestCase):
    def _run(self, recv_seq, command="echo hi"):
        c = _conn(recv_seq)
        args = mock.Mock()
        args.command = command
        args.timeout = 2.0
        return c, ws.cmd_exec(args, c)

    def test_success(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            c, rc = self._run([b"hi\n__LE_EXIT_CODE__=0\n"])
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "hi\n")

    def test_failure_rc1(self):
        c, rc = self._run([b"nope\n__LE_EXIT_CODE__=1\n"])
        self.assertEqual(rc, 1)


class TestCmdRead(unittest.TestCase):
    def test_reads_bytes_until_timeout(self):
        c = _conn([b"a", b"b", socket.timeout("t")])
        args = mock.Mock()
        args.timeout = 1.0
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws.cmd_read(args, c)
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "ab")

    def test_read_empty_ok(self):
        c = _conn([socket.timeout("t")])
        args = mock.Mock()
        args.timeout = 1.0
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws.cmd_read(args, c)
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue(), "")


class TestCmdIp(unittest.TestCase):
    def test_prints_ipv4(self):
        c = _conn([b"2: wlan0    inet 192.168.1.28/24 ...\n"
                   b"__LE_EXIT_CODE__=0\n"])
        args = mock.Mock()
        args.timeout = 2.0
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws.cmd_ip(args, c)
        self.assertEqual(rc, 0)
        self.assertEqual(buf.getvalue().strip(), "192.168.1.28")

    def test_filters_link_local(self):
        # 169.254/127 过滤后取可用地址
        c = _conn([b"2: wlan0 inet 169.254.1.1/16 ...\n"
                   b"2: wlan0 inet 10.0.0.8/24 ...\n"
                   b"__LE_EXIT_CODE__=0\n"])
        args = mock.Mock()
        args.timeout = 2.0
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws.cmd_ip(args, c)
        self.assertEqual(buf.getvalue().strip(), "10.0.0.8")

    def test_no_ipv4_is_no_ipv4_not_silent(self):
        # 方向 3：exec 返 0 证链路通，无 IPv4 判 NO_IPV4（设备侧无网），
        # 判 SERIAL_SILENT 会误导诊断
        c = _conn([b"2: wlan0    <BROADCAST,MULTICAST,UP,LOWER_UP> ...\n"
                   b"__LE_EXIT_CODE__=0\n"])
        args = mock.Mock()
        args.timeout = 2.0
        with self.assertRaises(ws.SerialError) as cm:
            ws.cmd_ip(args, c)
        self.assertEqual(cm.exception.category, "NO_IPV4")
        self.assertIn("无有效 IPv4", str(cm.exception))

    def test_ip_command_failed_unresponsive(self):
        c = _conn([b"ip: not found\n__LE_EXIT_CODE__=127\n"])
        args = mock.Mock()
        args.timeout = 2.0
        with self.assertRaises(ws.SerialError) as cm:
            ws.cmd_ip(args, c)
        self.assertEqual(cm.exception.category, "DEVICE_UNRESPONSIVE")


class TestCmdStatus(unittest.TestCase):
    def test_data_flow_reachable(self):
        # 读到数据流 → REACHABLE
        c = _conn([b"console boot log..."])
        args = mock.Mock()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws.cmd_status(args, c)
        self.assertEqual(rc, 0)
        self.assertIn("REACHABLE", buf.getvalue())
        self.assertIn("数据流", buf.getvalue())

    def test_recv_empty_serial_silent(self):
        # 方向 5：recv 返空（转发器已退出）→ SERIAL_SILENT，砖机三分法首分支可判定
        c = _conn([b""])
        args = mock.Mock()
        with self.assertRaises(ws.SerialError) as cm:
            ws.cmd_status(args, c)
        self.assertEqual(cm.exception.category, "SERIAL_SILENT")

    def test_no_output_still_reachable_annotated(self):
        # 连接在但 1s 无输出（空闲合法）→ REACHABLE 附注静默
        c = _conn([socket.timeout("t")])
        args = mock.Mock()
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = ws.cmd_status(args, c)
        self.assertEqual(rc, 0)
        self.assertIn("无输出", buf.getvalue())


class TestSerialEndpoint(unittest.TestCase):
    def test_defaults(self):
        # 无环境变量 → 127.0.0.1:9700（单一事实源默认）
        with mock.patch.dict("os.environ", {}, clear=False):
            host, port = ws.serial_endpoint()
        self.assertEqual((host, port), ("127.0.0.1", 9700))

    def test_env_override(self):
        with mock.patch.dict("os.environ", {"LC_SERIAL_HOST": "10.0.0.2",
                                            "LC_SERIAL_PORT": "9600"}):
            host, port = ws.serial_endpoint()
        self.assertEqual((host, port), ("10.0.0.2", 9600))

    def test_non_numeric_port_fallback(self):
        # 方向 4：LC_SERIAL_PORT 非数字回退默认 9700（argparse 构造前不抛 ValueError）
        with mock.patch.dict("os.environ", {"LC_SERIAL_PORT": "abc"}):
            host, port = ws.serial_endpoint()
        self.assertEqual(port, 9700)
        self.assertEqual(host, "127.0.0.1")


class TestReadTimeoutArg(unittest.TestCase):
    def _fake_conn_cls(self):
        class FakeConn:
            def __init__(self, host, port, timeout):
                self.host = host
                self.port = port
                self.timeout = timeout
                self.sock = FakeSock([socket.timeout("t")])
                self.closed = False

            def connect(self):
                pass

            def drain(self):
                pass

            def close(self):
                self.closed = True

        return FakeConn

    def test_parent_timeout_not_overridden(self):
        # 方向 2：read 子级 --timeout 不再覆盖父级显式传值
        FakeConn = self._fake_conn_cls()
        fake = FakeConn("h", 1, 0)
        with mock.patch.object(ws, "SerialConn", return_value=fake) as MC:
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = ws.main(["--timeout", "5", "read"])
        self.assertEqual(rc, 0)
        self.assertEqual(MC.call_args.args[2], 5.0)

    def test_default_timeout_from_env(self):
        # 方向 5：read 恒默认 1.0（LC_SERIAL_TIMEOUT 不参与，否则 1.0 复位失效）
        FakeConn = self._fake_conn_cls()
        fake = FakeConn("h", 1, 0)
        with mock.patch.object(ws, "SerialConn", return_value=fake) as MC:
            with mock.patch.dict("os.environ", {"LC_SERIAL_TIMEOUT": "10"}):
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = ws.main(["read"])
        self.assertEqual(rc, 0)
        self.assertEqual(MC.call_args.args[2], 1.0)

    def test_env_timeout_applies_to_exec(self):
        # 非 read 命令（exec）LC_SERIAL_TIMEOUT 仍生效
        FakeConn = self._fake_conn_cls()
        fake = FakeConn("h", 1, 0)
        with mock.patch.object(ws, "SerialConn", return_value=fake) as MC:
            with mock.patch.dict("os.environ", {"LC_SERIAL_TIMEOUT": "3.5"}):
                ws.main(["exec"])  # 缺命令返 3，重点验证 timeout 传递
        self.assertEqual(MC.call_args.args[2], 3.5)


if __name__ == "__main__":
    unittest.main()