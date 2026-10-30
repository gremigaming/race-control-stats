"""New members (GreMi 2026-10-14).

When someone joins, Race Control posts a welcome card (their avatar, name and
member number) in 👋┃welcome. An hour after the first of them joined, it says
hi to all new members of that hour in one message in 💬┃general. As soon as one
of them types anything on the server, that message goes out right away.

The waiting members are kept on disk (STATE), so a restart doesn't lose them.
Texts and the on/off switch are in bot/welcome.json. The general message ends
with a question from its "questions" list; the last few asked aren't repeated
(GreMi 2026-10-30: not always "favourite track"). The batch logic at the top
has no Discord code, so it can be tested on its own.
"""
import asyncio
import io
import json
import logging
import os
import pathlib
import random
import time

WELCOME_ID = 1556941692139995157  # 👋┃welcome
GENERAL_ID = 1079917340298321942  # 💬┃general
CONFIG_FILE = pathlib.Path(__file__).with_name("welcome.json")
STATE = pathlib.Path(os.environ.get("RACE_CONTROL_WELCOME", "/var/lib/race-control/welcome.json"))
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


def pick_question(questions, recent, rng=random):
    """A random question that wasn't one of the recent ones (up to half the list)."""
    keep = len(questions) // 2
    recent = [q for q in recent if q in questions][-keep:] if keep else []
    q = rng.choice([q for q in questions if q not in recent])
    return q, (recent + [q])[-keep:] if keep else []


DEFAULT_QUESTION = "tell us your favourite track 🏁"


def general_text(member_ids, config, question=DEFAULT_QUESTION):
    mentions = [f"<@{i}>" for i in member_ids]
    key = "general_one" if len(mentions) == 1 else "general_many"
    return config[key].format(names=names(mentions), question=question)


def card_name(display_name, username):
    """The card font only has Latin letters; fancy names fall back to the username."""
    name = " ".join((display_name or "").split())
    if name and all(ord(ch) < 0x250 for ch in name):
        return name
    return username


# ---------------------------------------------------------------- the card
# Design A (GreMi 2026-10-14): the stream schedule look. Wine to teal, Anton for
# the big letters, the name in a red-to-purple bar, a faded chequered flag.

FONTS = pathlib.Path(__file__).resolve().parents[1] / "fonts"
S = 2  # drawn at twice the size, scaled down at the end for smooth edges
W, H = 1200, 450
WINE, TEAL = (74, 10, 34), (12, 38, 54)
RED, PURPLE = (214, 32, 64), (118, 46, 196)
WHITE, MUTED, LILAC = (255, 255, 255), (196, 178, 190), (190, 160, 255)


def _s(*v):
    return tuple(int(x * S) for x in v)


def _anton(size):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(str(FONTS / "Anton-Regular.ttf"), int(size * S))
    except OSError:
        return ImageFont.load_default(int(size * S))


def _mont(size, weight=700):
    from PIL import ImageFont
    try:
        f = ImageFont.truetype(str(FONTS / "Montserrat.ttf"), int(size * S))
    except OSError:
        return ImageFont.load_default(int(size * S))
    try:
        f.set_variation_by_axes([weight])
    except Exception:
        pass
    return f


def _gradient(size, c1, c2, angle):
    """c1 to c2: angle 0 left to right, 90 top to bottom, 45 diagonal."""
    from PIL import Image
    if angle == 90:
        g = Image.linear_gradient("L")
    elif angle == 0:
        g = Image.linear_gradient("L").rotate(90).transpose(Image.FLIP_LEFT_RIGHT)
    else:
        big = Image.linear_gradient("L").resize((512, 512)).rotate(90 - angle, resample=Image.BICUBIC,
                                                                     expand=True)
        b = big.size[0] // 2
        g = big.crop((b - 181, b - 181, b + 181, b + 181))
    g = g.resize(size, Image.BICUBIC)
    return Image.composite(Image.new("RGB", size, c2), Image.new("RGB", size, c1), g)


def _stripes(img, color, alpha, gap, width):
    from PIL import Image, ImageDraw
    layer = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(layer)
    w, h = img.size
    for x in range(-h, w, gap * S):
        d.polygon([(x, h), (x + width * S, h), (x + width * S + h, 0), (x + h, 0)], fill=alpha)
    img.paste(color, (0, 0), layer)


def _checker(img, x0, cols, rows, sq, alpha):
    from PIL import Image, ImageDraw
    layer = Image.new("L", img.size, 0)
    d = ImageDraw.Draw(layer)
    for c in range(cols):
        for r in range(rows):
            if (c + r) % 2 == 0:
                d.rectangle([x0 + c * sq, r * sq, x0 + (c + 1) * sq - 1, (r + 1) * sq - 1],
                            fill=int(alpha * c / (cols - 1)))
    img.paste(WHITE, (0, 0), layer)


def _shadow_text(img, xy, text, font, fill, anchor="la", blur=6, alpha=0.55):
    from PIL import Image, ImageDraw, ImageFilter
    m = Image.new("L", img.size, 0)
    ImageDraw.Draw(m).text(xy, text, font=font, fill=255, anchor=anchor)
    img.paste((0, 0, 0), (0, 0), m.filter(ImageFilter.GaussianBlur(blur * S)).point(lambda v: int(v * alpha)))
    img.paste(fill, (0, 0), m)


def _round_mask(size, r, ellipse=False):
    from PIL import Image, ImageDraw
    w, h = size
    m = Image.new("L", (w * 2, h * 2), 0)
    if ellipse:
        ImageDraw.Draw(m).ellipse([0, 0, w * 2 - 1, h * 2 - 1], fill=255)
    else:
        ImageDraw.Draw(m).rounded_rectangle([0, 0, w * 2 - 1, h * 2 - 1], radius=r * 2, fill=255)
    return m.resize(size, Image.LANCZOS)


def draw_card(name, number, avatar=None, joined=None):
    """The welcome card as PNG bytes. avatar is the member's picture (any image bytes),
    joined a date like 14 OCT 2026 (today when left out)."""
    from PIL import Image, ImageDraw, ImageFilter
    joined = joined or time.strftime("%d %b %Y").upper().lstrip("0")
    img = _gradient(_s(W, H), WINE, TEAL, 90)
    _stripes(img, WHITE, 10, gap=140, width=46)
    _checker(img, 860 * S, 9, 6, 38 * S, 40)
    d = ImageDraw.Draw(img)
    # avatar in a red-to-purple ring with a red glow
    cx, cy, r = _s(205, 225, 150)
    ring = 10 * S
    glow = Image.new("L", img.size, 0)
    ImageDraw.Draw(glow).ellipse([cx - r - ring, cy - r - ring, cx + r + ring, cy + r + ring], fill=170)
    img.paste(RED, (0, 0), glow.filter(ImageFilter.GaussianBlur(26 * S)))
    rs = (2 * (r + ring),) * 2
    img.paste(_gradient(rs, RED, PURPLE, 45), (cx - r - ring, cy - r - ring), _round_mask(rs, 0, True))
    pic = None
    if avatar:
        try:
            pic = Image.open(io.BytesIO(avatar)).convert("RGB").resize((2 * r, 2 * r), Image.LANCZOS)
        except Exception:
            pic = None
    if pic is None:
        pic = Image.new("RGB", (2 * r, 2 * r), (88, 101, 242))
    img.paste(pic, (cx - r, cy - r), _round_mask((2 * r, 2 * r), 0, True))
    # WELCOME TO THE / GRID
    tx = 400
    _shadow_text(img, _s(tx, 52), "WELCOME TO THE", _mont(26, 800), MUTED)
    _shadow_text(img, _s(tx - 3, 78), "GRID", _anton(118), WHITE)
    # the name in a striped red-to-purple bar
    label = name.upper()
    size = 56
    while size > 24 and d.textlength(label, font=_anton(size)) > 680 * S:
        size -= 2
    f = _anton(size)
    bx0, by0, by1 = tx, 248, 330
    bx1 = tx + max(320, d.textlength(label, font=f) / S + 56)
    bar = _gradient(_s(bx1 - bx0, by1 - by0), RED, PURPLE, 0)
    _stripes(bar, WHITE, 22, gap=22, width=8)
    sh = Image.new("L", img.size, 0)
    ImageDraw.Draw(sh).rounded_rectangle(_s(bx0, by0 + 8, bx1, by1 + 8), radius=18 * S, fill=150)
    img.paste((0, 0, 0), (0, 0), sh.filter(ImageFilter.GaussianBlur(10 * S)))
    img.paste(bar, _s(bx0, by0), _round_mask(bar.size, 18 * S))
    _shadow_text(img, _s(bx0 + 28, (by0 + by1) / 2 + 2), label, f, WHITE, anchor="lm", blur=3, alpha=0.6)
    # driver number and join date, GreMi Gang tag top right
    d = ImageDraw.Draw(img)
    first = f"DRIVER #{number}"
    d.text(_s(tx, 358), first, font=_mont(24, 800), fill=WHITE)
    x = tx * S + d.textlength(first, font=_mont(24, 800)) + 18 * S
    d.text((x, 358 * S), f"·  JOINED {joined}", font=_mont(24, 700), fill=LILAC)
    d.rounded_rectangle(_s(W - 300, 32, W - 36, 74), radius=12 * S, outline=WHITE, width=3 * S)
    d.text(_s(W - 168, 53), "GreMi Gang", font=_mont(22, 800), fill=WHITE, anchor="mm")
    out = io.BytesIO()
    img.resize((W, H), Image.LANCZOS).save(out, "PNG", optimize=True)
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
            ids = list(self.state["ids"])
            self.state = {**fresh(), "asked": self.state.get("asked", [])}
            self.save()
        guild = self.bot.get_guild(self.guild_id)
        still = [i for i in ids if guild is None or guild.get_member(i)]
        if not still:
            return
        config = load_config()
        async with self.lock:
            question, self.state["asked"] = pick_question(
                config.get("questions") or [DEFAULT_QUESTION], self.state.get("asked", []))
            self.save()
        try:
            ch = await self.channel(GENERAL_ID)
            await ch.send(general_text(still, config, question),
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
