"""loop_adb.transport 合同测试。

用 FakeClient 注入，覆盖 AdbTransport 对 BaseTransport 接口的实现。
"""
import pytest

from loop_adb.client import AdbShellResult
from loop_adb.transport import AdbTransport
from loop_core.transport import BaseTransport, CommandCapture


class FakeClient:
    """AdbClient 测试替身，记录所有调用。"""

    def __init__(self, endpoint="192.168.1.55:5555", device_serial="192.168.1.55:5555"):
        self.endpoint = endpoint
        self.device_serial = device_serial
        self.connect_called_with = None
        self.reboot_called = False
        self.wait_called_with = None
        self.pull_called_with = None
        self.shell_calls = []
        # 默认 shell 返回值（按队列消费）
        self._shell_results = []

    def connect(self, timeout_sec):
        self.connect_called_with = timeout_sec
        from loop_adb.client import AdbCommandResult
        return AdbCommandResult(argv=["adb", "connect", self.endpoint], exit_code=0, stdout="", stderr="")

    def disconnect(self, timeout_sec=5.0):
        from loop_adb.client import AdbCommandResult
        return AdbCommandResult(argv=["adb", "disconnect", self.endpoint], exit_code=0, stdout="", stderr="")

    def root(self, timeout_sec):
        from loop_adb.client import AdbCommandResult
        return AdbCommandResult(argv=[], exit_code=0, stdout="", stderr="")

    def wait_for_device(self, timeout_sec):
        self.wait_called_with = timeout_sec
        from loop_adb.client import AdbCommandResult
        return AdbCommandResult(argv=[], exit_code=0, stdout="", stderr="")

    def reboot(self, timeout_sec):
        self.reboot_called = True
        from loop_adb.client import AdbCommandResult
        return AdbCommandResult(argv=[], exit_code=0, stdout="", stderr="")

    def pull(self, remote_path, local_path, timeout_sec):
        self.pull_called_with = (remote_path, local_path, timeout_sec)
        from loop_adb.client import AdbCommandResult
        return AdbCommandResult(argv=[], exit_code=0, stdout="", stderr="")

    def logcat(self, buffers, timeout_sec):
        from loop_adb.client import AdbCommandResult
        return AdbCommandResult(argv=[], exit_code=0, stdout="", stderr="")

    def shell(self, command, timeout_sec, as_root=False):
        self.shell_calls.append({"command": command, "timeout_sec": timeout_sec, "as_root": as_root})
        if self._shell_results:
            return self._shell_results.pop(0)
        return AdbShellResult(argv=[], output_lines=[], command_exit_code=0, raw_stdout="", stderr="")

    def queue_shell(self, result):
        self._shell_results.append(result)


def _make_transport(client=None, endpoint="192.168.1.55:5555"):
    if client is None:
        client = FakeClient(endpoint=endpoint)
    return AdbTransport(endpoint=endpoint, device_serial=endpoint, client=client), client


def test_transport_is_base_transport():
    transport, _ = _make_transport()
    assert isinstance(transport, BaseTransport)


def test_transport_connect_on_init():
    client = FakeClient()
    AdbTransport(endpoint="192.168.1.55:5555", device_serial="192.168.1.55:5555", client=client)
    assert client.connect_called_with is not None


def test_transport_capture_since_runs_pending_shell_command():
    transport, client = _make_transport()
    client.queue_shell(AdbShellResult(
        argv=[], output_lines=["ok"], command_exit_code=0, raw_stdout="ok\n__LE_EXIT_CODE__=0\n", stderr=""
    ))
    transport.acquire_writer()
    transport.send_line("echo ok")
    boundary = transport.mark_output_boundary()
    capture = transport.capture_since(boundary, timeout_sec=5.0, recent_limit=100)
    assert isinstance(capture, CommandCapture)
    texts = [l.text for l in capture.lines]
    assert texts == ["ok"]
    assert capture.exit_code == 0
    assert client.shell_calls[0]["command"] == "echo ok"


def test_transport_acquire_writer_returns_true_then_false():
    transport, _ = _make_transport()
    assert transport.acquire_writer() is True
    assert transport.acquire_writer() is False  # 已持有
    transport.release()
    assert transport.acquire_writer() is True


def test_transport_send_line_without_writer_raises():
    transport, _ = _make_transport()
    with pytest.raises(RuntimeError):
        transport.send_line("ls")


def test_transport_reboot_and_wait_uses_client_hooks():
    """reboot_and_wait 调用 client.reboot / wait_for_device / shell。"""
    client = FakeClient()
    client.queue_shell(AdbShellResult(
        argv=[],
        output_lines=["1"],
        command_exit_code=0,
        raw_stdout="1\n__LE_EXIT_CODE__=0\n",
        stderr="",
    ))
    transport = AdbTransport(
        endpoint="192.168.1.55:5555",
        device_serial="192.168.1.55:5555",
        client=client,
    )
    result = transport.reboot_and_wait(
        boot_markers=["ignored"],
        panic_markers=["ignored"],
        boot_complete_timeout=180.0,
        l3_timeout=60.0,
    )
    assert result.status == "pass"
    assert result.stage_reached == "l3_verified"
    assert client.reboot_called is True
    assert client.wait_called_with == 180.0
    # verify shell 调用了 getprop sys.boot_completed
    assert any("getprop sys.boot_completed" in c["command"] for c in client.shell_calls)


def test_transport_reboot_and_wait_fails_when_boot_completed_not_ready():
    client = FakeClient()
    client.queue_shell(AdbShellResult(
        argv=[],
        output_lines=[],
        command_exit_code=0,
        raw_stdout="__LE_EXIT_CODE__=0\n",
        stderr="",
    ))
    transport = AdbTransport(
        endpoint="192.168.1.55:5555",
        device_serial="192.168.1.55:5555",
        client=client,
    )
    result = transport.reboot_and_wait(
        boot_markers=["x"],
        panic_markers=["y"],
    )
    assert result.status == "fail"
    assert result.failure_reason == "boot_completed_not_ready"


def test_transport_reboot_fail_stage_is_adb_online_not_l2():
    """P2-4：失败路径 stage_reached 应诚实标注 adb_online，而非 l2_init_ready。

    回归 P2-4：adb 未做 L1/L2 boot marker 检测，wait_for_device 成功只代表
    adb 上线，不等于 L2 init_ready。原标注 l2_init_ready 名不副实。
    """
    client = FakeClient()
    client.queue_shell(AdbShellResult(
        argv=[], output_lines=[], command_exit_code=0,
        raw_stdout="__LE_EXIT_CODE__=0\n", stderr="",
    ))
    transport = AdbTransport(
        endpoint="192.168.1.55:5555",
        device_serial="192.168.1.55:5555",
        client=client,
    )
    result = transport.reboot_and_wait(boot_markers=["x"], panic_markers=["y"])
    assert result.status == "fail"
    assert result.stage_reached == "adb_online"


def test_transport_describe_runtime_context_includes_recent_commands():
    transport, client = _make_transport()
    client.queue_shell(AdbShellResult(
        argv=[], output_lines=["ok"], command_exit_code=0, raw_stdout="", stderr=""
    ))
    transport.acquire_writer()
    transport.send_line("uname -a")
    transport.capture_since(transport.mark_output_boundary(), 5.0, 100)
    ctx = transport.describe_runtime_context()
    assert ctx["adb_endpoint"] == "192.168.1.55:5555"
    assert ctx["adb_device_serial"] == "192.168.1.55:5555"
    assert ctx["adb_recent_commands"] == ["uname -a"]
    assert ctx["adb_wait_for_device_result"] == "not_run"


def test_transport_pull_artifact_returns_file_list(tmp_path):
    client = FakeClient()

    def fake_pull(remote_path, local_path, timeout_sec):
        # 模拟 adb pull 将文件写到 local_path
        from pathlib import Path
        target = Path(local_path) / "dump.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("hello")

    client.pull = fake_pull
    transport = AdbTransport(
        endpoint="192.168.1.55:5555",
        device_serial="192.168.1.55:5555",
        client=client,
    )
    paths = transport.pull_artifact("/data/vendor/dump.txt", str(tmp_path), timeout_sec=30.0)
    assert isinstance(paths, list)
    assert len(paths) >= 1
    assert any("dump.txt" in p for p in paths)
