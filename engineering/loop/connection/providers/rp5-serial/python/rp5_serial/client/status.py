import argparse
import json
import socket
import sys

from rp5_serial.shared.codec import decode_message, encode_message


def render_status(status: dict) -> str:
    """格式化 status 响应为可读字符串。"""
    return json.dumps(status, ensure_ascii=False, indent=2)


def fetch_status(host: str, port: int) -> dict | None:
    """连接 Host，发送 session.status 请求，返回解码响应。

    Host 不可达时返回 None。
    """
    try:
        sock = socket.create_connection((host, port), timeout=3)
    except OSError:
        return None
    try:
        request = {"op": "session.status", "data": {}}
        sock.sendall(encode_message(request))
        raw = sock.makefile("rb")
        line = raw.readline()
    finally:
        sock.close()
    if not line:
        return None
    return decode_message(line)


def main() -> int:
    parser = argparse.ArgumentParser(description="查询 rp5-serial host 状态")
    parser.add_argument("--host", default="127.0.0.1", help="host 监听地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9700, help="host 监听端口 (默认: 9700)")
    args = parser.parse_args()

    response = fetch_status(args.host, args.port)
    if response is None:
        print(f"ERROR: host {args.host}:{args.port} 不可达", file=sys.stderr)
        return 1
    print(render_status(response))
    return 0


if __name__ == "__main__":
    sys.exit(main())
