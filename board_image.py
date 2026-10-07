"""Small preview pictures for the socials board.

Each platform's latest stream or video becomes its own picture, cut to a wide strip,
so they all look the same under the text. Needs Pillow (pip install pillow).
"""
import io

from PIL import Image, ImageOps

# Bump when the pictures are made differently, so the live board is redrawn
LAYOUT = 18

# The preview sits under the text as the card's picture. Discord fits it to the
# card's width on PC and phone alike, which also gives all cards the same width.
# A wide 3:1 strip keeps it low, so all three cards fit on one screen.
THUMB = (480, 160)


def thumbnail(data):
    """PNG bytes of the wide preview strip, or None if the picture
    can't be read."""
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    # fully solid: see-through parts show up as a grey box in Discord, and
    # Discord rounds the corners itself
    img = ImageOps.fit(img.convert("RGB"), THUMB, Image.LANCZOS, centering=(0.5, 0.4))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


# The colour strip under the board: one slanted block per platform, in column
# order, with a soft glow. Drawn on the embed's own dark background (fully
# solid, since see-through parts show up as a grey box in Discord).
STRIP = (1000, 84)
EMBED_BG = (36, 36, 41)


def _rgb(color):
    return ((color >> 16) & 255, (color >> 8) & 255, color & 255)


def strip(colors):
    """PNG bytes of the colour strip: a slanted, glowing block per colour, so
    each platform column gets its own colour detail underneath."""
    from PIL import ImageDraw, ImageFilter
    w, h = STRIP
    scale = 2  # drawn larger and shrunk for smooth edges
    W, H = w * scale, h * scale
    gap, slant, bar = 22 * scale, 14 * scale, 16 * scale
    top = (H - bar) // 2
    seg = (W - gap * (len(colors) - 1)) / len(colors)
    glow = Image.new("RGB", (W, H), EMBED_BG)
    shapes = []
    for i, color in enumerate(colors):
        x0 = round(i * (seg + gap))
        x1 = round(x0 + seg)
        # the first and last blocks keep a straight outer edge
        left = 0 if i == 0 else slant
        right = 0 if i == len(colors) - 1 else slant
        poly = [(x0 + left, top), (x1, top), (x1 - right, top + bar), (x0, top + bar)]
        shapes.append((poly, _rgb(color), x0, x1))
        ImageDraw.Draw(glow).polygon(poly, fill=_rgb(color))
    # a soft glow in each colour behind the blocks
    glow = glow.filter(ImageFilter.GaussianBlur(10 * scale))
    img = Image.blend(Image.new("RGB", (W, H), EMBED_BG), glow, 0.55)
    for poly, rgb, x0, x1 in shapes:
        # each block runs from its colour to a lighter shade
        light = tuple(min(255, c + (255 - c) * 2 // 5) for c in rgb)
        grad = Image.new("RGB", (x1 - x0, bar))
        gd = ImageDraw.Draw(grad)
        for x in range(x1 - x0):
            t = x / max(1, x1 - x0 - 1)
            gd.line([(x, 0), (x, bar)],
                    fill=tuple(round(a + (b - a) * t) for a, b in zip(rgb, light)))
        mask = Image.new("L", (W, H), 0)
        ImageDraw.Draw(mask).polygon(poly, fill=255)
        layer = Image.new("RGB", (W, H))
        layer.paste(grad, (x0, top))
        img.paste(layer, (0, 0), mask)
        # a thin highlight along the top edge
        hi = Image.new("L", (W, H), 0)
        ImageDraw.Draw(hi).polygon([(px, min(py, top + 2 * scale)) for px, py in poly], fill=90)
        img.paste((255, 255, 255), (0, 0), Image.composite(hi, Image.new("L", (W, H), 0), mask))
    img = img.resize(STRIP, Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()


# An invisible full-width picture in each socials card: Discord stretches it to
# the widest a card can be, so all cards come out the same width. Painted in the
# card's own background colour (not see-through, which shows as a grey box).
SPACER = (1000, 2)


def spacer():
    out = io.BytesIO()
    Image.new("RGB", SPACER, EMBED_BG).save(out, "PNG", optimize=True)
    return out.getvalue()


# The thin detail line at the bottom of each socials card. Its full width also
# makes Discord draw every card the same width.
LINE = (1000, 12)


def detail_line(colors):
    """PNG bytes of a flat, rounded line on the card's background: one colour, or
    a smooth gradient through several (TikTok)."""
    from PIL import ImageDraw
    scale = 3  # drawn larger and shrunk for smooth ends
    w, h = LINE[0] * scale, LINE[1] * scale
    stops = [_rgb(c) for c in colors]
    fill = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(fill)
    for x in range(w):
        t = x / (w - 1) * (len(stops) - 1)
        i = min(int(t), len(stops) - 2) if len(stops) > 1 else 0
        a, b = stops[i], stops[min(i + 1, len(stops) - 1)]
        f = t - i if len(stops) > 1 else 0
        draw.line([(x, 0), (x, h)], fill=tuple(round(p + (q - p) * f) for p, q in zip(a, b)))
    bar = 4 * scale
    mask = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, (h - bar) // 2, w - 1, (h + bar) // 2],
                                           radius=bar // 2, fill=255)
    img = Image.new("RGB", (w, h), EMBED_BG)
    img.paste(fill, (0, 0), mask)
    out = io.BytesIO()
    img.resize(LINE, Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


def square_logo(data, size=128, margin=0.08):
    """PNG bytes of a platform logo centred on a square, see-through canvas, so
    Discord's square thumbnail never cuts it off. None if it can't be read."""
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    inner = round(size * (1 - 2 * margin))
    img.thumbnail((inner, inner), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()


def clean(text):
    """Stream titles carry emojis and chat commands; keep the readable words."""
    text = (text or "").replace("∣", "|").replace("｜", "|")
    text = "".join(ch for ch in text
                   if ord(ch) < 0x2000 or ch in "–—‘’“”")
    words = [w for w in text.split() if not w.startswith("!")]
    while words and words[-1] in "|-–—":
        words.pop()
    while words and words[0] in "|-–—":
        words.pop(0)
    out = []
    for w in words:  # no doubled separators where an emoji used to be
        if out and w in "|-" and out[-1] in "|-":
            continue
        out.append(w)
    return " ".join(out)
