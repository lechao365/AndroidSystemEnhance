"""DeployDecider 决策规则单元测试。"""
from loop_deploy.decider import decide
from loop_deploy.models import DeployMode


def test_kernel_c_changes_to_dd_boot():
    plan = decide(["kernel/new/vendor/lechao/LcIod/lciod_usbd.c"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT
    assert plan.requires_reboot


def test_kernel_h_changes_to_dd_boot():
    plan = decide(["kernel/new/vendor/lechao/LcIod/lciod_usbd.h"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT


def test_defconfig_changes_to_dd_boot():
    plan = decide(["kernel/modified/arch/arm64/configs/android_rpi5_defconfig.diff"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT


def test_usb_storage_diff_to_dd_boot():
    plan = decide(["kernel/modified/drivers/usb/storage/usb.c.diff"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT


def test_te_changes_to_flash_full():
    plan = decide(["device/brcm/rpi5/sepolicy/lechao_lciod.te"])
    assert plan.mode == DeployMode.FLASH_FULL


def test_rc_changes_to_dd_boot():
    plan = decide(["vendor/lechao/services/lechao_lciod/hal/lechao_lciod_hal.rc"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT


def test_cpp_changes_to_push_single():
    plan = decide(["vendor/lechao/services/lechao_lciod/hal/hal_service.cpp"])
    assert plan.mode == DeployMode.PUSH_SINGLE
    assert not plan.requires_reboot


def test_bp_changes_to_push_single():
    plan = decide(["vendor/lechao/services/lechao_lciod/hal/Android.bp"])
    assert plan.mode == DeployMode.PUSH_SINGLE


def test_usb_verify_changes_to_push_single():
    plan = decide(["others/usb-verify/src/main.c"])
    assert plan.mode == DeployMode.PUSH_SINGLE


def test_md_changes_to_skip():
    plan = decide(["docs/specs/test.md"])
    assert plan.mode == DeployMode.SKIP


def test_empty_list_skip():
    plan = decide([])
    assert plan.mode == DeployMode.SKIP


def test_mixed_changes_to_flash_full():
    plan = decide(["kernel/new/vendor/lechao/LcIod/lciod_usbd.c",
                   "vendor/lechao/services/lechao_lciod/hal/hal_service.cpp"])
    assert plan.mode == DeployMode.FLASH_FULL


def test_lcview_cpp_changes_to_push_single():
    plan = decide(["vendor/lechao/services/lechao_lcview/hal/LcView.cpp"])
    assert plan.mode == DeployMode.PUSH_SINGLE
    assert not plan.requires_reboot
    assert plan.deploy_targets[0].artifact_name == "lechao_lcview_hal"
    assert plan.deploy_targets[1].artifact_name == "lechao_lcview"


def test_lcview_rc_changes_to_dd_boot():
    plan = decide(["vendor/lechao/services/lechao_lcview/hal/lechao_lcview_hal.rc"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT
