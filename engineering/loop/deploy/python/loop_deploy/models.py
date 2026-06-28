"""loop_deploy 数据模型：DeployPlan / DeployMode / DeployTarget / DeployResult。"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class DeployMode(StrEnum):
    SKIP = "skip"
    PUSH_SINGLE = "push_single"
    DD_BOOT_REBOOT = "dd_boot_reboot"
    FLASH_FULL = "flash_full"


class DeployErrorCode(StrEnum):
    """Deployer 结构化错误码，用于 runtime 精确路由 (guard/revert)，消除字符串匹配。"""
    NONE = "NONE"
    ADB_ROOT_FAILED = "ADB_ROOT_FAILED"
    ADB_REMOUNT_FAILED = "ADB_REMOUNT_FAILED"
    ADB_PUSH_FAILED = "ADB_PUSH_FAILED"
    SHA256_MISMATCH = "SHA256_MISMATCH"
    IMAGE_VERIFY_FAILED = "IMAGE_VERIFY_FAILED"
    DEVICE_NOT_HEALTHY = "DEVICE_NOT_HEALTHY"
    DD_WRITE_FAILED = "DD_WRITE_FAILED"
    BOOT_COMPLETED_NOT_REACHED = "BOOT_COMPLETED_NOT_REACHED"
    KERNEL_PANIC = "KERNEL_PANIC"
    SERVICE_NOT_STARTED = "SERVICE_NOT_STARTED"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    HEALTH_CHECK_FAILED = "HEALTH_CHECK_FAILED"
    DISK_FULL = "DISK_FULL"
    DISK_CHECK_FAILED = "DISK_CHECK_FAILED"
    BACKUP_CORRUPT = "BACKUP_CORRUPT"
    BACKUP_FAILED = "BACKUP_FAILED"
    CRITICAL_SERVICE_DOWN = "CRITICAL_SERVICE_DOWN"
    ADB_PULL_FAILED = "ADB_PULL_FAILED"
    UNKNOWN = "UNKNOWN"


@dataclass
class DeployTarget:
    artifact_name: str
    remote_path: str
    service_name: str = ""
    block_device: str = ""
    oneshot: bool = False


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
    error_code: DeployErrorCode = DeployErrorCode.NONE
    backup_path: str = ""
    backup_sha: str = ""
    deployed_files: list[str] = field(default_factory=list)
    block_device: str = ""
    warnings: list[str] = field(default_factory=list)
