import base64
import hashlib
import hmac

import httpx

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


@pytest.mark.asyncio
async def test_fills_history_requires_inst_type_and_forwards_pagination():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"code": "0", "data": []})

    raw_client = httpx.AsyncClient(
        base_url="https://openapi.okx.com",
        transport=httpx.MockTransport(handler),
    )
    client = ReadOnlyOKXClient("https://openapi.okx.com", client=raw_client)

    async with client:
        await client.fills_history(
            "SWAP", after="bill-123", before="bill-456", limit=50
        )

    await raw_client.aclose()
    assert "instType=SWAP" in seen["url"]
    assert "after=bill-123" in seen["url"]
    assert "before=bill-456" in seen["url"]
    assert "limit=50" in seen["url"]


@pytest.mark.asyncio
async def test_account_bills_archive_forwards_pagination():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["query"] = request.url.query.decode()
        return httpx.Response(200, json={"code": "0", "data": []})

    raw_client = httpx.AsyncClient(
        base_url="https://openapi.okx.com",
        transport=httpx.MockTransport(handler),
    )
    client = ReadOnlyOKXClient("https://openapi.okx.com", client=raw_client)

    async with client:
        await client.account_bills_archive(
            "SWAP",
            after="bill-1",
            begin="1700000000000",
            end="1701000000000",
            limit=100,
        )

    await raw_client.aclose()
    assert seen["path"] == "/api/v5/account/bills-archive"
    assert "instType=SWAP" in seen["query"]
    assert "after=bill-1" in seen["query"]
    assert "begin=1700000000000" in seen["query"]
    assert "end=1701000000000" in seen["query"]
    assert "limit=100" in seen["query"]
