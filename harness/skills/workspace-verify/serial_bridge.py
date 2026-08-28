#!/usr/bin/env python3
"""serial_bridge — Windows 侧 serial↔TCP 字节管道转发器。

把 USB-TTL 串口（Pi 5 Android console）桥接为 TCP 服务，供 WSL2 侧
ws_serial.py 连接（串口↔TCP 双向互转）。

用法:
  python serial_bridge.py [--port COM5] [--baudrate 115200]
                          [--listen 0.0.0.0] [--listen-port 9700]
依赖: pyserial（Windows 侧 pip install pyserial）
退出码: 0 正常退出 / 1 串口打不开或端口被占 / 3 缺 pyserial

部署说明（目录互访 ≠ 网络可达）:
  WSL2 能看到 Windows 目录（/mnt/c 互访）与能从 Windows 网络栈访问
  localhost 是两回事：
  - NAT 模式：WSL2 是独立 NAT 网络，无法用 localhost 访问 Windows 进程，
    必须用 Windows 宿主 IP（ipconfig 查以太网适配器 IPv4），且监听须
    绑 0.0.0.0（本脚本默认）而非 127.0.0.1
  - mirrored 模式（Windows 11 22H2+）：WSL2 与 Windows 共享网络栈，
    localhost 直通，Windows 侧监听 127.0.0.1 即可
"""
from __future__ import annotations

import argparse
import socket
import sys
import threading


def _log(msg: str) -> None:
    print(f"[bridge] {msg}", file=sys.stderr, flush=True)


def _serial_reader(ser, clients: list, clients_lock: threading.Lock,
                   shutdown: threading.Event) -> None:
    """串口读线程：读到字节广播给全部 TCP 客户端。

    任一客户端 sendall 失败（断开/超时）即从列表移除，不影响其余客户端。
    串口读取失败（设备拔出/驱动异常）时置 shutdown 事件——main 的 accept
    循环检测后关闭监听退出，否则 WSL 侧见连接仍在却收不到数据，
    会误判设备全砖（黑洞误判）。
    """
    while not shutdown.is_set():
        try:
            data = ser.read(4096)
        except Exception as exc:  # noqa: BLE001
            _log(f"串口读取失败，关闭监听退出: {exc}")
            shutdown.set()
            break
        if not data:
            continue
        with clients_lock:
            dead = []
            for c in clients:
                try:
                    c.sendall(data)
                except OSError:
                    dead.append(c)
            for c in dead:
                clients.remove(c)


def _client_handler(conn: socket.socket, ser, clients: list,
                    clients_lock: threading.Lock, ser_lock: threading.Lock) -> None:
    """TCP 客户端线程：收到的字节写入串口；断开时自移除。

    ser.write 由 ser_lock 串行化——多客户端并发写同一物理串口时保证单写者
    （与 WSL 侧 ws_serial.py 的 flock 文件锁语义对称）。
    """
    try:
        while True:
            data = conn.recv(4096)
            if not data:
                break
            with ser_lock:
                ser.write(data)
    except OSError as exc:
        _log(f"客户端收发异常: {exc}")
    finally:
        with clients_lock:
            if conn in clients:
                clients.remove(conn)
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="serial↔TCP 字节管道转发器")
    parser.add_argument("--port", default="COM5",
                        help="串口名（默认 COM5，Pi 5 Android console）")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--listen", default="0.0.0.0",
                        help="监听地址（默认 0.0.0.0，NAT 下供宿主 IP 访问；"
                             "mirrored 下可改 127.0.0.1）")
    parser.add_argument("--listen-port", type=int, default=9700)
    args = parser.parse_args(argv)

    try:
        import serial
    except ImportError:
        _log("缺少 pyserial，请先执行: pip install pyserial")
        return 3

    try:
        ser = serial.Serial(args.port, args.baudrate, timeout=0.1)
    except Exception as exc:  # noqa: BLE001
        _log(f"串口打开失败 {args.port}: {exc}")
        return 1

    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        server.bind((args.listen, args.listen_port))
    except OSError as exc:
        _log(f"监听 {args.listen}:{args.listen_port} 失败（端口被占？）: {exc}")
        ser.close()
        return 1
    server.listen(5)
    _log(f"串口 {args.port}@{args.baudrate} → TCP {args.listen}:{args.listen_port}")

    clients: list[socket.socket] = []
    clients_lock = threading.Lock()
    # 串口写锁：多客户端并发写物理串口时串行化（与 WSL 侧 flock 对称）
    ser_lock = threading.Lock()
    # 串口读失败置位 → 关闭监听退出（防黑洞误判，见 _serial_reader）
    shutdown = threading.Event()
    threading.Thread(target=_serial_reader,
                     args=(ser, clients, clients_lock, shutdown),
                     daemon=True).start()
    # 轮询 shutdown 用的短超时，避免 accept 永久阻塞
    server.settimeout(0.5)

    try:
        while not shutdown.is_set():
            try:
                conn, addr = server.accept()
            except socket.timeout:
                continue
            _log(f"客户端接入: {addr}")
            with clients_lock:
                clients.append(conn)
            threading.Thread(target=_client_handler,
                             args=(conn, ser, clients, clients_lock, ser_lock),
                             daemon=True).start()
    except KeyboardInterrupt:
        _log("收到 Ctrl-C，退出")
    finally:
        # 关闭监听并断开全部客户端：WSL 侧 recv 立即返空（SERIAL_SILENT），
        # 不再出现已连接无数据的黑洞
        server.close()
        with clients_lock:
            for c in clients:
                try:
                    c.close()
                except OSError:
                    pass
            clients.clear()
        ser.close()
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    sys.exit(main())
