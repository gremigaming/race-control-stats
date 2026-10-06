"""Small preview pictures for the socials board.

Each platform's latest stream or video becomes its own picture, cut to 16:9,
so they all look the same next to the text. Needs Pillow (pip install pillow).
"""
import io

from PIL import Image, ImageOps

# Bump when the pictures are made differently, so the live board is redrawn
LAYOUT = 4

SIZE = (480, 270)


def thumbnail(data):
    """PNG bytes of a 16:9 preview, or None if the picture can't be read."""
    if not data:
        return None
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:
        return None
    out = io.BytesIO()
    ImageOps.fit(img, SIZE, Image.LANCZOS).save(out, "PNG", optimize=True)
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
