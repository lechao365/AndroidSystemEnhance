"""loop_deploy models 单元测试。"""
from loop_deploy.models import DeployMode, DeployPlan, DeployTarget, CompileResult, DeployResult


def test_deploy_mode_values():
    assert DeployMode.SKIP.value == "skip"
    assert DeployMode.PUSH_SINGLE.value == "push_single"
    assert DeployMode.DD_BOOT_REBOOT.value == "dd_boot_reboot"
    assert DeployMode.FLASH_FULL.value == "flash_full"


def test_deploy_plan_skip_factory():
    plan = DeployPlan.skip("no changes")
    assert plan.mode == DeployMode.SKIP
    assert plan.reason == "no changes"


def test_deploy_plan_flash_full_factory():
    plan = DeployPlan.flash_full(["foo.te"], "sepolicy change")
    assert plan.mode == DeployMode.FLASH_FULL
    assert "foo.te" in plan.changed_files


def test_deploy_target_fields():
    t = DeployTarget(artifact_name="boot.img", remote_path="/data/local/tmp/boot.img",
                     block_device="/dev/block/mmcblk0p1")
    assert t.artifact_name == "boot.img"
    assert t.block_device == "/dev/block/mmcblk0p1"


def test_compile_result_defaults():
    r = CompileResult(success=True, artifacts=["/tmp/boot.img"])
    assert r.success
    assert r.artifacts == ["/tmp/boot.img"]


def test_deploy_result():
    r = DeployResult(success=True, mode=DeployMode.PUSH_SINGLE, duration_seconds=2.5)
    assert r.success
    assert r.mode == DeployMode.PUSH_SINGLE
