from dataclasses import asdict, dataclass
from typing import Optional


@dataclass
class Session:
    session_id: str
    device_id: str
    mode: str
    writer_owner: Optional[str]
    started_at: str
    ended_at: Optional[str]
    state: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WriterLease:
    lease_id: str
    session_id: str
    owner_type: str
    owner_id: str
    acquired_at: str
    expires_at: str
    state: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StreamEvent:
    ts: str
    session_id: str
    seq: int
    direction: str
    source: str
    payload_text: str
    tags: list[str]

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class StatusResponse:
    host_state: str
    serial_state: str
    active_session: Optional[dict]
    active_writer: Optional[dict]
    subscriber_count: int

    def to_dict(self) -> dict:
        return asdict(self)
