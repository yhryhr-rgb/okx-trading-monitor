import base64
import hashlib
import hmac

import pytest

from app.okx_client import OKXCredentials
from app.ws_private import READ_ONLY_CHANNELS, WSConfig, make_ws_login, PrivateReadOnlyStream


def test_ws_login_signature_and_payload():
    creds = OKXCredentials("key", "secret", "pass")
    payload = make_ws_login(creds, epoch_seconds="1700000000")
    expected = base64.b64encode(
        hmac.new(
            b"secret",
            b"1700000000GET/users/self/verify",
            hashlib.sha256,
        ).digest()
    ).decode()
    assert payload["op"] == "login"
    assert payload["args"][0]["sign"] == expected
    assert payload["args"][0]["apiKey"] == "key"


def test_only_read_only_channels_are_subscribed():
    assert {item["channel"] for item in READ_ONLY_CHANNELS} == {
        "account", "positions", "orders", "balance_and_position"
    }
    assert all("channel" in item for item in READ_ONLY_CHANNELS)


def test_rejects_insecure_websocket_url():
    with pytest.raises(ValueError):
        WSConfig("ws://ws.okx.com/ws/v5/private")


def test_rejects_non_okx_private_websocket_host():
    with pytest.raises(ValueError):
        WSConfig("wss://example.com/ws/v5/private")
