import asyncio
import json
from datetime import datetime, timedelta, timezone
from time import monotonic

import httpx
import pytest

from app import okx_client
from app.okx_client import OKXCredentials, OKXError, ReadOnlyOKXClient, make_signature


BASE_URL = "https://openapi.okx.com"
PATH = "/api/v5/account/balance"
SUCCESS = {"code": "0", "data": []}


@pytest.fixture
def clock(monkeypatch):
    class Clock:
        def __init__(self):
            self.now = 0.0
            self.sleeps = []

        async def sleep(self, delay):
            self.sleeps.append(delay)
            self.now += delay
            await asyncio.sleep(0)

    value = Clock()
    monkeypatch.setattr(okx_client, "monotonic", lambda: value.now)
    monkeypatch.setattr(okx_client, "sleep", value.sleep)
    epoch = datetime(2026, 1, 1, tzinfo=timezone.utc)

    class ClockDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return (epoch + timedelta(seconds=value.now)).astimezone(tz)

    monkeypatch.setattr(okx_client, "datetime", ClockDateTime)
    return value


@pytest.mark.asyncio
async def test_concurrent_requests_are_spaced_and_signed_at_dispatch(clock):
    starts = []
    credentials = OKXCredentials("test-key", "test-secret", "test-passphrase")

    async def handle(request):
        starts.append(clock.now)
        assert request.method == "GET"
        assert request.url.raw_path == b"/api/v5/account/balance?ccy=BTC%2CETH"
        timestamp = request.headers["OK-ACCESS-TIMESTAMP"]
        assert timestamp == okx_client.utc_timestamp()
        assert request.headers["OK-ACCESS-SIGN"] == make_signature(
            timestamp, "GET", request.url.raw_path.decode(), credentials.api_secret
        )
        await asyncio.sleep(0)
        return httpx.Response(200, json=SUCCESS)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        client = ReadOnlyOKXClient(BASE_URL, credentials, client=http)
        results = await asyncio.gather(*[
            client.get(PATH, {"ccy": "BTC,ETH", "ignored": None}, authenticated=True)
            for _ in range(5)
        ])
    assert results == [SUCCESS] * 5
    assert starts == pytest.approx([0, 0.2, 0.4, 0.6, 0.8])


@pytest.mark.asyncio
async def test_cancelling_waiter_does_not_block_later_requests(clock, monkeypatch):
    started_sleep = asyncio.Event()
    blocked = asyncio.Event()

    async def wait(delay):
        started_sleep.set()
        await blocked.wait()

    async with httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=SUCCESS))
    ) as http:
        client = ReadOnlyOKXClient(BASE_URL, client=http)
        await client.get(PATH)
        monkeypatch.setattr(okx_client, "sleep", wait)
        task = asyncio.create_task(client.get(PATH))
        await asyncio.wait_for(started_sleep.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        monkeypatch.setattr(okx_client, "sleep", clock.sleep)
        assert await asyncio.wait_for(client.get(PATH), timeout=1) == SUCCESS
    assert clock.now == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_real_clock_concurrency_has_no_burst():
    starts = []

    async def handle(request):
        starts.append(monotonic())
        await asyncio.sleep(0)
        return httpx.Response(200, json=SUCCESS)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        client = ReadOnlyOKXClient(BASE_URL, requests_per_second=20, client=http)
        await asyncio.gather(*[client.get(PATH) for _ in range(4)])
    assert all(b - a >= 0.045 for a, b in zip(starts, starts[1:]))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 429])
async def test_throttle_retries_have_fresh_signatures_and_bounded_backoff(clock, status):
    starts, timestamps = [], []
    credentials = OKXCredentials("test-key", "test-secret", "test-passphrase")

    def handle(request):
        starts.append(clock.now)
        timestamps.append(request.headers["OK-ACCESS-TIMESTAMP"])
        assert request.method == "GET"
        assert str(request.url) == BASE_URL + PATH + "?ccy=BTC"
        assert request.headers["OK-ACCESS-SIGN"] == make_signature(
            okx_client.utc_timestamp(), "GET", PATH + "?ccy=BTC", credentials.api_secret
        )
        if len(starts) == 5:
            return httpx.Response(200, json=SUCCESS)
        if status == 429:
            return httpx.Response(429, text="not JSON")
        return httpx.Response(200, json={"code": "50011", "msg": "rate limited"})

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        client = ReadOnlyOKXClient(
            BASE_URL, credentials, client=http, max_retries=4, max_retry_backoff=2
        )
        assert await client.get(PATH, {"ccy": "BTC"}, authenticated=True) == SUCCESS
    assert starts == pytest.approx([0, 1, 3, 5, 7])
    assert len(set(timestamps)) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [200, 429])
async def test_retry_exhaustion_is_bounded_and_redacted(clock, caplog, status):
    starts = []
    provider_text = "private-provider-value"

    def handle(request):
        starts.append(clock.now)
        return httpx.Response(status, json={"code": "50011", "msg": provider_text})

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        client = ReadOnlyOKXClient(BASE_URL, client=http)
        with pytest.raises(OKXError, match="^OKX rate limit retries exhausted$") as error:
            await client.get(PATH)
    assert starts == pytest.approx([0, 1, 3, 7])
    assert provider_text not in str(error.value) + caplog.text


@pytest.mark.asyncio
async def test_disabling_retries_still_cools_down_next_caller(clock):
    starts = []

    def handle(request):
        starts.append(clock.now)
        return httpx.Response(429) if len(starts) == 1 else httpx.Response(200, json=SUCCESS)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        client = ReadOnlyOKXClient(BASE_URL, client=http, max_retries=0)
        with pytest.raises(OKXError, match="retries exhausted"):
            await client.get(PATH)
        assert clock.sleeps == []
        assert await client.get(PATH) == SUCCESS
    assert starts == pytest.approx([0, 1])


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_after,expected", [
    ("3", 3),
    ("Thu, 01 Jan 2026 00:00:05 GMT", 5),
    ("Wed, 31 Dec 2025 23:59:00 GMT", 1),
    ("Thu, 01 Jan 2026 00:00:05", 1),
    ("0", 1),
    ("", 1),
    ("-5", 1),
    ("1.5", 1),
    ("NaN", 1),
    ("Infinity", 1),
    ("9" * 400, 1),
    ("invalid date", 1),
])
@pytest.mark.parametrize("status", [200, 429])
async def test_retry_after_seconds_dates_and_invalid_values(clock, retry_after, expected, status):
    starts = []

    def handle(request):
        starts.append(clock.now)
        if len(starts) == 1:
            return httpx.Response(status, headers={"Retry-After": retry_after}, json={"code": "50011"})
        return httpx.Response(200, json=SUCCESS)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        client = ReadOnlyOKXClient(BASE_URL, client=http, max_retry_backoff=2)
        assert await client.get(PATH) == SUCCESS
    assert starts == pytest.approx([0, expected])


@pytest.mark.asyncio
async def test_rate_limit_extends_cooldown_for_already_waiting_caller(clock, monkeypatch):
    starts = []
    first_in_flight, release_first = asyncio.Event(), asyncio.Event()
    pacing_wait, release_pacing = asyncio.Event(), asyncio.Event()

    async def controlled_sleep(delay):
        if not pacing_wait.is_set():
            pacing_wait.set()
            await release_pacing.wait()
        await clock.sleep(delay)

    async def handle(request):
        caller = request.url.params["caller"]
        starts.append((caller, clock.now))
        if len(starts) == 1:
            first_in_flight.set()
            await release_first.wait()
            return httpx.Response(429, headers={"Retry-After": "3"})
        return httpx.Response(200, json=SUCCESS)

    monkeypatch.setattr(okx_client, "sleep", controlled_sleep)
    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        client = ReadOnlyOKXClient(BASE_URL, client=http)
        first = asyncio.create_task(client.get(PATH, {"caller": "a"}))
        await asyncio.wait_for(first_in_flight.wait(), timeout=1)
        second = asyncio.create_task(client.get(PATH, {"caller": "b"}))
        await asyncio.wait_for(pacing_wait.wait(), timeout=1)
        release_first.set()
        await asyncio.sleep(0)
        release_pacing.set()
        assert await asyncio.wait_for(asyncio.gather(first, second), timeout=1) == [SUCCESS, SUCCESS]
    assert [caller for caller, _ in starts] == ["a", "b", "a"]
    assert [when for _, when in starts] == pytest.approx([0, 3, 3.2])


@pytest.mark.asyncio
@pytest.mark.parametrize("payload,expected", [
    ({"code": "50113", "msg": "private-provider-value"}, "50113"),
    ({"code": "private-provider-value"}, "unknown"),
    ({"code": {"private-provider-value": "secret"}}, "unknown"),
    ({}, "unknown"),
    ([], "unknown"),
    (None, "unknown"),
])
async def test_other_api_errors_are_not_retried_or_echoed(clock, caplog, payload, expected):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, content=json.dumps(payload))

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        client = ReadOnlyOKXClient(BASE_URL, client=http)
        with pytest.raises(OKXError, match=f"^OKX API error {expected}$") as error:
            await client.get(PATH)
    assert len(requests) == 1
    assert "private-provider-value" not in str(error.value) + caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 503])
async def test_other_http_errors_are_not_retried(clock, status):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(status, json={"code": "50011"})

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await ReadOnlyOKXClient(BASE_URL, client=http).get(PATH)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_non_json_and_transport_errors_are_not_retried(clock):
    requests = []

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(200, text="private-provider-value")
        raise httpx.ReadTimeout("test timeout", request=request)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        client = ReadOnlyOKXClient(BASE_URL, client=http)
        with pytest.raises(OKXError, match="^OKX returned a non-JSON response$"):
            await client.get(PATH)
        assert len(requests) == 1
        with pytest.raises(httpx.ReadTimeout):
            await client.get(PATH)
    assert len(requests) == 2


@pytest.mark.parametrize("kwargs", [
    {"requests_per_second": 0}, {"requests_per_second": -1},
    {"requests_per_second": float("nan")}, {"requests_per_second": float("inf")},
    {"requests_per_second": 5e-324},
    {"max_retries": -1}, {"max_retries": 1.5}, {"max_retries": True},
    {"retry_backoff": 0}, {"retry_backoff": -1},
    {"retry_backoff": float("nan")}, {"retry_backoff": float("inf")},
    {"max_retry_backoff": 0.5}, {"max_retry_backoff": float("inf")},
    {"max_retry_backoff": float("nan")},
])
def test_rejects_invalid_rate_and_retry_settings(kwargs):
    with pytest.raises(ValueError):
        ReadOnlyOKXClient(BASE_URL, **kwargs)


@pytest.mark.asyncio
async def test_slow_configured_rate_is_respected(clock):
    async with httpx.AsyncClient(
        base_url=BASE_URL, transport=httpx.MockTransport(lambda _: httpx.Response(200, json=SUCCESS))
    ) as http:
        client = ReadOnlyOKXClient(BASE_URL, client=http, requests_per_second=0.05)
        await client.get(PATH)
        await client.get(PATH)
    assert clock.now == pytest.approx(20)


@pytest.mark.asyncio
async def test_invalid_requests_do_not_send_or_wait(clock):
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(200, json=SUCCESS)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        client = ReadOnlyOKXClient(BASE_URL, client=http)
        with pytest.raises(OKXError, match="without credentials"):
            await client.get(PATH, authenticated=True)
        with pytest.raises(ValueError, match="V5 API paths"):
            await client.get("https://example.com")
    with pytest.raises(RuntimeError, match="context manager"):
        await ReadOnlyOKXClient(BASE_URL).get(PATH)
    assert requests == []
    assert clock.sleeps == []


@pytest.mark.asyncio
async def test_collector_snapshot_uses_shared_pacing_and_get_only(clock, tmp_path):
    from app.collector import collect_once
    from app.database import initialize

    path = tmp_path / "monitor.db"
    initialize(path)
    requests, starts = [], []

    async def handle(request):
        requests.append(request)
        starts.append(clock.now)
        await asyncio.sleep(0)
        return httpx.Response(200, json=SUCCESS)

    async with httpx.AsyncClient(base_url=BASE_URL, transport=httpx.MockTransport(handle)) as http:
        client = ReadOnlyOKXClient(BASE_URL, OKXCredentials("key", "secret", "pass"), client=http)
        result = await collect_once(client, str(path))
    assert {request.url.path for request in requests} == {
        PATH, "/api/v5/account/positions", "/api/v5/trade/orders-pending",
        "/api/v5/trade/fills-history", "/api/v5/account/bills",
    }
    assert all(request.method == "GET" for request in requests)
    assert starts == pytest.approx([0, 0.2, 0.4, 0.6, 0.8])
    assert result["fills_received"] == result["fills_inserted"] == result["funding_bills_inserted"] == 0
