"""Pre-stream DM to GreMi, 1 hour before each Twitch stream (GreMi 2026-11-22).

The DM holds: the go-live notification lines to paste in Twitch, an F1 talk topic
and the challenge for the final race of the stream. Timing lives here in the
always-on bot, so it no longer depends on a scheduled Claude task firing at the
right minute. The content (news, topic, lines) is written to bot/prestream.json
on main each stream morning by the "Race Control: pre-stream prep" scheduled task
and read from GitHub, so it needs no restart. When that file is not for today the
DM still goes out with a standard line and a challenge from bot/challenges.json.

The logic at the top has no Discord code, so it can be tested on its own.
"""
import asyncio
import datetime
import json
import logging
import os
import pathlib

import discord

from bot.replies import TZ

HERE = pathlib.Path(__file__).parent
CONTENT_URL = "https://raw.githubusercontent.com/gremigaming/race-control-stats/main/bot/prestream.json"
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_PRESTREAM",
                                    "/var/lib/race-control/prestream.json"))
TWITCH = "twitch.tv/gremi_gaming"
LEAD = datetime.timedelta(minutes=60)
EARLY = datetime.timedelta(minutes=2)  # a tick may land just before the hour mark
EVERY = 60  # seconds between checks
MAX_TRIES = 3
FALLBACK_LINES = [
    "🤔 FAST OR CLEAN? MOST OF YOU CAN'T DO BOTH 😤🏁",
    "👀 THINK YOU'RE CLEANER THAN ME? PROVE IT 🏁🔥",
    "🏁 ZERO CONTACT, ZERO EXCUSES 😤 CAN YOU KEEP UP?",
    "😳 ONE MISTAKE AND IT'S OVER 🏎️ COULD YOU SURVIVE?",
]
log = logging.getLogger("race_control.prestream")


def load_challenges():
    return json.loads((HERE / "challenges.json").read_text(encoding="utf-8"))["challenges"]


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {"sent": {}, "used": {}, "tries": {}}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def event_key(event_id, start):
    """A rescheduled stream gets a new reminder."""
    return f"{event_id}@{start.isoformat()}"


def due(events, state, now):
    """Twitch streams starting within the hour that had no reminder yet.
    events: (id, start, name, location) with aware datetimes."""
    out = []
    for eid, start, name, location in events:
        if TWITCH not in (location or "").lower():
            continue
        key = event_key(eid, start)
        if key in state.get("sent", {}) or state.get("tries", {}).get(key, 0) >= MAX_TRIES:
            continue
        if now < start <= now + LEAD + EARLY:
            out.append((key, start, name))
    return sorted(out, key=lambda e: e[1])


def content_for(content, day):
    """The prep file's content if it was written for this Amsterdam day."""
    if isinstance(content, dict) and content.get("date") == day.isoformat():
        return content
    return {}


def pick_challenge(challenges, used, preferred=""):
    """The named challenge if it exists, else the one used longest ago (never used first)."""
    for c in challenges:
        if preferred and c["name"].lower() == preferred.lower():
            return c
    return min(challenges, key=lambda c: used.get(c["name"], 0))


def messages(name, start, content, challenge, day_number):
    """The DM: an intro block, then each go-live line on its own (easy to copy)."""
    local = start.astimezone(TZ)
    line_b = content.get("line_b") or FALLBACK_LINES[day_number % len(FALLBACK_LINES)]
    parts = [f"🔔 **Stream in 1 hour** ({local:%H:%M} {name})",
             "Go-live notification: hold a line below, tap Copy Text, paste it in "
             "Twitch Stream Manager > Edit Stream Info > Go Live Notification."]
    if content.get("news"):
        source = f" <{content['source']}>" if content.get("source") else ""
        parts.append(f"📰 News: {content['news']}{source}")
    topic = content.get("topic") or "No fresh F1 story today: ask chat for their hottest F1 take."
    parts.append(f"\n💬 **Talk topic:** {topic}")
    parts.append(f"\n🏆 **Final race challenge: {challenge['name']}.** {challenge['rules']}\n"
                 f"🎬 Why it clips: {challenge['clip']}")
    out = ["\n".join(parts)]
    if content.get("line_a"):
        out.append(content["line_a"])
    out.append(line_b)
    return out


class Runner:
    def __init__(self, bot, guild_id, owner_id):
        self.bot = bot
        self.guild_id = guild_id
        self.owner_id = owner_id
        self.state = load_state()

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception as e:  # never let the reminder take the bot down
                log.warning("pre-stream reminder failed: %s", e)
            await asyncio.sleep(EVERY)

    async def fetch_content(self):
        try:
            import aiohttp  # here, so the tests run without it
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
                async with s.get(CONTENT_URL) as r:
                    if r.status == 200:
                        return json.loads(await r.text())
        except Exception as e:
            log.warning("pre-stream content fetch failed: %s", e)
        try:  # the copy pulled with the code
            return json.loads((HERE / "prestream.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    async def tick(self):
        guild = self.bot.get_guild(self.guild_id)
        if guild is None:
            return
        events = [(e.id, e.start_time, e.name, e.location or getattr(e, "url", ""))
                  for e in guild.scheduled_events]
        now = datetime.datetime.now(datetime.timezone.utc)
        for key, start, name in due(events, self.state, now):
            day = start.astimezone(TZ).date()
            content = content_for(await self.fetch_content(), day)
            if content.get("already_sent"):
                self.state.setdefault("sent", {})[key] = "by hand"
                save_state(self.state)
                continue
            challenge = pick_challenge(load_challenges(), self.state.get("used", {}),
                                       content.get("challenge", ""))
            try:
                user = self.bot.get_user(self.owner_id) or await self.bot.fetch_user(self.owner_id)
                for text in messages(name, start, content, challenge, day.toordinal()):
                    await user.send(text)
            except discord.DiscordException as e:
                tries = self.state.setdefault("tries", {})
                tries[key] = tries.get(key, 0) + 1
                save_state(self.state)
                log.warning("pre-stream DM for %s failed (%s/%s): %s", name, tries[key], MAX_TRIES, e)
                continue
            self.state.setdefault("sent", {})[key] = now.isoformat()
            self.state.setdefault("used", {})[challenge["name"]] = now.timestamp()
            save_state(self.state)
            log.info("pre-stream DM sent for %s (%s)", name, challenge["name"])
