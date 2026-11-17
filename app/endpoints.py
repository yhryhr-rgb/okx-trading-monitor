"""Strict allow-list validation for official OKX API endpoints.

The account's service entity must still be confirmed by the operator. These
allow-lists prevent credentials from being sent to arbitrary HTTPS/WSS hosts;
they do not decide which region is permitted for a particular account.
"""
from __future__ import annotations

from urllib.parse import urlsplit


REST_HOSTS = frozenset({
    "openapi.okx.com",
    "us.okx.com",
    "eea.okx.com",
})

PRIVATE_WS_HOSTS = frozenset({
    "ws.okx.com",
    "wsus.okx.com",
    "wseea.okx.com",
})


def _validate_origin(value: str, *, scheme: str, allowed_hosts: frozenset[str],
                     expected_path: str | None = None) -> str:
    if not value or value != value.strip():
        raise ValueError("Endpoint URL is empty or contains surrounding whitespace")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Endpoint URL is malformed") from exc

    if parsed.scheme.lower() != scheme:
        raise ValueError(f"Endpoint must use {scheme}://")
    if parsed.username or parsed.password:
        raise ValueError("Endpoint URL must not contain user information")
    if not parsed.hostname or parsed.hostname.lower() not in allowed_hosts:
        raise ValueError("Endpoint host is not in the official OKX host allow-list")
    if port not in (None, 443):
        raise ValueError("Endpoint must use the default secure port 443")
    if parsed.query or parsed.fragment:
        raise ValueError("Endpoint URL must not contain a query or fragment")
    if expected_path is not None and parsed.path.rstrip("/") != expected_path:
        raise ValueError(f"Endpoint path must be {expected_path}")
    if expected_path is None and parsed.path not in ("", "/"):
        raise ValueError("REST base URL must not contain a path")
    return value.rstrip("/")


def validate_rest_base_url(value: str) -> str:
    """Validate a region-specific OKX REST origin."""
    return _validate_origin(
        value, scheme="https", allowed_hosts=REST_HOSTS, expected_path=None
    )


def validate_private_ws_url(value: str) -> str:
    """Validate a region-specific OKX private WebSocket endpoint."""
    return _validate_origin(
        value,
        scheme="wss",
        allowed_hosts=PRIVATE_WS_HOSTS,
        expected_path="/ws/v5/private",
    )
