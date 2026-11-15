"""What Race Control just sent, for GreMi's OBS dock (GreMi 2026-11-15).

The dock (race-stats public/overlay/gremi/dock.html) shows a small line in its chat
whenever Race Control posts: the text for Twitch and YouTube chat, and only
"Discord message sent in #channel" for Discord (GreMi doesn't need the whole post).
Discord posts are caught in the bot's on_message (every message the bot account
makes, also those sent by other scripts with the same token), so nothing else
has to call this for Discord. Staff log channels are left out.

The list lives on the server only and goes out in /dock.json as "sent".
"""
import json
import os
import pathlib
import time

STATE = pathlib.Path(os.environ.get("RACE_CONTROL_SENT", "/var/lib/race-control/sent_log.json"))
KEEP = 40          # entries kept
KEEP_SECS = 3600   # and only from the last hour
SKIP_CHANNELS = ("log", "change-request", "staff-command")   # staff noise, not useful on stream


def read(path=None):
    try:
        data = json.loads((path or STATE).read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def record(where, text="", channel="", now=None, path=None):
    """Adds one sent message. where: discord | twitch | youtube. Never raises."""
    try:
        now = now or time.time()
        path = path or STATE
        items = [e for e in read(path) if now - e.get("at", 0) < KEEP_SECS]
        last = items[-1]["id"] if items else 0
        items.append({"id": max(last + 1, int(now * 1000)),
                      "at": now, "where": where, "channel": channel[:60],
                      "text": "" if where == "discord" else " ".join(str(text).split())[:200]})
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items[-KEEP:]), encoding="utf-8")
        tmp.replace(path)
    except Exception:  # the dock line must never break sending
        pass


def skip_channel(name):
    name = (name or "").lower()
    return any(s in name for s in SKIP_CHANNELS)


def record_discord(message, now=None, path=None):
    """Called from on_message for the bot's own messages."""
    channel = getattr(message, "channel", None)
    if getattr(message, "guild", None) is None:
        name = "DM"
    else:
        name = getattr(channel, "name", "") or ""
        if skip_channel(name):
            return
        name = "#" + name
    record("discord", channel=name, now=now, path=path)


def recent(now=None, path=None):
    now = now or time.time()
    return [e for e in read(path) if now - e.get("at", 0) < KEEP_SECS]
