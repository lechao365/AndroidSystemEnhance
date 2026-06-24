"""镜像验证：完整性/大小合理性/本地备份。"""
from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class VerifyResult:
    passed: bool
    checks: dict = field(default_factory=dict)
    reason: str = ""
    backup_path: str = ""
    backup_sha256: str = ""


_MIN_IMAGE_SIZE = 4096


def verify_image(image_path: str, artifact_name: str, backup_dir: Path) -> VerifyResult:
    path = Path(image_path)
    if not path.exists():
        return VerifyResult(passed=False, reason=f"{artifact_name} not found: {image_path}")
    size = path.stat().st_size
    if size < _MIN_IMAGE_SIZE:
        return VerifyResult(passed=False, reason=f"{artifact_name} too small ({size} bytes)")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = backup_dir / f"{artifact_name}.bak"
    shutil.copy2(str(path), str(backup_path))
    backup_sha = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    return VerifyResult(
        passed=True,
        checks={"sha256": sha, "size_bytes": size},
        backup_path=str(backup_path),
        backup_sha256=backup_sha,
    )


def verify_backup_integrity(backup_path: Path, expected_sha: str) -> bool:
    if not backup_path.exists():
        return False
    return hashlib.sha256(backup_path.read_bytes()).hexdigest() == expected_sha
