"""loop_adb.client 合同测试。

用 FakeRunner 注入 adb 子进程调用，覆盖：
- connect / disconnect 命令构造
- shell 命令包装（exit code marker 解析）
- shell 超时转 AdbCommandError
- pull / root / logcat / as_root 命令构造
"""
import pytest

from loop_adb.client import (
    AdbClient,
    AdbCommandError,
    AdbCommandResult,
    AdbShellResult,
)


class FakeRunner:
    """记录 argv 并按队列返回 AdbCommandResult / 抛异常。"""

    def __init__(self, results=None, raises=None):
        self.calls = []
        self._results = list(results or [])
        self._raises = list(raises or [])

    def __call__(self, argv, timeout_sec):
        self.calls.append({"argv": list(argv), "timeout_sec": timeout_sec})
        if self._raises:
            raise self._raises.pop(0)
        if self._results:
            return self._results.pop(0)
        return AdbCommandResult(argv=list(argv), exit_code=0, stdout="", stderr="")


def _make_client(runner=None, endpoint="192.168.1.55:5555", device_serial="192.168.1.55:5555"):
    return AdbClient(endpoint=endpoint, device_serial=device_serial, runner=runner)


def test_connect_builds_expected_command():
    runner = FakeRunner()
    client = _make_client(runner)
    client.connect(timeout_sec=10.0)
    assert runner.calls[0]["argv"] == ["adb", "connect", "192.168.1.55:5555"]
    assert runner.calls[0]["timeout_sec"] == 10.0


def test_disconnect_builds_command():
    runner = FakeRunner()
    client = _make_client(runner)
    client.disconnect(timeout_sec=5.0)
    assert runner.calls[0]["argv"] == ["adb", "disconnect", "192.168.1.55:5555"]


def test_shell_wraps_exit_code_marker():
    runner = FakeRunner(
        results=[
            AdbCommandResult(
                argv=["adb", "-s", "192.168.1.55:5555", "shell", "echo hello; rc=$?; printf '\\n__LE_EXIT_CODE__=%s\\n' \"$rc\""],
                exit_code=0,
                stdout="hello\n__LE_EXIT_CODE__=0\n",
                stderr="",
            )
        ]
    )
    client = _make_client(runner)
    result = client.shell("echo hello", timeout_sec=5.0)
    assert isinstance(result, AdbShellResult)
    assert result.output_lines == ["hello"]
    assert result.command_exit_code == 0


def test_shell_timeout_raises_adb_command_error():
    runner = FakeRunner(raises=[TimeoutError("adb shell timed out")])
    client = _make_client(runner)
    with pytest.raises(AdbCommandError, match="timed out"):
        client.shell("sleep 60", timeout_sec=1.0)


def test_shell_missing_exit_code_marker_raises():
    runner = FakeRunner(
        results=[
            AdbCommandResult(
                argv=[],
                exit_code=0,
                stdout="just output, no marker",
                stderr="",
            )
        ]
    )
    client = _make_client(runner)
    with pytest.raises(AdbCommandError):
        client.shell("anything", timeout_sec=5.0)


def test_pull_builds_expected_command(tmp_path):
    runner = FakeRunner()
    client = _make_client(runner)
    local_dir = str(tmp_path)
    client.pull("/data/vendor/lechao_lcview/logs", local_dir, timeout_sec=30.0)
    argv = runner.calls[0]["argv"]
    assert "pull" in argv
    assert "/data/vendor/lechao_lcview/logs" in argv
    assert local_dir in argv
    assert argv[:3] == ["adb", "-s", "192.168.1.55:5555"]


def test_root_uses_adb_root_when_enabled():
    runner = FakeRunner()
    client = _make_client(runner)
    client.root(timeout_sec=10.0)
    assert runner.calls[0]["argv"] == [
        "adb", "-s", "192.168.1.55:5555", "root",
    ]


def test_logcat_includes_buffers():
    runner = FakeRunner()
    client = _make_client(runner)
    client.logcat(buffers=["main", "system"], timeout_sec=10.0)
    argv = runner.calls[0]["argv"]
    assert "-b" in argv
    assert "main" in argv
    assert "system" in argv
    assert "-d" in argv


def test_shell_as_root_wraps_with_su0():
    runner = FakeRunner(
        results=[
            AdbCommandResult(
                argv=[],
                exit_code=0,
                stdout="__LE_EXIT_CODE__=0\n",
                stderr="",
            )
        ]
    )
    client = _make_client(runner)
    client.shell("id", timeout_sec=5.0, as_root=True)
    argv = runner.calls[0]["argv"]
    # shell_cmd 是 argv 中 "shell" 之后的部分，整个 shell 命令字符串需包含 "su 0"
    shell_cmd = " ".join(argv[argv.index("shell") + 1:])
    assert "su 0" in shell_cmd


def test_reboot_builds_command():
    runner = FakeRunner()
    client = _make_client(runner)
    client.reboot(timeout_sec=10.0)
    assert "reboot" in runner.calls[0]["argv"]
    assert runner.calls[0]["argv"][:3] == ["adb", "-s", "192.168.1.55:5555"]


def test_wait_for_device_builds_command():
    runner = FakeRunner()
    client = _make_client(runner)
    client.wait_for_device(timeout_sec=120.0)
    assert runner.calls[0]["argv"] == [
        "adb", "-s", "192.168.1.55:5555", "wait-for-device",
    ]
