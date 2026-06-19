from rp5_serial.host.serial_runtime import RuntimeState


def test_interactive_acquire_writer_success():
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="cli-user")
    lease = state.acquire_writer(owner_type="human", owner_id="cli-user")
    assert lease is not None


def test_interactive_acquire_writer_busy_returns_none():
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="cli-user")
    state.acquire_writer(owner_type="human", owner_id="cli-user")
    assert state.acquire_writer(owner_type="human", owner_id="cli-user-2") is None
