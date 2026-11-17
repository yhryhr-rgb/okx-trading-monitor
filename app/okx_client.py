"""Minimal read-only OKX V5 REST client.

Only GET is implemented intentionally. No order, cancel, transfer, withdrawal,
or account-write methods belong in this module.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

import httpx


class OKXError(RuntimeError):
    """Raised for transport or non-zero OKX API response codes."""


@dataclass(frozen=True)
class OKXCredentials:
    api_key: str
    api_secret: str
    passphrase: str


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


class ReadOnlyOKXClient:
    """GET-only REST client. Credentials are optional for public endpoints."""

    def __init__(
        self,
        base_url: str,
        credentials: OKXCredentials | None = None,
        timeout: float = 10.0,
        requests_per_second: float = 5.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("OKX base URL must use HTTPS")
        self.base_url = base_url.rstrip("/")
        self.credentials = credentials
        self.timeout = timeout
        self.interval = 1.0 / max(requests_per_second, 0.1)
        self._last_request = 0.0
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

    async def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        authenticated: bool = False,
    ) -> dict[str, Any]:
        if not path.startswith("/api/v5/"):
            raise ValueError("Only OKX V5 API paths are allowed")
        if self._client is None:
            raise RuntimeError("Use the client as an async context manager")

        query = _canonical_query(params)
        request_path = path + query
        headers: dict[str, str] = {}

        if authenticated:
            if self.credentials is None:
                raise OKXError("Private endpoint requested without credentials")
            timestamp = utc_timestamp()
            headers = {
                "OK-ACCESS-KEY": self.credentials.api_key,
                "OK-ACCESS-SIGN": make_signature(timestamp, "GET", request_path, self.credentials.api_secret),
                "OK-ACCESS-TIMESTAMP": timestamp,
                "OK-ACCESS-PASSPHRASE": self.credentials.passphrase,
            }

        wait = self.interval - (time.monotonic() - self._last_request)
        if wait > 0:
            import asyncio
            await asyncio.sleep(wait)

        self._last_request = time.monotonic()
        response = await self._client.get(request_path, headers=headers)
        response.raise_for_status()

        try:
            payload = response.json()
        except ValueError as exc:
            raise OKXError("OKX returned a non-JSON response") from exc

        if not isinstance(payload, dict) or payload.get("code") != "0":
            code = payload.get("code", "unknown") if isinstance(payload, dict) else "unknown"
            message = payload.get("msg", "unknown error") if isinstance(payload, dict) else "invalid response"
            raise OKXError(f"OKX API error {code}: {message}")

        return payload

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
