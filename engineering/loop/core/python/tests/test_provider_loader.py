"""provider_loader 单元测试：仅覆盖 ValueError 路径，不依赖真实 provider。"""
import types

import pytest

from loop_core.config import DeviceProfile
from loop_core.provider_loader import build_live_transport


class _Args(types.SimpleNamespace):
    host = "127.0.0.1"
    port = 9700
    adb_endpoint = "192.168.1.55:5555"
    adb_serial = "192.168.1.55:5555"
    adb_root_mode = "auto"
    adb_connect_timeout = 15.0
    adb_command_timeout = 10.0


def test_build_live_transport_rejects_unknown_transport():
    profile = DeviceProfile(device_id="rp5", transport="bluetooth")
    with pytest.raises(ValueError, match="unsupported transport"):
        build_live_transport(profile, _Args())


def test_build_live_transport_requires_adb_endpoint():
    profile = DeviceProfile(device_id="rp5", transport="adb")
    args = _Args(adb_endpoint="")
    with pytest.raises(ValueError, match="adb endpoint is required"):
        build_live_transport(profile, args)
