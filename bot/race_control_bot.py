"""Race Control's always-online Discord bot: answers the server owner the moment
they tag it, with a reply written by Claude.

Needs DISCORD_BOT_TOKEN and ANTHROPIC_API_KEY in the environment.
Install with: pip install -r bot/requirements.txt
Run with: python3 -m bot.race_control_bot
"""
import logging
import os

import anthropic
import discord

from bot.replies import SYSTEM_PROMPT, build_messages, clean_reply, should_answer

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-5-5")
HISTORY = 15

log = logging.getLogger("race_control")
claude = anthropic.AsyncAnthropic(timeout=30.0)
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents,
                     allowed_mentions=discord.AllowedMentions.none())


def tags_me(message):
    if bot.user in message.mentions:
        return True
    me = message.guild.me if message.guild else None
    return bool(me) and any(r in message.role_mentions for r in me.roles if r.managed)


async def ask_claude(message):
    history = []
    async for m in message.channel.history(limit=HISTORY, before=message):
        history.append((m.author.display_name, m.clean_content))
    history.reverse()
    response = await claude.messages.create(
        model=MODEL,
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        output_config={"effort": "low"},
        messages=build_messages(history, message.clean_content, message.author.display_name),
    )
    if response.stop_reason == "refusal":
        return "I'll sit this one out. \U0001F6A9"
    return "".join(b.text for b in response.content if b.type == "text")


@bot.event
async def on_ready():
    log.info("Race Control is online as %s", bot.user)


@bot.event
async def on_message(message):
    if not message.guild:
        return
    if not should_answer(message.author.id, message.author.bot,
                         message.guild.owner_id, tags_me(message)):
        return
    try:
        async with message.channel.typing():
            text = await ask_claude(message)
    except anthropic.APIError as e:
        log.warning("Claude failed: %s", e)
        text = "Radio trouble, try me again in a minute. \U0001F4FB"
    await message.reply(clean_reply(text), mention_author=True)


def main():
    logging.basicConfig(level=logging.INFO)
    bot.run(os.environ["DISCORD_BOT_TOKEN"], log_handler=None)


if __name__ == "__main__":
    main()
