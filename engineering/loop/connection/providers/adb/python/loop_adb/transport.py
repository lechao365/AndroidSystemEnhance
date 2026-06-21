"""adb provider transport 适配层。

实现 loop_core.BaseTransport 接口，将 adb 命令式 shell 模型适配为
loop_core 的 acquire/send/capture/reboot 语义。

设计要点：
- send_line 缓存待执行命令，capture_since 时才通过 client.shell 执行
- reboot_and_wait 走 adb reboot + wait-for-device + getprop 验证链
- describe_runtime_context 返回 adb 运行上下文（endpoint / recent commands 等）
- pull_artifact 供 collector mode=adb_pull 调用，拉取设备文件到 host
"""
from __future__ import annotations

import time
from pathlib import Path

from loop_core.models import ObservedLine, RebootResult
from loop_core.transport import BaseTransport, CommandCapture
from loop_adb.client import AdbClient


class AdbTransport(BaseTransport):
    """通过 adb CLI 实现的 live transport。

    Args:
        endpoint: adb 网络端点，如 192.168.1.55:5555
        device_serial: adb -s 指定的设备 serial；缺省回落到 endpoint
        root_mode: 提权策略 auto/adb_root/su0/none
        connect_timeout_sec: connect / wait-for-device 超时
        command_timeout_sec: 单条 shell 命令默认超时
        client: AdbClient 实例；测试时注入 FakeClient
    """

    def __init__(
        self,
        endpoint: str,
        device_serial: str | None = None,
        root_mode: str = "auto",
        connect_timeout_sec: float = 15.0,
        command_timeout_sec: float = 10.0,
        client: AdbClient | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.device_serial = device_serial or endpoint
        self.root_mode = root_mode
        self.connect_timeout_sec = connect_timeout_sec
        self.command_timeout_sec = command_timeout_sec
        self.client = client or AdbClient(endpoint, self.device_serial)
        self._writer_held = False
        self._pending_command = ""
        self._boundary = 0
        self._recent_commands: list[str] = []
        self._reconnect_count = 0
        self._last_wait_for_device_result = "not_run"
        self.client.connect(timeout_sec=self.connect_timeout_sec)
        if self.root_mode in ("auto", "adb_root"):
            self.client.root(timeout_sec=self.connect_timeout_sec)

    # ------------------------------------------------------------------
    # writer / send
    # ------------------------------------------------------------------

    def acquire_writer(self) -> bool:
        if self._writer_held:
            return False
        self._writer_held = True
        return True

    def release(self) -> None:
        self._writer_held = False

    def send_line(self, text: str) -> None:
        if not self._writer_held:
            raise RuntimeError("writer not acquired")
        self._pending_command = text

    # ------------------------------------------------------------------
    # 采集 API
    # ------------------------------------------------------------------

    def mark_output_boundary(self) -> int:
        self._boundary += 1
        return self._boundary

    def _remember_command(self, command: str) -> None:
        self._recent_commands.append(command)
        if len(self._recent_commands) > 10:
            self._recent_commands = self._recent_commands[-10:]

    def capture_since(
        self,
        boundary: object,
        timeout_sec: float,
        recent_limit: int,
        prompt_markers: list[str] | None = None,
    ) -> CommandCapture:
        """执行缓存的 shell 命令并返回采集结果。"""
        del boundary, recent_limit, prompt_markers
        command = self._pending_command.strip()
        self._pending_command = ""
        if not command:
            return CommandCapture(
                lines=[], prompt_visible=False, exit_code=0
            )
        self._remember_command(command)
        result = self.client.shell(
            command,
            timeout_sec=timeout_sec or self.command_timeout_sec,
            as_root=self.root_mode == "su0",
        )
        lines = [
            ObservedLine(t=float(index), text=line)
            for index, line in enumerate(result.output_lines, start=1)
        ]
        return CommandCapture(
            lines=lines, prompt_visible=False, exit_code=result.command_exit_code
        )

    # ------------------------------------------------------------------
    # 旧 API（兼容期，adb 模式无实际意义）
    # ------------------------------------------------------------------

    def capture_window(self, timeout_sec: float, recent_limit: int):
        del timeout_sec, recent_limit
        return []

    def wait_for_pattern(
        self, patterns: list[str], timeout_sec: float, recent_limit: int
    ):
        del patterns, timeout_sec, recent_limit
        return None

    # ------------------------------------------------------------------
    # reboot API
    # ------------------------------------------------------------------

    def reboot_and_wait(
        self,
        boot_markers: list[str],
        panic_markers: list[str],
        boot_complete_timeout: float = 180.0,
        l1_timeout: float = 30.0,
        l2_timeout: float = 90.0,
        l3_timeout: float = 60.0,
        prompt_markers: list[str] | None = None,
    ) -> RebootResult:
        """adb reboot → wait-for-device → getprop sys.boot_completed 验证链。"""
        del boot_markers, panic_markers, l1_timeout, l2_timeout, prompt_markers
        start = time.monotonic()
        self.client.reboot(timeout_sec=self.command_timeout_sec)
        self.client.wait_for_device(timeout_sec=boot_complete_timeout)
        self._last_wait_for_device_result = "pass"
        verify = self.client.shell("getprop sys.boot_completed", timeout_sec=l3_timeout)
        if verify.command_exit_code == 0 and any(
            line.strip() == "1" for line in verify.output_lines
        ):
            return RebootResult(
                status="pass",
                transcript_lines=[
                    "adb reboot",
                    "wait-for-device",
                    "sys.boot_completed=1",
                ],
                stage_reached="l3_verified",
                boot_duration_sec=round(time.monotonic() - start, 3),
            )
        return RebootResult(
            status="fail",
            transcript_lines=["adb reboot", "wait-for-device"],
            failure_reason="boot_completed_not_ready",
            stage_reached="l2_init_ready",
            boot_duration_sec=round(time.monotonic() - start, 3),
        )

    # ------------------------------------------------------------------
    # 文件能力
    # ------------------------------------------------------------------

    def pull_artifact(
        self, remote_path: str, local_dir: str, timeout_sec: float = 60.0
    ) -> list[str]:
        """拉取远端路径到本地目录，返回落地的文件路径列表。

        adb pull 可能拉回单个文件或整个目录（当 remote_path 是目录时）。
        本方法在 pull 完成后扫描 local_dir，返回所有文件路径。
        """
        local_dir_path = Path(local_dir)
        local_dir_path.mkdir(parents=True, exist_ok=True)
        self.client.pull(remote_path, str(local_dir_path), timeout_sec)
        return [
            str(p) for p in local_dir_path.rglob("*") if p.is_file()
        ]

    # ------------------------------------------------------------------
    # runtime context
    # ------------------------------------------------------------------

    def describe_runtime_context(self, artifacts_dir: str | None = None) -> dict:
        """返回 adb 运行上下文，供 EvidenceBundle.runtime_context 使用。"""
        del artifacts_dir
        return {
            "adb_endpoint": self.endpoint,
            "adb_device_serial": self.device_serial,
            "adb_recent_commands": list(self._recent_commands),
            "adb_reconnect_count": self._reconnect_count,
            "adb_wait_for_device_result": self._last_wait_for_device_result,
            "adb_logcat_snapshot_path": "",
        }
