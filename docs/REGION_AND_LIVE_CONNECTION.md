# Regional endpoint and live-connection gate

Do not infer an account's OKX service entity from physical location, app language, or the generic "Other" registration label.

Before live connection:

1. Confirm the account's actual contracting/service entity in the OKX account/app interface or with OKX support.
2. Read the current official API documentation for that entity.
3. Confirm both REST and private/public WebSocket hostnames for that entity.
4. Confirm API access is permitted for that account and jurisdiction.
5. Create a key with Read permission only, bind it to the VPS public IP, and keep credentials server-side.
6. Configure OKX_BASE_URL and OKX_PRIVATE_WS_URL only after these checks.
7. First run read-only balance/position calls and compare them against the app.

The app currently has no default regional API hostname and does not attempt to discover or bypass regional restrictions.

## WebSocket behavior

OKX private WebSocket uses a login request followed by read-only channel subscriptions. This project subscribes only to account, positions, orders, and balance/position channels. It sends WebSocket ping for keepalive and reconnects with bounded exponential backoff.

Do not use port 8443. The current OKX documentation's October 2026 notice says WebSocket connections on port 8443 have been discontinued; use the region-appropriate documented host on the default secure WebSocket port (443).

## Data correctness

WebSocket messages are a low-latency update stream, not a substitute for historical reconciliation. Periodically reconcile REST snapshots and paginated fills/bills. Store exchange timestamps in UTC, deduplicate fills by exchange + instrument + trade ID, and mark PnL/metrics incomplete until deposits, withdrawals, transfers, fees, funding, contract multipliers, and currency conversion are accounted for.
