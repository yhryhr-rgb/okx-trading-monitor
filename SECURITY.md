# Security policy

## Scope

This project is designed for read-only account monitoring. It must not add exchange methods that place/cancel orders, transfer funds, withdraw assets, or modify account settings.

## API key requirements

1. Create an API key with **Read** permission only.
2. Do not grant Trade or Withdraw permissions.
3. Bind the key to the VPS public IP where supported.
4. Keep the key, secret, and passphrase in a server-side secret file or secret manager, never in GitHub, issue comments, screenshots, or chat.
5. Rotate/revoke a key immediately if it is exposed.

## Region and endpoint selection

The operator must confirm the account's permitted service entity/region and consult current official OKX API documentation before setting \`OKX_BASE_URL\`. Do not guess the region from the user's physical location or app language. Do not use network routing to evade regional restrictions.

## Deployment

- Bind the dashboard to loopback (\`127.0.0.1\`) by default.
- Use SSH port forwarding or an authenticated private access layer.
- Do not expose the FastAPI port directly to the public internet.
- Restrict environment-file permissions and redact credentials from logs.
- Back up SQLite before migrations and test migrations against a copy first.

## Reporting vulnerabilities

Do not publish credentials, account identifiers, private trade exports, or exploit details in public issues. Contact the repository owner privately.
