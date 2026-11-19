# Changelog

## Unreleased

- Fix REST request pacing for concurrent collection calls and sign requests after waiting.
- Retry HTTP 429 and OKX 50011 with bounded attempts, capped exponential backoff, and shared Retry-After cooldowns.
- Add offline regression tests for concurrency, fresh signatures, retry limits, cancellation, safe errors, and collector integration.
- Keep the REST surface GET-only; no live account access or VPS deployment performed.

## 0.2.0 - 2026-11-17

- Add read-only private WebSocket login and account/positions/orders/balance-position subscriptions.
- Add bounded reconnect behavior and ping/pong keepalive.
- Add REST snapshot collector for balances, positions, pending orders, fills, and bills.
- Add idempotent fill ingestion and funding-bill persistence foundation.
- Add completeness-aware trade metrics and drawdown helpers.
- Add tests for WebSocket signing, read-only channel selection, fill normalization, idempotency, and conservative analytics.
- Live connection remains disabled pending confirmation of the account's OKX service entity and region-specific REST/WebSocket endpoints.
- No real credentials or account data included; no VPS deployment performed.

## 0.1.0 - 2026-11-17

- Initialize public repository for the OKX read-only monitor.
- Add GET-only OKX V5 REST client skeleton and signature helper.
- Add SQLite idempotent fill storage foundation.
- Add offline-first FastAPI status endpoints.
- Add safe configuration template, ignore rules, and security guidance.
- No real account credentials or trading data included.
- No VPS deployment performed by this commit.
