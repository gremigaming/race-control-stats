#!/usr/bin/env bash
# One-time installer for the Race Control bot on a fresh Debian/Ubuntu server.
# Run on the server with:
#   curl -fsSL https://raw.githubusercontent.com/gremigaming/race-control-stats/main/bot/setup_vm.sh | sudo bash
# Safe to run again (for example to change the keys).
set -euo pipefail

DIR=/opt/race-control
ENV_FILE=/etc/race-control.env
REPO=https://github.com/gremigaming/race-control-stats.git
# The Claude Code routine "Race Control: Discord tag alert"
ROUTINE_ID=trig_01CLWEFWpyN4G8xmHr77Mg4T

echo "== Installing Python and git"
apt-get update -qq
apt-get install -y -qq git python3 python3-venv >/dev/null

echo "== Downloading the bot"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull -q
else
  git clone -q "$REPO" "$DIR"
fi
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/bot/requirements.txt"

if [ "${1:-}" = "--api-key" ] && [ -s "$ENV_FILE" ]; then
  echo "== Claude API key (what you paste stays hidden)"
  read -rsp "Claude API key: " AKEY </dev/tty; echo
  sed -i '/^ANTHROPIC_API_KEY=/d' "$ENV_FILE"
  printf 'ANTHROPIC_API_KEY=%s\n' "$AKEY" >> "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

if [ ! -s "$ENV_FILE" ] || [ "${1:-}" = "--keys" ]; then
  echo "== Keys (what you paste stays hidden)"
  read -rsp "Discord bot token: " DTOKEN </dev/tty; echo
  read -rsp "Claude routine token: " RTOKEN </dev/tty; echo
  read -rsp "Claude API key for quick answers (press Enter to skip): " AKEY </dev/tty; echo
  umask 077
  printf 'DISCORD_BOT_TOKEN=%s\nROUTINE_FIRE_TOKEN=%s\nROUTINE_ID=%s\nANTHROPIC_API_KEY=%s\n' \
    "$DTOKEN" "$RTOKEN" "$ROUTINE_ID" "$AKEY" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi

echo "== Starting Race Control (and restarting it after reboots or crashes)"
cat > /etc/systemd/system/race-control.service <<UNIT
[Unit]
Description=Race Control Discord bot
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=$DIR
EnvironmentFile=$ENV_FILE
ExecStartPre=-/usr/bin/git -C $DIR pull -q
ExecStartPre=-$DIR/.venv/bin/pip install -q -r $DIR/bot/requirements.txt
ExecStart=$DIR/.venv/bin/python -m bot.race_control_bot
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT

# Pick up new code from GitHub every night at 04:00 UTC
cat > /etc/systemd/system/race-control-update.service <<UNIT
[Unit]
Description=Update Race Control
[Service]
Type=oneshot
ExecStart=/bin/systemctl restart race-control
UNIT
cat > /etc/systemd/system/race-control-update.timer <<UNIT
[Unit]
Description=Update Race Control nightly
[Timer]
OnCalendar=*-*-* 04:00:00
[Install]
WantedBy=timers.target
UNIT

systemctl daemon-reload
systemctl enable -q --now race-control-update.timer
systemctl enable -q race-control
systemctl restart race-control
sleep 8
if systemctl is-active -q race-control && journalctl -u race-control -n 20 --no-pager | grep -q "is online"; then
  echo "== Race Control is online. You can close this window."
else
  echo "== Something went wrong. Copy the lines below and send them to Claude (they contain no keys):"
  journalctl -u race-control -n 20 --no-pager
fi
