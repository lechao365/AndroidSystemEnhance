"""验证 v1 旧架构模块（policy/state）已被彻底移除。"""
import importlib

import pytest


def test_policy_module_not_importable():
    with pytest.raises(ImportError):
        importlib.import_module("loop_controller.policy")


def test_state_module_not_importable():
    with pytest.raises(ImportError):
        importlib.import_module("loop_controller.state")


def test_stages_importable_without_policy():
    import loop_controller.stages
    assert hasattr(loop_controller.stages, "run_verify_stage")


def test_controller_init_clean():
    import loop_controller
    assert not hasattr(loop_controller, "decide_termination")
    assert not hasattr(loop_controller, "new_session")
