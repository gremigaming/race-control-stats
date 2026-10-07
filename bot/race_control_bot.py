"""Race Control's always-online Discord bot.

When the owner or an allow-listed moderator tags it, it shows "typing..." and
answers within seconds through one small Claude API call (bot/brain.py).
Change requests, and anything it can't answer, wake Claude Code (a routine)
instead, which posts a plan for a server change. When the owner reacts ✅ to such
a plan, it wakes Claude again to carry it out. Claude logs every change in the repo.

Needs DISCORD_BOT_TOKEN, ROUTINE_ID and ROUTINE_FIRE_TOKEN in the environment.
With ANTHROPIC_API_KEY set, quick answers come from the Claude API; without it,
every tag goes to Claude Code.
Install with: pip install -r bot/requirements.txt
Run with: python3 -m bot.race_control_bot
"""
import asyncio
import datetime
import logging
import os

import time

import aiohttp
import anthropic
import discord

from bot.brain import Brain, HandOff
from bot.members import GUILD_ID, Members

from bot.replies import (FIRE_HEADERS, FIRE_URL, approval_body, is_approval,
                         load_staff, plain, should_handle_tag, tag_body)

ROUTINE_ID = os.environ["ROUTINE_ID"]
ROUTINE_TOKEN = os.environ["ROUTINE_FIRE_TOKEN"]
OWNER_ID, MOD_IDS = load_staff()
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
members = Members()
brain = Brain(API_KEY, members) if API_KEY else None

log = logging.getLogger("race_control")
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = discord.Client(intents=intents,
                     allowed_mentions=discord.AllowedMentions.none())


def tags_me(message):
    if bot.user in message.mentions:
        return True
    return any(r in message.role_mentions for r in message.guild.me.roles if r.managed)


async def wake_claude(body):
    headers = dict(FIRE_HEADERS, Authorization=f"Bearer {ROUTINE_TOKEN}")
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as s:
        async with s.post(FIRE_URL.format(ROUTINE_ID), headers=headers, data=body) as r:
            if r.status >= 300:
                raise RuntimeError(f"routine fire failed: {r.status}")


@bot.event
async def on_ready():
    log.info("Race Control is online as %s", bot.user)
    if not members.backfilled():
        asyncio.create_task(backfill())
    if not getattr(bot, "saving", False):
        bot.saving = True
        asyncio.create_task(save_members())


async def save_members():
    """Writes the member counts to disk every minute, even in quiet hours."""
    while True:
        await asyncio.sleep(60)
        members.maybe_save(force=True)


async def backfill(days=90):
    """Once, on the first start with member memory: counts and archives the last 90 days."""
    after = discord.utils.utcnow() - datetime.timedelta(days=days)
    # Stop where live counting started, so nothing is counted twice
    started = min([m["first_seen"] for m in members.data.values()] + [time.time()])
    before = datetime.datetime.fromtimestamp(started, datetime.timezone.utc)
    seen = 0
    for guild in [g for g in bot.guilds if g.id == GUILD_ID]:
        channels = list(guild.text_channels) + list(guild.threads)
        for ch in channels:
            if not ch.permissions_for(guild.me).read_message_history:
                continue
            try:
                async for m in ch.history(limit=None, after=after, before=before,
                                            oldest_first=True):
                    if m.author.bot:
                        continue
                    at = m.created_at.timestamp()
                    members.message(m.author.id, plain(ch.name), at)
                    members.archive(m.id, m.author.id, plain(ch.name), m.clean_content, at)
                    seen += 1
            except discord.DiscordException as e:
                log.warning("backfill skipped %s: %s", ch.id, e)
    members.mark_backfilled()
    members.maybe_save(force=True)
    log.info("backfill done: %s messages", seen)


# Tags and plans Claude is working on: message id -> set when Claude's reply lands
waiting = {}
# Posted when a quick answer isn't enough; Claude Code's answer follows in a minute
RESEARCH_NOTE = "\U0001F50E Let me do some research, I'll be back shortly."
TYPING_FOR = 300  # seconds to keep showing "Race Control is typing..."


async def type_until_answered(channel, message_id):
    done = waiting.setdefault(message_id, asyncio.Event())
    try:
        async with channel.typing():
            await asyncio.wait_for(done.wait(), TYPING_FOR)
    except asyncio.TimeoutError:
        log.warning("no answer for %s after %ss", message_id, TYPING_FOR)
    finally:
        waiting.pop(message_id, None)


async def hand_over(channel, message_id, body, failed_text, reply_to):
    try:
        await wake_claude(body)
    except Exception as e:
        log.warning("%s", e)
        await reply_to.reply(failed_text, mention_author=False)
        return
    await type_until_answered(channel, message_id)


@bot.event
async def on_message(message):
    if not message.guild:
        return
    if not message.author.bot and message.guild.id == GUILD_ID:
        at = message.created_at.timestamp()
        members.message(message.author.id, plain(message.channel.name), at)
        members.archive(message.id, message.author.id, plain(message.channel.name),
                        message.clean_content, at)
    ref = message.reference.message_id if message.reference else None
    if message.author == bot.user and ref in waiting:
        waiting[ref].set()  # Claude answered, stop typing
        return
    if not should_handle_tag(message.author.id, message.author.bot, tags_me(message),
                             OWNER_ID, MOD_IDS):
        return
    if brain:
        try:
            async with message.channel.typing():
                text = await brain.answer(message)
            secs = round(time.time() - message.created_at.timestamp())
            await message.reply(f"{text}\n-# \u23F1\uFE0F {secs} s", mention_author=False)
            return
        except HandOff as e:
            log.info("handing to Claude Code: %s", e)
            await message.reply(RESEARCH_NOTE, mention_author=False)
        except anthropic.APIError as e:
            log.warning("Claude API failed, handing to Claude Code: %s", e)
    await hand_over(message.channel, message.id,
                    tag_body(message.channel.id, message.id, message.author.id),
                    "\U0001F4FB Radio trouble, I couldn't reach Claude. Try again in a minute.",
                    message)


@bot.event
async def on_voice_state_update(member, before, after):
    if (member.bot or member.guild.id != GUILD_ID
            or (before.channel is None) == (after.channel is None)):
        return
    members.voice(member.id, joined=after.channel is not None)


@bot.event
async def on_raw_reaction_add(payload):
    if not payload.guild_id or payload.user_id != OWNER_ID:
        return
    channel = bot.get_channel(payload.channel_id) or await bot.fetch_channel(payload.channel_id)
    plan = await channel.fetch_message(payload.message_id)
    if not is_approval(str(payload.emoji), payload.user_id, OWNER_ID,
                       plan.author.id, bot.user.id, plan.content):
        return
    await hand_over(channel, plan.id, approval_body(channel.id, plan.id, payload.user_id),
                    "\U0001F4FB Radio trouble, I couldn't reach Claude. "
                    "Remove the \u2705 and add it again.", plan)


def main():
    logging.basicConfig(level=logging.INFO)
    bot.run(os.environ["DISCORD_BOT_TOKEN"], log_handler=None)


if __name__ == "__main__":
    main()
