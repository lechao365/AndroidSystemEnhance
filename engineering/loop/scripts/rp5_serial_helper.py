#!/usr/bin/env python3
"""rp5-serial 辅助工具：通过 serial daemon 获取设备侧信息。

供 Loop Engineering host case 调用，解决"host 需要动态发现设备 IP"等场景。

用法：
    python3 rp5_serial_helper.py device-ip [--host 127.0.0.1] [--port 9700]
        输出设备 wlan0 的 IPv4 地址，找不到时 exit 1

    python3 rp5_serial_helper.py prop <name> [--host 127.0.0.1] [--port 9700]
        输出指定 getprop 值

    python3 rp5_serial_helper.py shell <cmd> [--host 127.0.0.1] [--port 9700] [--timeout 5]
        在设备上执行命令，输出采集到的所有文本

依赖 PYTHONPATH 含 rp5_serial 包目录（由调用方或 wrapper 设置）。
"""

from __future__ import annotations

import argparse
import re
import sys


def _send_and_read(client, command: str, read_time: float = 3.0) -> list[str]:
    """发命令并通过 stream 通道读回输出行。"""
    if not client.acquire_writer():
        raise OSError("writer busy")
    client.send_line(command)
    return client.read_until_timeout(read_time)


def get_device_ip(host: str, port: int) -> int:
    """获取设备 wlan0 IPv4 地址，输出到 stdout，失败 exit 1。

    优先 acquire writer 主动发命令；若 writer 被 le run 框架占用，
    降级到 read-only 模式：从 host 环形缓冲里捞最近的 ip addr 输出。
    """
    from rp5_serial.client.automation import AutomationClient

    with AutomationClient(host, port) as c:
        # 先尝试主动查询（writer 空闲时）
        if c.acquire_writer():
            c.send_line("ip -4 addr show wlan0")
            lines = c.read_until_timeout(3.0)
        else:
            # writer 被占用（le run 期间）：从 host buffer 捞最近的 ip 输出
            lines = c.capture_recent_lines(400)

    # 反向扫描，找最后一条匹配的 inet 行
    for line in reversed(lines):
        m = re.search(r"inet (192\.168\.1\.[0-9]+)", line)
        if m:
            print(m.group(1))
            return 0
    print("NO_IP_FOUND", file=sys.stderr)
    return 1


def get_prop(host: str, port: int, name: str) -> int:
    """输出指定属性值。"""
    from rp5_serial.client.automation import AutomationClient

    with AutomationClient(host, port) as c:
        lines = _send_and_read(c, f"getprop {name}", read_time=2.0)
    for line in lines:
        # 过滤命令回显和空行
        if line.strip() and "getprop" not in line:
            print(line.strip())
            return 0
    return 1


def run_shell(host: str, port: int, command: str, timeout: float) -> int:
    """在设备上执行命令并输出所有采集到的文本。

    安全契约：command 原样透传给设备端 shell，本函数不做任何转义/校验。
    调用方负责确保 command 不包含恶意输入（如未转义的用户可控数据、
    命令注入字符等）。仅在受信上下文中使用。
    """
    from rp5_serial.client.automation import AutomationClient

    with AutomationClient(host, port) as c:
        lines = _send_and_read(c, command, read_time=timeout)
    for line in lines:
        print(line)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="rp5-serial 辅助工具")
    parser.add_argument("action", choices=["device-ip", "prop", "shell"], help="操作类型")
    parser.add_argument("--host", default="127.0.0.1", help="serial daemon 监听地址")
    parser.add_argument("--port", type=int, default=9700, help="serial daemon 监听端口")
    parser.add_argument("--name", help="prop action 的属性名")
    parser.add_argument("--command", help="shell action 的命令")
    parser.add_argument("--timeout", type=float, default=5.0, help="shell action 的采集超时")
    args = parser.parse_args()

    if args.action == "device-ip":
        return get_device_ip(args.host, args.port)
    if args.action == "prop":
        if not args.name:
            print("ERROR: --name required for prop action", file=sys.stderr)
            return 2
        return get_prop(args.host, args.port, args.name)
    if args.action == "shell":
        if not args.command:
            print("ERROR: --command required for shell action", file=sys.stderr)
            return 2
        return run_shell(args.host, args.port, args.command, args.timeout)
    return 2


if __name__ == "__main__":
    sys.exit(main())
