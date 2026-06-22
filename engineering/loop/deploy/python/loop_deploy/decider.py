"""DeployDecider: git diff 内容分析 → 决策 DeployPlan。"""
from __future__ import annotations

import subprocess
from pathlib import Path
from loop_deploy.models import DeployMode, DeployPlan, DeployTarget


_KERNEL_PATTERNS = ["kernel/"]
_PUSH_CPP_PATTERNS = ["vendor/lechao/services/lechao_lciod"]
_TE_PATTERNS = ["sepolicy/"]
_RC_PATTERNS = ["lechao_lciod"]
_USB_VERIFY_PATTERNS = ["usb-verify", "usb-fault-inject"]
_SKIP_SUFFIXES = {".md", ".yaml", ".txt"}

_BOOT_TARGET = DeployTarget(
    artifact_name="boot.img",
    remote_path="/data/local/tmp/boot.img",
    block_device="/dev/block/mmcblk0p1",
)
_HAL_TARGET = DeployTarget(
    artifact_name="lechao_lciod_hal",
    remote_path="/vendor/bin/hw/lechao_lciod_hal",
    service_name="lechao_lciod_hal",
)
_DAEMON_TARGET = DeployTarget(
    artifact_name="lechao_lciod",
    remote_path="/system/bin/lechao_lciod",
    service_name="lechao_lciod",
)


def decide(diff_files: list[str]) -> DeployPlan:
    if not diff_files:
        return DeployPlan.skip("no changed files")

    has_kernel = False
    has_te = False
    has_cpp = False
    has_rc = False
    has_usb_verify = False
    all_docs = True

    for f in diff_files:
        f_lower = f.lower()
        if any(p in f for p in _KERNEL_PATTERNS):
            has_kernel = True
            all_docs = False
        if any(p in f_lower for p in _TE_PATTERNS):
            has_te = True
            all_docs = False
        if any(p in f for p in _PUSH_CPP_PATTERNS) and not f.endswith(".rc"):
            has_cpp = True
            all_docs = False
        if any(p in f for p in _RC_PATTERNS) and f.endswith(".rc"):
            has_rc = True
            all_docs = False
        if any(p in f for p in _USB_VERIFY_PATTERNS):
            has_usb_verify = True
            all_docs = False
        if Path(f).suffix.lower() not in _SKIP_SUFFIXES:
            all_docs = False

    if all_docs:
        return DeployPlan.skip(f"all changed files are docs: {diff_files}")

    type_count = sum([has_kernel, has_te, has_cpp, has_rc, has_usb_verify])
    if type_count >= 2:
        return DeployPlan.flash_full(diff_files, f"mixed changes: {type_count} types")

    if has_kernel:
        return DeployPlan(
            mode=DeployMode.DD_BOOT_REBOOT, changed_files=diff_files,
            reason="kernel driver changes require boot.img rebuild",
            build_targets=["mode_2"], deploy_targets=[_BOOT_TARGET],
            requires_reboot=True, estimated_seconds=1800,
        )
    if has_rc:
        return DeployPlan(
            mode=DeployMode.DD_BOOT_REBOOT, changed_files=diff_files,
            reason="init.rc changes require boot.img rebuild",
            build_targets=["mode_2"], deploy_targets=[_BOOT_TARGET],
            requires_reboot=True, estimated_seconds=1800,
        )
    if has_te:
        return DeployPlan.flash_full(diff_files, "sepolicy changes require full flash (vendor dd not verified)")

    if has_cpp:
        return DeployPlan(
            mode=DeployMode.PUSH_SINGLE, changed_files=diff_files,
            reason="lciod cpp changes: mmm + push binary",
            build_targets=["vendor/lechao/services/lechao_lciod"],
            deploy_targets=[_HAL_TARGET, _DAEMON_TARGET],
            requires_reboot=False, estimated_seconds=300,
        )
    if has_usb_verify:
        return DeployPlan(
            mode=DeployMode.PUSH_SINGLE, changed_files=diff_files,
            reason="usb-verify tool changes",
            build_targets=["usb-verify"],
            deploy_targets=[DeployTarget(artifact_name="fault-verify", remote_path="/system/bin/fault-verify")],
            requires_reboot=False, estimated_seconds=120,
        )

    return DeployPlan.skip(f"no recognized patterns in: {diff_files}")


def get_diff_files(rev: str = "HEAD") -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", rev],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise RuntimeError(f"git diff failed: {exc}")
    if result.returncode != 0:
        lines = result.stderr.strip().splitlines()
        raise RuntimeError(f"git diff failed: {lines[-1] if lines else 'unknown'}")
    return [f.strip() for f in result.stdout.splitlines() if f.strip()]
