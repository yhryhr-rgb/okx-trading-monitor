# OKX Trading Monitor

A self-hosted, read-only monitoring and analytics service for OKX spot and derivatives accounts.

## Status

- Repository scaffold and V1/V2 ingestion/analytics building blocks are present
- The core API supports environment-backed credentials and validates official endpoints
- The optional VPS position worker supports OKX Global accounts with a verified read-only key
- Trading actions intentionally unsupported
- Dashboard should remain bound to `127.0.0.1` and be accessed through SSH tunneling or an authenticated private network

## Safety model

- The REST client implements HTTP GET only.
- The private WebSocket client implements login and read-only subscriptions only.
- There are no order placement, cancellation, transfer, withdrawal, or account-write methods.
- Use an API key with **Read** permission only and bind it to the VPS public IP where OKX supports IP binding.
- Never commit API keys, secrets, passphrases, Telegram bot tokens, account exports, SQLite databases, logs, backups, or SSH material.
- The app validates endpoint schemes and exact hostnames against official OKX endpoint allow-lists; arbitrary endpoints are rejected.
- Do not select an API host by guessing. Confirm the account's service entity/region and current OKX documentation first.
- This repository is public: all committed source and documentation are visible to everyone.

## Components

- `app/config.py`: server-side environment loading and offline/configured state
- `app/endpoints.py`: strict official OKX REST/private WebSocket hostname validation
- `app/okx_client.py`: signed, GET-only OKX V5 REST client
- `app/ws_private.py`: authenticated read-only private WebSocket subscriber
- `app/database.py`: SQLite schema and idempotent fill storage
- `app/main.py`: local FastAPI health/status and read-only API surface
- `tests/`: offline unit tests; no real credentials or exchange account access required

## REST rate limiting

Each `ReadOnlyOKXClient` spaces request starts across concurrent callers (5 per
second by default). Reuse one instance for a collection cycle; this limiter is
local to that instance and does not coordinate other processes or clients.

HTTP 429 and OKX code `50011` trigger a shared cooldown and up to 3 retries
(4 attempts total). Backoff starts at 1 second and doubles up to 30 seconds.
A valid `Retry-After` header, in seconds or HTTP-date form, can extend that wait
beyond the backoff cap. Every retry is paced and freshly signed. Waiting is
cancellable, and other HTTP, API, JSON, and transport errors are not retried.

Constructor keyword options `max_retries`, `retry_backoff`, and
`max_retry_backoff` control this policy; `max_retries=0` disables retries while
retaining the cooldown for subsequent callers. Rates and backoff values must
be finite and positive, and the backoff cap must be at least the initial delay.
The client continues to expose GET requests only.

See [OKX rate limits](https://app.okx.com/docs-v5/en/#overview-rate-limits)
and [HTTP Retry-After semantics](https://httpwg.org/specs/rfc9110.html#field.retry-after).

## Local development

Python 3.11+ is recommended.

    python -m venv .venv
    . .venv/bin/activate
    pip install -r requirements.txt
    pytest -q

Run the API locally:

    uvicorn app.main:app --host 127.0.0.1 --port 8765

The service starts in offline mode unless all three credentials and both explicitly verified regional endpoint URLs are configured. A `configured` status does not mean the account is connected; live connection and sync orchestration remain a separate step. Do not expose port 8765 directly to the public internet.

## Configuration

Copy `.env.example` to a private server-side `.env` file. Keep it out of Git and restrict its permissions. The example deliberately leaves credentials and endpoints blank. Confirm the account's actual OKX service entity before setting `OKX_BASE_URL` and `OKX_PRIVATE_WS_URL`; do not infer it from physical location, app language, or the generic "Other" label.

See [regional endpoint and live-connection gate](docs/REGION_AND_LIVE_CONNECTION.md).

## VPS deployment notes

The optional systemd services provide two independently installable monitors on Debian 12.

- **OKX positions**. A loopback dashboard on port 8766 shows positions, fills and alert history. QQ email alerts trigger at 10 USDT of unrealized profit per position. A separate timer emails a realized and unrealized P&L report at 08:00 Beijing time.
- **DMIT Los Angeles inventory**. A stock worker checks the VPSDeer public catalog every 30 seconds and emails available plans with official DMIT product links.

Each service takes its own mail credentials in a private VPS terminal. Installation, dashboard access, data sources and verification are documented in the [VPS deployment guide](docs/VPS_MONITORS.md).

## Analytics limitations

Performance metrics must be marked incomplete when imported trade/bill history is insufficient. Spot inventory accounting, derivatives contract multipliers, fees, funding, transfers, deposits, withdrawals, and multi-currency conversion must be handled explicitly before presenting a consolidated net PnL. Never infer missing records as zero.
