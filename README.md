# OKX Trading Monitor

A self-hosted, read-only monitoring and analytics service for OKX spot and derivatives accounts.

## Status

- Repository scaffold initialized
- Exchange connection disabled until the account's permitted OKX service region and API host are verified
- Trading actions intentionally unsupported
- Dashboard should remain bound to \`127.0.0.1\` and be accessed through SSH tunneling or an authenticated private network

## Safety model

- The client only implements HTTP GET requests.
- There are no order placement, cancellation, transfer, withdrawal, or account-write methods.
- Use an API key with **Read** permission only and bind it to the VPS public IP where OKX supports IP binding.
- Never commit API keys, secrets, passphrases, Telegram bot tokens, account exports, SQLite databases, logs, backups, or SSH material.
- Do not select an API host by guessing. Confirm the account's service entity/region and current OKX documentation first.
- This repository is public: all committed source and documentation are visible to everyone.

## Components

- \`app/okx_client.py\`: signed, GET-only OKX V5 REST client
- \`app/database.py\`: SQLite schema and idempotent fill storage
- \`app/main.py\`: local FastAPI health/status and read-only API surface
- \`tests/\`: offline unit tests; no real credentials or network access required

## Local development

Python 3.11+ is recommended.

    python -m venv .venv
    . .venv/bin/activate
    pip install -r requirements.txt
    pytest -q

Run the API locally:

    uvicorn app.main:app --host 127.0.0.1 --port 8765

The service starts in offline mode unless valid credentials and an explicitly verified base URL are configured. Do not expose port 8765 directly to the public internet.

## Configuration

Copy \`.env.example\` to a private environment file on the server. Keep that file outside the repository and restrict its permissions (for example, root-owned mode 0640 with the service account granted only the access it needs).

\`OKX_BASE_URL\` is deliberately blank in the example. Set it only after confirming the correct official endpoint for the account's permitted service region.

## VPS deployment notes

The existing deployment described during setup uses Debian 12, Python 3.11, systemd service \`okx-monitor\`, Uvicorn bound to \`127.0.0.1:8765\`, and SQLite under \`/opt/okx-monitor/data/\`.

This repository commit does **not** deploy or modify the VPS. Back up the running application and database before any future deployment. Do not change unrelated proxy services or ports.

## Analytics limitations

Performance metrics must be marked incomplete when imported trade/bill history is insufficient. Spot inventory accounting, derivatives contract multipliers, fees, funding, transfers, deposits, withdrawals, and multi-currency conversion must be handled explicitly before presenting a consolidated net PnL. Never infer missing records as zero.
