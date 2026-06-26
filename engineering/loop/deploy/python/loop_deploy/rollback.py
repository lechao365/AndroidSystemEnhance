"""Serial 回退逻辑：dd 备份恢复。"""
from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RollbackResult:
    success: bool
    reason: str = ""


def _serial_push_file(serial_shell: callable, host_path: str, device_path: str) -> bool:
    """通过 serial shell（base64 分块）将 host 文件传输到 device 路径。"""
    if not Path(host_path).exists():
        return False
    with open(host_path, "rb") as f:
        encoded = base64.b64encode(f.read()).decode()
    CHUNK = 2048
    serial_shell(f"rm -f {device_path}")
    for i in range(0, len(encoded), CHUNK):
        chunk = encoded[i:i + CHUNK]
        serial_shell(f"echo -n {chunk} >> {device_path}")
    serial_shell(f"base64 -d {device_path} > {device_path}.raw && mv {device_path}.raw {device_path}")
    return True


def serial_rollback_dd(
    serial_shell: callable | None,
    backup_path: str,
    block_device: str,
    remote_backup_dir: str = "/tmp",
) -> RollbackResult:
    if serial_shell is None:
        return RollbackResult(success=False, reason="serial not available, cannot rollback")

    backup = Path(backup_path)
    # --- 分支1：backup 在 host 上（DD_BOOT 新行为）---
    if backup.exists():
        # 通过 serial 将 host 文件推送到设备 /tmp（tmpfs，不依赖 /data 挂载）
        device_tmp = f"{remote_backup_dir}/{backup.name}"
        if not _serial_push_file(serial_shell, backup_path, device_tmp):
            return RollbackResult(success=False, reason=f"serial push failed for {backup_path}")
        dd_cmd = f"dd if={device_tmp} of={block_device} bs=4M && sync"
        result = serial_shell(f"su 0 sh -c '{dd_cmd}'")
        if result and "error" not in result.lower():
            serial_shell(f"rm -f {device_tmp}")
            serial_shell("reboot")
            return RollbackResult(success=True, reason="rollback initiated via serial (host backup)")
        return RollbackResult(success=False, reason=f"dd rollback failed: {result}")

    # --- 分支2：backup 在设备端（旧行为，/data 路径回退）---
    remote_backup = f"{remote_backup_dir}/{backup.name}"
    check = serial_shell(f"ls {remote_backup} 2>/dev/null")
    if not check or "No such file" in check:
        return RollbackResult(success=False, reason=f"remote backup not found: {remote_backup}")
    dd_cmd = f"dd if={remote_backup} of={block_device} bs=4M && sync"
    result = serial_shell(f"su 0 sh -c '{dd_cmd}'")
    if result and "error" not in result.lower():
        serial_shell("reboot")
        return RollbackResult(success=True, reason="rollback initiated via serial")
    return RollbackResult(success=False, reason=f"dd rollback failed: {result}")


def verify_remote_backup_sha(serial_shell: callable, remote_path: str, expected_sha: str) -> bool:
    result = serial_shell(f"sha256sum {remote_path} 2>/dev/null")
    if not result:
        return False
    parts = result.strip().split()
    return len(parts) > 0 and parts[0] == expected_sha
