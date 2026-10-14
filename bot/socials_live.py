"""Live follower and viewer numbers for GreMi's OBS stats overlay (/socials on the staff page).

Runs next to the Discord bot and writes STATE every TICK seconds; staff/app.py serves it as
/socials.json and the overlay (socials_overlay.html) as /socials.

  Twitch followers + viewers   the Race Control Twitch login (twitch_chat.BOT)
  YouTube subscribers          GreMi's YouTube login (youtube_chat.YT), rounded by YouTube
  YouTube viewers              the same login, summed over every live broadcast (horizontal + vertical)
  TikTok followers             the TikTok stat channel name, kept fresh by stats.yml (TikTok login lives
                               in GitHub, not on this server)
  TikTok viewers               not here: the overlay reads them from TikFinity on GreMi's PC (local websocket)
"""

import asyncio
import json
import logging
import os
import pathlib
import time
import unicodedata

from bot import twitch_chat, youtube_chat

HERE = pathlib.Path(__file__).resolve().parent
OVERLAY = HERE / "socials_overlay.html"
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_SOCIALS", "/var/lib/race-control/socials_live.json"))
TICK = 30             # seconds between viewer checks
FOLLOWERS_EVERY = 120  # Twitch followers
SUBS_EVERY = 600       # YouTube subscribers (YouTube only changes them in steps)
STALE = 300            # a viewer count older than this counts as not live

log = logging.getLogger("race_control.socials")


def digits(text):
    """1645 from "🟣┃𝗧𝗪𝗜𝗧𝗖𝗛: 𝟭,𝟲𝟰𝟱" (the stat channels use bold maths digits)."""
    out = "".join(str(unicodedata.digit(ch)) for ch in text.split(":")[-1]
                  if unicodedata.digit(ch, None) is not None)
    return int(out) if out else None


def channel_count(guild, word):
    """The number in the STATS voice channel whose name holds word (in bold caps)."""
    marker = "".join(chr(0x1D5D4 + ord(c) - ord("A")) for c in word.upper())
    for ch in getattr(guild, "voice_channels", []):
        if marker in ch.name and ":" in ch.name:
            return digits(ch.name)
    return None


def public(state, now):
    """What the overlay gets: numbers, and live viewers only while they are fresh."""
    out = {"now": now, "followers": {}, "viewers": {}}
    for key, value in (state.get("followers") or {}).items():
        if value is not None:
            out["followers"][key] = value
    for key, item in (state.get("viewers") or {}).items():
        if item and item.get("count") is not None and now - item.get("at", 0) < STALE:
            out["viewers"][key] = item["count"]
    return out


def read_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state))
    tmp.replace(STATE)


async def twitch_followers(tw):
    res = await tw.call("GET", "/channels/followers", params={"broadcaster_id": tw.broadcaster, "first": "1"})
    return int(res["total"])


async def twitch_viewers(tw):
    """Viewers now, or None when offline."""
    data = (await tw.call("GET", "/streams", params={"user_id": tw.broadcaster}))["data"]
    return int(data[0]["viewer_count"]) if data else None


async def youtube_subscribers(yt):
    items = (await yt.call("GET", "/channels", params={"part": "statistics", "mine": "true"})).get("items", [])
    if not items or items[0]["statistics"].get("hiddenSubscriberCount"):
        return None
    return int(items[0]["statistics"]["subscriberCount"])


async def youtube_viewers(yt):
    """Viewers summed over every live broadcast, or None when not live."""
    ids = getattr(yt, "live_ids", [])
    if not ids:
        return None
    items = (await yt.call("GET", "/videos", params={"part": "liveStreamingDetails",
                                                      "id": ",".join(ids)})).get("items", [])
    counts = [int(i["liveStreamingDetails"]["concurrentViewers"]) for i in items
              if (i.get("liveStreamingDetails") or {}).get("concurrentViewers") is not None]
    return sum(counts) if counts else None


async def run(bot, guild_id):
    """Never raises; problems go to staff LOGS once per distinct line."""
    state = read_state()
    state.setdefault("followers", {})
    state.setdefault("viewers", {})
    checked = {"twitch": 0.0, "youtube": 0.0}
    while True:
        now = time.time()
        steps = []
        tw, yt = twitch_chat.BOT, youtube_chat.YT
        if tw and tw.broadcaster:
            if now - checked["twitch"] >= FOLLOWERS_EVERY:
                steps.append(("Twitch followers", twitch_followers(tw), "followers", "twitch", "twitch"))
            steps.append(("Twitch viewers", twitch_viewers(tw), "viewers", "twitch", None))
        if yt:
            if now - checked["youtube"] >= SUBS_EVERY:
                steps.append(("YouTube subscribers", youtube_subscribers(yt), "followers", "youtube", "youtube"))
            steps.append(("YouTube viewers", youtube_viewers(yt), "viewers", "youtube", None))
        for name, call, kind, key, mark in steps:
            try:
                value = await call
            except Exception as e:
                await twitch_chat.report(f"stats overlay: {name} failed ({e})")
                continue
            if mark:
                checked[mark] = now
            if kind == "followers":
                if value is not None:
                    state["followers"][key] = value
            else:
                state["viewers"][key] = {"count": value, "at": now}
        guild = bot.get_guild(guild_id)
        tiktok = channel_count(guild, "tiktok") if guild else None
        if tiktok is not None:
            state["followers"]["tiktok"] = tiktok
        state["updated"] = now
        try:
            save_state(state)
        except OSError as e:
            await twitch_chat.report(f"stats overlay: can't save ({e})")
        await asyncio.sleep(TICK)
