from rp5_serial.host.serial_runtime import RuntimeState


def test_status_reports_no_writer_initially():
    state = RuntimeState(device_id="rp5")
    status = state.status().to_dict()
    assert status["active_writer"] is None


def test_status_reports_serial_disconnected_without_port():
    state = RuntimeState(device_id="rp5")
    status = state.status().to_dict()
    assert status["serial_state"] in ("DISCONNECTED", "NO_DRIVER")
