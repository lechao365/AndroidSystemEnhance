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

    def wait_oneshot_started(self, service_name: str, timeout: float = 15.0) -> bool:
        """等待 oneshot 服务启动过：getprop 曾出现 running，或 logcat 有启动日志。

        oneshot 服务 restart 后立即退出变 stopped，无法用
        wait_service_running 判定。改为：
        1) 先 setprop ctl.start 触发启动；
        2) 轮询 logcat -d 是否出现服务的启动日志（logcat 默认保留最近 buffer）。
        判定标志：出现服务名相关启动行即视为部署成功（进程已加载新二进制）。
        """
        marker = service_name
        deadline = time.time() + timeout
        while time.time() < deadline:
            result = self._client.shell(
                f"logcat -d -s {service_name}:* 2>/dev/null | tail -20",
                timeout_sec=5.0,
            )
            if result.command_exit_code == 0:
                stdout = result.raw_stdout or ""
                if marker in stdout and ("start" in stdout or "loaded" in stdout):
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
