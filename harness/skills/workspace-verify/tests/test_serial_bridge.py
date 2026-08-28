import socket
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import serial_bridge as sb


class FakeClient:
    """伪 TCP 客户端：sendall 记录；可注入抛 OSError 模拟断开。"""

    def __init__(self, fail_send=False):
        self.received = []
        self.closed = False
        self._fail = fail_send
        self._recv_iter = None

    def sendall(self, data):
        if self._fail:
            raise OSError("client gone")
        self.received.append(data)

    def recv(self, n):
        if self._recv_iter is None:
            raise OSError("closed")
        data = next(self._recv_iter, b"")
        if not data:
            raise OSError("closed")
        return data

    def close(self):
        self.closed = True


class FakeSer:
    """伪串口：read 按脚本序列返回（异常项抛）；write 记录并测并发。"""

    def __init__(self, reads=None):
        self._reads = list(reads or [])
        self.writes = []
        self._w_lock = threading.Lock()
        self._active = 0
        self._max_active = 0

    def read(self, n):
        if self._reads:
            item = self._reads.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        time.sleep(0.005)
        return b""

    def write(self, data):
        with self._w_lock:
            self._active += 1
            self._max_active = max(self._max_active, self._active)
            time.sleep(0.005)
            self._active -= 1
        self.writes.append(data)

    @property
    def max_concurrent(self):
        return self._max_active


class TestSerialReader(unittest.TestCase):
    def test_broadcasts_to_all_clients(self):
        # 串口读到的字节广播给全部 TCP 客户端
        ser = FakeSer(reads=[b"hello", OSError("device pulled")])
        c1, c2 = FakeClient(), FakeClient()
        clients, lock = [c1, c2], threading.Lock()
        shutdown = threading.Event()
        t = threading.Thread(target=sb._serial_reader,
                             args=(ser, clients, lock, shutdown), daemon=True)
        t.start()
        t.join(timeout=3)
        self.assertTrue(shutdown.is_set())
        self.assertEqual(b"".join(c1.received), b"hello")
        self.assertEqual(b"".join(c2.received), b"hello")

    def test_dead_client_removed(self):
        # 任一客户端 sendall 失败即移除，不影响其余客户端
        ser = FakeSer(reads=[b"x", b"y", OSError("end")])
        dead, alive = FakeClient(fail_send=True), FakeClient()
        clients, lock = [dead, alive], threading.Lock()
        shutdown = threading.Event()
        t = threading.Thread(target=sb._serial_reader,
                             args=(ser, clients, lock, shutdown), daemon=True)
        t.start()
        t.join(timeout=3)
        self.assertEqual(clients, [alive])
        self.assertEqual(b"".join(alive.received), b"xy")

    def test_read_failure_shuts_down(self):
        # 串口读失败（设备拔出/驱动异常）→ 置 shutdown（main 关监听退出防黑洞）
        ser = FakeSer(reads=[OSError("serial gone")])
        clients, lock = [], threading.Lock()
        shutdown = threading.Event()
        sb._serial_reader(ser, clients, lock, shutdown)
        self.assertTrue(shutdown.is_set())


class TestClientHandler(unittest.TestCase):
    def test_single_writer_lock(self):
        # 单写者锁：多客户端并发写同一物理串口串行化（max 并发 = 1）
        ser = FakeSer()
        clients, clients_lock = [], threading.Lock()
        ser_lock = threading.Lock()
        conns = []
        for i in range(8):
            c = FakeClient()
            c._recv_iter = iter([f"msg-{i}".encode(), b""])
            conns.append(c)
        threads = []
        for c in conns:
            t = threading.Thread(
                target=sb._client_handler,
                args=(c, ser, clients, clients_lock, ser_lock), daemon=True)
            threads.append(t)
            t.start()
        for t in threads:
            t.join(timeout=3)
        self.assertEqual(ser.max_concurrent, 1)
        self.assertEqual(len(ser.writes), 8)

    def test_client_disconnect_self_removes(self):
        # 客户端断开时从列表自移除（须在列表中才移除）
        ser = FakeSer()

        class BoomConn:
            def recv(self, n):
                raise OSError("reset")

            def close(self):
                pass

        conn = BoomConn()
        clients, clients_lock = [conn], threading.Lock()
        ser_lock = threading.Lock()
        sb._client_handler(conn, ser, clients, clients_lock, ser_lock)
        self.assertEqual(clients, [])


class TestMainImportSerial(unittest.TestCase):
    def test_missing_pyserial_returns_3(self):
        # 缺 pyserial → exit 3（import serial 惰性可伪）
        with mock.patch.dict(sys.modules, {"serial": None}):
            with mock.patch.object(sb, "argparse") as mocked_ap:
                parser = mocked_ap.ArgumentParser.return_value
                parser.parse_args.return_value = mock.Mock()
                rc = sb.main([])
        self.assertEqual(rc, 3)


if __name__ == "__main__":
    unittest.main()