"""adb 部署辅助操作：wait_service_running / wait_boot_completed。"""
from __future__ import annotations

import time
from loop_adb.client import AdbClient


class AdbOps:
    def __init__(self, client: AdbClient):
        self._client = client

    def wait_service_running(self, service_name: str, timeout: float = 15.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._client.shell(f"getprop init.svc.{service_name}", timeout_sec=5.0)
            if result.command_exit_code == 0 and "running" in result.raw_stdout:
                return True
            time.sleep(0.5)
        return False

    def wait_boot_completed(self, timeout: float = 120.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._client.shell("getprop sys.boot_completed", timeout_sec=10.0)
            if result.command_exit_code == 0 and "1" in result.raw_stdout:
                return True
            time.sleep(2.0)
        return False
