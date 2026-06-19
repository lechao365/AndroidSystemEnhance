"""rp5-serial 交互式终端客户端。

职责：
1. 连接 Host（TCP socket）
2. 发送 ``writer.acquire`` 请求；writer 被占用时清晰报错退出
3. 成功后进入交互循环：后台线程打印 host 推送的串口输出，主线程读 stdin 逐行发送
4. Ctrl-C / EOF 退出时在 finally 中释放 writer、关闭连接
"""

import argparse
import os
import socket
import sys
import threading

from rp5_serial.shared.codec import decode_message, encode_message
from rp5_serial.shared.errors import OK, WRITER_BUSY


def _read_response(reader) -> dict | None:
    """从 socket file 对象读取一行并解码为响应 dict。

    连接被对端关闭（读到空）时返回 None。
    """
    line = reader.readline()
    if not line:
        return None
    return decode_message(line)


def acquire_writer(sock: socket.socket, reader, owner_id: str) -> dict | None:
    """发送 ``writer.acquire`` 请求并返回响应 dict。"""
    request = {
        "op": "writer.acquire",
        "data": {"owner_type": "human", "owner_id": owner_id},
    }
    sock.sendall(encode_message(request))
    return _read_response(reader)


def release_writer(sock: socket.socket) -> None:
    """尽力发送 ``writer.release`` 释放 writer，忽略所有错误（用于退出路径）。"""
    request = {"op": "writer.release", "data": {}}
    try:
        sock.sendall(encode_message(request))
    except OSError:
        pass


def send_line(sock: socket.socket, text: str) -> None:
    """发送 ``input.send_line`` 将一行输入转发到串口。"""
    request = {"op": "input.send_line", "data": {"text": text}}
    sock.sendall(encode_message(request))


def output_reader(reader, stop_event: threading.Event, out) -> None:
    """后台线程：持续读取 host 推送的串口输出并打印到 stdout。

    遇到 EOF（连接关闭）或 stop_event 置位时退出。
    """
    while not stop_event.is_set():
        line = reader.readline()
        if not line:
            break
        out.write(line.decode("utf-8", errors="replace"))
        out.flush()


def run_interactive(host: str, port: int, stdin=None, stdout=None) -> int:
    """运行交互式终端。

    成功退出返回 0；连接失败、writer 占用或其他错误返回 1。
    """
    if stdin is None:
        stdin = sys.stdin
    if stdout is None:
        stdout = sys.stdout

    owner_id = f"cli-{os.getpid()}"
    try:
        sock = socket.create_connection((host, port), timeout=3)
    except OSError as e:
        print(f"ERROR: 无法连接 host {host}:{port}: {e}", file=sys.stderr)
        return 1

    reader = sock.makefile("rb")
    stop_event = threading.Event()
    try:
        response = acquire_writer(sock, reader, owner_id)
        if response is None:
            print("ERROR: host 关闭连接", file=sys.stderr)
            return 1
        if response.get("code") == WRITER_BUSY:
            print("ERROR: writer 已被占用，无法进入交互模式", file=sys.stderr)
            return 1
        if response.get("code") != OK:
            print(
                f"ERROR: {response.get('message', 'unknown error')}",
                file=sys.stderr,
            )
            return 1

        # 后台线程负责实时打印 host 推送的串口输出
        reader_thread = threading.Thread(
            target=output_reader,
            args=(reader, stop_event, stdout),
            daemon=True,
        )
        reader_thread.start()

        # 主线程：逐行读取 stdin 并发送
        for line in stdin:
            text = line.rstrip("\n\r")
            send_line(sock, text)
    except KeyboardInterrupt:
        # Ctrl-C 视为正常退出
        pass
    except OSError as e:
        print(f"ERROR: 连接异常: {e}", file=sys.stderr)
        return 1
    finally:
        stop_event.set()
        release_writer(sock)
        try:
            reader.close()
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="rp5-serial 交互式终端（申请 writer 后读写串口）"
    )
    parser.add_argument("--host", default="127.0.0.1", help="host 监听地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9700, help="host 监听端口 (默认: 9700)")
    args = parser.parse_args()
    return run_interactive(args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
