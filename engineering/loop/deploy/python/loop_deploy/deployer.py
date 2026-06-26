"""Deployer: push_single / dd_boot_reboot 部署执行。"""
from __future__ import annotations

import hashlib
import os
import tempfile
import time
from pathlib import Path
from loop_adb.client import AdbClient
from loop_deploy.adb_ops import AdbOps
from loop_deploy.models import DeployPlan, DeployMode, DeployResult, DeployErrorCode


class Deployer:
    def __init__(self, client: AdbClient, aosp_out: str = ""):
        self._client = client
        self._ops = AdbOps(client)
        self._aosp_out = aosp_out

    def deploy(self, plan: DeployPlan, artifacts: list[str]) -> DeployResult:
        if plan.mode == DeployMode.SKIP:
            return DeployResult(success=True, mode=DeployMode.SKIP)
        if plan.mode == DeployMode.FLASH_FULL:
            return DeployResult(success=False, mode=DeployMode.FLASH_FULL,
                                error="FLASH_FULL requires manual full image flash")
        if plan.mode == DeployMode.PUSH_SINGLE:
            return self._deploy_push_single(plan, artifacts)
        if plan.mode == DeployMode.DD_BOOT_REBOOT:
            return self._deploy_dd_boot(plan, artifacts)
        return DeployResult(success=False, mode=plan.mode, error=f"unknown mode: {plan.mode}")

    def _deploy_push_single(self, plan: DeployPlan, artifacts: list[str]) -> DeployResult:
        start = time.time()
        backup_files: list[str] = []

        root_r = self._client.root(timeout_sec=10.0)
        if root_r.exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                error=f"adb root failed: {root_r.stderr}",
                                error_code=DeployErrorCode.ADB_ROOT_FAILED,
                                duration_seconds=time.time() - start)
        remount_r = self._client.remount(timeout_sec=15.0)
        if remount_r.exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                error=f"adb remount failed: {remount_r.stderr}",
                                error_code=DeployErrorCode.ADB_REMOUNT_FAILED,
                                duration_seconds=time.time() - start)

        backup_dir = Path(tempfile.gettempdir()) / f"le_push_backup_{int(time.time())}"
        backup_dir.mkdir(parents=True, exist_ok=True)

        for target in plan.deploy_targets:
            if not target.artifact_name:
                continue
            local_path = self._find_artifact(artifacts, target.artifact_name)
            if not local_path:
                return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                    error=f"artifact {target.artifact_name} not found",
                                    error_code=DeployErrorCode.ARTIFACT_NOT_FOUND,
                                    duration_seconds=time.time() - start)
            # adb pull 当前远端文件作为备份（用于 rollback）
            if target.remote_path:
                backup_local = backup_dir / Path(target.remote_path).name
                try:
                    self._client.pull(target.remote_path, str(backup_local), timeout_sec=15.0)
                    backup_files.append(target.remote_path)
                except Exception:
                    pass
            push_r = self._client.push(local_path, target.remote_path, timeout_sec=30.0)
            if push_r.exit_code != 0:
                return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                    error=f"adb push failed: {push_r.stderr}",
                                    error_code=DeployErrorCode.ADB_PUSH_FAILED,
                                    duration_seconds=time.time() - start,
                                    backup_path=str(backup_dir),
                                    deployed_files=backup_files)
            if target.service_name:
                self._client.shell(f"setprop ctl.restart {target.service_name}", timeout_sec=5.0)
                if not self._ops.wait_service_running(target.service_name, timeout=15.0):
                    return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                        error=f"service {target.service_name} did not start",
                                        error_code=DeployErrorCode.SERVICE_NOT_STARTED,
                                        duration_seconds=time.time() - start,
                                        backup_path=str(backup_dir),
                                        deployed_files=backup_files)

        return DeployResult(success=True, mode=DeployMode.PUSH_SINGLE,
                            duration_seconds=time.time() - start,
                            backup_path=str(backup_dir),
                            deployed_files=backup_files)

    def _deploy_dd_boot(self, plan: DeployPlan, artifacts: list[str]) -> DeployResult:
        start = time.time()
        block_device = plan.deploy_targets[0].block_device if plan.deploy_targets else "/dev/block/mmcblk0p1"
        boot_img = None
        for a in artifacts:
            if a.endswith("boot.img"):
                boot_img = a
                break
        if not boot_img:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error="boot.img not found in artifacts",
                                error_code=DeployErrorCode.ARTIFACT_NOT_FOUND,
                                duration_seconds=time.time() - start)

        self._client.root(timeout_sec=10.0)
        remote = "/data/local/tmp/boot.img"
        push_r = self._client.push(boot_img, remote, timeout_sec=60.0)
        if push_r.exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"adb push boot.img failed: {push_r.stderr}",
                                error_code=DeployErrorCode.ADB_PUSH_FAILED)

        with open(boot_img, "rb") as f:
            host_sha = hashlib.sha256(f.read()).hexdigest()
        sha_result = self._client.shell(f"sha256sum {remote}", timeout_sec=10.0)
        remote_sha = sha_result.raw_stdout.strip().split()[0] if sha_result.command_exit_code == 0 else ""
        if host_sha != remote_sha:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"sha256 mismatch: host={host_sha[:16]}... remote={remote_sha[:16]}...",
                                error_code=DeployErrorCode.SHA256_MISMATCH)

        from loop_deploy.image_verify import verify_image

        backup_dir = Path("/tmp") / f"le_backup_{int(time.time())}"
        verify_result = verify_image(boot_img, "boot.img", backup_dir)
        if not verify_result.passed:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"image verify failed: {verify_result.reason}",
                                error_code=DeployErrorCode.IMAGE_VERIFY_FAILED)

        try:
            health = self._client.shell("getprop sys.boot_completed", timeout_sec=10.0)
            if health.command_exit_code != 0 or "1" not in health.raw_stdout:
                return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                    error="device not healthy (boot_completed != 1), abort dd",
                                    error_code=DeployErrorCode.DEVICE_NOT_HEALTHY)
        except Exception as e:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"health check failed: {e}",
                                error_code=DeployErrorCode.HEALTH_CHECK_FAILED)

        # --- 备份原 boot 分区到 HOST（不依赖 /data 挂载，串口回滚可达）---
        ts = int(time.time())
        host_backup = f"/tmp/le_boot_backup_{ts}.img"
        try:
            pull_r = self._client.shell(
                "dd if=/dev/block/mmcblk0p1 bs=4M", timeout_sec=60.0, as_root=True,
            )
            with open(host_backup, "wb") as f:
                f.write(pull_r.raw_stdout.encode("latin-1") if isinstance(pull_r.raw_stdout, str) else pull_r.raw_stdout)
            with open(host_backup, "rb") as f:
                backup_sha = hashlib.sha256(f.read()).hexdigest()
        except Exception:
            backup_sha = ""
            host_backup = ""

        # --- 同时写入设备端 /data 备份（adb 可用时的快速回滚路径）---
        remote_backup = f"/data/local/tmp/boot_backup_{ts}.img"
        try:
            self._client.shell(
                f"dd if=/dev/block/mmcblk0p1 of={remote_backup} bs=4M",
                timeout_sec=60.0, as_root=True,
            )
        except Exception:
            pass

        dd_r = self._client.shell("dd if=/data/local/tmp/boot.img of=/dev/block/mmcblk0p1 bs=4M", timeout_sec=30.0, as_root=True)
        if dd_r.command_exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                requires_reboot=False,
                                error=f"dd write failed (exit {dd_r.command_exit_code}): {(dd_r.raw_stdout or '')[:200]}",
                                error_code=DeployErrorCode.DD_WRITE_FAILED,
                                duration_seconds=time.time() - start,
                                backup_path=host_backup,
                                backup_sha=backup_sha)
        self._client.shell("sync", timeout_sec=10.0, as_root=True)
        self._client.shell(f"rm {remote}", timeout_sec=5.0, as_root=True)
        self._client.reboot(timeout_sec=15.0)
        time.sleep(5)
        if not self._ops.wait_boot_completed(timeout=120.0):
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error="boot_completed not reached after reboot",
                                error_code=DeployErrorCode.BOOT_COMPLETED_NOT_REACHED,
                                backup_path=host_backup,
                                backup_sha=backup_sha)
        try:
            logcat = self._client.logcat(["crash"], timeout_sec=10.0)
            if logcat.exit_code == 0 and any("panic" in line.lower() for line in logcat.stdout.splitlines()):
                return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                    error="kernel panic detected in logcat after reboot",
                                    error_code=DeployErrorCode.KERNEL_PANIC,
                                    backup_path=host_backup,
                                    backup_sha=backup_sha)
        except Exception:
            pass
        self._client.connect(timeout_sec=15.0)
        return DeployResult(success=True, mode=DeployMode.DD_BOOT_REBOOT, requires_reboot=True,
                            duration_seconds=time.time() - start,
                            backup_path=host_backup,
                            backup_sha=backup_sha)

    def _find_artifact(self, artifacts: list[str], name: str) -> str:
        for a in artifacts:
            if a.endswith(name) or name in a:
                return a
        if self._aosp_out:
            for root, _dirs, files in os.walk(self._aosp_out):
                for f in files:
                    if f == name:
                        return str(Path(root) / f)
        return ""
