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


def test_serial_rollback_branch2_sha_mismatch_refuses_dd(tmp_path: Path):
    """分支2（设备端备份）：expected_sha 与 remote sha 不匹配时必须 fail-closed，
    拒绝执行 dd（防止损坏备份写坏设备）。

    回归 P0-3：原 `sha_parts[0] != sha_parts[0].strip()` 恒 False，校验架空，
    损坏备份照样 dd。修复后必须真实比对 expected_sha 并在不匹配时拒绝。
    """
    expected = "a" * 64
    calls: list[str] = []

    def fake_shell(cmd):
        calls.append(cmd)
        if cmd.startswith("ls "):
            return "/tmp/boot.img.bak"
        if cmd.startswith("sha256sum"):
            return ("b" * 64) + "  /tmp/boot.img.bak\n"  # 与 expected 不符
        return "ok"

    result = serial_rollback_dd(
        serial_shell=fake_shell,
        backup_path=str(tmp_path / "boot.img.bak"),  # host 不存在 → 走分支2
        block_device="/dev/block/mmcblk0p1",
        expected_sha=expected,
    )
    assert result.success is False
    assert "sha" in result.reason.lower()
    # 关键：sha 不匹配时 dd 绝不能执行
    assert not any("dd if=" in c for c in calls)


def test_serial_rollback_branch2_sha_unavailable_refuses_dd(tmp_path: Path):
    """分支2：提供了 expected_sha 但 remote sha256sum 取不到（命令失败/空）→ fail-closed。"""
    calls: list[str] = []

    def fake_shell(cmd):
        calls.append(cmd)
        if cmd.startswith("ls "):
            return "/tmp/boot.img.bak"
        if cmd.startswith("sha256sum"):
            return ""  # 取不到 sha
        return "ok"

    result = serial_rollback_dd(
        serial_shell=fake_shell,
        backup_path=str(tmp_path / "boot.img.bak"),
        block_device="/dev/block/mmcblk0p1",
        expected_sha="a" * 64,
    )
    assert result.success is False
    assert not any("dd if=" in c for c in calls)


def test_serial_rollback_branch2_sha_match_proceeds(tmp_path: Path):
    """分支2：expected_sha 与 remote sha 匹配 → 正常执行 dd 回滚。"""
    expected = "a" * 64
    calls: list[str] = []

    def fake_shell(cmd):
        calls.append(cmd)
        if cmd.startswith("ls "):
            return "/tmp/boot.img.bak"
        if cmd.startswith("sha256sum"):
            return expected + "  /tmp/boot.img.bak\n"
        return "ok"  # dd 成功（无 "dd:" 错误前缀）

    result = serial_rollback_dd(
        serial_shell=fake_shell,
        backup_path=str(tmp_path / "boot.img.bak"),
        block_device="/dev/block/mmcblk0p1",
        expected_sha=expected,
    )
    assert result.success is True
    assert any("dd if=" in c for c in calls)

