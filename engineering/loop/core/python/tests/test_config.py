"""loop_core/config.py 单元测试。"""
from loop_core.config import DeviceProfile


def test_device_profile_defaults():
    profile = DeviceProfile()
    assert profile.device_id == ""
    assert profile.transport == "serial"
    assert profile.prompt_markers == []
    assert profile.line_ending == "\n"


def test_device_profile_execution_defaults():
    """DeviceProfile 包含默认执行参数。"""
    profile = DeviceProfile()
    assert profile.default_capture_timeout == 5.0
    assert profile.default_recent_limit == 400


def test_device_profile_accepts_extra_fields_filtered_by_caller():
    """调用方（如 CLI）应过滤未知字段后再构造 DeviceProfile。"""
    profile = DeviceProfile(device_id="rp5", default_capture_timeout=8.0)
    assert profile.device_id == "rp5"
    assert profile.default_capture_timeout == 8.0


def test_device_profile_supports_boot_and_panic_markers():
    """boot_markers / panic_markers 字段可独立配置，默认空 list。"""
    from loop_core.config import DeviceProfile

    profile = DeviceProfile(
        device_id="rp5",
        boot_markers=["Booting Linux on physical CPU", "init: ... started service 'zygote' has pid"],
        panic_markers=["Kernel panic", "BUG:", "Internal error"],
    )
    assert profile.boot_markers == [
        "Booting Linux on physical CPU",
        "init: ... started service 'zygote' has pid",
    ]
    assert profile.panic_markers == ["Kernel panic", "BUG:", "Internal error"]


def test_device_profile_defaults_empty_markers():
    """未配置时 boot_markers / panic_markers 默认空 list。"""
    from loop_core.config import DeviceProfile

    profile = DeviceProfile(device_id="x")
    assert profile.boot_markers == []
    assert profile.panic_markers == []
