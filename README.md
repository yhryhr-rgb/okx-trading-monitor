# OKX Trading Monitor

A self-hosted, read-only monitoring and analytics service for OKX spot and derivatives accounts.

## Status

- Repository scaffold and V1/V2 ingestion/analytics building blocks are present
- Environment-backed credentials and strict official endpoint allow-list are available on the `feat/secure-region-config` branch
- Exchange connection remains disabled until the account's permitted OKX service entity and API host are confirmed
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

The existing deployment described during setup uses Debian 12, Python 3.11, systemd service `okx-monitor`, Uvicorn bound to `127.0.0.1:8765`, and SQLite under `/opt/okx-monitor/data/`.

The staging instance shown in the latest screenshots is separate and runs at `127.0.0.1:8766`. This branch does **not** deploy or modify either VPS service. Back up the running application and database before any deployment. Do not change unrelated proxy services or ports.

## Analytics limitations

Performance metrics must be marked incomplete when imported trade/bill history is insufficient. Spot inventory accounting, derivatives contract multipliers, fees, funding, transfers, deposits, withdrawals, and multi-currency conversion must be handled explicitly before presenting a consolidated net PnL. Never infer missing records as zero.
