"""adb_ops 单元测试（mock AdbClient）。"""
import loop_adb.client as adb_mod
from loop_deploy.adb_ops import AdbOps


def _make_client(shell_outputs: list[str]):
    idx = 0

    def fake_runner(argv, timeout):
        nonlocal idx
        raw = shell_outputs[idx] if idx < len(shell_outputs) else "running"
        idx += 1
        out = f"{raw}\n__LE_EXIT_CODE__=0\n"
        return adb_mod.AdbCommandResult(argv=argv, exit_code=0, stdout=out, stderr="")

    return adb_mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555", runner=fake_runner)


def test_wait_service_running_immediate():
    client = _make_client(["running"])
    ops = AdbOps(client)
    assert ops.wait_service_running("test_svc", timeout=5.0) is True


def test_wait_service_running_timeout():
    outputs = ["stopped"] * 10
    client = _make_client(outputs)
    ops = AdbOps(client)
    assert ops.wait_service_running("test_svc", timeout=0.5) is False


def test_wait_boot_completed_immediate():
    client = _make_client(["1"])
    ops = AdbOps(client)
    assert ops.wait_boot_completed(timeout=5.0) is True
