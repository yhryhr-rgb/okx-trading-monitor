# VPS monitors

The two services can run independently on Debian 12 with systemd. Use a root-owned checkout of the published commit and a private terminal on the VPS. The installers are for fresh installations and stop if their target files already exist. Publishing to GitHub does not deploy an update to a running host.

## OKX positions and daily reports

Install `git`, `python3-venv`, `iproute2` and `ca-certificates`. Confirm that the account uses the OKX Global endpoint supported by this worker. The monitor checks the account's read-only permission before collection. In the repository root, run:

```sh
bash services/profit-watch/install.sh
okx-profit-setup
```

Enter the QQ sender/recipient address when installing. During activation enter the OKX read-only key, secret, passphrase and QQ SMTP authorization code at the hidden prompts. The default SMTP port is 465. To use STARTTLS on 587, set `/etc/okx-profit-watch/port` to `587` before activation. The worker and daily reporter receive host-encrypted systemd credentials. The web dashboard reads SQLite state and does not load the account credential.

The worker samples open positions every 30 seconds. One position reaching **10 USDT** of unrealized profit starts an alert. A confirmed drop below 10 USDT or closure ends the episode. Where available, fresh OKX USDT index prices convert other settlement currencies. The dashboard also shows smaller positive P&L events and records QQ SMTP acceptance.

At **08:00 Asia/Shanghai**, the timer reports on the previous local calendar day. Realized P&L uses paginated position bills with signed P&L, fees, funding and supported settlement adjustments. The email breaks out settlement currencies and labels USDT conversions at report time. Spot purchases, transfers and account-level costs are outside this measure. Unrealized P&L uses the final saved position sample for the reporting day. The email gives the sample time and flags an early final sample or a fallback to current positions. Missing valuations are identified in the report. Daily drafts and sent dates survive service restarts.

```sh
systemctl is-active okx-monitor-rest okx-profit-watch okx-profit-daily.timer
systemctl list-timers --all okx-profit-daily.timer
curl -fsS http://127.0.0.1:8766/health
```

To send the latest due report after activation, run `systemctl start okx-profit-daily.service`. Already recorded deliveries are skipped. SQLite state is in `/var/lib/okx-monitor-rest/`. Back up that directory and the host credential configuration privately before changing an existing installation.

### Open the private dashboard

Use a verified SSH host alias in place of `my-vps`. On your own computer, keep the following tunnel open and visit [the local dashboard](http://127.0.0.1:18766/).

```sh
ssh -N -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes -L 127.0.0.1:18766:127.0.0.1:8766 my-vps
```

On Windows, `services/profit-watch/Open-OKX-Dashboard.ps1 -SshTarget my-vps` opens the tunnel and browser together.

## DMIT Los Angeles inventory

The standalone service needs Python 3, systemd and trusted CA certificates. From the repository root, run:

```sh
bash services/dmit-stock/install.sh
dmit-stock-mail-setup
```

The second command prompts for a QQ address and its dedicated SMTP authorization code, verifies login and restarts the worker. Inventory polling starts before mail setup and the first configured poll reports plans that are still available. This service uses its own systemd identity, credential and SQLite state under `/var/lib/dmit-stock/`.

The worker checks the public [VPSDeer DMIT catalog](https://vpsdeer.com/merchants/dmit) every 30 seconds. It accepts fresh, explicit stock observations for active US plans with a `LAX.` name. It sends once on availability and rearms after a confirmed out-of-stock observation. Failures trigger backoff. Detection depends on the catalog's own probe time and coverage. Use the official DMIT link in the email to confirm current availability.

```sh
systemctl is-active dmit-stock
systemctl is-enabled dmit-stock
journalctl -u dmit-stock --grep='Mail accepted for' -n 5
```

## Offline tests

With the root `requirements.txt` installed, run `python -m pytest -q` from the repository root. Tests use temporary databases, mock exchange responses and simulated SMTP. They require no account or mail credentials.
