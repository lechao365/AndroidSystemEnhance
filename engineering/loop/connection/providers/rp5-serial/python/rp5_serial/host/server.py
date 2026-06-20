"""rp5-serial Windows Host 入口。

职责：
1. 初始化 RuntimeState、Logger、串口
2. 启动串口读线程（持续调 ``state.read_lines()``，新行广播给订阅者）
3. 启动 TCP accept 循环（每个 client spawn 一个 handler 线程）
4. Ctrl-C 优雅退出

协议请求详见 ``engineering/loop/connection/protocol/rp5_serial_protocol.md``。
"""

from __future__ import annotations

import argparse
import logging
import socket
import threading
import time

from rp5_serial.host.handler import ClientHandler, StreamBroker
from rp5_serial.host.logging_utils import build_logger
from rp5_serial.host.serial_runtime import RuntimeState

# 统一路径工具（从 engineering/harness/lib/python 加载）
try:
    from harness_path_util import ensure_dir
except ImportError:
    # PYTHONPATH 未包含 lib/python 时的友好报错
    raise ImportError(
        "缺少 harness_path_util，请将 engineering/harness/lib/python 加入 PYTHONPATH"
    )

# 串口读循环空闲时的休眠间隔（秒）
_SERIAL_READ_INTERVAL = 0.01


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="rp5 serial host")
    parser.add_argument("--config", required=False, help="host config path")
    parser.add_argument(
        "--listen-host", default="0.0.0.0", help="TCP 监听地址 (默认: 0.0.0.0)"
    )
    parser.add_argument(
        "--listen-port", type=int, default=9700, help="TCP 监听端口 (默认: 9700)"
    )
    parser.add_argument(
        "--port",
        required=False,
        help="serial device path, e.g. COM3 or /dev/ttyUSB0",
    )
    parser.add_argument(
        "--baudrate", type=int, default=115200, help="serial baudrate (默认: 115200)"
    )
    parser.add_argument(
        "--log-dir",
        default=str(ensure_dir("HOST_LOG_DIR")),
        help="Host 轻量日志目录",
    )
    return parser.parse_args()


def serial_read_loop(
    state: RuntimeState,
    broker: StreamBroker,
    stop_event: threading.Event,
    logger: logging.Logger,
) -> None:
    """串口读线程：持续读取串口并把新行广播给所有订阅者。

    无串口或无订阅者时进入低频轮询，避免空转。
    """
    while not stop_event.is_set():
        try:
            new_lines = state.read_lines()
        except Exception as e:  # 防御性兜底，读线程不能挂
            logger.warning("串口读异常: %s", e)
            new_lines = []
        for line in new_lines:
            broker.publish(line)
        # 无数据或无串口时空转休眠
        if not new_lines:
            stop_event.wait(_SERIAL_READ_INTERVAL)


def serve(
    state: RuntimeState,
    broker: StreamBroker,
    listen_host: str,
    listen_port: int,
    stop_event: threading.Event,
    logger: logging.Logger,
) -> None:
    """TCP accept 循环：为每个 client spawn handler 线程。"""
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server_sock.bind((listen_host, listen_port))
    server_sock.listen(8)
    server_sock.settimeout(0.5)
    logger.info("TCP 监听 %s:%s", listen_host, listen_port)
    try:
        while not stop_event.is_set():
            try:
                conn, addr = server_sock.accept()
            except socket.timeout:
                continue
            except OSError as e:
                if stop_event.is_set():
                    break
                logger.warning("accept 异常: %s", e)
                continue
            logger.info("client 连接 %s:%s", addr[0], addr[1])
            handler = ClientHandler(conn, addr, state, broker, stop_event)
            t = threading.Thread(
                target=handler.run,
                name=f"client-{addr[0]}:{addr[1]}",
                daemon=True,
            )
            t.start()
    finally:
        try:
            server_sock.close()
        except OSError:
            pass
        logger.info("TCP server 已关闭")


def main() -> int:
    args = parse_args()
    logger = build_logger("rp5_serial_host", args.log_dir)
    logger.info(
        "host starting config=%s port=%s baudrate=%s listen=%s:%s",
        args.config,
        args.port,
        args.baudrate,
        args.listen_host,
        args.listen_port,
    )

    state = RuntimeState(
        device_id="rp5",
        serial_port=args.port,
        baudrate=args.baudrate,
    )
    if args.port:
        if state.open_serial():
            logger.info("serial opened port=%s", args.port)
        else:
            logger.warning("serial open failed port=%s", args.port)
    logger.info("runtime ready status=%s", state.status().to_dict())
    # 显式打印串口状态，便于启动后快速确认
    print(f"[Serial] port={args.port or '(none)'} state={state.status().serial_state}")

    broker = StreamBroker()
    stop_event = threading.Event()

    # 启动串口读线程
    reader_thread = threading.Thread(
        target=serial_read_loop,
        args=(state, broker, stop_event, logger),
        name="serial-read",
        daemon=True,
    )
    reader_thread.start()

    # 启动 TCP server（主线程）
    try:
        serve(state, broker, args.listen_host, args.listen_port, stop_event, logger)
    except KeyboardInterrupt:
        logger.info("收到 Ctrl-C，开始退出")
    finally:
        stop_event.set()
        state.close_serial()
        logger.info("host stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
