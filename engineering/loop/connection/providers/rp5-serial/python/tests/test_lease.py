from rp5_serial.shared.models import WriterLease


def test_writer_lease_to_dict_contains_owner_fields():
    lease = WriterLease(
        lease_id="l-1",
        session_id="s-1",
        owner_type="human",
        owner_id="cli-user",
        acquired_at="2026-06-19T10:00:00+0800",
        expires_at="2026-06-19T10:05:00+0800",
        state="HELD",
    )
    data = lease.to_dict()
    assert data["owner_type"] == "human"
    assert data["owner_id"] == "cli-user"
    assert data["state"] == "HELD"


from rp5_serial.host.serial_runtime import RuntimeState


def test_acquire_writer_when_free_succeeds():
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="cli-user")
    lease = state.acquire_writer(owner_type="human", owner_id="cli-user")
    assert lease.owner_id == "cli-user"


def test_acquire_writer_when_busy_fails():
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="cli-user")
    state.acquire_writer(owner_type="human", owner_id="cli-user")
    assert state.acquire_writer(owner_type="workflow", owner_id="auto-1") is None
