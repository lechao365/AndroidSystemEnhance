"""adb 子进程封装。

封装 adb CLI 调用，统一 stdout/stderr/exit_code 返回结构与错误映射。
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass


class AdbCommandError(RuntimeError):
    """adb 命令执行阶段的不可恢复错误。"""


@dataclass
class AdbCommandResult:
    """adb 子进程统一返回结构。"""

    argv: list[str]
    exit_code: int
    stdout: str
    stderr: str


@dataclass
class AdbShellResult:
    """adb shell 命令解析后结果。

    通过 __LE_EXIT_CODE__ 标记解析出设备端命令的真实 exit code。
    """

    argv: list[str]
    output_lines: list[str]
    command_exit_code: int
    raw_stdout: str
    stderr: str


class AdbClient:
    """adb CLI 子进程封装。

    通过 runner 回调执行实际命令，便于测试注入 FakeRunner。

    Args:
        endpoint: adb 网络端点（格式 <ip>:5555，由 serial bootstrap 动态发现）
        device_serial: adb -s 指定的设备 serial；通常与 endpoint 相同
        runner: callable (argv, timeout_sec) -> AdbCommandResult，缺省走真实 subprocess
    """

    def __init__(self, endpoint: str, device_serial: str, runner=None) -> None:
        self.endpoint = endpoint
        self.device_serial = device_serial
        self._runner = runner or self._run_subprocess

    def _run_subprocess(self, argv: list[str], timeout_sec: float) -> AdbCommandResult:
        """真实子进程执行。"""
        try:
            completed = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise AdbCommandError(
                f"adb command timed out after {timeout_sec}s: {' '.join(argv)}"
            ) from exc
        except OSError as exc:
            raise AdbCommandError(f"failed to execute adb: {exc}") from exc
        return AdbCommandResult(
            argv=list(argv),
            exit_code=completed.returncode,
            stdout=completed.stdout or "",
            stderr=completed.stderr or "",
        )

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------

    def connect(self, timeout_sec: float) -> AdbCommandResult:
        """adb connect <endpoint>。"""
        return self._runner(["adb", "connect", self.endpoint], timeout_sec)

    def disconnect(self, timeout_sec: float = 5.0) -> AdbCommandResult:
        """adb disconnect <endpoint>。"""
        return self._runner(["adb", "disconnect", self.endpoint], timeout_sec)

    def wait_for_device(self, timeout_sec: float) -> AdbCommandResult:
        """adb -s <serial> wait-for-device。"""
        return self._runner(
            ["adb", "-s", self.device_serial, "wait-for-device"], timeout_sec
        )

    # ------------------------------------------------------------------
    # 命令执行
    # ------------------------------------------------------------------

    def shell(
        self, command: str, timeout_sec: float, as_root: bool = False
    ) -> AdbShellResult:
        """在设备上执行 shell 命令并解析 exit code。

        通过 __LE_EXIT_CODE__ 标记法捕获设备端命令真实退出码，
        避免 adb 本身的退出码干扰。
        """
        wrapped = command if not as_root else f"su 0 sh -c {command!r}"
        shell_cmd = f"{wrapped}; rc=$?; printf '\\n__LE_EXIT_CODE__=%s\\n' \"$rc\""
        try:
            result = self._runner(
                ["adb", "-s", self.device_serial, "shell", shell_cmd], timeout_sec
            )
        except AdbCommandError:
            raise
        except (TimeoutError, OSError) as exc:
            raise AdbCommandError(f"adb shell failed: {exc}") from exc
        lines = result.stdout.splitlines()
        if not lines or not lines[-1].startswith("__LE_EXIT_CODE__="):
            raise AdbCommandError(
                "adb shell result missing exit code marker: "
                f"raw_stdout={result.stdout!r}"
            )
        exit_code = int(lines[-1].split("=", 1)[1])
        return AdbShellResult(
            argv=result.argv,
            output_lines=lines[:-1],
            command_exit_code=exit_code,
            raw_stdout=result.stdout,
            stderr=result.stderr,
        )

    # ------------------------------------------------------------------
    # 权限与环境
    # ------------------------------------------------------------------

    def root(self, timeout_sec: float) -> AdbCommandResult:
        """adb -s <serial> root。"""
        return self._runner(["adb", "-s", self.device_serial, "root"], timeout_sec)

    # ------------------------------------------------------------------
    # 文件能力
    # ------------------------------------------------------------------

    def pull(
        self, remote_path: str, local_path: str, timeout_sec: float
    ) -> AdbCommandResult:
        """adb -s <serial> pull <remote> <local>。"""
        return self._runner(
            [
                "adb",
                "-s",
                self.device_serial,
                "pull",
                remote_path,
                local_path,
            ],
            timeout_sec,
        )

    # ------------------------------------------------------------------
    # 调试能力
    # ------------------------------------------------------------------

    def reboot(self, timeout_sec: float) -> AdbCommandResult:
        """adb -s <serial> reboot。"""
        return self._runner(["adb", "-s", self.device_serial, "reboot"], timeout_sec)

    def logcat(
        self, buffers: list[str], timeout_sec: float
    ) -> AdbCommandResult:
        """adb -s <serial> logcat -d -b <buffer>...

        Args:
            buffers: 要采集的 logcat buffer 列表，如 ["main", "system", "crash"]
            timeout_sec: 超时
        """
        argv = ["adb", "-s", self.device_serial, "logcat", "-d"]
        for buffer_name in buffers:
            argv.extend(["-b", buffer_name])
        return self._runner(argv, timeout_sec)

    def push(self, local_path: str, remote_path: str, timeout_sec: float) -> AdbCommandResult:
        return self._runner(
            ["adb", "-s", self.device_serial, "push", local_path, remote_path],
            timeout_sec,
        )

    def remount(self, timeout_sec: float) -> AdbCommandResult:
        return self._runner(
            ["adb", "-s", self.device_serial, "remount"], timeout_sec
        )
