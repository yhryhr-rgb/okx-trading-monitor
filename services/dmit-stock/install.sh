#!/bin/bash
# Install a fresh stock monitor. SMTP is activated separately on this host.
set -euo pipefail
test "$(id -u)" = 0
src=$(cd "$(dirname "$0")" && pwd)
app=/opt/dmit-stock
etc=/etc/dmit-stock
unit=/etc/systemd/system/dmit-stock.service
cmd=/usr/local/bin/dmit-stock-mail-setup
for path in "$app" "$etc" "$unit" "$cmd"; do
    if [[ -e $path ]]; then echo "Existing installation: $path" >&2; exit 1; fi
done
command -v systemd-creds >/dev/null
install -d -m 0755 "$app"
install -d -m 0700 "$etc"
install -m 0644 "$src/stock.py" "$app/"
install -m 0755 "$src/mail_setup.py" "$cmd"
install -m 0644 "$src/dmit-stock.service" "$unit"
systemctl daemon-reload
systemd-analyze verify dmit-stock.service
systemctl enable --now dmit-stock.service
echo "Run dmit-stock-mail-setup in the private VPS terminal to activate email."
