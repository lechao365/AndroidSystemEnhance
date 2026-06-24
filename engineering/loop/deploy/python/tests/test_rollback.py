from pathlib import Path
from loop_deploy.rollback import serial_rollback_dd, verify_remote_backup_sha


def test_serial_rollback_no_serial():
    result = serial_rollback_dd(
        serial_shell=None,
        backup_path="/tmp/backup/boot.img.bak",
        block_device="/dev/block/mmcblk0p1",
    )
    assert result.success is False
    assert "serial not available" in result.reason


def test_serial_rollback_backup_missing(tmp_path: Path):
    def fake_shell(cmd):
        return "ok"
    result = serial_rollback_dd(
        serial_shell=fake_shell,
        backup_path=str(tmp_path / "nonexistent.bak"),
        block_device="/dev/block/mmcblk0p1",
    )
    assert result.success is False
    assert "backup not found" in result.reason


def test_serial_rollback_remote_missing(tmp_path: Path):
    backup = tmp_path / "boot.img.bak"
    backup.write_bytes(b"\x00" * 4096)
    def fake_shell(cmd):
        return "No such file or directory"
    result = serial_rollback_dd(
        serial_shell=fake_shell,
        backup_path=str(backup),
        block_device="/dev/block/mmcblk0p1",
    )
    assert result.success is False
    assert "remote backup not found" in result.reason


def test_verify_remote_backup_sha_ok():
    def fake_shell(cmd):
        return "abc123  /data/local/tmp/boot.img.bak\n"
    assert verify_remote_backup_sha(fake_shell, "/data/local/tmp/boot.img.bak", "abc123") is True


def test_verify_remote_backup_sha_mismatch():
    def fake_shell(cmd):
        return "wrong_sha  /data/local/tmp/boot.img.bak\n"
    assert verify_remote_backup_sha(fake_shell, "/data/local/tmp/boot.img.bak", "abc123") is False
