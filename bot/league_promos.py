"""League promos in self promotion (GreMi 2026-10-20).

When someone advertises a league in 📣┃self-promotion, Race Control replies
that it belongs in 🏆┃community-leagues (the way GreMi sent Romijn there) and
removes the original message 10 minutes after it was posted. Members without
the League Racer role are told how to get it, since only that role sees the
forum.

Detection is a cheap keyword score, no Claude call: a Discord invite plus one
league word, or a few league words, counts as a league promo. A stream or clip
link with "league race" next to it does not. Staff are never touched.
The removals still to do are kept on disk (STATE), so a restart doesn't lose
them. The scoring at the top has no Discord code, so it can be tested on its own.
"""
import asyncio
import json
import logging
import os
import pathlib
import re
import time

import discord

PROMO_ID = 1079924693890510962  # 📣┃self-promotion
LEAGUES_ID = 1532005211235684362  # 🏆┃community-leagues (forum)
LEAGUE_RACER_ID = 1556952638413742180  # the only role that sees the forum
DELETE_AFTER = 10 * 60  # seconds after the promo was posted
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_LEAGUE_PROMOS",
                                    "/var/lib/race-control/league_promos.json"))
EVERY = 20  # seconds between checks for removals that are due
log = logging.getLogger("race_control.league_promos")

# ---------------------------------------------------------------- the score

INVITE = re.compile(r"(discord\.gg|discord(app)?\.com/invite)/\w+", re.I)
STRONG = re.compile(r"\bleagues?\b|\bchampionships?\b|\bseason \d|\bteam principals?\b", re.I)
WORDS = [re.compile(p, re.I) for p in (
    r"\btiers?\b", r"\bseason\b", r"\bsign ?ups?\b|\bsigning up\b|\bregist",
    r"\brecruit", r"\blooking for (clean |fast |new |more )?(drivers|racers|members)",
    r"\bdrivers (wanted|needed)\b", r"\bjoin (us|our)\b", r"\bfia\b|\bstewards?\b",
    r"\bline-?ups?\b", r"\bseats? (open|available|left)\b", r"\bdivisions?\b",
    r"\bclean racing\b", r"\bpoints? system\b", r"\bpodiums?\b",
)]
NEEDED = 3


def score(text):
    """Invite 2, a strong league word 2, each other league word 1."""
    points = 2 if INVITE.search(text) else 0
    if STRONG.search(text):
        points += 2
    points += sum(1 for w in WORDS if w.search(text))
    return points


def is_league_promo(text):
    return score(text) >= NEEDED


def reply_text(author_id, has_role):
    text = (f"<@{author_id}> Please check <#{LEAGUES_ID}>, league promos go there. "
            f"Your message here will be removed in 10 minutes.")
    if not has_role:
        text += ("\nYou need the **League Racer** role to see it, pick it in "
                 "<id:customize>.")
    return text


def due(state, now):
    """The removals whose time has come, oldest first."""
    return [p for p in state if p["at"] <= now]


# ---------------------------------------------------------------- Discord side

class Watcher:
    def __init__(self, bot, is_staff):
        self.bot = bot
        self.is_staff = is_staff  # callable(member) -> bool
        self.state = self.load()

    def load(self):
        try:
            return json.loads(STATE.read_text())
        except (OSError, ValueError):
            return []

    def save(self):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(self.state))

    async def on_message(self, message):
        if message.channel.id != PROMO_ID or message.author.bot or message.reference:
            return
        if self.is_staff(message.author):
            return
        text = message.content + " " + " ".join(e.title or "" for e in message.embeds)
        if not is_league_promo(text):
            return
        roles = getattr(message.author, "roles", [])
        has_role = any(r.id == LEAGUE_RACER_ID for r in roles)
        try:
            await message.reply(
                reply_text(message.author.id, has_role),
                allowed_mentions=discord.AllowedMentions(users=[message.author],
                                                         replied_user=True))
        except discord.HTTPException as e:
            log.warning("league promo reply failed: %s", e)
        self.state.append({"channel": message.channel.id, "id": message.id,
                           "at": message.created_at.timestamp() + DELETE_AFTER})
        self.save()
        log.info("league promo by %s, removal in 10 minutes", message.author.id)

    async def step(self):
        for p in due(self.state, time.time()):
            try:
                channel = self.bot.get_channel(p["channel"]) or await self.bot.fetch_channel(p["channel"])
                await channel.get_partial_message(p["id"]).delete()
            except discord.NotFound:
                pass  # already gone
            except discord.HTTPException as e:
                log.warning("removing league promo %s failed: %s", p["id"], e)
                if p["at"] > time.time() - 86400:
                    continue  # try again next round, give up after a day
            self.state.remove(p)
            self.save()

    async def run(self):
        while True:
            try:
                await self.step()
            except Exception as e:  # pragma: no cover
                log.warning("league promo step failed: %s", e)
            await asyncio.sleep(EVERY)
