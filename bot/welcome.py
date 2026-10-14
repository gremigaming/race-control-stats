"""The welcome sign: when someone finishes the join screen (gets the Member role),
Race Control posts a welcome card with their avatar in introductions."""
import io
import logging
import pathlib

import discord
from PIL import Image, ImageDraw, ImageFont, ImageOps

MEMBER_ROLE_ID = 1097214965640855764
WELCOME_CHANNEL_ID = 1556941692139995157  # 👤┃introductions
GENERAL_ID = 1079917340298321942
FONTS = pathlib.Path(__file__).with_name("fonts")
W, H = 1100, 400
RED = (225, 6, 0)

log = logging.getLogger("race_control")
welcomed = set()  # ids greeted since the last restart, so a role re-add doesn't repeat it


def _font(name, size):
    return ImageFont.truetype(str(FONTS / name), size)


def _fit(draw, text, name, size, width):
    """The biggest size up to `size` at which `text` fits in `width`."""
    while size > 24 and draw.textlength(text, font=_font(name, size)) > width:
        size -= 2
    return _font(name, size)


def printable(name, fallback):
    """The name without emoji and other symbols the card font can't draw."""
    clean = "".join(c for c in name if ord(c) < 0x2000).strip()
    return clean or fallback


def card(name, avatar_bytes, number):
    """PNG bytes of the welcome card."""
    img = Image.new("RGB", (W, H), (21, 22, 26))
    d = ImageDraw.Draw(img)
    # Checkered flag strip on the right, fading into the dark background
    sq = 40
    for x in range(W - 7 * sq, W, sq):
        for y in range(0, H, sq):
            if (x // sq + y // sq) % 2 == 0:
                fade = int(55 * (x - (W - 7 * sq)) / (7 * sq))
                d.rectangle([x, y, x + sq - 1, y + sq - 1], fill=(21 + fade, 22 + fade, 26 + fade))
    # Red racing stripes
    d.polygon([(0, H - 26), (W, H - 70), (W, H - 56), (0, H - 12)], fill=RED)
    d.polygon([(0, H - 8), (W, H - 52), (W, H - 46), (0, H - 2)], fill=(255, 255, 255))
    # Round avatar with a red ring
    size = 230
    try:
        av = Image.open(io.BytesIO(avatar_bytes)).convert("RGB")
    except Exception:
        av = Image.new("RGB", (size, size), (60, 62, 70))
    av = ImageOps.fit(av, (size, size))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).ellipse([0, 0, size, size], fill=255)
    ax, ay = 60, (H - size) // 2 - 20
    d.ellipse([ax - 8, ay - 8, ax + size + 8, ay + size + 8], fill=RED)
    img.paste(av, (ax, ay), mask)
    # Text
    tx = ax + size + 50
    room = W - tx - 40
    d.text((tx, 70), "WELCOME TO THE PADDOCK", font=_font("Inter-SemiBold.otf", 30), fill=RED)
    d.text((tx, 112), name, font=_fit(d, name, "InterDisplay-Black.otf", 76, room),
           fill=(255, 255, 255))
    d.text((tx, 215), f"Driver #{number} on the GreMi Gang grid",
           font=_font("Inter-SemiBold.otf", 32), fill=(185, 187, 195))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def became_member(before, after):
    had = any(r.id == MEMBER_ROLE_ID for r in before.roles)
    has = any(r.id == MEMBER_ROLE_ID for r in after.roles)
    return has and not had and not after.bot


async def greet(bot, member):
    if member.id in welcomed:
        return
    welcomed.add(member.id)
    try:
        avatar = await member.display_avatar.replace(size=256, static_format="png").read()
        png = card(printable(member.display_name, member.name), avatar, member.guild.member_count)
        channel = bot.get_channel(WELCOME_CHANNEL_ID) or await bot.fetch_channel(WELCOME_CHANNEL_ID)
        await channel.send(
            f"🏁 Lights out for {member.mention}! Welcome to the GreMi Gang. "
            f"Tell us who you are, and come say hi in <#{GENERAL_ID}>.",
            file=discord.File(io.BytesIO(png), "welcome.png"),
            allowed_mentions=discord.AllowedMentions(users=[member]))
    except Exception as e:  # a failed welcome must never take the bot down
        log.warning("welcome for %s failed: %s", member.id, e)
