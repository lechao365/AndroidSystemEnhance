"""rp5-serial Host 侧 TCP 协议处理。

职责：
1. ``StreamBroker``：订阅者队列管理。串口读线程读到新行后调 ``publish`` 广播给所有订阅者；
   handler 线程通过 ``get_queue`` 取出推送给 client。
2. ``ClientHandler``：每个 client 连接一个实例，循环读取 JSON Lines 请求并分发到对应处理函数。
   对 ``stream.subscribe`` 的 client，进入"推送模式"，同时处理新请求和推送队列。

协议请求详见 ``engineering/loop/connection/protocol/rp5_serial_protocol.md``。
"""

from __future__ import annotations

import logging
import queue
import socket
import threading
from typing import Any

from rp5_serial.host.serial_runtime import RuntimeState
from rp5_serial.shared.codec import decode_message, encode_message, make_error, make_ok
from rp5_serial.shared.errors import (
    INVALID_MODE,
    INVALID_REQUEST,
    OK,
    SERIAL_NOT_AVAILABLE,
    SESSION_NOT_FOUND,
    WRITER_BUSY,
)

_logger = logging.getLogger("rp5_serial_host")

# 推送模式轮询 broker 队列的超时（秒），用短超时以便及时响应 stop_event / 新请求
_POLL_TIMEOUT = 0.5

# 合法 session 模式
_VALID_MODES = ("monitor", "interactive", "automation")


class StreamBroker:
    """线程安全的订阅者队列管理。

    每个订阅者持有一个 ``queue.Queue``，串口读线程通过 ``publish`` 把新行推入所有队列，
    handler 线程通过 ``get_queue`` 取出推送给对应 client。
    """

    def __init__(self) -> None:
        self._subscribers: dict[int, queue.Queue[str]] = {}
        self._lock = threading.Lock()
        self._next_id = 0

    def subscribe(self) -> int:
        """注册一个新订阅者，返回其订阅 ID。"""
        with self._lock:
            sub_id = self._next_id
            self._next_id += 1
            self._subscribers[sub_id] = queue.Queue()
            return sub_id

    def unsubscribe(self, sub_id: int) -> None:
        """注销订阅者，可重复调用。"""
        with self._lock:
            self._subscribers.pop(sub_id, None)

    def publish(self, text: str) -> None:
        """把一行新输出广播给所有订阅者。"""
        with self._lock:
            subs = list(self._subscribers.values())
        for q in subs:
            q.put(text)

    def get_queue(self, sub_id: int) -> queue.Queue[str] | None:
        """返回指定订阅者的队列；不存在返回 None。"""
        with self._lock:
            return self._subscribers.get(sub_id)


class ClientHandler:
    """单个 client 连接的协议处理。

    生命周期由 server.py 在独立线程中调用 ``run`` 驱动。连接断开时 ``run`` 返回。
    """

    def __init__(
        self,
        conn: socket.socket,
        addr: tuple[str, int],
        state: RuntimeState,
        broker: StreamBroker,
        stop_event: threading.Event,
    ) -> None:
        self._conn = conn
        self._addr = addr
        self._state = state
        self._broker = broker
        self._stop = stop_event
        # 订阅状态
        self._sub_id: int | None = None
        # 推送模式标志：置位后主循环同时消费 broker 队列
        self._streaming = False

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------

    def run(self) -> None:
        """处理循环：读请求 -> 分发 -> 响应。

        非推送模式：阻塞读一行请求并处理。
        推送模式：select 同时监听请求与 broker 队列，优先推送新行。
        """
        conn = self._conn
        try:
            reader = conn.makefile("rb")
            while not self._stop.is_set():
                if self._streaming:
                    if not self._pump_stream(reader):
                        break
                else:
                    line = reader.readline()
                    if not line:
                        break
                    if not self._dispatch_line(line):
                        continue
        except (OSError, ValueError) as e:
            _logger.warning("client %s 连接异常: %s", self._addr, e)
        finally:
            self._cleanup()
            try:
                self._conn.close()
            except OSError:
                pass
            _logger.info("client %s 断开", self._addr)

    # ------------------------------------------------------------------
    # 分发
    # ------------------------------------------------------------------

    def _dispatch_line(self, line: bytes) -> bool:
        """解析并分发一行请求。返回 True 表示请求已处理（不一定是成功响应）。"""
        try:
            request = decode_message(line)
        except Exception:
            self._send(make_error(INVALID_REQUEST, "payload is not valid JSON"))
            return True

        if not isinstance(request, dict):
            self._send(make_error(INVALID_REQUEST, "request must be a JSON object"))
            return True

        op = request.get("op")
        data = request.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        handler = self._OP_TABLE.get(op) if isinstance(op, str) else None
        if handler is None:
            self._send(make_error(INVALID_REQUEST, f"unknown op: {op!r}"))
            return True
        try:
            handler(self, data)
        except Exception as e:  # 防御性兜底，保证单请求异常不杀线程
            _logger.exception("op %s 处理异常", op)
            self._send(make_error("INTERNAL_ERROR", f"handler error: {e}"))
        return True

    # ------------------------------------------------------------------
    # 操作实现
    # ------------------------------------------------------------------

    def _op_session_status(self, data: dict[str, Any]) -> None:
        self._send(make_ok(self._state.status().to_dict()))

    def _op_session_open(self, data: dict[str, Any]) -> None:
        mode = data.get("mode")
        owner_id = data.get("owner_id") or "anonymous"
        if mode not in _VALID_MODES:
            self._send(make_error(INVALID_MODE, f"mode must be one of {_VALID_MODES}"))
            return
        session = self._state.open_session(mode=mode, owner_id=owner_id)
        self._send(make_ok(session.to_dict()))

    def _op_session_close(self, data: dict[str, Any]) -> None:
        self._state.close_session()
        self._send(make_ok())

    def _op_writer_acquire(self, data: dict[str, Any]) -> None:
        owner_type = data.get("owner_type")
        owner_id = data.get("owner_id")
        if owner_type not in ("human", "workflow") or not owner_id:
            self._send(
                make_error(
                    INVALID_REQUEST,
                    "owner_type must be human|workflow and owner_id required",
                )
            )
            return
        lease = self._state.acquire_writer(owner_type=owner_type, owner_id=owner_id)
        if lease is None:
            self._send(make_error(WRITER_BUSY, "writer is held by another client"))
            return
        self._send(make_ok(lease.to_dict()))

    def _op_writer_release(self, data: dict[str, Any]) -> None:
        self._state.release_writer()
        self._send(make_ok())

    def _op_input_send_line(self, data: dict[str, Any]) -> None:
        if self._state.active_writer is None:
            self._send(make_error(SESSION_NOT_FOUND, "no active writer; acquire first"))
            return
        text = data.get("text")
        if text is None or not isinstance(text, str):
            self._send(make_error(INVALID_REQUEST, "text required"))
            return
        try:
            self._state.send_line(text)
        except RuntimeError as e:
            msg = str(e)
            code = SERIAL_NOT_AVAILABLE if "serial port not open" in msg else INVALID_REQUEST
            self._send(make_error(code, msg))
            return
        self._send(make_ok())

    def _op_stream_subscribe(self, data: dict[str, Any]) -> None:
        self._sub_id = self._broker.subscribe()
        self._state.inc_subscriber()
        self._streaming = True
        self._send(make_ok())
        _logger.info("client %s 订阅流 sub_id=%s", self._addr, self._sub_id)

    def _op_stream_read_recent(self, data: dict[str, Any]) -> None:
        try:
            limit = int(data.get("limit", 100))
        except (TypeError, ValueError):
            limit = 100
        entries = self._state.recent_entries(limit)
        self._send(make_ok({"lines": [entry["text"] for entry in entries], "entries": entries}))

    # 操作分发表
    _OP_TABLE = {
        "session.status": _op_session_status,
        "session.open": _op_session_open,
        "session.close": _op_session_close,
        "writer.acquire": _op_writer_acquire,
        "writer.release": _op_writer_release,
        "input.send_line": _op_input_send_line,
        "stream.subscribe": _op_stream_subscribe,
        "stream.read_recent": _op_stream_read_recent,
    }

    # ------------------------------------------------------------------
    # 推送模式
    # ------------------------------------------------------------------

    def _pump_stream(self, reader) -> bool:
        """推送模式下的单次循环：等待 broker 队列新行并推给 client。

        采用 ``_POLL_TIMEOUT`` 短超时阻塞，避免空转同时能及时响应 stop_event。
        返回 True 表示继续循环；False 表示应退出（连接已断开或订阅被清除）。
        """
        sub_q = self._broker.get_queue(self._sub_id) if self._sub_id is not None else None
        if sub_q is None:
            # 订阅被外部清除，退出推送
            return False

        try:
            text = sub_q.get(timeout=_POLL_TIMEOUT)
        except queue.Empty:
            return not self._stop.is_set()
        return self._send_stream_data(text)

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def _send(self, payload: dict) -> bool:
        """发送一条响应。返回是否成功（连接断开时返回 False 并记日志）。"""
        try:
            self._conn.sendall(encode_message(payload))
            return True
        except OSError as e:
            _logger.warning("client %s 响应发送失败: %s", self._addr, e)
            return False

    def _send_stream_data(self, text: str) -> bool:
        """推送一条流数据消息。"""
        return self._send({"op": "stream.data", "data": {"text": text}})

    def _cleanup(self) -> None:
        if self._sub_id is not None:
            self._broker.unsubscribe(self._sub_id)
            self._state.dec_subscriber()
            self._sub_id = None
