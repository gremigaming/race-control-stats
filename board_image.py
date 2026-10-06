"""Draws the socials board picture: three platform columns side by side.

Each column has the platform's own colour, its follower number and a small
preview of the latest stream or video. Needs Pillow (pip install pillow).
"""
import io
import os

from PIL import Image, ImageDraw, ImageFont, ImageOps

FONTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")

# Discord shows a single picture about 550 pixels wide, so everything is drawn
# large enough to stay readable at a little over half size.
W, H = 1000, 500
PAD = 16             # outside edge
GAP = 16             # between columns
INNER = 22           # inside a column
COL_W = (W - 2 * PAD - 2 * GAP) // 3

PANEL = (30, 31, 34, 255)
EDGE = (255, 255, 255, 18)
TEXT = (242, 243, 245, 255)
MUTED = (148, 155, 164, 255)
LIVE_RED = (237, 66, 69, 255)


def font(weight, size):
    return ImageFont.truetype(os.path.join(FONTS, f"Inter-{weight}.otf"), size)


def rgba(color):
    return ((color >> 16) & 255, (color >> 8) & 255, color & 255, 255)


def picture(data, size):
    """Bytes of an image, cropped to fill size, or None if it can't be read."""
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    return ImageOps.fit(img, size, Image.LANCZOS)


def rounded(img, radius):
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, img.size[0] - 1, img.size[1] - 1),
                                           radius, fill=255)
    img.putalpha(mask)
    return img


def clean(text):
    """Stream titles carry emojis and chat commands; keep the readable words."""
    text = (text or "").replace("\u2223", "|").replace("\uFF5C", "|")
    text = "".join(ch for ch in text if ord(ch) < 0x2000 or ch in "\u2013\u2014\u2018\u2019\u201C\u201D")
    words = [w for w in text.split() if not w.startswith("!")]
    while words and words[-1] in "|-\u2013\u2014":
        words.pop()
    while words and words[0] in "|-\u2013\u2014":
        words.pop(0)
    out = []
    for w in words:  # no doubled separators where an emoji used to be
        if out and w in "|-" and out[-1] in "|-":
            continue
        out.append(w)
    return " ".join(out)


def wrap(draw, text, fnt, width, lines):
    """Splits text into at most `lines` lines that fit width, with an ellipsis."""
    words, out, line = (text or "").split(), [], ""
    for word in words:
        test = f"{line} {word}".strip()
        if draw.textlength(test, font=fnt) <= width:
            line = test
            continue
        if line:
            out.append(line)
        line = word
        if len(out) == lines:
            break
    if line and len(out) < lines:
        out.append(line)
    used = " ".join(out).split()
    if len(used) < len(words) and out:
        last = out[-1]
        while last and draw.textlength(last + "…", font=fnt) > width:
            last = last[:-1]
        out[-1] = last.rstrip() + "…"
    return out


def logo(col, size):
    img = picture(col.get("logo"), (size, size))
    if img is not None:
        return img
    # no logo picture: a coloured circle with the first letter
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((0, 0, size - 1, size - 1), fill=rgba(col["color"]))
    f = font("Bold", size // 2)
    d.text((size / 2, size / 2), col["name"][0], font=f, fill=TEXT, anchor="mm")
    return img


def draw_column(img, x, col):
    d = ImageDraw.Draw(img)
    top, bottom = PAD, H - PAD
    accent = rgba(col["color"])
    d.rounded_rectangle((x, top, x + COL_W, bottom), 18, fill=PANEL, outline=EDGE, width=2)
    # brand colour line along the top edge
    bar = Image.new("RGBA", (COL_W + 1, 36), (0, 0, 0, 0))
    ImageDraw.Draw(bar).rounded_rectangle((0, 0, COL_W, 35), 18, fill=accent)
    bar = bar.crop((0, 0, COL_W + 1, 7))
    img.alpha_composite(bar, (x, top))

    cx, y = x + INNER, top + 30
    img.alpha_composite(logo(col, 40), (cx, y))
    d.text((cx + 52, y + 20), col["name"], font=font("Bold", 31), fill=TEXT, anchor="lm")
    if col.get("badge"):
        f = font("Bold", 18)
        label = col["badge"]
        bw = int(d.textlength(label, font=f)) + 26
        bx = x + COL_W - INNER - bw
        fill = LIVE_RED if col.get("live") else (78, 80, 88, 255)
        d.rounded_rectangle((bx, y + 6, bx + bw, y + 34), 14, fill=fill)
        d.text((bx + bw / 2, y + 20), label, font=f, fill=TEXT, anchor="mm")

    y += 58
    number = f"{col['count']:,}" if col.get("count") is not None else "—"
    d.text((cx, y), number, font=font("ExtraBold", 64), fill=TEXT, anchor="lt")
    d.text((cx, y + 74), col["word"], font=font("SemiBold", 25), fill=MUTED, anchor="lt")

    # preview: label, then the picture across the column, then the title
    preview = col.get("preview")
    py = y + 122
    tw = COL_W - 2 * INNER
    th = tw * 9 // 16
    label_font, title_font = font("Bold", 17), font("SemiBold", 20)
    if not preview:
        # keep the columns the same height: a quiet box with the logo
        d.rounded_rectangle((cx, py + 26, cx + tw, py + 26 + th), 12, fill=(43, 45, 49, 255))
        img.alpha_composite(logo(col, 56), (cx + tw // 2 - 28, py + 26 + th // 2 - 44))
        d.text((cx + tw / 2, py + 26 + th // 2 + 30), f"Follow on {col['name']}",
               font=font("SemiBold", 19), fill=MUTED, anchor="mm")
        return
    label = (wrap(d, preview["label"].upper(), label_font, tw, 1) or [""])[0]
    d.text((cx, py), label, font=label_font,
           fill=LIVE_RED if col.get("live") else accent, anchor="lt")
    thumb = picture(preview.get("image"), (tw, th))
    if thumb is not None:
        img.alpha_composite(rounded(thumb, 12), (cx, py + 26))
    else:
        d.rounded_rectangle((cx, py + 26, cx + tw, py + 26 + th), 12, fill=(43, 45, 49, 255))
    for i, line in enumerate(wrap(d, clean(preview.get("title", "")), title_font, tw, 2)):
        d.text((cx, py + 26 + th + 30 + i * 25), line, font=title_font, fill=TEXT, anchor="ls")


def render(columns):
    """PNG bytes of the board. columns: list of 3 dicts with name, color, count,
    word, optional badge/live, logo (image bytes) and preview
    {label, title, image (bytes)}."""
    img = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    for i, col in enumerate(columns[:3]):
        draw_column(img, PAD + i * (COL_W + GAP), col)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
