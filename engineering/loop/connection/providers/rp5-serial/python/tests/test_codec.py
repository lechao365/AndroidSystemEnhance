from rp5_serial.shared.codec import decode_message, encode_message, make_error, make_ok


def test_encode_decode_request_roundtrip():
    payload = {"op": "session.status", "data": {"device_id": "rp5"}}
    encoded = encode_message(payload)
    decoded = decode_message(encoded)
    assert decoded == payload


def test_make_ok_has_expected_shape():
    response = make_ok({"host_state": "READY"})
    assert response["ok"] is True
    assert response["code"] == "OK"
    assert response["data"]["host_state"] == "READY"


def test_make_error_has_expected_shape():
    response = make_error("INVALID_REQUEST", "bad payload")
    assert response["ok"] is False
    assert response["code"] == "INVALID_REQUEST"
    assert response["message"] == "bad payload"
