"""Read-only OKX private WebSocket subscriber.

Only login and subscribe operations are sent. No order or account mutation
operations are implemented. Supply a region-verified wss URL explicitly.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, AsyncIterator

import websockets

from app.endpoints import validate_private_ws_url
from app.okx_client import OKXCredentials

log = logging.getLogger(__name__)

READ_ONLY_CHANNELS = (
    {"channel": "account"},
    {"channel": "positions", "instType": "ANY"},
    {"channel": "orders", "instType": "ANY"},
    {"channel": "balance_and_position"},
)


@dataclass(frozen=True)
class WSConfig:
    url: str
    ping_interval_seconds: int = 15
    pong_timeout_seconds: int = 10
    reconnect_min_seconds: float = 1.0
    reconnect_max_seconds: float = 30.0

    def __post_init__(self) -> None:
        validate_private_ws_url(self.url)


def make_ws_login(credentials: OKXCredentials, epoch_seconds: str | None = None) -> dict[str, Any]:
    timestamp = epoch_seconds or str(int(time.time()))
    message = f"{timestamp}GET/users/self/verify".encode()
    digest = hmac.new(credentials.api_secret.encode(), message, hashlib.sha256).digest()
    signature = base64.b64encode(digest).decode()
    return {"op": "login", "args": [{
        "apiKey": credentials.api_key,
        "passphrase": credentials.passphrase,
        "timestamp": timestamp,
        "sign": signature,
    }]}


class PrivateReadOnlyStream:
    """Reconnectable async stream of private account/order/position messages."""

    def __init__(self, config: WSConfig, credentials: OKXCredentials,
                 channels: tuple[dict[str, str], ...] = READ_ONLY_CHANNELS) -> None:
        if any("channel" not in item for item in channels):
            raise ValueError("Each subscription must specify a channel")
        self.config = config
        self.credentials = credentials
        self.channels = channels
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def listen(self) -> AsyncIterator[dict[str, Any]]:
        delay = self.config.reconnect_min_seconds
        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    self.config.url, ping_interval=None, close_timeout=5,
                    max_size=4 * 1024 * 1024,
                ) as ws:
                    await ws.send(json.dumps(make_ws_login(self.credentials)))
                    login = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                    if login.get("event") != "login" or login.get("code") != "0":
                        raise RuntimeError("OKX private WebSocket login failed")

                    await ws.send(json.dumps({"op": "subscribe", "args": list(self.channels)}))
                    delay = self.config.reconnect_min_seconds
                    last_rx = time.monotonic()

                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=self.config.ping_interval_seconds)
                            last_rx = time.monotonic()
                            if raw == "pong":
                                continue
                            message = json.loads(raw)
                            if message.get("event") == "error":
                                raise RuntimeError(f"OKX WebSocket subscription error {message.get('code', 'unknown')}")
                            yield message
                        except asyncio.TimeoutError:
                            if time.monotonic() - last_rx >= self.config.ping_interval_seconds:
                                await ws.send("ping")
                                try:
                                    pong = await asyncio.wait_for(ws.recv(), timeout=self.config.pong_timeout_seconds)
                                except asyncio.TimeoutError as exc:
                                    raise RuntimeError("OKX WebSocket pong timeout") from exc
                                if pong != "pong":
                                    yield json.loads(pong)
                                last_rx = time.monotonic()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Do not log exception contents; they may contain provider data.
                log.warning("Private WebSocket disconnected (%s); reconnecting", type(exc).__name__)
                if self._stop.is_set():
                    break
                await asyncio.sleep(delay)
                delay = min(delay * 2, self.config.reconnect_max_seconds)
