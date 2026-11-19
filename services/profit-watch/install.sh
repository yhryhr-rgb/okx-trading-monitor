#!/bin/bash
# Install a fresh dashboard, position worker and daily timer from a Git checkout.
set -euo pipefail
test "$(id -u)" = 0
src=$(cd "$(dirname "$0")" && pwd)
repo=$(git -C "$src" rev-parse --show-toplevel)
rev=$(git -C "$repo" rev-parse HEAD)
rel=/opt/okx-monitor-releases/$rev
app=/opt/okx-profit-watch
etc=/etc/okx-profit-watch
units=/etc/systemd/system
drop=$units/okx-monitor-rest.service.d/watch.conf
cmd=/usr/local/bin/okx-profit-setup
for path in "$rel" "$app" "$etc" "$cmd" "$drop" "$units/okx-monitor-rest.service" \
    "$units/okx-profit-watch.service" "$units/okx-profit-daily.service" "$units/okx-profit-daily.timer"; do
    if [[ -e $path ]]; then echo "Existing installation: $path" >&2; exit 1; fi
done
test -z "$(ss -H -lnt 'sport = :8766')"
read -r -p "QQ SMTP sender and recipient address: " recipient
[[ $recipient =~ ^[a-zA-Z0-9._%+-]+@qq\.com$ ]]
command -v systemd-creds >/dev/null
install -d -m 0755 "$rel"
git -C "$repo" archive "$rev" app requirements.txt services/profit-watch | tar -x -C "$rel"
src=$rel/services/profit-watch
python3 -m venv "$rel/venv"
"$rel/venv/bin/python" -m pip install -r "$rel/requirements.txt"
install -d -m 0755 "$app" "$etc" "$(dirname "$drop")"
install -d -m 0700 "$etc/private"
install -m 0644 "$src"/{configure,daily,portal,watch}.py "$app/"
printf '%s\n' "$recipient" >"$etc/recipient"
chmod 0644 "$etc/recipient"
echo 465 >"$etc/port"
sed "s|@RELEASE@|$rel|g" "$src/okx-monitor-rest.service" >"$units/okx-monitor-rest.service"
sed "s|@RELEASE@|$rel|g" "$src/worker.service" >"$units/okx-profit-watch.service"
sed "s|@RELEASE@|$rel|g" "$src/daily.service" >"$units/okx-profit-daily.service"
sed "s|@RELEASE@|$rel|g" "$src/portal.conf" >"$drop"
install -m 0644 "$src/daily.timer" "$units/okx-profit-daily.timer"
cat >"$cmd" <<EOF
#!/bin/sh
set -eu
export PYTHONPATH='$rel' PYTHONDONTWRITEBYTECODE=1
exec '$rel/venv/bin/python' '$app/configure.py'
EOF
chmod 0755 "$cmd"
systemctl daemon-reload
systemd-analyze verify okx-monitor-rest.service okx-profit-watch.service okx-profit-daily.service okx-profit-daily.timer
systemctl enable --now okx-monitor-rest.service okx-profit-watch.service okx-profit-daily.timer
echo "Run okx-profit-setup in the private VPS terminal to activate the worker."
