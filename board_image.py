"""Small preview pictures for the socials board.

Each platform's latest stream or video becomes its own picture, cut to 4:3,
so they all look the same next to the text. Needs Pillow (pip install pillow).
"""
import io

from PIL import Image, ImageDraw, ImageOps

# Bump when the pictures are made differently, so the live board is redrawn
LAYOUT = 9

# 4:3 rather than 16:9: Discord shows thumbnails at most 80 pixels wide, so a
# taller picture shows up bigger
SIZE = (480, 360)


def thumbnail(data):
    """PNG bytes of a 4:3 preview, or None if the picture can't be read."""
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    out = io.BytesIO()
    ImageOps.fit(img, SIZE, Image.LANCZOS).save(out, "PNG", optimize=True)
    return out.getvalue()


# The preview row under the three text columns: one small picture per platform,
# each with its own rounded corners and a line in the platform's colour.
ROW_W, TILE_GAP, BAR = 960, 24, 8


def rgb(color):
    return ((color >> 16) & 255, (color >> 8) & 255, color & 255)


def preview_row(tiles):
    """PNG bytes of the preview row. tiles: list of 3 (picture bytes or None, colour)."""
    tile_w = (ROW_W - 2 * TILE_GAP) // 3
    tile_h = tile_w * 9 // 16
    img = Image.new("RGBA", (ROW_W, tile_h + BAR + 6), (0, 0, 0, 0))
    for i, (data, color) in enumerate(tiles[:3]):
        x = i * (tile_w + TILE_GAP)
        tile = Image.new("RGBA", (tile_w, tile_h), (30, 31, 34, 255))
        try:
            if data:
                tile = ImageOps.fit(Image.open(io.BytesIO(data)).convert("RGBA"),
                                    (tile_w, tile_h), Image.LANCZOS)
        except Exception:
            pass
        mask = Image.new("L", (tile_w, tile_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, tile_w - 1, tile_h - 1), 14, fill=255)
        img.paste(tile, (x, 0), mask)
        ImageDraw.Draw(img).rounded_rectangle(
            (x, tile_h + 6, x + tile_w - 1, tile_h + 5 + BAR), BAR // 2, fill=rgb(color))
    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
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
