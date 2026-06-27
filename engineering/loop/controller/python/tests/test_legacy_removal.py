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


def test_control_cli_not_importable():
    with pytest.raises(ImportError):
        importlib.import_module("loop_controller.control_cli")


def test_loop_core_cli_no_control_subcommand():
    """loop_core.cli 不应再挂载 control 子命令。"""
    import argparse

    import loop_core.cli as cli_mod

    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command")
    # 重新调用 main 的 parser 构造逻辑不可行（main 内部封装），
    # 这里通过源码静态检查：cli.py 不应 import control_cli / add_control_parser
    src = open(cli_mod.__file__, encoding="utf-8").read()
    assert "from loop_controller.control_cli import add_control_parser" not in src
    assert "add_control_parser(sub)" not in src


def test_controller_init_no_control_reexport():
    import loop_controller
    assert not hasattr(loop_controller, "add_control_parser")
