import json
from collections import deque

from rp5_serial.client.automation import AutomationClient
from rp5_serial.shared.codec import encode_message


class FakeRaw:
    def __init__(self, messages: list[dict]):
        self._messages = deque(encode_message(message) for message in messages)
        self.closed = False

    def readline(self):
        if not self._messages:
            return b""
        return self._messages.popleft()

    def pending_count(self) -> int:
        return len(self._messages)

    def close(self):
        self.closed = True
        return None


class FakeStreamSocket:
    """模拟 stream channel socket，支持 recv + settimeout + gettimeout。

    connect 后先返回 subscribe 响应，之后按序返回预置消息。
    """

    def __init__(self, messages: list[dict]):
        self._data = b"".join(encode_message(m) for m in messages)
        self.sent: list[dict] = []
        self._timeout = None
        self.closed = False

    def makefile(self, mode: str):
        assert mode == "rb"

        class _FakeFile:
            def __init__(self, data: bytes):
                self._data = data

            def readline(self):
                if not self._data:
                    return b""
                line, _, self._data = self._data.partition(b"\n")
                return line + b"\n"

        return _FakeFile(self._data)

    def sendall(self, payload: bytes):
        self.sent.append(json.loads(payload.decode("utf-8")))

    def settimeout(self, value):
        self._timeout = value

    def gettimeout(self):
        return self._timeout

    def recv(self, bufsize: int) -> bytes:
        if not self._data:
            return b""
        chunk = self._data[:bufsize]
        self._data = self._data[bufsize:]
        return chunk

    def close(self):
        self.closed = True


class FakeCmdSocket:
    """模拟 command channel socket，用 FakeRaw 提供 makefile readline。"""

    def __init__(self, raw: FakeRaw):
        self.raw = raw
        self.sent: list[dict] = []

    def makefile(self, mode: str):
        assert mode == "rb"
        return self.raw

    def sendall(self, payload: bytes):
        self.sent.append(json.loads(payload.decode("utf-8")))

    def settimeout(self, value):
        pass

    def gettimeout(self):
        return None

    def close(self):
        pass


class RaisingCmdSocket(FakeCmdSocket):
    def sendall(self, payload: bytes):
        if json.loads(payload.decode("utf-8")).get("op") == "writer.release":
            raise OSError("release failed")
        super().sendall(payload)


def _patch_connections(monkeypatch, cmd_sock, stream_sock) -> None:
    pending = deque([cmd_sock, stream_sock])
    monkeypatch.setattr("socket.create_connection", lambda *args, **kwargs: pending.popleft())


def _ok(msgs: list[dict] | None = None) -> list[dict]:
    """subscribe 响应在最前面"""
    base = [{"ok": True, "code": "OK", "message": "ok", "data": {}}]
    if msgs:
        base.extend(msgs)
    return base


def test_connect_subscribes_stream_channel(monkeypatch):
    cmd_sock = FakeCmdSocket(FakeRaw([]))
    stream_sock = FakeStreamSocket(_ok())
    _patch_connections(monkeypatch, cmd_sock, stream_sock)

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()

    assert stream_sock.sent[0]["op"] == "stream.subscribe"
    assert cmd_sock.sent == []


def test_send_line_consumes_command_response(monkeypatch):
    cmd_sock = FakeCmdSocket(
        FakeRaw(
            [
                {"ok": True, "code": "OK", "message": "ok", "data": {}},
                {"ok": True, "code": "OK", "message": "ok", "data": {}},
            ]
        )
    )
    stream_sock = FakeStreamSocket(_ok())
    _patch_connections(monkeypatch, cmd_sock, stream_sock)

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()
    assert client.acquire_writer() is True

    client.send_line("uname -a")

    assert [message["op"] for message in cmd_sock.sent] == ["writer.acquire", "input.send_line"]
    assert cmd_sock.raw.pending_count() == 0


def test_read_until_timeout_returns_only_stream_text(monkeypatch):
    cmd_sock = FakeCmdSocket(FakeRaw([]))
    stream_sock = FakeStreamSocket(
        _ok(
            [
                {"op": "stream.data", "data": {"text": "console:/ $"}},
            ]
        )
    )
    _patch_connections(monkeypatch, cmd_sock, stream_sock)

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()

    assert client.read_until_timeout(0.01) == ["console:/ $"]


def test_capture_recent_lines_uses_command_channel(monkeypatch):
    cmd_sock = FakeCmdSocket(FakeRaw([{"ok": True, "code": "OK", "message": "ok", "data": {"lines": ["line1", "line2"]}}]))
    stream_sock = FakeStreamSocket(_ok())
    _patch_connections(monkeypatch, cmd_sock, stream_sock)

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()

    assert client.capture_recent_lines(2) == ["line1", "line2"]
    assert [message["op"] for message in cmd_sock.sent] == ["stream.read_recent"]
    assert stream_sock.sent[0]["op"] == "stream.subscribe"


def test_capture_recent_lines_raises_on_error_response(monkeypatch):
    cmd_sock = FakeCmdSocket(FakeRaw([{"ok": False, "code": "INVALID_REQUEST", "message": "bad limit", "data": {}}]))
    stream_sock = FakeStreamSocket(_ok())
    _patch_connections(monkeypatch, cmd_sock, stream_sock)

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()

    try:
        client.capture_recent_lines(2)
    except OSError as exc:
        assert str(exc) == "bad limit"
    else:
        raise AssertionError("expected OSError for failed stream.read_recent response")


def test_capture_recent_lines_returns_empty_list_for_invalid_lines_type(monkeypatch):
    cmd_sock = FakeCmdSocket(FakeRaw([{"ok": True, "code": "OK", "message": "ok", "data": {"lines": "not-a-list"}}]))
    stream_sock = FakeStreamSocket(_ok())
    _patch_connections(monkeypatch, cmd_sock, stream_sock)

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()

    assert client.capture_recent_lines(2) == []


def test_capture_recent_lines_returns_empty_list_for_non_string_items(monkeypatch):
    cmd_sock = FakeCmdSocket(FakeRaw([{"ok": True, "code": "OK", "message": "ok", "data": {"lines": ["line1", 2]}}]))
    stream_sock = FakeStreamSocket(_ok())
    _patch_connections(monkeypatch, cmd_sock, stream_sock)

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()

    assert client.capture_recent_lines(2) == []


def test_release_closes_both_channels(monkeypatch):
    cmd_sock = RaisingCmdSocket(FakeRaw([]))
    stream_sock = FakeStreamSocket(_ok())
    _patch_connections(monkeypatch, cmd_sock, stream_sock)

    client = AutomationClient("127.0.0.1", 9700)
    client.connect()
    client.release()

    assert stream_sock.closed is True
