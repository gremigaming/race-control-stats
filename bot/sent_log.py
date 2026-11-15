"""What Race Control just sent, for GreMi's OBS dock (GreMi 2026-11-15).

The dock (race-stats public/overlay/gremi/dock.html) shows a small line in its chat
whenever Race Control posts in Twitch chat (timers like Simgasm, Discord, join answers).
GreMi only wants the Twitch chat messages, not Discord posts (2026-11-15).
YouTube sends are logged too, but the dock leaves them out (same text as Twitch).

The list lives on the server only and goes out in /dock.json as "sent".
"""
import json
import os
import pathlib
import re
import time

HERE = pathlib.Path(__file__).resolve().parent
NAMES = {"names": "Show names", "nlr": "Next Level Racing", "moza": "MOZA", "subs": "Sub"}

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


def _bits(template):
    """The fixed parts of a message template ({name} etc. left out), longest first."""
    return sorted((b.strip() for b in re.split(r"\{[^}]*\}", template or "")), key=len, reverse=True)


def label_for(text):
    """Which message this is, as GreMi knows it: "Simgasm", "Discord", "Join", "Ad", ...
    GreMi only wants to see that one went out, not its text (2026-11-15)."""
    text = text or ""
    found = []
    try:
        timers = json.loads((HERE / "twitch_timers.json").read_text(encoding="utf-8"))
        for t in timers.get("timers", []):
            name = NAMES.get(t.get("id"), str(t.get("id", "")).capitalize())
            found += [(name, t.get(k)) for k in ("text", "ask_text")]
        ads = timers.get("ads") or {}
        found += [("Ad", ads.get(k)) for k in ("notice_race", "notice_lobby")]
    except (OSError, ValueError):
        pass
    try:
        queue = json.loads((HERE / "lobby_queue.json").read_text(encoding="utf-8"))
        found += [("Join", v) for v in (queue.get("texts") or {}).values()]
    except (OSError, ValueError):
        pass
    for name, template in found:
        bit = next(iter(_bits(template)), "")
        if len(bit) >= 8 and bit[:60] in text:
            return name
    if re.search(r"giveaway", text, re.I):
        return "Giveaway"
    return "Race Control"


def record(where, text="", channel="", now=None, path=None):
    """Adds one sent message. where: discord | twitch | youtube. Never raises."""
    try:
        now = now or time.time()
        path = path or STATE
        items = [e for e in read(path) if now - e.get("at", 0) < KEEP_SECS]
        last = items[-1]["id"] if items else 0
        items.append({"id": max(last + 1, int(now * 1000)),
                      "at": now, "where": where, "channel": channel[:60],
                      "label": "" if where == "discord" else label_for(text),
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
