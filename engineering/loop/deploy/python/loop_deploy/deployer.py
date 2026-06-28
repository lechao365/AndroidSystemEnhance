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
    def __init__(self, client: AdbClient, aosp_out: str = "",
                 serial_shell_provider: "callable | None" = None):
        self._client = client
        self._ops = AdbOps(client)
        self._aosp_out = aosp_out
        # serial_shell_provider：用于 dd reboot 后 panic 检测和 serial shell 可达性探测
        # 签名：callable[[str], str | None] —— 传入 shell 命令，返回输出文本或 None（不可达）
        self._serial_shell_provider = serial_shell_provider

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
        warnings: list[str] = []

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
                except Exception as e:
                    # 备份失败不静默吞掉：记录 warning，该文件回滚时不可达
                    warnings.append(f"backup failed for {target.remote_path}: {type(e).__name__}: {e}")
            push_r = self._client.push(local_path, target.remote_path, timeout_sec=30.0)
            if push_r.exit_code != 0:
                return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                    error=f"adb push failed: {push_r.stderr}",
                                    error_code=DeployErrorCode.ADB_PUSH_FAILED,
                                    duration_seconds=time.time() - start,
                                    backup_path=str(backup_dir),
                                    deployed_files=backup_files,
                                    warnings=warnings)
            if target.service_name:
                self._client.shell(f"setprop ctl.restart {target.service_name}", timeout_sec=5.0)
                if target.oneshot:
                    started = self._ops.wait_oneshot_started(target.service_name, timeout=15.0)
                else:
                    started = self._ops.wait_service_running(target.service_name, timeout=15.0)
                if not started:
                    return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                        error=f"service {target.service_name} did not start",
                                        error_code=DeployErrorCode.SERVICE_NOT_STARTED,
                                        duration_seconds=time.time() - start,
                                        backup_path=str(backup_dir),
                                        deployed_files=backup_files,
                                        warnings=warnings)

        # 部署成功后清 logcat buffer，确保后续 verify 检查的是新 daemon 日志
        try:
            self._client.shell("logcat -c", timeout_sec=5.0)
        except Exception:
            pass

        return DeployResult(success=True, mode=DeployMode.PUSH_SINGLE,
                            duration_seconds=time.time() - start,
                            backup_path=str(backup_dir),
                            deployed_files=backup_files,
                            warnings=warnings)

    def _deploy_dd_boot(self, plan: DeployPlan, artifacts: list[str]) -> DeployResult:
        start = time.time()
        warnings: list[str] = []
        # 统一使用 plan 中的 block_device，消除硬编码
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

        root_r = self._client.root(timeout_sec=10.0)
        if root_r.exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"adb root failed: {root_r.stderr}",
                                error_code=DeployErrorCode.ADB_ROOT_FAILED,
                                duration_seconds=time.time() - start)
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

        from loop_deploy.image_verify import verify_image, verify_backup_integrity

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

        # --- 阶段3-a：磁盘空间检查（dd 写入前确保 /data 有足够空间）---
        # fail-closed：无法确认可用空间（df 命令失败 / 输出不可解析）即中止，
        # 不在空间不确定的情况下执行不可逆的 dd 写分区。
        boot_img_size = Path(boot_img).stat().st_size
        try:
            df_r = self._client.shell("df /data", timeout_sec=10.0)
        except Exception as exc:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"disk space check failed (df command error): {exc}",
                                error_code=DeployErrorCode.DISK_CHECK_FAILED,
                                duration_seconds=time.time() - start)
        avail_bytes = None
        if df_r.command_exit_code == 0:
            # 解析 df 输出：用 output_lines（已剥离 exit code marker）
            lines = getattr(df_r, "output_lines", None) or df_r.raw_stdout.strip().splitlines()
            if lines:
                parts = lines[-1].split()
                if len(parts) >= 4:
                    try:
                        avail_bytes = int(parts[3]) * 1024
                    except ValueError:
                        avail_bytes = None
        if avail_bytes is None:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"disk space check failed (df output unparseable): {df_r.raw_stdout[:120]!r}",
                                error_code=DeployErrorCode.DISK_CHECK_FAILED,
                                duration_seconds=time.time() - start)
        # 至少需要 boot_img 大小的 2 倍（备份 + 写入）
        if avail_bytes < boot_img_size * 2:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"disk full: /data available {avail_bytes} bytes, need {boot_img_size * 2}",
                                error_code=DeployErrorCode.DISK_FULL,
                                duration_seconds=time.time() - start)

        # --- 备份原 boot 分区到 HOST（不依赖 /data 挂载，串口回滚可达）---
        # fail-closed：备份创建失败即中止——没有可用回滚备份就执行 dd 等于砖化无救。
        ts = int(time.time())
        host_backup = f"/tmp/le_boot_backup_{ts}.img"
        try:
            # 注意：adb shell dd 输出二进制流，latin-1 是唯一能无损 round-trip 任意字节的编码
            pull_r = self._client.shell(
                f"dd if={block_device} bs=4M", timeout_sec=60.0, as_root=True,
            )
            with open(host_backup, "wb") as f:
                f.write(pull_r.raw_stdout.encode("latin-1") if isinstance(pull_r.raw_stdout, str) else pull_r.raw_stdout)
            with open(host_backup, "rb") as f:
                backup_sha = hashlib.sha256(f.read()).hexdigest()
        except Exception as exc:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"host boot backup failed, refuse dd (no rollback path): {exc}",
                                error_code=DeployErrorCode.BACKUP_FAILED,
                                duration_seconds=time.time() - start)

        # --- 阶段3-b：备份完整性校验（dd 写入前确保回滚备份可用）---
        if not verify_backup_integrity(Path(host_backup), backup_sha):
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error="host backup integrity check failed (sha mismatch)",
                                error_code=DeployErrorCode.BACKUP_CORRUPT,
                                duration_seconds=time.time() - start,
                                backup_path=host_backup,
                                backup_sha=backup_sha)

        # --- 同时写入设备端 /data 备份（adb 可用时的快速回滚路径）---
        remote_backup = f"/data/local/tmp/boot_backup_{ts}.img"
        try:
            self._client.shell(
                f"dd if={block_device} of={remote_backup} bs=4M",
                timeout_sec=60.0, as_root=True,
            )
        except Exception:
            warnings.append("remote backup write failed (adb rollback path unavailable)")

        dd_r = self._client.shell(f"dd if=/data/local/tmp/boot.img of={block_device} bs=4M", timeout_sec=30.0, as_root=True)
        if dd_r.command_exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                requires_reboot=False,
                                error=f"dd write failed (exit {dd_r.command_exit_code}): {(dd_r.raw_stdout or '')[:200]}",
                                error_code=DeployErrorCode.DD_WRITE_FAILED,
                                duration_seconds=time.time() - start,
                                backup_path=host_backup,
                                backup_sha=backup_sha,
                                block_device=block_device,
                                warnings=warnings)
        self._client.shell("sync", timeout_sec=10.0, as_root=True)
        self._client.shell(f"rm {remote}", timeout_sec=5.0, as_root=True)
        self._client.reboot(timeout_sec=15.0)
        time.sleep(5)
        if not self._ops.wait_boot_completed(timeout=120.0):
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error="boot_completed not reached after reboot",
                                error_code=DeployErrorCode.BOOT_COMPLETED_NOT_REACHED,
                                backup_path=host_backup,
                                backup_sha=backup_sha,
                                block_device=block_device,
                                warnings=warnings)

        # --- 阶段4-a：panic 检测（优先 serial buffer，logcat 作辅助）---
        panic_detected = False
        panic_source = ""
        # 优先用 serial shell 检测（kernel panic 时 adb 可能不通，serial 是唯一可信通道）
        if self._serial_shell_provider:
            try:
                serial_output = self._serial_shell_provider("dmesg | tail -50")
                if serial_output and isinstance(serial_output, str):
                    lowered = serial_output.lower()
                    for marker in ("kernel panic", "kernel oops", "---[ end trace", "bug:"):
                        if marker in lowered:
                            panic_detected = True
                            panic_source = f"serial:{marker}"
                            break
            except Exception:
                pass  # serial 不可用，降级到 logcat
        # logcat 作为辅助检测手段（adb 可达时有效）
        if not panic_detected:
            try:
                logcat = self._client.logcat(["crash"], timeout_sec=10.0)
                if logcat.exit_code == 0 and any("panic" in line.lower() for line in logcat.stdout.splitlines()):
                    panic_detected = True
                    panic_source = "logcat:panic"
            except Exception:
                pass
        # fail-safe：serial 和 logcat 均不可达时，判定为 panic（宁可误报失败）
        if not panic_detected and not self._serial_shell_provider:
            try:
                self._client.shell("echo __LE_ADB_OK__", timeout_sec=5.0)
            except Exception:
                panic_detected = True
                panic_source = "fail-safe:adb_unreachable"
        if panic_detected:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"kernel panic detected after reboot ({panic_source})",
                                error_code=DeployErrorCode.KERNEL_PANIC,
                                backup_path=host_backup,
                                backup_sha=backup_sha,
                                block_device=block_device,
                                warnings=warnings)

        # --- 阶段4-b：关键 service 存活检查 ---
        for target in plan.deploy_targets:
            if target.service_name:
                if target.oneshot:
                    started = self._ops.wait_oneshot_started(target.service_name, timeout=30.0)
                else:
                    started = self._ops.wait_service_running(target.service_name, timeout=30.0)
                if not started:
                    return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                        error=f"critical service {target.service_name} not running after dd reboot",
                                        error_code=DeployErrorCode.CRITICAL_SERVICE_DOWN,
                                        backup_path=host_backup,
                                        backup_sha=backup_sha,
                                        block_device=block_device,
                                        warnings=warnings)

        # --- 阶段4-c：serial shell 可达性探测（确保回滚路径可达）---
        if self._serial_shell_provider:
            try:
                serial_check = self._serial_shell_provider("echo __LE_SER_OK__")
                if not serial_check or "__LE_SER_OK__" not in str(serial_check):
                    warnings.append("serial shell reachable check failed (rollback path may be unavailable)")
            except Exception:
                warnings.append("serial shell reachable check failed (rollback path may be unavailable)")

        self._client.connect(timeout_sec=15.0)
        return DeployResult(success=True, mode=DeployMode.DD_BOOT_REBOOT, requires_reboot=True,
                            duration_seconds=time.time() - start,
                            backup_path=host_backup,
                            backup_sha=backup_sha,
                            block_device=block_device,
                            warnings=warnings)

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
