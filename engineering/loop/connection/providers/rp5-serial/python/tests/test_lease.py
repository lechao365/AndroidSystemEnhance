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


# ---------------------------------------------------------------------------
# P1-4：Writer 泄漏修复（owner 级释放 + TTL 过期回收）
# ---------------------------------------------------------------------------

def test_release_for_owner_releases_matching_writer():
    """持 writer 的 client 断开时，按 owner_id 释放 writer，后续可重新 acquire。

    回归 P1-4：原 handler._cleanup 不释放 writer，client 异常断开后
    active_writer 永驻，必须重启 host。
    """
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="cli-user")
    state.acquire_writer(owner_type="human", owner_id="cli-user")
    # 模拟持 writer 的 client 断开：按 owner 释放
    state.release_for_owner("cli-user")
    # writer 已释放，新 client 可获取
    lease = state.acquire_writer(owner_type="workflow", owner_id="auto-1")
    assert lease is not None
    assert lease.owner_id == "auto-1"


def test_release_for_owner_wrong_owner_no_effect():
    """A 持有 writer 时，B 的 release 不应影响 A 的 writer。"""
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="A")
    state.acquire_writer(owner_type="human", owner_id="A")
    state.release_for_owner("B")  # B 断开，不应释放 A 的 writer
    assert state.active_writer is not None
    assert state.active_writer.owner_id == "A"


def test_expired_lease_is_reclaimed_on_acquire():
    """超过 TTL 的 lease 在下次 acquire 时被自动回收。"""
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="A")
    state.acquire_writer(owner_type="human", owner_id="A")
    # 篡改 acquired_at 为远古时间，模拟 lease 已超 TTL
    state.active_writer.acquired_at = "2020-01-01T00:00:00+0800"
    state.active_writer.expires_at = "2020-01-01T00:05:00+0800"
    # 新 client acquire 应自动回收过期 lease 并成功
    lease = state.acquire_writer(owner_type="workflow", owner_id="B")
    assert lease is not None
    assert lease.owner_id == "B"


def test_fresh_lease_has_real_expiry():
    """新 lease 的 expires_at 必须晚于 acquired_at（真实 TTL，非相等）。"""
    state = RuntimeState(device_id="rp5")
    state.open_session(mode="interactive", owner_id="A")
    lease = state.acquire_writer(owner_type="human", owner_id="A")
    assert lease is not None
    assert lease.expires_at > lease.acquired_at
