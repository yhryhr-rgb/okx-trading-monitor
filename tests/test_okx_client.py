import base64
import hashlib
import hmac

import pytest

from app.okx_client import ReadOnlyOKXClient, decimal_or_none, make_signature


def test_signature_matches_hmac_sha256():
    timestamp = "2024-01-01T00:00:00.000Z"
    method = "GET"
    path = "/api/v5/account/balance"
    secret = "test-secret"
    expected = base64.b64encode(
        hmac.new(secret.encode(), f"{timestamp}{method}{path}".encode(), hashlib.sha256).digest()
    ).decode()
    assert make_signature(timestamp, method, path, secret) == expected


def test_decimal_parser():
    assert str(decimal_or_none("1.2300")) == "1.2300"
    assert decimal_or_none("") is None
    assert decimal_or_none("not-a-number") is None


def test_rejects_non_https_base_url():
    with pytest.raises(ValueError):
        ReadOnlyOKXClient("http://openapi.okx.com")


def test_rejects_non_okx_https_host():
    with pytest.raises(ValueError):
        ReadOnlyOKXClient("https://example.com")


def test_private_client_can_be_constructed_without_credentials():
    client = ReadOnlyOKXClient("https://openapi.okx.com")
    assert client.credentials is None
