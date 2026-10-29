#!/usr/bin/env bash
# One-time setup of the Live Race Viewer server (Hetzner CX23, Ubuntu 24.04).
# Run it as root on the server, after logging in with your SSH key:
#   curl -fsSL https://raw.githubusercontent.com/gremigaming/race-control-stats/main/live/bootstrap.sh | bash
#
# It locks the server down (key-only SSH, firewall, fail2ban, automatic security
# updates), installs Caddy (HTTPS for live.gremigaming.com) and the race-live
# service from this repo, and asks for the three Cloudflare R2 values. The server
# pulls new code from GitHub by itself every 2 minutes, so nobody needs to log in
# again to update it. Safe to run again; add --keys to type the R2 values again.
set -euo pipefail

REPO=https://github.com/gremigaming/race-control-stats.git
DIR=/opt/race-live
DATA=/var/lib/race-live
ENV_FILE=/etc/race-live.env
HOST=live.gremigaming.com
USER_NAME=race-live

if [ "$(id -u)" != 0 ]; then echo "== Run this as root (log in with: ssh root@<server IP>)"; exit 1; fi

echo "== Checking that your SSH key is in place before password logins are switched off"
if ! grep -qE '^(ssh-|ecdsa-)' /root/.ssh/authorized_keys 2>/dev/null; then
  echo "   No SSH key found for root. Add your key in the Hetzner console (Rebuild with the key ticked) first."
  exit 1
fi

echo "== Updating the system (a few minutes the first time)"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get -y -qq -o Dpkg::Options::=--force-confold upgrade >/dev/null
apt-get install -y -qq git python3-venv ufw fail2ban unattended-upgrades caddy curl >/dev/null

echo "== Security: key-only SSH, firewall, fail2ban, automatic security updates"
cat > /etc/ssh/sshd_config.d/01-race-live.conf <<'SSHD'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin prohibit-password
SSHD
sshd -t && systemctl reload ssh
ufw allow OpenSSH >/dev/null
ufw allow 80/tcp >/dev/null
ufw allow 443/tcp >/dev/null
ufw --force enable >/dev/null
systemctl enable -q --now fail2ban
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
  > /etc/apt/apt.conf.d/20auto-upgrades

echo "== The race-live service"
id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home "$DATA" --shell /usr/sbin/nologin "$USER_NAME"
mkdir -p "$DATA"
if [ ! -d "$DIR/.git" ]; then git clone -q "$REPO" "$DIR"; else sudo -u "$USER_NAME" git -C "$DIR" pull -q; fi
chown -R "$USER_NAME:$USER_NAME" "$DIR" "$DATA"
sudo -u "$USER_NAME" python3 -m venv "$DIR/.venv"
sudo -u "$USER_NAME" "$DIR/.venv/bin/pip" install -q --upgrade pip
sudo -u "$USER_NAME" "$DIR/.venv/bin/pip" install -q -r "$DIR/live/requirements.txt"

touch "$ENV_FILE"
if ! grep -q '^R2_SECRET_ACCESS_KEY=.' "$ENV_FILE" || [ "${1:-}" = "--keys" ]; then
  echo "== Cloudflare R2 (what you paste stays hidden; right-click pastes)"
  read -rp  "R2 Account ID (Cloudflare > R2 > right-hand side): " R2ACC </dev/tty
  read -rsp "R2 Access Key ID: " R2KEY </dev/tty; echo
  read -rsp "R2 Secret Access Key: " R2SECRET </dev/tty; echo
  sed -i '/^R2_ACCOUNT_ID=/d;/^R2_ACCESS_KEY_ID=/d;/^R2_SECRET_ACCESS_KEY=/d' "$ENV_FILE"
  printf 'R2_ACCOUNT_ID=%s\nR2_ACCESS_KEY_ID=%s\nR2_SECRET_ACCESS_KEY=%s\n' "$R2ACC" "$R2KEY" "$R2SECRET" >> "$ENV_FILE"
fi
NEW_TOKEN=0
if ! grep -q '^INGEST_TOKEN=.' "$ENV_FILE"; then
  printf 'INGEST_TOKEN=%s\n' "$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')" >> "$ENV_FILE"
  NEW_TOKEN=1
fi
grep -q '^DATA_DIR=' "$ENV_FILE" || printf 'DATA_DIR=%s\nPORT=8100\n' "$DATA" >> "$ENV_FILE"
chown root:"$USER_NAME" "$ENV_FILE"
chmod 640 "$ENV_FILE"

cat > /etc/systemd/system/race-live.service <<UNIT
[Unit]
Description=GreMi Gang live race server
After=network-online.target
Wants=network-online.target

[Service]
User=$USER_NAME
WorkingDirectory=$DIR
EnvironmentFile=$ENV_FILE
ExecStart=$DIR/.venv/bin/python -m live.server
Restart=always
RestartSec=5
NoNewPrivileges=true
ProtectSystem=strict
ReadWritePaths=$DATA
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT

# new code from GitHub every 2 minutes; the service restarts only when live/ changed
cat > /usr/local/bin/race-live-update <<UPDATE
#!/usr/bin/env bash
set -euo pipefail
cd $DIR
before=\$(sudo -u $USER_NAME git rev-parse HEAD)
sudo -u $USER_NAME git pull -q --ff-only
if [ -n "\$(sudo -u $USER_NAME git diff --name-only "\$before" HEAD -- live/)" ]; then
  sudo -u $USER_NAME $DIR/.venv/bin/pip install -q -r live/requirements.txt
  systemctl restart race-live
fi
UPDATE
chmod 755 /usr/local/bin/race-live-update
cat > /etc/systemd/system/race-live-update.service <<'UNIT'
[Unit]
Description=Pull new race-live code from GitHub

[Service]
Type=oneshot
ExecStart=/usr/local/bin/race-live-update
UNIT
cat > /etc/systemd/system/race-live-update.timer <<'UNIT'
[Unit]
Description=Pull new race-live code every 2 minutes

[Timer]
OnBootSec=1min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
UNIT

cat > /etc/caddy/Caddyfile <<CADDY
$HOST {
	reverse_proxy 127.0.0.1:8100
}
CADDY

systemctl daemon-reload
systemctl enable -q --now race-live race-live-update.timer
systemctl restart race-live
systemctl enable -q caddy
systemctl restart caddy

echo "== Waiting for the HTTPS certificate (up to a minute)"
ok=0
for _ in $(seq 1 12); do
  sleep 5
  if curl -fsS -o /dev/null --max-time 10 "https://$HOST/health"; then ok=1; break; fi
done

echo
if [ "$ok" = 1 ]; then
  echo "== Done: https://$HOST/status answers."
else
  echo "== Installed, but https://$HOST doesn't answer yet. Most likely the certificate:"
  echo "   in Cloudflare DNS, click the orange cloud of 'live' to make it grey (DNS only),"
  echo "   wait a minute, run this script again, then switch the cloud back to orange."
  echo "   Still stuck? Send Claude these lines (they contain no keys):"
  journalctl -u race-live -u caddy -n 15 --no-pager || true
fi
if [ "$NEW_TOKEN" = 1 ]; then
  echo
  echo "== Live token for the Race Uploader on your racing PC (shown once, keep it private):"
  echo "   $(grep '^INGEST_TOKEN=' "$ENV_FILE" | cut -d= -f2)"
  echo "   The uploader asks for it the next time it starts. To see it again later:"
  echo "   ssh root@<server IP> grep INGEST_TOKEN $ENV_FILE"
fi
