"""Draws the green and red arrow, digit and comma emojis for the growth numbers on
the socials board and uploads them as Race Control's own app emojis. Run once;
the ids it prints go in socials_board.GROWTH_EMOJI.

    python3 discord/make_growth_emojis.py      (needs DISCORD_BOT_TOKEN, Pillow, Inter)
    python3 discord/make_growth_emojis.py --labels   arrow+number emojis 1-99 (rc3_),
                                                     ids go in growth_emojis.json
    python3 discord/make_growth_emojis.py --preview   writes the pictures only
"""
import base64
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request

from PIL import Image, ImageDraw, ImageFont

APP_ID = "1532679389429502012"  # Race Control
FONT = "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf"
H = 128
COLORS = {"g": (35, 165, 90), "r": (242, 63, 67)}
# Discord draws an emoji 1.375 times the text size with its bottom 0.4 text
# sizes below the line, so the text's baseline sits at y=91. The glyphs are
# drawn smaller than the text (about 52 high) and stand on that baseline.
BASE, TALL = 91, 52
PREFIX = "rc2_"


def glyph(font, ch, color):
    l, t, r, b = font.getbbox(ch)
    bottom8 = font.getbbox("8")[3]
    img = Image.new("RGBA", (max(r - l + 6, 16), H), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((3 - l, BASE - bottom8), ch, font=font, fill=color)
    return img


def arrow(up, color):
    img = Image.new("RGBA", (TALL + 8, H), (0, 0, 0, 0))
    top, bottom, w = BASE - TALL + 6, BASE, TALL + 8
    ImageDraw.Draw(img).polygon([(w / 2, top), (w - 4, bottom), (4, bottom)] if up else
                                [(4, top), (w - 4, top), (w / 2, bottom)], fill=color)
    return img


def pictures():
    font = ImageFont.truetype(FONT, 72)
    out = {"gup": arrow(True, COLORS["g"]), "rdown": arrow(False, COLORS["r"])}
    for tone, color in COLORS.items():
        for ch in "0123456789":
            out[tone + ch] = glyph(font, ch, color)
        out[tone + "comma"] = glyph(font, ",", color)
    return out


def upload(name, img):
    data = io.BytesIO()
    img.save(data, "PNG")
    return upload_as(PREFIX + name, data.getvalue())


def upload_as(name, png):
    body = json.dumps({"name": name, "image": "data:image/png;base64,"
                       + base64.b64encode(png).decode()}).encode()
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


if __name__ == "__main__" and "--labels" not in sys.argv:
    if "--preview" in sys.argv:
        for n, img in pictures().items():
            img.save(f"{n}.png")
    else:
        print(json.dumps({n: upload(n, img) for n, img in pictures().items()}, indent=1))


# ---------- whole labels (arrow and number in one emoji) ----------
# Discord centres every emoji in a square box, so an arrow emoji next to a digit
# emoji shows a gap. For weekly growth up to 99 the arrow and number are drawn
# together in one picture, against the left edge, sitting on the text baseline.
LABEL_MAX = 99


def label(font, up, number, color):
    text = str(number)
    l, t, r, b = font.getbbox(text)
    bottom8 = font.getbbox("8")[3]
    aw, gap = TALL - 6, 8
    w = aw + gap + (r - l)
    big = Image.new("RGBA", (w + 4, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(big)
    top, bottom = BASE - TALL + 10, BASE
    d.polygon([(2 + aw / 2, top), (2 + aw, bottom), (2, bottom)] if up else
              [(2, top), (2 + aw, top), (2 + aw / 2, bottom)], fill=color)
    d.text((2 + aw + gap - l, BASE - bottom8), text, font=font, fill=color)
    if big.width > H:  # shrink wide labels, keeping them on the baseline
        s = H / big.width
        small = big.resize((H, round(H * s)), Image.LANCZOS)
        big = Image.new("RGBA", (H, H), (0, 0, 0, 0))
        big.alpha_composite(small, (0, round(BASE - BASE * s)))
    img = Image.new("RGBA", (H, H), (0, 0, 0, 0))
    img.alpha_composite(big, (0, 0))
    return img


def labels():
    font = ImageFont.truetype(FONT, 72)
    out = {}
    for n in range(1, LABEL_MAX + 1):
        out[f"g{n}"] = label(font, True, n, COLORS["g"])
        out[f"r{n}"] = label(font, False, n, COLORS["r"])
    return out


if __name__ == "__main__" and "--labels" in sys.argv:
    pics = labels()
    if "--preview" in sys.argv:
        for n in ("g1", "g7", "g24", "g99", "r1", "r13"):
            pics[n].save(f"label-{n}.png")
    else:
        out = {}
        for n, img in pics.items():
            data = io.BytesIO()
            img.save(data, "PNG")
            out[n] = upload_as("rc3_" + n, data.getvalue())
        print(json.dumps(out, indent=1))
