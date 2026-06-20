from pathlib import Path

from rp5_serial.host.serial_runtime import RuntimeState


def test_recent_entries_include_timestamp_and_text(tmp_path):
    state = RuntimeState(device_id="rp5", transcript_dir=str(tmp_path))
    state._line_buffer = [
        {"text": "Booting Linux", "ts": "2026-06-20T12:00:00+0800"},
        {"text": "init: starting service 'zygote'", "ts": "2026-06-20T12:00:01+0800"},
    ]
    state._rx_buf = b"console:/ $"

    recent = state.recent_entries(3)

    assert recent[0]["text"] == "Booting Linux"
    assert recent[0]["ts"] == "2026-06-20T12:00:00+0800"
    assert recent[-1]["text"] == "console:/ $"
    assert recent[-1]["pending"] is True


def test_read_lines_appends_transcript_file(tmp_path):
    class FakeSerial:
        in_waiting = 33
        is_open = True

        def read(self, waiting):
            return b"line1\nline2\n"

    state = RuntimeState(device_id="rp5", transcript_dir=str(tmp_path))
    state._serial = FakeSerial()

    lines = state.read_lines()

    assert lines == ["line1", "line2"]
    transcript = Path(state.transcript_path)
    assert transcript.exists()
    text = transcript.read_text(encoding="utf-8")
    assert "line1" in text
    assert "line2" in text


def test_status_contains_transcript_metadata(tmp_path):
    state = RuntimeState(device_id="rp5", transcript_dir=str(tmp_path))
    status = state.status().to_dict()

    assert status["transcript_path"].endswith("rp5-serial-transcript.log")
    assert status["recent_buffer_limit"] >= 500


def test_status_reports_no_writer_initially():
    state = RuntimeState(device_id="rp5")
    status = state.status().to_dict()
    assert status["active_writer"] is None


def test_status_reports_serial_disconnected_without_port():
    state = RuntimeState(device_id="rp5")
    status = state.status().to_dict()
    assert status["serial_state"] in ("DISCONNECTED", "NO_DRIVER")


def test_recent_lines_includes_pending_prompt_text():
    state = RuntimeState(device_id="rp5")
    state._line_buffer = ["Booting Linux"]
    state._rx_buf = b"console:/ $"

    assert state.recent_lines(10) == ["Booting Linux", "console:/ $"]


def test_recent_lines_respects_limit_when_pending_prompt_exists():
    state = RuntimeState(device_id="rp5")
    state._line_buffer = ["line1", "line2", "line3"]
    state._rx_buf = b"console:/ #"

    assert state.recent_lines(2) == ["line3", "console:/ #"]
