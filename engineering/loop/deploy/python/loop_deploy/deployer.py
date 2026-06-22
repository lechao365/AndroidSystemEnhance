"""Deployer: push_single / dd_boot_reboot 部署执行。"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path
from loop_adb.client import AdbClient
from loop_deploy.adb_ops import AdbOps
from loop_deploy.models import DeployPlan, DeployMode, DeployResult


class Deployer:
    def __init__(self, client: AdbClient, aosp_out: str = ""):
        self._client = client
        self._ops = AdbOps(client)
        self._aosp_out = aosp_out

    def deploy(self, plan: DeployPlan, artifacts: list[str]) -> DeployResult:
        if plan.mode == DeployMode.SKIP:
            return DeployResult(success=True, mode=DeployMode.SKIP)
        if plan.mode == DeployMode.FLASH_FULL:
            return DeployResult(success=False, mode=DeployMode.FLASH_FULL,
                                error="FLASH_FULL requires manual full image flash")
        if plan.mode == DeployMode.PUSH_SINGLE:
            return self._deploy_push_single(plan, artifacts)
        if plan.mode == DeployMode.DD_BOOT_REBOOT:
            return self._deploy_dd_boot(artifacts)
        return DeployResult(success=False, mode=plan.mode, error=f"unknown mode: {plan.mode}")

    def _deploy_push_single(self, plan: DeployPlan, artifacts: list[str]) -> DeployResult:
        start = time.time()

        root_r = self._client.root(timeout_sec=10.0)
        if root_r.exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                error=f"adb root failed: {root_r.stderr}", duration_seconds=time.time() - start)
        remount_r = self._client.remount(timeout_sec=15.0)
        if remount_r.exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                error=f"adb remount failed: {remount_r.stderr}", duration_seconds=time.time() - start)

        for target in plan.deploy_targets:
            if not target.artifact_name:
                continue
            local_path = self._find_artifact(artifacts, target.artifact_name)
            if not local_path:
                return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                    error=f"artifact {target.artifact_name} not found",
                                    duration_seconds=time.time() - start)
            push_r = self._client.push(local_path, target.remote_path, timeout_sec=30.0)
            if push_r.exit_code != 0:
                return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                    error=f"adb push failed: {push_r.stderr}",
                                    duration_seconds=time.time() - start)
            if target.service_name:
                self._client.shell(f"setprop ctl.restart {target.service_name}", timeout_sec=5.0)
                if not self._ops.wait_service_running(target.service_name, timeout=15.0):
                    return DeployResult(success=False, mode=DeployMode.PUSH_SINGLE,
                                        error=f"service {target.service_name} did not start",
                                        duration_seconds=time.time() - start)

        return DeployResult(success=True, mode=DeployMode.PUSH_SINGLE,
                            duration_seconds=time.time() - start)

    def _deploy_dd_boot(self, artifacts: list[str]) -> DeployResult:
        start = time.time()
        boot_img = None
        for a in artifacts:
            if a.endswith("boot.img"):
                boot_img = a
                break
        if not boot_img:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error="boot.img not found in artifacts", duration_seconds=time.time() - start)

        self._client.root(timeout_sec=10.0)
        remote = "/data/local/tmp/boot.img"
        push_r = self._client.push(boot_img, remote, timeout_sec=60.0)
        if push_r.exit_code != 0:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"adb push boot.img failed: {push_r.stderr}")

        host_sha = hashlib.sha256(open(boot_img, "rb").read()).hexdigest()
        sha_result = self._client.shell(f"sha256sum {remote}", timeout_sec=10.0)
        remote_sha = sha_result.raw_stdout.strip().split()[0] if sha_result.command_exit_code == 0 else ""
        if host_sha != remote_sha:
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error=f"sha256 mismatch: host={host_sha[:16]}... remote={remote_sha[:16]}...")

        self._client.shell("dd if=/data/local/tmp/boot.img of=/dev/block/mmcblk0p1 bs=4M", timeout_sec=30.0, as_root=True)
        self._client.shell("sync", timeout_sec=10.0, as_root=True)
        self._client.shell(f"rm {remote}", timeout_sec=5.0, as_root=True)
        self._client.reboot(timeout_sec=15.0)
        time.sleep(5)
        if not self._ops.wait_boot_completed(timeout=120.0):
            return DeployResult(success=False, mode=DeployMode.DD_BOOT_REBOOT,
                                error="boot_completed not reached after reboot")
        self._client.connect(timeout_sec=15.0)
        return DeployResult(success=True, mode=DeployMode.DD_BOOT_REBOOT, requires_reboot=True,
                            duration_seconds=time.time() - start)

    def _find_artifact(self, artifacts: list[str], name: str) -> str:
        for a in artifacts:
            if a.endswith(name) or name in a:
                return a
        if self._aosp_out:
            for root, _dirs, files in Path(self._aosp_out).walk():
                for f in files:
                    if f == name:
                        return str(Path(root) / f)
        return ""
