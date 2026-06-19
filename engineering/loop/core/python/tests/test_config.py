"""loop_core/config.py 单元测试。"""
import json
from pathlib import Path

import pytest

from loop_core.config import BaseWorkflowConfig, DeviceProfile, merge_profiles


def test_device_profile_defaults():
    profile = DeviceProfile()
    assert profile.device_id == ""
    assert profile.transport == "serial"
    assert profile.prompt_markers == []
    assert profile.line_ending == "\n"


def test_base_workflow_config_defaults():
    cfg = BaseWorkflowConfig()
    assert cfg.observe_timeout_sec == 90
    assert cfg.capture_window_sec == 5
    assert cfg.recent_lines_limit == 400
    assert cfg.max_reassess_rounds == 1


def test_base_workflow_config_can_be_inherited():
    """业务层可以继承 BaseWorkflowConfig 添加自己的阈值。"""

    class ChildConfig(BaseWorkflowConfig):
        quiet_window_sec: int = 8
        custom_threshold: int = 100

    cfg = ChildConfig()
    # 父类字段
    assert cfg.observe_timeout_sec == 90
    assert cfg.recent_lines_limit == 400
    # 子类字段（通过 dataclass field 或注解定义）
    # 注意：这里只验证继承不报错，不验证注解语法


def test_merge_profiles_combines_device_and_workflow(tmp_path):
    device = tmp_path / "device.json"
    device.write_text(json.dumps({
        "device_id": "rp5",
        "prompt_markers": ["console:/ $"],
    }))
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({
        "observe_timeout_sec": 60,
        "reboot_loop_threshold": 3,
    }))

    merged = merge_profiles(str(device), str(workflow))
    assert merged["device_id"] == "rp5"
    assert merged["prompt_markers"] == ["console:/ $"]
    assert merged["observe_timeout_sec"] == 60
    assert merged["reboot_loop_threshold"] == 3


def test_merge_profiles_override_takes_priority(tmp_path):
    device = tmp_path / "device.json"
    device.write_text(json.dumps({"device_id": "rp5"}))
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({"observe_timeout_sec": 90}))

    merged = merge_profiles(
        str(device), str(workflow), override={"observe_timeout_sec": 30}
    )
    assert merged["observe_timeout_sec"] == 30


def test_merge_profiles_returns_dict_not_typed(tmp_path):
    """merge_profiles 返回原始 dict，不绑定具体类型。"""
    device = tmp_path / "device.json"
    device.write_text(json.dumps({"device_id": "rp5"}))
    workflow = tmp_path / "workflow.json"
    workflow.write_text(json.dumps({"threshold": 5}))

    merged = merge_profiles(str(device), str(workflow))
    assert isinstance(merged, dict)
    # 不应有任何 Config 类型绑定
    assert not isinstance(merged, DeviceProfile)
    assert not isinstance(merged, BaseWorkflowConfig)


def test_merge_profiles_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        merge_profiles(str(tmp_path / "nonexistent.json"), str(tmp_path / "wf.json"))
