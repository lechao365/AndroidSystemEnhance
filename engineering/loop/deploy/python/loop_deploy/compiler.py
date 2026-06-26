"""Compiler: 调用 mk_rpi5_full_image.sh / mmm 编译产物。"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from loop_deploy.models import DeployPlan, DeployMode, CompileResult


def compile_plan(plan: DeployPlan, workspace_root: str = "") -> CompileResult:
    if plan.mode == DeployMode.SKIP:
        return CompileResult(success=True, artifacts=[])
    if plan.mode == DeployMode.FLASH_FULL:
        return CompileResult(success=False, error="FLASH_FULL mode requires manual full image build")

    if not workspace_root:
        workspace_root = _find_workspace_root()

    if plan.mode == DeployMode.DD_BOOT_REBOOT:
        return _compile_dd_boot()
    if plan.mode == DeployMode.PUSH_SINGLE:
        return _compile_push_single(plan, workspace_root)

    return CompileResult(success=False, error=f"unknown mode: {plan.mode}")


def _compile_dd_boot() -> CompileResult:
    start = time.time()
    harness_dir = Path(__file__).resolve().parent.parent.parent.parent.parent / "harness" / "scripts"
    script = harness_dir / "mk_rpi5_full_image.sh"
    cmd = f"bash {script} -mode 2"
    try:
        result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=1800)
    except subprocess.TimeoutExpired:
        return CompileResult(success=False, error="mode 2 compilation timed out (30min)")
    elapsed = time.time() - start
    if result.returncode != 0:
        err = (result.stderr or result.stdout)[-500:]
        return CompileResult(success=False, error=f"mode 2 failed (exit {result.returncode}): {err}")

    aosp_out = os.environ.get("ANDROID_PRODUCT_OUT", os.path.expanduser("~/workspace/aosp/out/target/product/rpi5"))
    boot_img = os.path.join(aosp_out, "boot.img")
    if not os.path.isfile(boot_img):
        return CompileResult(success=False, error=f"boot.img not found at {boot_img}")
    return CompileResult(success=True, artifacts=[boot_img], elapsed_seconds=elapsed)


def _compile_push_single(plan: DeployPlan, workspace_root: str) -> CompileResult:
    start = time.time()
    build_target = plan.build_targets[0] if plan.build_targets else ""
    cmd = (
        f"cd {workspace_root} && "
        f"source build/envsetup.sh 2>/dev/null && "
        f"lunch aosp_rpi5-bp1a-userdebug 2>/dev/null && "
        f"mmm {build_target} -j$(nproc)"
    )
    try:
        result = subprocess.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return CompileResult(success=False, error="mmm compilation timed out (10min)")
    elapsed = time.time() - start
    if result.returncode != 0:
        err = (result.stderr or result.stdout)[-500:]
        return CompileResult(success=False, error=f"mmm failed (exit {result.returncode}): {err}")

    # 从 $OUT 目录查找编译产物，匹配 plan.deploy_targets 的 artifact_name
    aosp_out = os.environ.get("ANDROID_PRODUCT_OUT", os.path.expanduser("~/workspace/aosp/out/target/product/rpi5"))
    artifacts = _find_artifacts_in_out(aosp_out, plan)
    return CompileResult(success=True, artifacts=artifacts, elapsed_seconds=elapsed)


def _find_artifacts_in_out(aosp_out: str, plan: DeployPlan) -> list[str]:
    """在 $OUT 目录下递归查找 plan.deploy_targets 的 artifact_name 对应文件。"""
    found: list[str] = []
    if not plan.deploy_targets:
        return found
    for target in plan.deploy_targets:
        if not target.artifact_name:
            continue
        matched = _find_file_in_out(aosp_out, target.artifact_name)
        if matched:
            found.append(matched)
    return found


def _find_file_in_out(aosp_out: str, name: str) -> str:
    """在 $OUT 目录下查找 name 文件的绝对路径。"""
    out_path = Path(aosp_out)
    if not out_path.exists():
        return ""
    # 直接路径优先
    direct = out_path / name
    if direct.is_file():
        return str(direct)
    # 递归查找（最多2层）
    for root, _dirs, files in os.walk(str(out_path)):
        if name in files:
            return str(Path(root) / name)
        # 限制深度
        if Path(root).relative_to(out_path).parts and len(Path(root).relative_to(out_path).parts) > 2:
            _dirs.clear()
    return ""


def _find_workspace_root() -> str:
    return os.environ.get("AOSP_ROOT", os.path.expanduser("~/workspace/aosp"))
