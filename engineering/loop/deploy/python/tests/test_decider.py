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


def test_mixed_kernel_cpp_upgrades_to_dd_boot():
    """kernel + cpp 混合 → 取最高风险（kernel 需 dd boot），不误升级 FLASH_FULL。

    回归 P1-3：原 type_count>=2 无条件 flash_full，但 kernel 完全可走 dd_boot，
    仅 sepolicy(.te) 才必须 flash_full。'能 push/dd 就不 flash'。
    """
    plan = decide(["kernel/new/vendor/lechao/LcIod/lciod_usbd.c",
                   "vendor/lechao/services/lechao_lciod/hal/hal_service.cpp"])
    assert plan.mode == DeployMode.DD_BOOT_REBOOT


def test_two_pushable_cpp_modules_not_upgraded_to_flash():
    """两个均可 push 的 cpp 模块（lciod + lcview）不应升级 FLASH_FULL。

    回归 P1-3：原 type_count>=2 无条件 flash_full，违反'能 push 不 dd'。
    修复后纯可 push 类型组合仍按 PUSH_SINGLE 处理。
    """
    plan = decide([
        "vendor/lechao/services/lechao_lciod/hal/hal_service.cpp",
        "vendor/lechao/services/lechao_lcview/hal/LcView.cpp",
    ])
    assert plan.mode != DeployMode.FLASH_FULL
    assert plan.mode == DeployMode.PUSH_SINGLE


def test_te_mixed_with_kernel_still_flash_full():
    """sepolicy(.te) + kernel 混合 → 仍 FLASH_FULL（te 必须全量刷机）。"""
    plan = decide([
        "device/brcm/rpi5/sepolicy/lechao_lciod.te",
        "kernel/drivers/foo.c",
    ])
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
