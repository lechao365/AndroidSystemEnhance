#!/usr/bin/env python3
"""ws_serial — serial 链路客户端（WSL2 侧，经 TCP 访问 Windows serial_bridge）。

纯标准库 socket，不依赖 pyserial 与 fcntl（写互斥由转发器 ser_lock 承担）。
默认连 127.0.0.1:9700（mirrored 模式 localhost 直通；NAT 模式用 --host 宿主 IP）。

子命令:
  status                转发器连通性探测（connect + 读字节辨串口静默）
  exec <cmd>            下发命令 + echo __LE_EXIT_CODE__=$?（合成一行），收输出与退出码
  read [--timeout N]    一次性读串口当前输出（发命令前弃残留）
  ip                    经串口执行 ip -o -4 addr show wlan0 取设备 IPv4

错误分类（方向 4）:
  ENDPOINT_UNREACHABLE  TCP 连不上（connect 失败）
  DEVICE_UNRESPONSIVE   超时（timeout 内未捕获 marker / 输出无响应）
  SERIAL_SILENT         recv 返空（连接被关闭，转发器退出或串口关闭）
  NO_IPV4               链路通但设备侧无有效 IPv4（wlan0 未拿到地址，非串口静默）
"""
from __future__ import annotations

import argparse
import os
import re
import socket
import sys

from ws_adb_connect import parse_exec_output

# 与 ws_adb_connect._EXEC_TAG_RE 同款：exec 末尾 marker
_EXEC_TAG_RE = re.compile(r"__LE_EXIT_CODE__=(\d+)\s*$", re.MULTILINE)
# ip 输出提取 IPv4（过滤环回/链路本地/0.0.0.0）
_IPV4_RE = re.compile(r"inet (\d+\.\d+\.\d+\.\d+)/")
_BAD_IPS = ("127.", "169.254.", "0.0.0.0")


def _env(key: str, default: str) -> str:
    return os.environ.get(key, default)


def serial_endpoint():
    """转发器端点单一事实源（LC_SERIAL_HOST / LC_SERIAL_PORT 环境变量覆盖）。

    返回 (host, port)。LC_SERIAL_PORT 非数字时回退默认 9700（输入防御：
    否则 int() 在 argparse 构造前抛 ValueError 无捕获）。
    """
    host = _env("LC_SERIAL_HOST", "127.0.0.1")
    try:
        port = int(_env("LC_SERIAL_PORT", "9700"))
    except ValueError:
        port = 9700
    return host, port


class SerialError(Exception):
    """三分类错误：category ∈ ENDPOINT_UNREACHABLE / DEVICE_UNRESPONSIVE / SERIAL_SILENT。"""

    def __init__(self, category: str, message: str):
        super().__init__(f"{category}: {message}")
        self.category = category


class SerialConn:
    """TCP 到转发器的连接封装（纯标准库 socket）。"""

    def __init__(self, host: str = "127.0.0.1", port: int = 9700, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.sock: socket.socket | None = None

    def connect(self) -> None:
        try:
            self.sock = socket.create_connection((self.host, self.port),
                                                 timeout=self.timeout)
        except OSError as exc:
            raise SerialError("ENDPOINT_UNREACHABLE",
                              f"TCP 连不上 {self.host}:{self.port}: {exc}")

    def send_line(self, line: str) -> None:
        """下发一行，末尾带 CR（console 回车执行）。"""
        if not self.sock:
            raise SerialError("ENDPOINT_UNREACHABLE", "未连接")
        self.sock.sendall((line + "\r").encode("utf-8"))

    def drain(self) -> None:
        """发命令前弃残留：非阻塞清空接收缓冲。

        recv 返空 = 连接已被关闭（转发器退出）→ SERIAL_SILENT。
        """
        if not self.sock:
            raise SerialError("ENDPOINT_UNREACHABLE", "未连接")
        self.sock.settimeout(0)
        try:
            while True:
                try:
                    data = self.sock.recv(4096)
                except (BlockingIOError, socket.timeout):
                    break
                if not data:
                    raise SerialError("SERIAL_SILENT",
                                      "recv 返空（转发器已退出或串口关闭）")
        finally:
            self.sock.settimeout(self.timeout)

    def read_until_marker(self, marker_re: re.Pattern, timeout: float | None = None) -> str:
        """累积读直到命中 marker；recv 返空 → SERIAL_SILENT，超时 → DEVICE_UNRESPONSIVE。"""
        if not self.sock:
            raise SerialError("ENDPOINT_UNREACHABLE", "未连接")
        self.sock.settimeout(timeout or self.timeout)
        buf = b""
        while True:
            try:
                data = self.sock.recv(4096)
            except socket.timeout:
                raise SerialError(
                    "DEVICE_UNRESPONSIVE",
                    f"读取超时（{timeout or self.timeout}s 内未捕获 marker）")
            except OSError as exc:
                raise SerialError("SERIAL_SILENT", f"recv 异常: {exc}")
            if not data:
                raise SerialError("SERIAL_SILENT", "recv 返空（连接已断开）")
            buf += data
            if marker_re.search(buf.decode("utf-8", errors="replace")):
                return buf.decode("utf-8", errors="replace")
            if len(buf) > 8 * 1024 * 1024:
                raise SerialError("DEVICE_UNRESPONSIVE", "输出膨胀未捕获 marker")

    def close(self) -> None:
        if self.sock:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None


def _execute(conn: SerialConn, cmd: str, timeout: float) -> tuple[str, str | None]:
    """下发 cmd + echo marker（合成一行，照 ws_adb_connect.build_exec_cmd）。

    分两次 send_line 时第二行可能被首命令当 stdin 吞（如 cat/交互命令），
    合成 `cmd; echo __LE_EXIT_CODE__=$?` 保证 marker 恒在命令后执行。
    """
    conn.drain()
    conn.send_line(f"{cmd}; echo __LE_EXIT_CODE__=$?")
    text = conn.read_until_marker(_EXEC_TAG_RE, timeout)
    return parse_exec_output(text)


def _read_some(conn: SerialConn, timeout: float) -> str:
    """一次性读串口当前输出：弃残留后读 timeout 秒累积字节。"""
    conn.drain()
    conn.sock.settimeout(timeout)
    buf = b""
    while True:
        try:
            data = conn.sock.recv(4096)
        except socket.timeout:
            break
        except OSError as exc:
            raise SerialError("SERIAL_SILENT", f"recv 异常: {exc}")
        if not data:
            raise SerialError("SERIAL_SILENT", "recv 返空（连接已断开）")
        buf += data
    return buf.decode("utf-8", errors="replace")


def cmd_status(args: argparse.Namespace, conn: SerialConn) -> int:
    conn.connect()
    # 读一次字节：recv 返空 = 转发器已退出/串口关闭（SERIAL_SILENT，砖机三分法
    # 首分支可判定）；超时 = 连接在但当前无输出（空闲合法，附注报告）
    conn.sock.settimeout(1.0)
    try:
        data = conn.sock.recv(4096)
    except socket.timeout:
        print(f"REACHABLE {conn.host}:{conn.port}（1s 内无输出，串口空闲或静默）")
        return 0
    except OSError as exc:
        raise SerialError("SERIAL_SILENT", f"recv 异常: {exc}")
    if not data:
        raise SerialError("SERIAL_SILENT", "recv 返空（转发器已退出或串口关闭）")
    print(f"REACHABLE {conn.host}:{conn.port}（收到 {len(data)}B 数据流）")
    return 0


def cmd_exec(args: argparse.Namespace, conn: SerialConn) -> int:
    if not args.command:
        print("error: exec 需要命令", file=sys.stderr)
        return 3
    conn.connect()
    body, code = _execute(conn, args.command, args.timeout)
    if code is None:
        raise SerialError("DEVICE_UNRESPONSIVE", "未捕获 exit code")
    if body:
        sys.stdout.write(body + "\n" if not body.endswith("\n") else body)
    return 0 if str(code) == "0" else 1


def cmd_read(args: argparse.Namespace, conn: SerialConn) -> int:
    conn.connect()
    out = _read_some(conn, args.timeout)
    if out:
        sys.stdout.write(out)
    return 0


def cmd_ip(args: argparse.Namespace, conn: SerialConn) -> int:
    conn.connect()
    body, code = _execute(conn, "ip -o -4 addr show wlan0", args.timeout)
    if code is None:
        raise SerialError("DEVICE_UNRESPONSIVE", "未捕获 exit code")
    if str(code) != "0":
        raise SerialError("DEVICE_UNRESPONSIVE", f"ip 命令失败 exit_code={code}")
    for line in body.splitlines():
        m = _IPV4_RE.search(line)
        if m and not m.group(1).startswith(_BAD_IPS):
            print(m.group(1))
            return 0
    # exec 已返 0 证链路通（marker 捕获），无 IPv4 是设备侧无网，
    # 判 SERIAL_SILENT 会误导诊断（把"设备没网"当"串口静默"）
    raise SerialError("NO_IPV4",
                      "设备侧无有效 IPv4（wlan0 未拿到地址或链路不通，ip 命令 exit=0）")


def main(argv: list[str] | None = None) -> int:
    # 端点默认走 serial_endpoint 单一事实源（与 ws_adb_connect.rescue 共用）
    def_host, def_port = serial_endpoint()
    ap = argparse.ArgumentParser(description="serial 链路客户端（WSL2 侧）")
    ap.add_argument("--host", default=def_host,
                    help="转发器地址（NAT 模式传宿主 IP，mirrored 默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=def_port)
    ap.add_argument("--timeout", type=float, default=argparse.SUPPRESS,
                    help="超时秒（默认 LC_SERIAL_TIMEOUT 或 10；read 恒默认 1.0，"
                         "LC_SERIAL_TIMEOUT 不参与）")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_status = sub.add_parser("status", help="转发器连通性探测")
    p_status.set_defaults(func=cmd_status)

    p_exec = sub.add_parser("exec", help="下发命令收输出与退出码")
    p_exec.add_argument("command", nargs="?")
    p_exec.set_defaults(func=cmd_exec)

    p_read = sub.add_parser("read", help="一次性读串口当前输出")
    # 与父级 --timeout 同名：default 用 SUPPRESS，未显式传时保留父级值，
    # 否则子级默认 1.0 会静默覆盖父级显式传值（如 --timeout 5 read 变 1.0）
    p_read.add_argument("--timeout", type=float, default=argparse.SUPPRESS)
    p_read.set_defaults(func=cmd_read)

    p_ip = sub.add_parser("ip", help="取设备 IPv4（经串口执行 ip 命令）")
    p_ip.set_defaults(func=cmd_ip)

    args = ap.parse_args(argv)
    # timeout 兜底：显式 --timeout（父级或子级）优先；read 恒默认 1.0（裸跑
    # 不阻塞 10s，LC_SERIAL_TIMEOUT 不参与 read 否则复位失效），其余命令
    # 默认 10.0 且 LC_SERIAL_TIMEOUT 可覆盖
    if not hasattr(args, "timeout"):
        if args.cmd == "read":
            args.timeout = 1.0
        else:
            env_t = os.environ.get("LC_SERIAL_TIMEOUT")
            try:
                args.timeout = float(env_t) if env_t else 10.0
            except ValueError:
                args.timeout = 10.0
    conn = SerialConn(args.host, args.port, args.timeout)
    try:
        return args.func(args, conn)
    except SerialError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
