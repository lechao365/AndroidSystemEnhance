"""live transport provider loader。"""
from __future__ import annotations

from loop_core.config import DeviceProfile


def build_live_transport(profile: DeviceProfile, args):
    """根据 profile.transport 选择 live provider 并返回 transport 实例。"""
    if profile.transport == "serial":
        from rp5_serial.client.automation import AutomationClient
        from rp5_serial.transport import Rp5SerialTransport

        client = AutomationClient(args.host, args.port)
        client.connect()
        return Rp5SerialTransport(client)

    if profile.transport == "adb":
        if not args.adb_endpoint:
            raise ValueError("adb endpoint is required for transport=adb")
        from loop_adb.transport import AdbTransport

        return AdbTransport(
            endpoint=args.adb_endpoint,
            device_serial=args.adb_serial or args.adb_endpoint,
            root_mode=args.adb_root_mode,
            connect_timeout_sec=args.adb_connect_timeout,
            command_timeout_sec=args.adb_command_timeout,
        )

    raise ValueError(f"unsupported transport: {profile.transport}")
