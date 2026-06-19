import json
from rp5_serial.shared.errors import OK


def encode_message(payload: dict) -> bytes:
    return (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")


def decode_message(raw: bytes) -> dict:
    return json.loads(raw.decode("utf-8").strip())


def make_ok(data: dict | None = None, message: str = "ok") -> dict:
    return {
        "ok": True,
        "code": OK,
        "message": message,
        "data": data or {},
    }


def make_error(code: str, message: str) -> dict:
    return {
        "ok": False,
        "code": code,
        "message": message,
        "data": {},
    }
