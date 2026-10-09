#!/usr/bin/env bash
# One-time installer for the staff page on the Race Control server (after
# bot/setup_vm.sh). Run on the server with:
#   curl -fsSL https://raw.githubusercontent.com/gremigaming/race-control-stats/main/staff/setup_staff.sh | sudo bash
# Safe to run again; add --keys to type the keys again.
set -euo pipefail

DIR=/opt/race-control
ENV_FILE=/etc/race-control.env

if [ ! -d "$DIR/.git" ] || [ ! -s "$ENV_FILE" ]; then
  echo "== Install Race Control first (bot/setup_vm.sh)"; exit 1
fi
git -C "$DIR" pull -q

echo "== Installing Caddy (it gets the HTTPS certificate by itself)"
apt-get update -qq
if ! apt-get install -y -qq caddy >/dev/null 2>&1; then
  apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl gpg >/dev/null
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi

IP=$(curl -fs -H "Metadata-Flavor: Google" \
  http://metadata.google.internal/computeMetadata/v1/instance/network-interfaces/0/access-configs/0/external-ip || curl -fs https://api.ipify.org)
HOST="${IP//./-}.sslip.io"
URL="https://$HOST"

if ! grep -q '^STAFF_GITHUB_TOKEN=.' "$ENV_FILE" || [ "${1:-}" = "--keys" ]; then
  echo "== Keys (what you paste stays hidden)"
  read -rsp "Discord client secret (Race Control app, OAuth2 page): " CSECRET </dev/tty; echo
  read -rsp "GitHub token for race-stats (contents read and write): " GTOKEN </dev/tty; echo
  sed -i '/^DISCORD_CLIENT_SECRET=/d;/^STAFF_GITHUB_TOKEN=/d' "$ENV_FILE"
  printf 'DISCORD_CLIENT_SECRET=%s\nSTAFF_GITHUB_TOKEN=%s\n' "$CSECRET" "$GTOKEN" >> "$ENV_FILE"
fi
grep -q '^STAFF_SECRET=.' "$ENV_FILE" || printf 'STAFF_SECRET=%s\n' "$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> "$ENV_FILE"
sed -i '/^STAFF_URL=/d' "$ENV_FILE"
printf 'STAFF_URL=%s\n' "$URL" >> "$ENV_FILE"
chmod 600 "$ENV_FILE"
mkdir -p /var/lib/race-control

echo "== Starting the staff page"
cat > /etc/systemd/system/race-control-staff.service <<UNIT
[Unit]
Description=GreMi Gang staff page
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
EnvironmentFile=$ENV_FILE
ExecStartPre=-/usr/bin/git -C $DIR pull -q
ExecStart=$DIR/.venv/bin/python -m staff.app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
# the nightly update restarts the staff page too, so it picks up new code
mkdir -p /etc/systemd/system/race-control-update.service.d
printf '[Service]\nExecStart=/bin/systemctl restart race-control-staff\n' \
  > /etc/systemd/system/race-control-update.service.d/staff.conf

cat > /etc/caddy/Caddyfile <<CADDY
$HOST {
	reverse_proxy 127.0.0.1:8090
}
CADDY

systemctl daemon-reload
systemctl enable -q race-control-staff
systemctl restart race-control-staff
systemctl enable -q caddy
systemctl restart caddy
sleep 10

echo
if curl -fsS -o /dev/null --max-time 20 "$URL/"; then
  echo "== The staff page is up: $URL"
else
  echo "== Started, but $URL doesn't answer yet."
  echo "   Check that the server allows HTTP and HTTPS traffic (Google Cloud: VM > Edit > Firewalls),"
  echo "   then run this script again. If it still fails, send Claude these lines (they contain no keys):"
  journalctl -u race-control-staff -u caddy -n 15 --no-pager
fi
echo
echo "Add this redirect in the Discord developer portal (Race Control > OAuth2 > Redirects):"
echo "   $URL/callback"
