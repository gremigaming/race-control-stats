"""Race Control's always-online Discord bot.

When the owner or an allow-listed moderator tags it, it answers within seconds
that it's on it and wakes Claude (a Claude Code routine), which answers or posts
a plan for a server change. When the owner reacts ✅ to such a plan, it wakes
Claude again to carry it out. Claude logs every change in the repo.

Needs DISCORD_BOT_TOKEN, ROUTINE_ID and ROUTINE_FIRE_TOKEN in the environment.
Install with: pip install -r bot/requirements.txt
Run with: python3 -m bot.race_control_bot
"""
import logging
import os

import aiohttp
import discord

from bot.replies import (ACK, FIRE_HEADERS, FIRE_URL, approval_body, is_approval,
                         load_staff, should_handle_tag, tag_body)

ROUTINE_ID = os.environ["ROUTINE_ID"]
ROUTINE_TOKEN = os.environ["ROUTINE_FIRE_TOKEN"]
OWNER_ID, MOD_IDS = load_staff()

log = logging.getLogger("race_control")
intents = discord.Intents.default()
intents.message_content = True
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


@bot.event
async def on_message(message):
    if not message.guild:
        return
    if not should_handle_tag(message.author.id, message.author.bot, tags_me(message),
                             OWNER_ID, MOD_IDS):
        return
    ack = await message.reply(ACK, mention_author=False)
    try:
        await wake_claude(tag_body(message.channel.id, message.id, ack.id,
                                   message.author.id))
    except Exception as e:
        log.warning("%s", e)
        await ack.edit(content="\U0001F4FB Radio trouble, I couldn't reach Claude. "
                               "Try again in a minute.")


@bot.event
async def on_raw_reaction_add(payload):
    if not payload.guild_id or payload.user_id != OWNER_ID:
        return
    channel = bot.get_channel(payload.channel_id) or await bot.fetch_channel(payload.channel_id)
    plan = await channel.fetch_message(payload.message_id)
    if not is_approval(str(payload.emoji), payload.user_id, OWNER_ID,
                       plan.author.id, bot.user.id, plan.content):
        return
    try:
        await wake_claude(approval_body(channel.id, plan.id, payload.user_id))
        await plan.reply("\U0001F3C1 Approved, carrying it out now.", mention_author=False)
    except Exception as e:
        log.warning("%s", e)
        await plan.reply("\U0001F4FB Radio trouble, I couldn't reach Claude. "
                         "Remove the ✅ and add it again.", mention_author=False)


def main():
    logging.basicConfig(level=logging.INFO)
    bot.run(os.environ["DISCORD_BOT_TOKEN"], log_handler=None)


if __name__ == "__main__":
    main()
