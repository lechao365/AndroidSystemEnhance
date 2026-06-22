"""deployer 单元测试（skip/flash_full 逻辑）。"""
from loop_deploy.models import DeployPlan, DeployMode
from loop_deploy.deployer import Deployer


def test_skip_returns_success():
    import loop_adb.client as mod
    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555")
    d = Deployer(client)
    plan = DeployPlan.skip("no changes")
    result = d.deploy(plan, [])
    assert result.success
    assert result.mode == DeployMode.SKIP


def test_flash_full_returns_error():
    import loop_adb.client as mod
    client = mod.AdbClient("1.2.3.4:5555", "1.2.3.4:5555")
    d = Deployer(client)
    plan = DeployPlan.flash_full(["foo.te"])
    result = d.deploy(plan, [])
    assert not result.success
    assert "FLASH_FULL" in result.error
