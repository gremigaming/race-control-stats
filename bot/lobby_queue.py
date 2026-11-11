"""Lobby waitlist (GreMi 2026-11-11): chatters who want to race with GreMi.

Race Control reads Twitch and YouTube chat (twitch_chat.py, youtube_chat.py). When
someone shows they want to join the lobby ("can I join?", "how do I get an
invite", "mag ik meedoen") or types !join / !ea, they go on the waitlist and get
one answer with how to join. Nobody has to type a command. Someone already in
the last `size` entries is not added or answered again; once they drop out of
the last `size`, a new sign of interest adds them again (they may have come back).

The OBS lobby dock (bot/lobby_dock.html, served by the staff page at /dock)
shows the last `size` and reads TikTok chat itself on GreMi's PC (TikFinity),
with the same interest rules from /dock.json.

Settings: bot/lobby_queue.json (read live). Waitlist: STATE on the server only.
"""
import json
import os
import pathlib
import re
import time

HERE = pathlib.Path(__file__).resolve().parent
CONFIG = HERE / "lobby_queue.json"
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_WAITLIST", "/var/lib/race-control/lobby_waitlist.json"))
KEEP = 50   # entries kept on the server


def load_config(path=CONFIG):
    data = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    data.setdefault("size", 10)
    data.setdefault("keep_hours", 8)
    data.setdefault("command_cooldown", 60)
    data["commands"] = [c.lower() for c in data.get("commands", [])]
    return data


def interested(config, text):
    """Whether a chat line shows interest in joining the lobby: topic AND cue, NOT not."""
    ask = config.get("interest") or {}
    if not ask.get("topic") or not re.search(ask["topic"], text, re.I):
        return False
    if ask.get("cue") and not re.search(ask["cue"], text, re.I):
        return False
    return not (ask.get("not") and re.search(ask["not"], text, re.I))


def is_command(config, text):
    word = (text.split() or [""])[0].lower()
    return word in config["commands"]


def reply_text(config, game, name):
    texts = config.get("texts") or {}
    game = (game or "").lower()
    text = next((v for k, v in texts.items() if k != "default" and k in game), texts.get("default", ""))
    return text.replace("{name}", name or "").strip()


def key_of(platform, user):
    return f"{platform}:{(user or '').strip().lower()}"


def read_state(path=None):
    try:
        return json.loads((path or STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"entries": []}


def save_state(state, path=None):
    p = path or STATE
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        tmp.replace(p)
    except OSError:
        pass


def recent(config, entries, now):
    """The last `size` entries still fresh (newest last)."""
    fresh = [e for e in entries if now - e.get("at", 0) <= config["keep_hours"] * 3600]
    return fresh[-config["size"]:]


def add(config, state, platform, user, name, text, now):
    """Puts someone on the waitlist unless they are already in the last `size`. True when added."""
    entries = state.setdefault("entries", [])
    key = key_of(platform, user)
    if any(e.get("key") == key for e in recent(config, entries, now)):
        return False
    entries.append({"key": key, "platform": platform, "name": name or user, "at": now, "text": (text or "")[:120]})
    del entries[:-KEEP]
    return True


def handle(config, state, platform, user, name, text, game, now):
    """One chat line. Returns the answer to post, or None. Changes state (save it after)."""
    command = is_command(config, text)
    if not command and not interested(config, text):
        return None
    added = add(config, state, platform, user, name, text, now)
    answered = state.setdefault("answered", {})
    key = key_of(platform, user)
    if added or (command and now - answered.get(key, 0) > config["command_cooldown"]):
        answered[key] = now
        for k in [k for k, t in answered.items() if now - t > 86400]:
            answered.pop(k)
        return reply_text(config, game, name or user)
    return None


def chat_line(platform, user, name, text, game=None, now=None):
    """For the chat bridges: handles one line with the live settings and the saved waitlist.
    Returns (matched, answer): matched = it was a join line (skip other replies for it)."""
    try:
        config = load_config()
    except (OSError, ValueError):
        return False, None
    now = now or time.time()
    if not is_command(config, text) and not interested(config, text):
        return False, None
    state = read_state()
    answer = handle(config, state, platform, user, name, text, game, now)
    save_state(state)
    return True, answer


def public(state, config, now):
    """What the dock gets: the waitlist and the rules for TikTok."""
    return {"size": config["size"], "keep_hours": config["keep_hours"],
            "entries": [{k: e.get(k) for k in ("key", "platform", "name", "at", "text")}
                        for e in recent(config, state.get("entries", []), now)],
            "commands": config["commands"], "interest": config.get("interest", {}),
            "tiktok_action": config.get("tiktok_action", ""), "now": now}
