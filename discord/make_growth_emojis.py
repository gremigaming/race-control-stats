"""Draws the green and red arrow, digit and comma emojis for the growth numbers on
the socials board and uploads them as Race Control's own app emojis. Run once;
the ids it prints go in socials_board.GROWTH_EMOJI.

    python3 discord/make_growth_emojis.py      (needs DISCORD_BOT_TOKEN, Pillow, Inter)
"""
import base64
import io
import json
import os
import time
import urllib.error
import urllib.request

from PIL import Image, ImageDraw, ImageFont

APP_ID = "1532679389429502012"  # Race Control
FONT = "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf"
H = 128
COLORS = {"g": (35, 165, 90), "r": (242, 63, 67)}


def glyph(font, ch, color):
    l, t, r, b = font.getbbox(ch)
    top8 = font.getbbox("8")
    img = Image.new("RGBA", (max(r - l + 12, 24), H), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((6 - l, (H - (top8[3] - top8[1])) // 2 - top8[1]), ch,
                             font=font, fill=color)
    return img


def arrow(up, color):
    img = Image.new("RGBA", (96, H), (0, 0, 0, 0))
    top, bottom = 26, 102
    ImageDraw.Draw(img).polygon([(48, top), (90, bottom), (6, bottom)] if up else
                                [(6, top), (90, top), (48, bottom)], fill=color)
    return img


def pictures():
    font = ImageFont.truetype(FONT, 104)
    out = {"gup": arrow(True, COLORS["g"]), "rdown": arrow(False, COLORS["r"])}
    for tone, color in COLORS.items():
        for ch in "0123456789":
            out[tone + ch] = glyph(font, ch, color)
        out[tone + "comma"] = glyph(font, ",", color)
    return out


def upload(name, img):
    data = io.BytesIO()
    img.save(data, "PNG")
    body = json.dumps({"name": "rc_" + name, "image": "data:image/png;base64,"
                       + base64.b64encode(data.getvalue()).decode()}).encode()
    req = urllib.request.Request(
        f"https://discord.com/api/v10/applications/{APP_ID}/emojis", data=body, method="POST",
        headers={"Authorization": f"Bot {os.environ['DISCORD_BOT_TOKEN']}",
                 "Content-Type": "application/json", "User-Agent": "race-control"})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req) as resp:
                return json.load(resp)["id"]
        except urllib.error.HTTPError as e:
            if e.code != 429:
                raise
            time.sleep(float(json.load(e).get("retry_after", 1)) + 0.3)
    raise RuntimeError(f"could not upload {name}")


if __name__ == "__main__":
    print(json.dumps({n: upload(n, img) for n, img in pictures().items()}, indent=1))
