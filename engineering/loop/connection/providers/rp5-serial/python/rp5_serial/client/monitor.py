import argparse
import socket
import sys

from rp5_serial.shared.codec import encode_message


def subscribe(host: str, port: int) -> socket.socket | None:
    """连接 Host 并发送 stream.subscribe 请求。

    Host 不可达时返回 None。
    """
    try:
        sock = socket.create_connection((host, port), timeout=3)
    except OSError:
        return None
    request = {"op": "stream.subscribe", "data": {}}
    sock.sendall(encode_message(request))
    # 订阅为长连接，由调用方持续读取
    sock.settimeout(None)
    return sock


def run_monitor(host: str, port: int, out=sys.stdout, err=sys.stderr) -> int:
    """持续读取并打印 Host 推送的流数据。

    连接失败返回 1；正常结束（EOF / Ctrl-C）返回 0。
    """
    sock = subscribe(host, port)
    if sock is None:
        print(f"ERROR: host {host}:{port} 不可达", file=err)
        return 1
    try:
        raw = sock.makefile("rb")
        for line in iter(raw.readline, b""):
            out.write(line.decode("utf-8", errors="replace"))
            out.flush()
    except KeyboardInterrupt:
        # Ctrl-C 视为正常退出
        pass
    finally:
        sock.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="订阅 rp5-serial host 的串口流（只读，不申请 writer）")
    parser.add_argument("--host", default="127.0.0.1", help="host 监听地址 (默认: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=9700, help="host 监听端口 (默认: 9700)")
    args = parser.parse_args()

    return run_monitor(args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
