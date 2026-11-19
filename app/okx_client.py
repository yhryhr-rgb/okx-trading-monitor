"""Minimal read-only OKX V5 REST client.

Only GET is implemented intentionally. No order, cancel, transfer, withdrawal,
or account-write methods belong in this module.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import math
from asyncio import Lock, sleep
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from email.utils import parsedate_to_datetime
from time import monotonic
from typing import Any
from urllib.parse import urlencode

import httpx

from app.endpoints import validate_rest_base_url


class OKXError(RuntimeError):
    """Raised for transport or non-zero OKX API response codes."""


@dataclass(frozen=True)
class OKXCredentials:
    # repr=False prevents accidental credential disclosure in debug logs.
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)
    passphrase: str = field(repr=False)


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _canonical_query(params: dict[str, Any] | None) -> str:
    if not params:
        return ""
    clean = [(k, str(v)) for k, v in sorted(params.items()) if v is not None]
    return "?" + urlencode(clean) if clean else ""


def make_signature(timestamp: str, method: str, request_path: str, secret: str, body: str = "") -> str:
    """Return Base64(HMAC-SHA256(timestamp + method + path + body))."""
    message = f"{timestamp}{method.upper()}{request_path}{body}".encode()
    digest = hmac.new(secret.encode(), message, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _retry_after_seconds(response: httpx.Response) -> float:
    value = response.headers.get("Retry-After", "").strip()
    try:
        if value.isascii() and value.isdigit():
            delay = float(value)
        else:
            retry_at = parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                return 0.0
            delay = (retry_at - datetime.now(timezone.utc)).total_seconds()
    except (ValueError, TypeError, OverflowError):
        return 0.0
    return max(0.0, delay) if math.isfinite(delay) else 0.0


class ReadOnlyOKXClient:
    """GET-only client with per-instance pacing and bounded throttle retries.

    HTTP 429 and OKX 50011 share a cooldown across concurrent callers. Only
    these rate-limit failures are retried. Retry-After can extend the capped
    exponential backoff. Credentials are optional for public endpoints.
    """

    def __init__(
        self,
        base_url: str,
        credentials: OKXCredentials | None = None,
        timeout: float = 10.0,
        requests_per_second: float = 5.0,
        client: httpx.AsyncClient | None = None,
        *,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
        max_retry_backoff: float = 30.0,
    ) -> None:
        if not math.isfinite(requests_per_second) or requests_per_second <= 0:
            raise ValueError("requests_per_second must be finite and positive")
        if not math.isfinite(1.0 / requests_per_second):
            raise ValueError("requests_per_second is too small")
        if isinstance(max_retries, bool) or not isinstance(max_retries, int) or max_retries < 0:
            raise ValueError("max_retries must be a non-negative integer")
        if not math.isfinite(retry_backoff) or retry_backoff <= 0:
            raise ValueError("retry_backoff must be finite and positive")
        if not math.isfinite(max_retry_backoff) or max_retry_backoff < retry_backoff:
            raise ValueError("max_retry_backoff must be finite and at least retry_backoff")
        self.base_url = validate_rest_base_url(base_url)
        self.credentials = credentials
        self.timeout = timeout
        self.interval = 1.0 / requests_per_second
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.max_retry_backoff = max_retry_backoff
        self._last_request: float | None = None
        self._cooldown_until = 0.0
        self._rate_lock = Lock()
        self._client = client
        self._owns_client = client is None

    async def __aenter__(self) -> "ReadOnlyOKXClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={"User-Agent": "okx-trading-monitor/0.1"},
            )
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def _wait_for_slot(self) -> None:
        # Serialize reservation of send times, not network I/O. Recheck after
        # sleep because an in-flight response can extend the shared cooldown.
        async with self._rate_lock:
            while True:
                now = monotonic()
                next_request = now if self._last_request is None else self._last_request + self.interval
                wait = max(next_request, self._cooldown_until) - now
                if wait <= 0:
                    self._last_request = now
                    return
                await sleep(wait)

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        if not path.startswith("/api/v5/") or path.startswith("//"):
            raise ValueError("Only OKX V5 API paths are allowed")
        if self._client is None:
            raise RuntimeError("Use the client as an async context manager")

        query = _canonical_query(params)
        request_path = path + query
        if authenticated and self.credentials is None:
            raise OKXError("Private endpoint requested without credentials")

        backoff = self.retry_backoff
        for attempt in range(self.max_retries + 1):
            await self._wait_for_slot()
            headers: dict[str, str] = {}
            if authenticated:
                # Sign immediately before every send, including retries.
                timestamp = utc_timestamp()
                headers = {
                    "OK-ACCESS-KEY": self.credentials.api_key,
                    "OK-ACCESS-SIGN": make_signature(timestamp, "GET", request_path, self.credentials.api_secret),
                    "OK-ACCESS-TIMESTAMP": timestamp,
                    "OK-ACCESS-PASSPHRASE": self.credentials.passphrase,
                }

            response = await self._client.get(request_path, headers=headers)
            if response.status_code != 429:
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError as exc:
                    raise OKXError("OKX returned a non-JSON response") from exc

                if isinstance(payload, dict) and payload.get("code") == "0":
                    return payload
                code = payload.get("code") if isinstance(payload, dict) else None
                if code != "50011":
                    # Only echo a standard numeric code, never provider text.
                    if not isinstance(code, str) or len(code) != 5 or not code.isascii() or not code.isdigit():
                        code = "unknown"
                    raise OKXError(f"OKX API error {code}")

            delay = max(backoff, _retry_after_seconds(response))
            # No await between reading and extending this event-loop-local state.
            self._cooldown_until = max(self._cooldown_until, monotonic() + delay)
            if attempt == self.max_retries:
                raise OKXError("OKX rate limit retries exhausted")
            backoff = min(backoff * 2, self.max_retry_backoff)

    async def account_balance(self) -> dict[str, Any]:
        return await self.get("/api/v5/account/balance", authenticated=True)

    async def positions(self, inst_type: str | None = None) -> dict[str, Any]:
        return await self.get("/api/v5/account/positions", {"instType": inst_type}, authenticated=True)

    async def pending_orders(self, inst_type: str | None = None) -> dict[str, Any]:
        return await self.get("/api/v5/trade/orders-pending", {"instType": inst_type}, authenticated=True)

    async def fills_history(self, inst_type: str | None = None) -> dict[str, Any]:
        return await self.get("/api/v5/trade/fills-history", {"instType": inst_type}, authenticated=True)

    async def account_bills(self, inst_type: str | None = None) -> dict[str, Any]:
        return await self.get("/api/v5/account/bills", {"instType": inst_type}, authenticated=True)

    async def instruments(self, inst_type: str) -> dict[str, Any]:
        if inst_type not in {"SPOT", "SWAP", "FUTURES", "OPTION"}:
            raise ValueError("Unsupported instrument type")
        return await self.get("/api/v5/public/instruments", {"instType": inst_type})


def decimal_or_none(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None
