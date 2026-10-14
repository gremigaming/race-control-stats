"""New members (GreMi 2026-10-14).

When someone joins, Race Control posts a welcome card (their avatar, name and
member number) in 👋┃welcome. An hour after the first of them joined, it says
hi to all new members of that hour in one message in 💬┃general. As soon as one
of them types anything on the server, that message goes out right away.

The waiting members are kept on disk (STATE), so a restart doesn't lose them.
Texts and the on/off switch are in bot/welcome.json. The batch logic at the top
has no Discord code, so it can be tested on its own.
"""
import asyncio
import io
import json
import logging
import os
import pathlib
import time

WELCOME_ID = 1556941692139995157  # 👋┃welcome
GENERAL_ID = 1079917340298321942  # 💬┃general
CONFIG_FILE = pathlib.Path(__file__).with_name("welcome.json")
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_WELCOME", "/var/lib/race-control/welcome.json"))
FONT = pathlib.Path(__file__).resolve().parents[1] / "fonts" / "Montserrat.ttf"
EVERY = 30  # seconds between checks for a batch that is due
log = logging.getLogger("race_control.welcome")


def load_config():
    return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- the batch

def fresh():
    return {"due": 0, "ids": []}


def add(state, member_id, now, wait):
    """A new member waits for the general message; the first one starts the hour."""
    if not state["ids"]:
        state["due"] = now + wait
    if member_id not in state["ids"]:
        state["ids"].append(member_id)


def remove(state, member_id):
    """Someone who left before the message isn't greeted."""
    if member_id in state["ids"]:
        state["ids"].remove(member_id)
    if not state["ids"]:
        state.update(fresh())


def is_due(state, now, typed_by=None):
    """Due an hour after the first join, or as soon as one of them types."""
    if not state["ids"]:
        return False
    return now >= state["due"] or typed_by in state["ids"]


def names(mentions):
    if len(mentions) == 1:
        return mentions[0]
    return ", ".join(mentions[:-1]) + " and " + mentions[-1]


def general_text(member_ids, config):
    mentions = [f"<@{i}>" for i in member_ids]
    key = "general_one" if len(mentions) == 1 else "general_many"
    return config[key].format(names=names(mentions))


def card_name(display_name, username):
    """The card font only has Latin letters; fancy names fall back to the username."""
    name = " ".join((display_name or "").split())
    if name and all(ord(ch) < 0x250 for ch in name):
        return name
    return username


# ---------------------------------------------------------------- the card

W, H = 1100, 400
BG = (21, 22, 26)
RED = (225, 6, 0)
WHITE = (255, 255, 255)
MUTED = (190, 192, 198)
CHECKER = ((32, 33, 38), (48, 49, 55), (62, 63, 69))


def _font(size, weight):
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(str(FONT), size)
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
        return f
    except OSError:
        return ImageFont.load_default(size)


def _fit(draw, text, size, weight, width):
    while size > 28:
        f = _font(size, weight)
        if draw.textlength(text, font=f) <= width:
            return f
        size -= 4
    return _font(size, weight)


def draw_card(name, number, avatar=None):
    """The welcome card as PNG bytes. avatar is the member's picture (any image bytes)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # chequered flag fading in on the right
    sq = 40
    for col in range(9):
        x = W - (col + 1) * sq
        for row in range(H // sq):
            if (col + row) % 2 == 0 and (col < 6 or row % 3 == 0):
                shade = CHECKER[min(2, max(0, 2 - col // 3))]
                d.rectangle([x, row * sq, x + sq - 1, (row + 1) * sq - 1], fill=shade)
    # avatar in a red ring
    size, x0, y0 = 240, 55, 55
    d.ellipse([x0 - 8, y0 - 8, x0 + size + 8, y0 + size + 8], fill=RED)
    pic = None
    if avatar:
        try:
            pic = Image.open(io.BytesIO(avatar)).convert("RGB").resize((size, size))
        except Exception:
            pic = None
    if pic is None:
        pic = Image.new("RGB", (size, size), (88, 101, 242))
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size * 4, size * 4], fill=255)
    img.paste(pic, (x0, y0), mask.resize((size, size), Image.LANCZOS))
    # texts
    tx = 345
    d.text((tx, 72), "WELCOME TO THE PADDOCK", font=_font(28, 700), fill=RED)
    d.text((tx, 160), name, font=_fit(d, name, 72, 800, W - tx - 40), fill=WHITE, anchor="lm")
    d.text((tx, 215), f"Driver #{number} on the GreMi Gang grid", font=_font(30, 600), fill=MUTED)
    # red and white stripes along the bottom
    d.polygon([(0, 372), (W, 330), (W, 344), (0, 386)], fill=RED)
    d.polygon([(0, 390), (W, 348), (W, 354), (0, 396)], fill=WHITE)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


# ---------------------------------------------------------------- Discord

class Welcomer:
    def __init__(self, bot, guild_id):
        self.bot, self.guild_id = bot, guild_id
        self.state = self.load()
        self.lock = asyncio.Lock()

    def load(self):
        try:
            return json.loads(STATE.read_text())
        except (OSError, ValueError):
            return fresh()

    def save(self):
        STATE.parent.mkdir(parents=True, exist_ok=True)
        STATE.write_text(json.dumps(self.state))

    async def channel(self, channel_id):
        return self.bot.get_channel(channel_id) or await self.bot.fetch_channel(channel_id)

    async def on_join(self, member):
        import discord
        config = load_config()
        if not config.get("on") or member.bot or member.guild.id != self.guild_id:
            return
        try:
            avatar = await member.display_avatar.replace(size=256, format="png").read()
        except discord.DiscordException:
            avatar = None
        name = card_name(member.display_name, member.name)
        try:
            png = await asyncio.to_thread(draw_card, name, member.guild.member_count, avatar)
            ch = await self.channel(WELCOME_ID)
            await ch.send(config["card_text"].format(mention=member.mention, general=GENERAL_ID),
                          file=discord.File(io.BytesIO(png), "welcome.png"),
                          allowed_mentions=discord.AllowedMentions(users=[member]))
        except Exception as e:
            log.warning("welcome card for %s not posted: %s", member.id, e)
        async with self.lock:
            add(self.state, member.id, time.time(), config.get("wait_minutes", 60) * 60)
            self.save()

    async def on_leave(self, member):
        if member.guild.id != self.guild_id:
            return
        async with self.lock:
            remove(self.state, member.id)
            self.save()

    async def on_message(self, message):
        if message.author.id in self.state["ids"]:
            await self.step(typed_by=message.author.id)

    async def step(self, typed_by=None):
        import discord
        async with self.lock:
            if not is_due(self.state, time.time(), typed_by):
                return
            ids, self.state = list(self.state["ids"]), fresh()
            self.save()
        guild = self.bot.get_guild(self.guild_id)
        still = [i for i in ids if guild is None or guild.get_member(i)]
        if not still:
            return
        try:
            ch = await self.channel(GENERAL_ID)
            await ch.send(general_text(still, load_config()),
                          allowed_mentions=discord.AllowedMentions(
                              users=[discord.Object(i) for i in still]))
        except Exception as e:
            log.warning("general welcome for %s not posted: %s", still, e)

    async def run(self):
        while True:
            try:
                await self.step()
            except Exception as e:  # never let a welcome take the bot down
                log.warning("welcome check failed: %s", e)
            await asyncio.sleep(EVERY)
