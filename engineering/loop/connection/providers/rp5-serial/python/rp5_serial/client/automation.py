"""rp5-serial 自动化客户端（供 workflow / 调试循环编程调用）。

职责：
1. 提供稳定的可编程接口 ``AutomationClient``，封装连接、writer 申请/释放、
   发送输入、读取输出、抓取最近缓冲等操作
2. 支持上下文管理器（``with AutomationClient(...) as client:``），保证退出时释放资源
3. 附带最小 CLI 入口（``--help`` / ``--send`` / ``--capture``），便于人工验证

与 ``interactive.py``（人机交互）不同，本模块面向自动化场景：
- ``acquire_writer`` 在 writer 被占用时直接返回 False，不阻塞、不排队
- ``read_until_timeout`` 在固定时长内持续采集 host 推送的串口输出
- ``capture_recent_lines`` 通过 ``stream.read_recent`` 拉取 host 侧环形缓冲
"""

import argparse
import os
import socket
import sys
import time

from rp5_serial.shared.codec import decode_message, encode_message
from rp5_serial.shared.errors import OK


class AutomationClient:
    """面向 workflow 的 rp5-serial 自动化客户端。

    典型用法::

        with AutomationClient("127.0.0.1", 9700) as client:
            if not client.acquire_writer():
                raise RuntimeError("writer busy")
            client.send_line("uname -a")
            lines = client.read_until_timeout(2.0)
    """

    def __init__(self, host: str = "127.0.0.1", port: int = 9700, owner_id: str | None = None):
        """初始化客户端配置，不建立连接。

        Args:
            host: Host 监听地址，默认 ``127.0.0.1``
            port: Host 监听端口，默认 ``9700``
            owner_id: writer owner 标识；为空时自动生成 ``auto-<pid>``
        """
        self.host = host
        self.port = port
        self.owner_id = owner_id or f"auto-{os.getpid()}"
        self._cmd_sock: socket.socket | None = None
        self._cmd_raw = None  # command socket.makefile("rb") 产生的只读文件对象
        self._stream_sock: socket.socket | None = None
        self._stream_raw = None  # stream socket.makefile("rb") 产生的只读文件对象

    def connect(self) -> None:
        """建立到 Host 的 TCP 连接。

        Raises:
            OSError: 连接失败（Host 不可达 / 拒绝连接等）
        """
        self.release()
        self._cmd_sock = socket.create_connection((self.host, self.port), timeout=5)
        self._cmd_raw = self._cmd_sock.makefile("rb")
        try:
            self._stream_sock = socket.create_connection((self.host, self.port), timeout=5)
            self._stream_raw = self._stream_sock.makefile("rb")
            self._stream_sock.sendall(encode_message({"op": "stream.subscribe", "data": {}}))
            response = self._read_response(self._stream_raw)
            if response is None:
                raise OSError("host 关闭连接")
            if response.get("code") != OK:
                raise OSError(response.get("message") or "stream.subscribe failed")
        except Exception:
            self.release()
            raise

    def acquire_writer(self) -> bool:
        """申请 writer（owner_type=workflow）。

        成功返回 True；writer 被占用（WRITER_BUSY）或其他失败返回 False。
        不会阻塞排队，busy 即立即返回 False。

        Raises:
            OSError: 连接异常或被对端关闭
        """
        request = {
            "op": "writer.acquire",
            "data": {"owner_type": "workflow", "owner_id": self.owner_id},
        }
        self._cmd_sock.sendall(encode_message(request))
        response = self._read_response(self._cmd_raw)
        if response is None:
            raise OSError("host 关闭连接")
        return response.get("code") == OK

    def send_line(self, text: str) -> None:
        """向串口发送一行输入（input.send_line）。

        Args:
            text: 待发送文本（无需包含换行符，由 Host 负责转发）

        Raises:
            OSError: 连接异常
        """
        request = {"op": "input.send_line", "data": {"text": text}}
        self._cmd_sock.sendall(encode_message(request))
        response = self._read_response(self._cmd_raw)
        if response is None:
            raise OSError("host 关闭连接")
        if response.get("code") != OK:
            raise OSError(response.get("message") or "input.send_line failed")

    def read_until_timeout(self, timeout_sec: float) -> list[str]:
        """在 ``timeout_sec`` 秒内持续读取 Host 推送的串口输出。

        采用 0.2s 轮询超时，循环直到 deadline。读到 EOF（连接关闭）时立即结束。
        读取结束后将 socket 恢复为阻塞模式。

        Args:
            timeout_sec: 采样总时长（秒）

        Returns:
            采集到的行列表（仅包含 ``stream.data`` 中的文本）
        """
        if timeout_sec <= 0:
            return []

        lines: list[str] = []
        deadline = time.monotonic() + timeout_sec
        self._stream_sock.settimeout(0.2)
        try:
            while time.monotonic() < deadline:
                try:
                    payload = self._read_response(self._stream_raw)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if payload is None:
                    break
                if payload.get("op") != "stream.data":
                    continue
                text = payload.get("data", {}).get("text")
                if isinstance(text, str):
                    lines.append(text)
        finally:
            try:
                self._stream_sock.settimeout(None)
            except OSError:
                pass
        return lines

    def capture_recent_lines(self, limit: int) -> list[str]:
        """请求 Host 返回最近 N 行缓冲（stream.read_recent）。

        Args:
            limit: 期望抓取的行数

        Returns:
            Host 返回的 ``list[str]``；当响应结构异常（如 ``data.lines`` 不是 ``list[str]``）时返回空列表

        Raises:
            OSError: 连接异常、被对端关闭或 host 返回失败响应
        """
        request = {"op": "stream.read_recent", "data": {"limit": limit}}
        self._cmd_sock.sendall(encode_message(request))
        response = self._read_response(self._cmd_raw)
        if response is None:
            raise OSError("host 关闭连接")
        if response.get("code") != OK:
            raise OSError(response.get("message") or "stream.read_recent failed")
        lines = response.get("data", {}).get("lines", [])
        if not isinstance(lines, list):
            return []
        if not all(isinstance(line, str) for line in lines):
            return []
        return lines

    def release(self) -> None:
        """释放 writer 并关闭连接。

        尽力发送 ``writer.release``，忽略所有 OSError；保证 socket 被关闭、
        内部状态被重置，可安全重复调用。
        """
        cmd_sock = self._cmd_sock
        if cmd_sock is not None:
            try:
                request = {"op": "writer.release", "data": {}}
                cmd_sock.sendall(encode_message(request))
            except OSError:
                pass
        self._close_stream_channel()
        self._close_command_channel()

    def _close_command_channel(self) -> None:
        raw = self._cmd_raw
        sock = self._cmd_sock
        self._cmd_raw = None
        self._cmd_sock = None
        if raw is not None:
            try:
                raw.close()
            except OSError:
                pass
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _close_stream_channel(self) -> None:
        raw = self._stream_raw
        sock = self._stream_sock
        self._stream_raw = None
        self._stream_sock = None
        if raw is not None:
            try:
                raw.close()
            except OSError:
                pass
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _read_response(self, raw) -> dict | None:
        """从指定 stream 读取一行并解码为响应 dict。

        对端关闭连接（读到空）时返回 None。
        """
        line = raw.readline()
        if not line:
            return None
        return decode_message(line)

    def __enter__(self) -> "AutomationClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        self.release()
        return False


def main() -> int:
    """最小 CLI 入口，主要用于验证连通性。

    支持 ``--send`` 发送单行、``--capture`` 抓取最近 N 行；两者可组合，
    也可以都不提供（仅验证 writer 申请是否成功）。
    """
    parser = argparse.ArgumentParser(
        description="rp5-serial automation client（workflow 编程接口的 CLI 验证入口）"
    )
    parser.add_argument("--host", default="127.0.0.1", help="host 监听地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9700, help="host 监听端口 (默认: 9700)")
    parser.add_argument("--send", help="发送一行后退出")
    parser.add_argument("--capture", type=int, help="抓取最近 N 行后退出")
    args = parser.parse_args()

    client = AutomationClient(args.host, args.port)
    try:
        client.connect()
    except OSError as e:
        print(f"ERROR: 无法连接 host {args.host}:{args.port}: {e}", file=sys.stderr)
        return 1

    try:
        if not client.acquire_writer():
            print("ERROR: writer 已被占用", file=sys.stderr)
            return 1

        if args.send:
            client.send_line(args.send)
        if args.capture:
            lines = client.capture_recent_lines(args.capture)
            for line in lines:
                print(line)
    except OSError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    finally:
        client.release()
    return 0


if __name__ == "__main__":
    sys.exit(main())
