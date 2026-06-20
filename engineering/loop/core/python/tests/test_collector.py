"""Collector 测试：命令边界隔离、异常降级、partial evidence。"""
from loop_core.collector import Collector
from loop_core.transport import FixtureTransport


def test_collector_capture_isolated_per_command():
    """每条命令只看到自己发送后的输出。"""
    transport = FixtureTransport([
        {"t": 0.1, "text": "dmesg line 1"},
        {"t": 0.2, "text": "console:/ $"},
        {"t": 0.3, "text": "logcat line 1"},
        {"t": 0.4, "text": "console:/ $"},
    ])
    transport.acquire_writer()
    result = Collector(transport).run(
        "debug",
        {"commands": ["dmesg", "logcat -d"], "hints": "check logs"},
        capture_timeout=5.0,
        recent_limit=100,
        prompt_markers=["console:/ $"],
    )
    assert result.status == "ok"
    assert result.outputs[0]["lines"] == ["dmesg line 1"]
    assert result.outputs[1]["lines"] == ["logcat line 1"]


def test_collector_error_marks_degraded():
    """单条命令失败时，collector 标记为 degraded/partial。"""

    class FlakyTransport(FixtureTransport):
        def __init__(self, rows):
            super().__init__(rows)
            self._call_count = 0

        def send_line(self, text: str) -> None:
            self._call_count += 1
            if self._call_count == 1:  # First command fails
                raise OSError("connection lost")
            super().send_line(text)

    transport = FlakyTransport([
        {"t": 0.1, "text": "recovery output"},
        {"t": 0.2, "text": "console:/ $"},
    ])
    transport.acquire_writer()
    result = Collector(transport).run(
        "flaky",
        {"commands": ["failing_cmd", "recovery_cmd"], "hints": ""},
        capture_timeout=5.0,
        recent_limit=100,
        prompt_markers=["console:/ $"],
    )
    assert result.status == "degraded"
    assert result.partial is True
    assert "connection lost" in result.error
    # First command has error, second has output
    assert "error" in result.outputs[0]
    assert result.outputs[1]["lines"] == ["recovery output"]


def test_collector_all_commands_fail_status_error():
    """所有命令都失败时 status 为 error（partial=False 因没有成功命令）。"""

    class BrokenTransport(FixtureTransport):
        def send_line(self, text: str) -> None:
            raise OSError("totally broken")

    transport = BrokenTransport([])
    transport.acquire_writer()
    result = Collector(transport).run(
        "broken",
        {"commands": ["cmd1", "cmd2"], "hints": ""},
        capture_timeout=5.0,
        recent_limit=100,
    )
    assert result.status == "error"
    assert result.partial is False
    assert len(result.outputs) == 2
    assert all("error" in out for out in result.outputs)


def test_collector_status_ok_when_all_succeed():
    """所有命令成功时 status 为 ok。"""
    transport = FixtureTransport([
        {"t": 0.1, "text": "output1"},
        {"t": 0.2, "text": "console:/ $"},
    ])
    transport.acquire_writer()
    result = Collector(transport).run(
        "ok_collector",
        {"commands": ["cmd1"], "hints": "all good"},
        capture_timeout=5.0,
        recent_limit=100,
        prompt_markers=["console:/ $"],
    )
    assert result.status == "ok"
    assert result.partial is False
    assert result.error == ""
