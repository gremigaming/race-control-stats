"""Small preview pictures for the socials board.

Each platform's latest stream or video becomes its own picture, cut to 16:9,
so they all look the same under the text. Needs Pillow (pip install pillow).
"""
import io

from PIL import Image, ImageDraw, ImageOps

# Bump when the pictures are made differently, so the live board is redrawn
LAYOUT = 10

# The preview sits under the text. Discord stretches a card's picture to the
# card's width, so the 16:9 preview is drawn on the left half of a wider
# see-through canvas: it shows at about half the card width on every screen.
THUMB = (480, 270)
CANVAS = (960, 270)


def thumbnail(data):
    """PNG bytes of the preview (rounded corners, left half of the canvas), or None
    if the picture can't be read."""
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGBA")
    except Exception:
        return None
    img = ImageOps.fit(img, THUMB, Image.LANCZOS)
    mask = Image.new("L", THUMB, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, THUMB[0] - 1, THUMB[1] - 1), 18, fill=255)
    canvas = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    canvas.paste(img, (0, 0), mask)
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
