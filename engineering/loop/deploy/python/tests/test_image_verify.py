import hashlib
from pathlib import Path
from loop_deploy.image_verify import verify_image, verify_backup_integrity


def test_verify_image_ok(tmp_path: Path):
    img = tmp_path / "boot.img"
    img.write_bytes(b"\x00" * 8192)
    backup_dir = tmp_path / "backup"
    result = verify_image(str(img), "boot.img", backup_dir)
    assert result.passed is True
    assert "sha256" in result.checks
    assert result.backup_path != ""
    assert Path(result.backup_path).exists()


def test_verify_image_missing(tmp_path: Path):
    result = verify_image("/tmp/nonexistent.img", "boot.img", tmp_path)
    assert result.passed is False
    assert "not found" in result.reason


def test_verify_image_too_small(tmp_path: Path):
    img = tmp_path / "boot.img"
    img.write_bytes(b"\x00" * 1024)
    result = verify_image(str(img), "boot.img", tmp_path)
    assert result.passed is False
    assert "too small" in result.reason


def test_verify_backup_integrity_ok(tmp_path: Path):
    backup = tmp_path / "boot.img.bak"
    data = b"\x00" * 4096
    backup.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    assert verify_backup_integrity(backup, sha) is True


def test_verify_backup_integrity_mismatch(tmp_path: Path):
    backup = tmp_path / "boot.img.bak"
    backup.write_bytes(b"\x00" * 4096)
    assert verify_backup_integrity(backup, "wrong_sha") is False


def test_verify_backup_integrity_missing(tmp_path: Path):
    assert verify_backup_integrity(tmp_path / "nonexistent.bak", "any") is False
