"""Serial 回退逻辑：dd 备份恢复。"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class RollbackResult:
    success: bool
    reason: str = ""


def serial_rollback_dd(
    serial_shell: callable | None,
    backup_path: str,
    block_device: str,
    remote_backup_dir: str = "/data/local/tmp",
) -> RollbackResult:
    if serial_shell is None:
        return RollbackResult(success=False, reason="serial not available, cannot rollback")
    backup = Path(backup_path)
    if not backup.exists():
        return RollbackResult(success=False, reason=f"backup not found: {backup_path}")
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
