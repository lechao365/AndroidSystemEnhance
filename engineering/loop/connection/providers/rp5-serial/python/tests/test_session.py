from rp5_serial.shared.models import Session


def test_session_to_dict_contains_required_fields():
    session = Session(
        session_id="s-1",
        device_id="rp5",
        mode="monitor",
        writer_owner=None,
        started_at="2026-06-19T10:00:00+0800",
        ended_at=None,
        state="ACTIVE",
    )
    data = session.to_dict()
    assert data["session_id"] == "s-1"
    assert data["device_id"] == "rp5"
    assert data["mode"] == "monitor"
    assert data["state"] == "ACTIVE"


from rp5_serial.host.serial_runtime import RuntimeState


def test_open_session_creates_active_session():
    state = RuntimeState(device_id="rp5")
    session = state.open_session(mode="monitor", owner_id="observer")
    assert session.device_id == "rp5"
    assert state.active_session is not None
    assert state.active_session.mode == "monitor"
