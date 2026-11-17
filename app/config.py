"""Environment-backed settings for the offline-first monitor."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping
from dotenv import load_dotenv

from app.endpoints import validate_private_ws_url, validate_rest_base_url
from app.okx_client import OKXCredentials


PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Settings:
    credentials: OKXCredentials | None
    okx_base_url: str | None
    okx_private_ws_url: str | None
    app_host: str
    app_port: int
    database_path: Path
    log_level: str

    @property
    def api_configured(self) -> bool:
        return bool(self.credentials and self.okx_base_url and self.okx_private_ws_url)

    @property
    def mode(self) -> str:
        # Configured means credentials/endpoints exist; it does not mean connected.
        return "configured" if self.api_configured else "offline"


def load_settings(environ: Mapping[str, str] | None = None) -> Settings:
    """Load settings without ever logging or formatting secret values.

    When no mapping is supplied, read the private project-root .env file and
    then the process environment. Process environment variables take priority.
    """
    if environ is None:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        source: Mapping[str, str] = os.environ
    else:
        source = environ

    key = source.get("OKX_API_KEY", "").strip()
    secret = source.get("OKX_API_SECRET", "").strip()
    passphrase = source.get("OKX_API_PASSPHRASE", "").strip()
    supplied = (bool(key), bool(secret), bool(passphrase))

    if any(supplied) and not all(supplied):
        raise ValueError("OKX credentials are incomplete; all three values are required")

    base_raw = source.get("OKX_BASE_URL", "").strip()
    ws_raw = source.get("OKX_PRIVATE_WS_URL", "").strip()
    base_url = validate_rest_base_url(base_raw) if base_raw else None
    private_ws_url = validate_private_ws_url(ws_raw) if ws_raw else None

    credentials = OKXCredentials(key, secret, passphrase) if all(supplied) else None
    if credentials and not base_url:
        raise ValueError("OKX_BASE_URL must be explicitly set before enabling private API access")
    if credentials and not private_ws_url:
        raise ValueError("OKX_PRIVATE_WS_URL must be explicitly set before enabling private WebSocket access")

    host = source.get("APP_HOST", "127.0.0.1").strip()
    if host not in {"127.0.0.1", "::1", "localhost"}:
        raise ValueError("APP_HOST must remain loopback-only until an authenticated access layer is configured")

    try:
        port = int(source.get("APP_PORT", "8765"))
    except ValueError as exc:
        raise ValueError("APP_PORT must be an integer") from exc
    if not 1 <= port <= 65535:
        raise ValueError("APP_PORT must be between 1 and 65535")

    log_level = source.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        raise ValueError("LOG_LEVEL is invalid")

    return Settings(
        credentials=credentials,
        okx_base_url=base_url,
        okx_private_ws_url=private_ws_url,
        app_host=host,
        app_port=port,
        database_path=Path(source.get("DATABASE_PATH", "/opt/okx-monitor/data/monitor.db")),
        log_level=log_level,
    )
