"""loop_deploy 数据模型：DeployPlan / DeployMode / DeployTarget / DeployResult。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class DeployMode(StrEnum):
    SKIP = "skip"
    PUSH_SINGLE = "push_single"
    DD_BOOT_REBOOT = "dd_boot_reboot"
    FLASH_FULL = "flash_full"


@dataclass
class DeployTarget:
    artifact_name: str
    remote_path: str
    service_name: str = ""
    block_device: str = ""


@dataclass
class DeployPlan:
    mode: DeployMode
    changed_files: list[str] = field(default_factory=list)
    reason: str = ""
    build_targets: list[str] = field(default_factory=list)
    deploy_targets: list[DeployTarget] = field(default_factory=list)
    requires_reboot: bool = False
    estimated_seconds: int = 0

    @classmethod
    def skip(cls, reason: str = "") -> DeployPlan:
        return cls(mode=DeployMode.SKIP, reason=reason)

    @classmethod
    def flash_full(cls, changed_files: list[str], reason: str = "") -> DeployPlan:
        return cls(mode=DeployMode.FLASH_FULL, changed_files=changed_files, reason=reason)


@dataclass
class CompileResult:
    success: bool
    artifacts: list[str] = field(default_factory=list)
    error: str = ""
    elapsed_seconds: float = 0.0


@dataclass
class DeployResult:
    success: bool
    mode: DeployMode
    duration_seconds: float = 0.0
    requires_reboot: bool = False
    error: str = ""
    backup_path: str = ""
    backup_sha: str = ""
    deployed_files: list[str] = field(default_factory=list)
