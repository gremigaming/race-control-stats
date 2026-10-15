"""Announcements posted at a set time, with an optional reaction role (GreMi 2026-10-15).

Each post in bot/scheduled_posts.json goes out once at post_at (Amsterdam time)
in its channel. Race Control adds the reaction itself; members who press it get
reaction_role, and lose it again when they remove their reaction.

Posted message ids are kept on disk (STATE), so a restart never posts twice and
the reaction role keeps working. A post more than LATE_LIMIT late is skipped.
The timing logic at the top has no Discord code, so it can be tested on its own.
"""
import asyncio
import datetime
import json
import logging
import os
import pathlib

import discord

from bot.replies import TZ

CONFIG_FILE = pathlib.Path(__file__).with_name("scheduled_posts.json")
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_SCHEDULED",
                                    "/var/lib/race-control/scheduled_posts.json"))
EVERY = 30  # seconds between checks
LATE_LIMIT = datetime.timedelta(hours=6)
log = logging.getLogger("race_control.scheduled_posts")


def load_config():
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))["posts"]


def load_state():
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(state):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state))


def post_time(post):
    return datetime.datetime.strptime(post["post_at"], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)


def due(posts, state, now):
    """Posts whose time has come, not yet posted and not too late."""
    return [p for p in posts if p["key"] not in state
            and post_time(p) <= now < post_time(p) + LATE_LIMIT]


def role_for(posts, state, message_id, emoji):
    """The role a reaction gives, or None."""
    for p in posts:
        if (p.get("reaction_role") and str(state.get(p["key"])) == str(message_id)
                and emoji == p.get("reaction")):
            return p["reaction_role"]
    return None


class Runner:
    def __init__(self, bot, guild_id):
        self.bot = bot
        self.guild_id = guild_id
        self.state = load_state()

    async def run(self):
        while True:
            try:
                await self.tick()
            except Exception as e:  # never let a post take the bot down
                log.warning("scheduled post failed: %s", e)
            await asyncio.sleep(EVERY)

    async def tick(self):
        posts = load_config()
        for p in due(posts, self.state, datetime.datetime.now(TZ)):
            channel = (self.bot.get_channel(p["channel"])
                       or await self.bot.fetch_channel(p["channel"]))
            roles = [discord.Object(p["ping_role"])] if p.get("ping_role") else []
            msg = await channel.send(p["text"], allowed_mentions=discord.AllowedMentions(
                everyone=False, users=False, roles=roles))
            self.state[p["key"]] = msg.id
            save_state(self.state)  # saved before the reaction, so a retry never posts twice
            if p.get("reaction"):
                await msg.add_reaction(p["reaction"])
            log.info("scheduled post %s sent (%s)", p["key"], msg.id)

    async def on_reaction(self, payload, added):
        if payload.guild_id != self.guild_id or payload.user_id == self.bot.user.id:
            return
        role_id = role_for(load_config(), self.state, payload.message_id, str(payload.emoji))
        if not role_id:
            return
        guild = self.bot.get_guild(self.guild_id)
        member = payload.member or guild.get_member(payload.user_id)
        if member is None:
            member = await guild.fetch_member(payload.user_id)
        if member.bot:
            return
        role = guild.get_role(role_id)
        try:
            if added and role not in member.roles:
                await member.add_roles(role, reason="Reaction role")
            elif not added and role in member.roles:
                await member.remove_roles(role, reason="Reaction role removed")
        except discord.DiscordException as e:
            log.warning("reaction role %s for %s failed: %s", role_id, member.id, e)
