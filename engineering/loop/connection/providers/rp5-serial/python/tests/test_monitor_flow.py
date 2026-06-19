from rp5_serial.host.serial_runtime import RuntimeState


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
