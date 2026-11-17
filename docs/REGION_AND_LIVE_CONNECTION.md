# Regional endpoint and live-connection gate

Do not infer an account's OKX service entity from physical location, app language, or the generic "Other" registration label. Mainland China physical location does not, by itself, establish which OKX service entity an existing account uses or whether API access is permitted.

## Official production endpoint families

The current official OKX API guide identifies these production endpoint families:

| Account registration/service entity | REST base URL | Private WebSocket |
| --- | --- | --- |
| Standard/global service | `https://openapi.okx.com` | `wss://ws.okx.com/ws/v5/private` |
| US/AU accounts registered via `app.okx.com` | `https://us.okx.com` | `wss://wsus.okx.com/ws/v5/private` |
| EEA accounts registered via `my.okx.com` | `https://eea.okx.com` | `wss://wseea.okx.com/ws/v5/private` |

Official documentation:
- https://www.okx.com/docs-v5/en/
- https://my.okx.com/docs-v5/en/

The generic "Other" label is not enough to prove which service entity applies. Confirm the actual contracting/service entity shown for the account in the OKX app/account documents or with OKX support before selecting a row. Do not use network routing to evade regional restrictions.

## Configuration

Set both `OKX_BASE_URL` and `OKX_PRIVATE_WS_URL` explicitly in the server-side `.env`. The application validates the scheme, exact hostname, port, and WebSocket path against an allow-list of official OKX endpoints. It rejects arbitrary hosts to prevent API credentials being sent to an attacker-controlled endpoint.

The application remains in offline mode until all three credentials and both endpoint URLs are present. A `configured` status means only that configuration is complete; it does not mean an exchange login or data sync has succeeded.

## Before live connection

1. Confirm the account's actual contracting/service entity in the OKX account/app interface or with OKX support.
2. Confirm API access is permitted for that account and jurisdiction.
3. Create an API key with **Read** permission only. Do not grant Trade or Withdraw.
4. Bind the key to the VPS public IP where OKX supports IP binding.
5. Store the key, secret, and passphrase only in a server-side secret file or secret manager.
6. Restrict the environment file permissions (for example, root-owned mode 0640 with the service account granted only the access it needs).
7. First run read-only balance/position calls and compare them against the app.

## Authentication

Private REST requests use the four OKX access headers. The signature is Base64(HMAC-SHA256(timestamp + uppercase method + request path including query + body)). The timestamp is ISO 8601 UTC with millisecond precision; OKX rejects requests more than 30 seconds away from server time.

Private WebSocket login signs `timestamp + GET + /users/self/verify` using HMAC-SHA256 and Base64, then subscribes only to read-only account, positions, orders, and balance/position channels.

## WebSocket behavior

The private stream uses ping/pong keepalive and bounded exponential reconnect. Do not use port 8443; use the region-appropriate documented secure WebSocket endpoint on port 443.

## Data correctness

WebSocket messages are a low-latency update stream, not a substitute for historical reconciliation. Periodically reconcile REST snapshots and paginated fills/bills. Store exchange timestamps in UTC, deduplicate fills by exchange + instrument + trade ID, and mark PnL/metrics incomplete until deposits, withdrawals, transfers, fees, funding, contract multipliers, and currency conversion are accounted for.
