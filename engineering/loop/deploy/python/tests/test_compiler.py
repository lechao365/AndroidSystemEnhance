"""compiler 单元测试（skip/flash_full 逻辑）。"""
from loop_deploy.models import DeployPlan, DeployMode
from loop_deploy.compiler import compile_plan


def test_skip_returns_empty():
    plan = DeployPlan.skip("no changes")
    result = compile_plan(plan)
    assert result.success
    assert result.artifacts == []


def test_flash_full_returns_error():
    plan = DeployPlan.flash_full(["foo.te"])
    result = compile_plan(plan)
    assert not result.success
    assert "FLASH_FULL" in result.error
