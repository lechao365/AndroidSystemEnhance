import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from rp5_serial.shared.models import Session, StatusResponse, WriterLease

# 可选依赖：pyserial 缺失时降级，不阻断 host 启动
try:
    import serial  # type: ignore
    HAS_SERIAL = True
except ImportError:  # pragma: no cover - 环境相关
    serial = None  # type: ignore
    HAS_SERIAL = False

TZ = timezone(timedelta(hours=8))

# 最近输出缓冲上限
MAX_LINE_BUFFER = 2000

# transcript 文件名
TRANSCRIPT_FILENAME = "rp5-serial-transcript.log"


def now_iso() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S%z")


@dataclass
class RuntimeState:
    device_id: str
    # transcript 落盘目录（None 时降级到 .host-log）
    transcript_dir: str | None = None
    active_session: Session | None = None
    active_writer: WriterLease | None = None
    subscriber_count: int = 0
    # 串口配置
    serial_port: str | None = None
    baudrate: int = 115200
    # pyserial Serial 实例（类型为 Any，避免硬依赖）
    _serial: object | None = None
    # 最近输出缓冲（保留最近 MAX_LINE_BUFFER 条结构化条目）
    _line_buffer: list[dict] = field(default_factory=list)
    # 接收半行缓冲（尚未遇到 \n 的字节）
    _rx_buf: bytes = b""
    # 并发锁：保护 session/writer/buffer 的读写一致性
    _lock: threading.RLock = field(default_factory=threading.RLock)
    # transcript 文件绝对路径（__post_init__ 初始化，不作为构造参数）
    transcript_path: str = field(init=False, default="")

    def __post_init__(self) -> None:
        base = Path(self.transcript_dir or ".host-log")
        base.mkdir(parents=True, exist_ok=True)
        self.transcript_path = str(base / TRANSCRIPT_FILENAME)

    def open_session(self, mode: str, owner_id: str) -> Session:
        with self._lock:
            session = Session(
                session_id=f"s-{uuid4().hex[:8]}",
                device_id=self.device_id,
                mode=mode,
                writer_owner=None,
                started_at=now_iso(),
                ended_at=None,
                state="ACTIVE",
            )
            self.active_session = session
            return session

    def close_session(self) -> None:
        """结束当前 session，同时释放 writer。"""
        with self._lock:
            if self.active_session is None:
                return
            self.active_writer = None
            self.active_session.ended_at = now_iso()
            self.active_session.state = "ENDED"
            self.active_session = None

    def acquire_writer(self, owner_type: str, owner_id: str) -> WriterLease | None:
        with self._lock:
            if self.active_writer is not None:
                return None
            if self.active_session is None:
                self.open_session(mode="interactive", owner_id=owner_id)
            lease = WriterLease(
                lease_id=f"l-{uuid4().hex[:8]}",
                session_id=self.active_session.session_id,
                owner_type=owner_type,
                owner_id=owner_id,
                acquired_at=now_iso(),
                expires_at=now_iso(),
                state="HELD",
            )
            self.active_writer = lease
            self.active_session.writer_owner = owner_id
            return lease

    def release_writer(self) -> None:
        with self._lock:
            self.active_writer = None
            if self.active_session:
                self.active_session.writer_owner = None

    def send_line(self, text: str) -> None:
        """向串口写入一行（自动追加 \\n）。

        必须持有 writer lease，否则拒绝写入。
        """
        with self._lock:
            if self.active_writer is None:
                raise RuntimeError("no active writer lease; acquire_writer first")
            if self._serial is None:
                raise RuntimeError("serial port not open")
            payload = text.encode("utf-8")
            if not payload.endswith(b"\n"):
                payload += b"\n"
            self._serial.write(payload)  # type: ignore[union-attr]

    def _pending_text(self) -> str | None:
        """返回尚未换行的接收文本快照；空文本返回 None。"""
        if not self._rx_buf:
            return None
        text = self._rx_buf.decode("utf-8", errors="replace").rstrip("\r")
        return text or None

    def _append_entry(self, text: str, pending: bool = False) -> dict:
        """构造一条结构化条目；非 pending 时写入 ring buffer 与 transcript 文件。

        调用方应已持有 ``_lock``（如 ``read_lines`` 内部）。
        """
        entry = {"text": text, "ts": now_iso(), "pending": pending}
        if not pending:
            self._line_buffer.append(entry)
            if len(self._line_buffer) > MAX_LINE_BUFFER:
                del self._line_buffer[: len(self._line_buffer) - MAX_LINE_BUFFER]
            try:
                with Path(self.transcript_path).open("a", encoding="utf-8") as fp:
                    fp.write(f"{entry['ts']} {text}\n")
            except OSError:
                # transcript 落盘失败不阻断串口读取
                pass
        return entry

    def recent_entries(self, limit: int) -> list[dict]:
        """返回最近 N 条结构化条目；若存在半行，作为 pending 条目追加在末尾。

        兼容历史上 ``_line_buffer`` 内为纯字符串的情况。
        """
        with self._lock:
            if limit <= 0:
                return []
            entries: list[dict] = []
            for item in self._line_buffer[-limit:]:
                if isinstance(item, dict):
                    entries.append(item)
                else:
                    entries.append({"text": item, "ts": now_iso(), "pending": False})
            pending = self._pending_text()
            if pending:
                entries.append({"text": pending, "ts": now_iso(), "pending": True})
            if len(entries) > limit:
                entries = entries[-limit:]
            return entries

    def recent_lines(self, limit: int) -> list[str]:
        """返回最近 N 行文本（从结构化条目提取 text）。"""
        return [entry["text"] for entry in self.recent_entries(limit)]

    def inc_subscriber(self) -> int:
        with self._lock:
            self.subscriber_count += 1
            return self.subscriber_count

    def dec_subscriber(self) -> int:
        with self._lock:
            self.subscriber_count = max(0, self.subscriber_count - 1)
            return self.subscriber_count

    # ------------------------------------------------------------------
    # 串口 I/O
    # ------------------------------------------------------------------

    def open_serial(self) -> bool:
        """打开串口。成功返回 True；无驱动或失败返回 False。"""
        if not HAS_SERIAL:
            return False
        if self._serial is not None:
            return True
        try:
            self._serial = serial.Serial(  # type: ignore[union-attr]
                self.serial_port,
                baudrate=self.baudrate,
                timeout=0,  # 非阻塞读取
            )
            return True
        except Exception:
            self._serial = None
            return False

    def close_serial(self) -> None:
        """关闭串口连接。"""
        if self._serial is None:
            return
        try:
            self._serial.close()  # type: ignore[union-attr]
        except Exception:
            pass
        finally:
            self._serial = None

    def read_lines(self) -> list[str]:
        """非阻塞读取串口当前可用字节，按 \\n 切分。

        返回本次新增的完整行列表；半行暂存到 _rx_buf，下次拼接。
        最近输出缓冲保留最近 MAX_LINE_BUFFER 行。
        """
        with self._lock:
            if self._serial is None:
                return []
            try:
                waiting = getattr(self._serial, "in_waiting", 0) or 0
                chunk = self._serial.read(waiting) if waiting else b""  # type: ignore[union-attr]
            except Exception:
                return []
            if not chunk:
                return []
            data = self._rx_buf + chunk
            parts = data.split(b"\n")
            # 最后一段尚未遇到换行，保留到下次
            self._rx_buf = parts[-1]
            new_lines: list[str] = []
            for raw in parts[:-1]:
                text = raw.decode("utf-8", errors="replace").rstrip("\r")
                self._append_entry(text)
                new_lines.append(text)
            return new_lines

    # ------------------------------------------------------------------
    # 状态
    # ------------------------------------------------------------------

    def _serial_state(self) -> str:
        if self._serial is not None and getattr(self._serial, "is_open", False):
            return "CONNECTED"
        if not HAS_SERIAL:
            return "NO_DRIVER"
        return "DISCONNECTED"

    def status(self) -> StatusResponse:
        with self._lock:
            return StatusResponse(
                host_state="READY",
                serial_state=self._serial_state(),
                active_session=self.active_session.to_dict() if self.active_session else None,
                active_writer=self.active_writer.to_dict() if self.active_writer else None,
                subscriber_count=self.subscriber_count,
                transcript_path=self.transcript_path,
                recent_buffer_limit=MAX_LINE_BUFFER,
                recent_line_count=len(self._line_buffer),
            )
